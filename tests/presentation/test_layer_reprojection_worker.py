"""图层重投影后台线程测试。"""

import os
import time

# 必须在导入 Qt 前启用无界面平台，测试才能在没有显示器的环境运行。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.application.errors import WorkspaceOperationCancelled
from app.presentation.widgets.layer_reprojection_worker import (
    LayerReprojectionWorker,
)


def _pump_until_finished(application: QApplication, worker: LayerReprojectionWorker) -> None:
    """泵事件循环直到工作线程结束，并派发完线程队列信号。"""
    deadline: float = time.monotonic() + 5.0
    while worker.isRunning() and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.01)
    worker.wait(1000)
    application.processEvents()


def test_worker_reports_progress_and_completed() -> None:
    """工作线程应转发进度并发出完成信号。"""
    application = QApplication.instance() or QApplication([])
    progress: list[tuple[int, int]] = []
    completed: list[object] = []

    def task(report_progress):
        report_progress(0, 2)
        time.sleep(0.02)
        report_progress(1, 2)
        time.sleep(0.02)
        report_progress(2, 2)
        return "prepared"

    worker = LayerReprojectionWorker(task)
    worker.progress_changed.connect(lambda done, total: progress.append((done, total)))
    worker.completed.connect(completed.append)
    worker.start()
    _pump_until_finished(application, worker)
    worker.deleteLater()

    assert progress == [(0, 2), (1, 2), (2, 2)]
    assert completed == ["prepared"]


def test_worker_cancel_raises_workspace_cancelled() -> None:
    """任务内部抛出取消异常时，工作线程应发出取消消息的失败信号。"""
    application = QApplication.instance() or QApplication([])
    failed: list[str] = []

    def task(report_progress):
        time.sleep(0.02)
        raise WorkspaceOperationCancelled("已取消重投影。")

    worker = LayerReprojectionWorker(task)
    worker.failed.connect(failed.append)
    worker.start()
    _pump_until_finished(application, worker)
    worker.deleteLater()

    assert failed == ["已取消重投影。"]


def test_worker_cancel_request_before_completed_emits_failed() -> None:
    """任务完成后但已请求取消时，不应发出完成信号。"""
    application = QApplication.instance() or QApplication([])
    completed: list[object] = []
    failed: list[str] = []

    def task(report_progress):
        time.sleep(0.02)
        return "prepared"

    worker = LayerReprojectionWorker(task)
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)
    worker.request_cancel()
    worker.start()
    _pump_until_finished(application, worker)
    worker.deleteLater()

    assert completed == []
    assert failed == ["已取消重投影。"]


def test_worker_progress_callback_stops_after_cancel() -> None:
    """进度回调在取消后应返回 False，通知任务停止。"""
    application = QApplication.instance() or QApplication([])
    worker = LayerReprojectionWorker(lambda report_progress: report_progress(0, 1))
    worker.request_cancel()
    worker.start()
    _pump_until_finished(application, worker)
    worker.deleteLater()

    assert worker.cancel_requested is True
