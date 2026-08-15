"""空间数据文件后台读取任务。"""

from collections.abc import Callable

from PySide6.QtCore import QThread, Signal

from app.domain.spatial_layer import SpatialLayer

OpenDataProgressCallback = Callable[[int, int, str], None]
OpenDataTask = Callable[..., SpatialLayer]


class OpenDataWorker(QThread):
    """在后台线程准备单个空间图层，工作区提交仍由主线程负责。"""

    completed = Signal(object)
    failed = Signal(object)
    progress = Signal(int, int, str)

    def __init__(self, task: OpenDataTask, parent: object | None = None) -> None:
        """保存只读文件准备任务。"""
        super().__init__(parent)  # type: ignore[arg-type]
        self._task = task

    def run(self) -> None:
        """执行文件读取，并将结果或原始异常传回主线程。"""
        def report(current: int, total: int, message: str) -> None:
            self.progress.emit(current, total, message)

        try:
            layer = self._invoke_task(report)
        except Exception as error:  # noqa: BLE001 线程边界必须转发所有读取异常。
            self.failed.emit(error)
            return
        self.completed.emit(layer)

    def _invoke_task(self, report: OpenDataProgressCallback) -> SpatialLayer:
        """优先把进度回调传给准备函数，兼容旧的无参测试任务。"""
        try:
            return self._task(report)
        except TypeError:
            return self._task()
