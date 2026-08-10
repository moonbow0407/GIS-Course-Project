"""地图显示坐标系转换后台线程。"""

import threading
from collections.abc import Callable

from PySide6.QtCore import QThread, Signal

from app.application.errors import WorkspaceOperationCancelled
from app.application.raster_analysis_service import ProgressCallback
from app.application.results import DisplayCrsPreparation

# 坐标系转换任务只准备结果，不在工作线程提交地图文档状态。
CrsReprojectionTask = Callable[[ProgressCallback], DisplayCrsPreparation]


class CrsReprojectionWorker(QThread):
    """在线程中读取并转换图层，完成后把待提交结果交回主线程。"""

    completed = Signal(object)
    failed = Signal(str)
    progress_changed = Signal(int, int)

    def __init__(
        self,
        task: CrsReprojectionTask,
        parent: object | None = None,
    ) -> None:
        """初始化坐标系转换任务。"""
        super().__init__(parent)  # type: ignore[arg-type]
        self._task: CrsReprojectionTask = task
        self._cancel_event: threading.Event = threading.Event()

    def run(self) -> None:
        """执行转换准备；异常和取消通过信号通知主线程。"""
        try:
            preparation = self._task(self._on_progress)
        except WorkspaceOperationCancelled as error:
            self.failed.emit(str(error))
            return
        except Exception as error:  # noqa: BLE001  工作线程需转换为界面提示。
            if self._cancel_event.is_set():
                self.failed.emit("已取消坐标系转换。")
            else:
                self.failed.emit(str(error))
            return
        if self._cancel_event.is_set():
            self.failed.emit("已取消坐标系转换。")
            return
        self.completed.emit(preparation)

    def request_cancel(self) -> None:
        """请求取消；当前图层读取完成后在下一个进度点安全退出。"""
        self._cancel_event.set()

    @property
    def cancel_requested(self) -> bool:
        """返回是否已经请求取消。"""
        return self._cancel_event.is_set()

    def _on_progress(self, done: int, total: int) -> bool:
        """向主线程转发进度，并返回是否继续执行。"""
        self.progress_changed.emit(done, total)
        return not self._cancel_event.is_set()
