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
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from rasterio.enums import Resampling

from app.application.errors import (
    CoordinateReferenceSystemRequired,
    UnsupportedVectorFormat,
)
from app.domain.raster_layer import RasterLayer
from app.infrastructure.file_io.raster_overview_service import RasterOverviewResult
from app.presentation.main_window import MainWindow


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
