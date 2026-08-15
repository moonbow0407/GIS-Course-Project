"""栅格显示金字塔后台构建任务。"""

from collections.abc import Callable

from PySide6.QtCore import QThread, Signal

from app.infrastructure.file_io.raster_overview_service import RasterOverviewResult

RasterOverviewTask = Callable[[], RasterOverviewResult]


class RasterOverviewWorker(QThread):
    """在后台构建或复用单个栅格的显示 Overview 缓存。"""

    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, task: RasterOverviewTask, parent: object | None = None) -> None:
        """保存不修改工作区的 Overview 任务。"""
        super().__init__(parent)  # type: ignore[arg-type]
        self._task = task

    def run(self) -> None:
        """执行自动优化，并把结构化结果或中文错误传回主线程。"""
        try:
            result = self._task()
        except Exception as error:  # noqa: BLE001 后台边界必须转发所有构建错误。
            self.failed.emit(str(error))
            return
        self.completed.emit(result)
