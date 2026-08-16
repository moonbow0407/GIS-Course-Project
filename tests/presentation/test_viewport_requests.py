"""栅格视口请求去重与结果收养行为测试。"""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from affine import Affine
from pyproj import CRS
from PySide6.QtWidgets import QApplication

from app.application.results import LayerSnapshot, WorkspaceSnapshot
from app.domain.raster_layer import RasterLayer
from app.presentation import main_window as main_window_module
from app.presentation.main_window import MainWindow


class _SignalStub:
    """记录连接的空信号，不真正发射。"""

    def __init__(self) -> None:
        """创建未连接任何槽的空信号。"""
        self.calls: list[object] = []

    def connect(self, slot: object) -> None:
        """记录连接请求。"""
        self.calls.append(slot)


class _FrozenViewportWorker:
    """start 后永不结束的 worker 替身，用于确定性地测试去重判定。"""

    def __init__(self, request: object, parent: object | None = None) -> None:
        """保存请求对象，不做任何读取。"""
        self.request = request

    completed = _SignalStub()
    failed = _SignalStub()
    finished = _SignalStub()

    def start(self) -> None:
        """占位启动：测试中不应触发真实 I/O。"""

    def isRunning(self) -> bool:
        """向关闭流程报告线程未运行。"""
        return False

    def wait(self, milliseconds: int) -> None:
        """空等待，供关闭流程调用。"""
        del milliseconds


def _make_file_raster() -> RasterLayer:
    """创建带源路径的内存栅格，满足视口请求的图层条件。"""
    return RasterLayer.create(
        layer_id="viewport-raster",
        name="视口栅格",
        raster_data=np.ones((1, 4, 4), dtype=np.uint8),
        image_data=np.full((4, 4, 4), 255, dtype=np.uint8),
        valid_mask=np.ones((4, 4), dtype=np.bool_),
        transform=Affine(1, 0, 0, 0, -1, 4),
        crs=CRS.from_epsg(3857),
        bounds=(0, 0, 4, 4),
        source_path=Path("dummy.tif"),
    )


def test_duplicate_viewport_requests_reuse_running_worker(
    monkeypatch,
) -> None:
    """同参数视口读取未完成时应收养在跑线程，不重复启动。"""
    application: QApplication = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        main_window_module, "RasterViewportWorker", _FrozenViewportWorker
    )
    window = MainWindow()
    raster = _make_file_raster()
    window._map_canvas.set_snapshot(
        WorkspaceSnapshot(
            layers=(LayerSnapshot(layer=raster, visible=True, selected_feature_ids=()),),
            active_layer_id=raster.layer_id,
            display_crs=raster.crs,
        )
    )

    window._request_raster_viewports((0.0, 0.0, 4.0, 4.0), (800, 600))
    first_workers = dict(window._viewport_workers)
    assert len(first_workers) == 1

    # 同参数再次请求：不新增线程，且在跑 worker 的结果仍可被当前视口接纳。
    window._request_raster_viewports((0.0, 0.0, 4.0, 4.0), (800, 600))
    assert window._viewport_workers == first_workers
    assert window._viewport_layer_accept[raster.layer_id] == 1

    # 视口参数变化后必须启动新的读取线程。
    window._request_raster_viewports((1.0, 1.0, 2.0, 2.0), (800, 600))
    assert len(window._viewport_workers) == 2
    assert window._viewport_layer_accept[raster.layer_id] == 3

    window.close()
    assert application is not None
