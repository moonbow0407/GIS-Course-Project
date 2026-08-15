"""主窗口栅格分析入口回归测试。"""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from affine import Affine
from pyproj import CRS
from PySide6.QtWidgets import QApplication

from app.application.results import DisplayCrsPreparation
from app.domain.raster_layer import RasterLayer
from app.presentation.main_window import MainWindow


def test_main_window_routes_all_raster_analysis_actions(
    monkeypatch,
) -> None:
    """Ribbon 中的四项实验要求栅格分析动作应路由到对应处理方法。"""
    qt_application = QApplication.instance() or QApplication([])
    window = MainWindow()
    called: list[str] = []
    actions = (
        ("raster_calculator", "_raster_calculator"),
        ("raster_reclassify", "_raster_reclassify"),
        ("dem_analysis", "_dem_analysis"),
        ("raster_clip", "_raster_clip"),
    )

    for action_id, method_name in actions:
        monkeypatch.setattr(
            window,
            method_name,
            lambda action_id=action_id: called.append(action_id),
        )
        window._handle_action(action_id)

    assert called == [action_id for action_id, _method_name in actions]
    window.close()
    qt_application.processEvents()


def test_main_window_runs_raster_task_in_background_with_progress(monkeypatch) -> None:
    """主窗口应创建进度框并在 worker 完成后释放运行状态。"""
    qt_application = QApplication.instance() or QApplication([])
    window = MainWindow()
    completed: list[RasterLayer] = []

    monkeypatch.setattr(
        window,
        "_on_raster_analysis_completed",
        lambda result: completed.append(result),
    )
    monkeypatch.setattr(window, "_on_raster_analysis_failed", lambda message: None)

    result_layer = RasterLayer.create(
        name="后台结果",
        raster_data=np.ones((1, 2, 2), dtype=np.float32),
        image_data=np.full((2, 2, 4), 255, dtype=np.uint8),
        valid_mask=np.ones((2, 2), dtype=bool),
        transform=Affine.identity(),
        crs=CRS.from_epsg(32650),
        bounds=(0.0, 0.0, 2.0, 2.0),
    )

    def task(service) -> RasterLayer:
        service._report_progress(0, 1)  # noqa: SLF001  验证进度信号桥接。
        service._report_progress(1, 1)  # noqa: SLF001
        return result_layer

    assert window._start_raster_analysis(
        title="测试栅格分析",
        task=task,
        algorithm_id="test_raster_analysis",
        input_layer_ids=(),
        parameters={},
        output_layer_name="后台结果",
        success_message=lambda _result: "完成",
    )

    deadline = time.monotonic() + 5.0
    while window._raster_worker is not None and time.monotonic() < deadline:
        qt_application.processEvents()
        time.sleep(0.01)
    qt_application.processEvents()

    assert completed == [result_layer]
    assert window._raster_worker is None
    assert window._raster_progress_dialog is None
    window.close()
    qt_application.processEvents()


def test_main_window_runs_crs_reprojection_in_background_with_progress(monkeypatch) -> None:
    """主窗口设置地图 CRS 时应在线程中执行准备阶段并释放进度状态。"""
    qt_application = QApplication.instance() or QApplication([])
    window = MainWindow()
    completed: list[DisplayCrsPreparation] = []
    target_crs = CRS.from_epsg(4326)
    preparation = DisplayCrsPreparation(target_crs, (), ())

    def prepare(_target_crs, report_progress):
        report_progress(0, 1)
        time.sleep(0.02)
        report_progress(1, 1)
        return preparation

    monkeypatch.setattr(window._application, "prepare_display_crs", prepare)
    monkeypatch.setattr(
        window,
        "_on_crs_reprojection_completed",
        lambda result: completed.append(result),
    )
    monkeypatch.setattr(window, "_on_crs_reprojection_failed", lambda _message: None)

    assert window._start_crs_reprojection(target_crs)

    deadline = time.monotonic() + 5.0
    while window._crs_worker is not None and time.monotonic() < deadline:
        qt_application.processEvents()
        time.sleep(0.01)
    qt_application.processEvents()

    assert completed == [preparation]
    assert window._crs_worker is None
    assert window._crs_progress_dialog is None
    window.close()
    qt_application.processEvents()
