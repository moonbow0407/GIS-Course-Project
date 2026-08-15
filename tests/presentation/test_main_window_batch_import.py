"""主窗口批量导入空间数据测试。"""

import os
import threading
import time
from pathlib import Path

# 必须在导入 Qt 前启用无界面平台，测试才能在没有显示器的环境运行。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from affine import Affine
from pyproj import CRS
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QProgressDialog
from rasterio.enums import Resampling
from shapely.geometry import Point

from app.application.errors import (
    CoordinateReferenceSystemRequired,
    UnsupportedVectorFormat,
)
from app.domain.feature import Feature
from app.domain.raster_layer import RasterLayer
from app.domain.vector_layer import VectorLayer
from app.infrastructure.file_io.raster_overview_service import RasterOverviewResult
from app.presentation.main_window import MainWindow, _OpenDataRequest


def _pump_until_open_finished(application: QApplication, window: MainWindow) -> None:
    """处理 Qt 事件，直到后台文件队列完成。"""
    deadline = time.monotonic() + 3.0
    while window._open_data_progress_dialog is not None and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.005)
    application.processEvents()
    assert window._open_data_progress_dialog is None


def _pump_until_overview_finished(application: QApplication, window: MainWindow) -> None:
    """处理 Qt 事件，直到自动 Overview 队列完成。"""
    deadline = time.monotonic() + 3.0
    while (
        getattr(window, "_overview_worker", None) is not None
        or getattr(window, "_overview_pending", [])
    ) and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.005)
    application.processEvents()
    assert getattr(window, "_overview_worker", None) is None


def _raster_layer(path: Path, index: int) -> RasterLayer:
    """创建可提交到真实应用服务的单像元测试栅格。"""
    values = np.array([[[index]]], dtype=np.int16)
    image = np.array([[[index, index, index, 255]]], dtype=np.uint8)
    return RasterLayer.create(
        layer_id=f"fake-{index}",
        name=path.stem,
        raster_data=values,
        image_data=image,
        valid_mask=np.ones((1, 1), dtype=np.bool_),
        transform=Affine.identity(),
        crs=CRS.from_epsg(4326),
        bounds=(0.0, 0.0, 1.0, 1.0),
        source_path=path,
    )


def _vector_layer(path: Path, layer_id: str, crs: CRS) -> VectorLayer:
    """创建用于首图层 CRS 确认流程的点图层。"""
    point = Point(500_000.0, 3_300_000.0) if crs == CRS.from_epsg(4549) else Point(120, 30)
    return VectorLayer.create(
        layer_id=layer_id,
        name=path.stem,
        features=(Feature(1, point, {}),),
        crs=crs,
        source_path=path,
    )


def test_open_data_reads_selected_raster_off_main_thread(monkeypatch) -> None:
    """栅格读取不应阻塞 Qt 主线程。"""
    application: QApplication = QApplication.instance() or QApplication([])
    window: MainWindow = MainWindow()
    main_thread_id: int = threading.get_ident()
    reader_thread_ids: list[int] = []

    monkeypatch.setattr(
        window,
        "_select_spatial_data_files",
        lambda: [str(Path("large-dem.tif"))],
    )

    def prepare_open_data(
        path: Path,
        layer_name: str | None = None,
        source_crs_override: object | None = None,
    ) -> RasterLayer:
        reader_thread_ids.append(threading.get_ident())
        return _raster_layer(path, 1)

    monkeypatch.setattr(window._application, "prepare_open_data", prepare_open_data)
    monkeypatch.setattr(window, "_refresh_workspace", lambda *args, **kwargs: None)
    monkeypatch.setattr(window, "_confirm_project_switch", lambda: True)

    window._open_data()
    _pump_until_open_finished(application, window)

    assert reader_thread_ids
    assert reader_thread_ids[0] != main_thread_id
    window.close()


def test_open_data_accepts_multiple_files_and_reports_partial_failures(monkeypatch) -> None:
    """多选文件应逐个加载，单项失败不能阻止其余文件加入工作区。"""
    application: QApplication = QApplication.instance() or QApplication([])
    window: MainWindow = MainWindow()
    selected_paths: list[str] = [
        str(Path("roads.geojson")),
        str(Path("broken.xyz")),
        str(Path("elevation.tif")),
    ]
    opened_paths: list[Path] = []
    refresh_count: list[int] = []
    warning_messages: list[tuple[str, str]] = []

    monkeypatch.setattr(
        window,
        "_select_spatial_data_files",
        lambda: selected_paths,
    )
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("批量导入应使用支持 Ctrl/Shift 多选的文件对话框")
        ),
    )

    def prepare_open_data(
        path: Path,
        layer_name: str | None = None,
        source_crs_override: object | None = None,
    ) -> RasterLayer:
        opened_paths.append(path)
        if path.suffix == ".xyz":
            raise UnsupportedVectorFormat(f"不支持的数据格式：{path.suffix}")
        return _raster_layer(path, len(opened_paths))

    monkeypatch.setattr(window._application, "prepare_open_data", prepare_open_data)
    monkeypatch.setattr(
        window,
        "_refresh_workspace",
        lambda *args, **kwargs: refresh_count.append(1),
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, message: warning_messages.append((title, message)),
    )
    monkeypatch.setattr(window, "_confirm_project_switch", lambda: True)

    window._open_data()
    _pump_until_open_finished(application, window)

    assert opened_paths == [Path(path) for path in selected_paths]
    assert refresh_count == [1]
    assert window._ready_label.text() == "已加载  2 个数据"
    assert warning_messages == [
        ("部分数据打开失败", "broken.xyz：不支持的数据格式：.xyz")
    ]
    window.close()


def test_open_data_retries_unknown_crs_from_main_thread(monkeypatch) -> None:
    """后台发现 CRS 缺失后应由主线程询问，并使用用户定义重新读取。"""
    application: QApplication = QApplication.instance() or QApplication([])
    window = MainWindow()
    selected_crs = CRS.from_epsg(3857)
    received_overrides: list[object | None] = []

    monkeypatch.setattr(
        window,
        "_select_spatial_data_files",
        lambda: [str(Path("unknown-crs.tif"))],
    )

    def prepare_open_data(
        path: Path,
        layer_name: str | None = None,
        source_crs_override: object | None = None,
    ) -> RasterLayer:
        received_overrides.append(source_crs_override)
        if source_crs_override is None:
            raise CoordinateReferenceSystemRequired("数据未声明坐标参考系统")
        return _raster_layer(path, 1)

    monkeypatch.setattr(window._application, "prepare_open_data", prepare_open_data)
    monkeypatch.setattr(window, "_prompt_layer_crs", lambda name: selected_crs)
    monkeypatch.setattr(window, "_refresh_workspace", lambda *args, **kwargs: None)
    monkeypatch.setattr(window, "_confirm_project_switch", lambda: True)

    window._open_data()
    _pump_until_open_finished(application, window)

    assert received_overrides == [None, selected_crs]
    assert window._ready_label.text() == "已加载  unknown-crs.tif"
    window.close()


def test_loaded_raster_builds_overview_in_background_and_refreshes_viewport(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """文件加载完成后应后台自动优化，并让后续视口读取使用新缓存。"""
    application: QApplication = QApplication.instance() or QApplication([])
    window = MainWindow()
    source_path = tmp_path / "large-dem.tif"
    source_path.touch()
    main_thread_id = threading.get_ident()
    optimize_thread_ids: list[int] = []
    refresh_requests: list[bool] = []

    class RecordingOverviewService:
        """记录自动优化所在的线程。"""

        def optimize(
            self,
            path: Path,
            *,
            resampling: Resampling = Resampling.average,
        ) -> RasterOverviewResult:
            optimize_thread_ids.append(threading.get_ident())
            return RasterOverviewResult(
                source_path=path,
                display_path=path,
                factors=(2, 4),
                built=True,
                reason="automatic_threshold",
            )

    monkeypatch.setattr(
        window,
        "_select_spatial_data_files",
        lambda: [str(source_path)],
    )
    monkeypatch.setattr(
        window._application,
        "prepare_open_data",
        lambda path, layer_name=None, source_crs_override=None: _raster_layer(path, 1),
    )
    window._overview_service = RecordingOverviewService()
    monkeypatch.setattr(window._map_canvas, "set_snapshot", lambda snapshot: None)
    monkeypatch.setattr(
        window._map_canvas,
        "schedule_viewport_refresh",
        lambda *, force=False: refresh_requests.append(force),
    )
    monkeypatch.setattr(window, "_confirm_project_switch", lambda: True)

    window._open_data()
    _pump_until_open_finished(application, window)
    _pump_until_overview_finished(application, window)

    assert optimize_thread_ids
    assert optimize_thread_ids[0] != main_thread_id
    assert refresh_requests == [True]
    window.close()


def test_open_data_progress_dialog_receives_determinate_updates(monkeypatch) -> None:
    """大文件读取应在后台回报可显示的进度文本，不能只给无限转圈。"""
    application: QApplication = QApplication.instance() or QApplication([])
    window: MainWindow = MainWindow()
    progress_events: list[tuple[int, int, str]] = []

    monkeypatch.setattr(
        window,
        "_select_spatial_data_files",
        lambda: [str(Path("dem.tif"))],
    )

    def prepare_open_data(
        path: Path,
        layer_name: str | None = None,
        source_crs_override: object | None = None,
        progress_callback=None,
    ) -> RasterLayer:
        del layer_name, source_crs_override
        if progress_callback is not None:
            progress_callback(0, 4, "正在准备显示金字塔…")
            progress_callback(2, 4, "正在读取预览…")
            progress_callback(4, 4, "读取完成")
        return _raster_layer(path, 1)

    def capture_progress(current: int, total: int, message: str) -> None:
        progress_events.append((current, total, message))
        dialog = window._open_data_progress_dialog
        if dialog is not None:
            progress_events.append((dialog.minimum(), dialog.maximum(), dialog.labelText()))

    monkeypatch.setattr(window._application, "prepare_open_data", prepare_open_data)
    monkeypatch.setattr(window, "_refresh_workspace", lambda *args, **kwargs: None)
    monkeypatch.setattr(window, "_on_open_data_progress", capture_progress)
    monkeypatch.setattr(window, "_confirm_project_switch", lambda: True)

    window._open_data()
    _pump_until_open_finished(application, window)

    assert any(total > 0 and current == total for current, total, _message in progress_events)
    assert any("金字塔" in message or "预览" in message for _c, _t, message in progress_events)
    window.close()


def test_first_unsuitable_layer_can_choose_independent_display_crs(monkeypatch) -> None:
    """首图层确认应在提交前设置地图 CRS，后续图层不得再次改变画布。"""
    QApplication.instance() or QApplication([])
    window = MainWindow()
    choices: list[str] = []
    monkeypatch.setattr(window, "_confirm_project_switch", lambda: True)

    def choose(layer: object) -> tuple[bool, CRS | None]:
        choices.append(layer.layer_id)
        return True, CRS.from_epsg(4490)

    monkeypatch.setattr(window, "_choose_initial_display_crs", choose)

    first_path = Path("local-grid.shp")
    window._open_data_current = _OpenDataRequest(first_path)
    first = _vector_layer(first_path, "local-grid", CRS.from_epsg(4549))
    window._on_open_data_prepared(first)

    second_path = Path("global.geojson")
    window._open_data_current = _OpenDataRequest(second_path)
    second = _vector_layer(second_path, "global", CRS.from_epsg(4326))
    window._on_open_data_prepared(second)

    snapshot = window._application.snapshot()
    assert choices == ["local-grid"]
    assert snapshot.display_crs == CRS.from_epsg(4490)
    assert tuple(item.layer.crs for item in snapshot.layers) == (
        CRS.from_epsg(4549),
        CRS.from_epsg(4326),
    )
    window.close()


class _FinishedWorker:
    """只提供 deleteLater，用于在确认框期间模拟 QThread.finished。"""

    def deleteLater(self) -> None:
        return None


def _begin_open_data_batch(window: MainWindow, path: Path) -> _FinishedWorker:
    """构造一个已显示进度框、等待提交的打开队列状态。"""
    progress = QProgressDialog(window)
    progress.setAutoClose(False)
    progress.setAutoReset(False)
    window._open_data_progress_dialog = progress
    window._open_data_current = _OpenDataRequest(path)
    window._open_data_pending = []
    window._open_data_loaded_paths = []
    window._open_data_failures = []
    window._open_data_warnings = []
    worker = _FinishedWorker()
    window._open_data_worker = worker  # type: ignore[assignment]
    return worker


def test_open_data_crs_confirmation_survives_finished_reentry(monkeypatch) -> None:
    """确认显示 CRS 的嵌套循环处理 finished 时，不得提前销毁进度框。"""
    application: QApplication = QApplication.instance() or QApplication([])
    window = MainWindow()
    path = Path("narrow-zone.shp")
    layer = _vector_layer(path, "narrow-zone", CRS.from_epsg(4549))
    worker = _begin_open_data_batch(window, path)
    monkeypatch.setattr(window, "_refresh_workspace", lambda *args, **kwargs: None)
    monkeypatch.setattr(window, "_confirm_project_switch", lambda: True)

    def choose(_layer: object) -> tuple[bool, CRS | None]:
        window._on_open_data_finished(worker)  # type: ignore[arg-type]
        # QDialog.exec() 会泵出 DeferredDelete；普通 processEvents 不会。
        application.processEvents()
        application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        return True, CRS.from_epsg(4490)

    monkeypatch.setattr(window, "_choose_initial_display_crs", choose)

    window._on_open_data_prepared(layer)
    application.processEvents()

    snapshot = window._application.snapshot()
    assert len(snapshot.layers) == 1
    assert snapshot.display_crs == CRS.from_epsg(4490)
    assert window._open_data_progress_dialog is None
    window.close()


def test_unknown_crs_prompt_does_not_finish_batch_before_retry(monkeypatch) -> None:
    """定义缺失 CRS 的嵌套循环不得在重新入队前提前清空打开队列。"""
    application: QApplication = QApplication.instance() or QApplication([])
    window = MainWindow()
    path = Path("unknown-crs.tif")
    worker = _begin_open_data_batch(window, path)
    start_calls: list[tuple[Path, CRS | None]] = []
    monkeypatch.setattr(window, "_confirm_project_switch", lambda: True)

    def start_next() -> None:
        current = window._open_data_pending[0] if window._open_data_pending else None
        start_calls.append(
            (current.path, current.source_crs_override) if current is not None else (Path("."), None)
        )

    def prompt(_name: str) -> CRS:
        window._on_open_data_finished(worker)  # type: ignore[arg-type]
        application.processEvents()
        application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert start_calls == []
        assert window._open_data_progress_dialog is not None
        return CRS.from_epsg(3857)

    monkeypatch.setattr(window, "_start_next_open_data", start_next)
    monkeypatch.setattr(window, "_prompt_layer_crs", prompt)

    window._on_open_data_failed(CoordinateReferenceSystemRequired("数据未声明坐标参考系统"))
    application.processEvents()

    assert start_calls == [(path, CRS.from_epsg(3857))]
    assert window._open_data_progress_dialog is not None
    window.close()
