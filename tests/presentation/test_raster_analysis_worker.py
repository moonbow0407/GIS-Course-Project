"""栅格分析后台线程测试。"""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from affine import Affine
from pyproj import CRS
from PySide6.QtWidgets import QApplication

from app.domain.raster_layer import RasterLayer
from app.presentation.widgets.raster_analysis_worker import RasterAnalysisWorker


def _result_layer() -> RasterLayer:
    """构建一个最小结果图层。"""
    data = np.ones((1, 2, 2), dtype=np.float32)
    return RasterLayer.create(
        name="result",
        raster_data=data,
        image_data=np.full((2, 2, 4), 255, dtype=np.uint8),
        valid_mask=np.ones((2, 2), dtype=bool),
        transform=Affine.identity(),
        crs=CRS.from_epsg(32650),
        bounds=(0.0, 0.0, 2.0, 2.0),
    )


def test_worker_forwards_progress_and_completion() -> None:
    """后台任务应转发进度，并在结束时发出结果。"""
    application = QApplication.instance() or QApplication([])
    progress: list[tuple[int, int]] = []
    completed: list[RasterLayer] = []

    def task(service) -> RasterLayer:
        service._report_progress(0, 2)  # noqa: SLF001  测试 worker 的回调桥接。
        service._report_progress(1, 2)  # noqa: SLF001
        service._report_progress(2, 2)  # noqa: SLF001
        return _result_layer()

    worker = RasterAnalysisWorker(task)
    worker.progress_changed.connect(lambda done, total: progress.append((done, total)))
    worker.completed.connect(lambda result: completed.append(result))
    worker.start()
    deadline = time.monotonic() + 5.0
    while worker.isRunning() and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.01)
    worker.wait(1000)
    application.processEvents()

    assert not worker.isRunning()
    assert progress[-1] == (2, 2)
    assert completed and completed[0].name == "result"
    worker.deleteLater()
    application.processEvents()
