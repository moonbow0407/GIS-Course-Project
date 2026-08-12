"""图层重投影后台线程。"""

import threading
from collections.abc import Callable

from PySide6.QtCore import QThread, Signal

from app.application.errors import WorkspaceOperationCancelled
from app.application.results import ReprojectionPreparation

# 重投影任务只准备结果，不在工作线程提交地图文档状态。
LayerReprojectionTask = Callable[[Callable[[int, int], bool]], ReprojectionPreparation]


class LayerReprojectionWorker(QThread):
    """在线程中执行重投影并写出结果文件，完成后把待提交结果交回主线程。"""

    completed = Signal(object)
    failed = Signal(str)
    progress_changed = Signal(int, int)

    def __init__(
        self,
        task: LayerReprojectionTask,
        parent: object | None = None,
    ) -> None:
        """初始化重投影任务。"""
        super().__init__(parent)  # type: ignore[arg-type]
        self._task: LayerReprojectionTask = task
        self._cancel_event: threading.Event = threading.Event()

    def run(self) -> None:
        """执行重投影；异常和取消通过信号通知主线程。"""
        try:
            preparation = self._task(self._on_progress)
        except WorkspaceOperationCancelled as error:
            self.failed.emit(str(error))
            return
        except Exception as error:  # noqa: BLE001  工作线程需转换为界面提示。
            if self._cancel_event.is_set():
                self.failed.emit("已取消重投影。")
            else:
                self.failed.emit(str(error))
            return
        if self._cancel_event.is_set():
            self.failed.emit("已取消重投影。")
            return
        self.completed.emit(preparation)

    def request_cancel(self) -> None:
        """请求取消正在执行的重投影任务。"""
        self._cancel_event.set()

    @property
    def cancel_requested(self) -> bool:
        """返回是否已请求取消。"""
        return self._cancel_event.is_set()

    def _on_progress(self, done: int, total: int) -> bool:
        """工作线程进度回调：转发给界面并返回是否继续。"""
        self.progress_changed.emit(done, total)
        return not self._cancel_event.is_set()
