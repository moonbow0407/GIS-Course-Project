"""地图坐标系转换后台线程测试。"""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.presentation.widgets.crs_reprojection_worker import CrsReprojectionWorker


def test_worker_forwards_progress_and_prepared_result() -> None:
    """坐标系转换耗时阶段应在线程中执行，并转发进度与结果。"""
    application = QApplication.instance() or QApplication([])
    progress: list[tuple[int, int]] = []
    completed: list[object] = []

    def task(report_progress) -> object:
        report_progress(0, 1)
        time.sleep(0.02)
        report_progress(1, 1)
        return "prepared"

    worker = CrsReprojectionWorker(task)
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
    assert progress[-1] == (1, 1)
    assert completed == ["prepared"]
    worker.deleteLater()
    application.processEvents()
