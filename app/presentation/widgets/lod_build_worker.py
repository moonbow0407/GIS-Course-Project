"""矢量图层 LOD 金字塔后台构建任务。"""

from collections.abc import Callable

from PySide6.QtCore import QThread, Signal

from app.domain.lod import LodPyramid

LodBuildTask = Callable[[], LodPyramid]


class LodBuildWorker(QThread):
    """在后台线程为矢量图层构建多级 LOD 金字塔。

    LOD 构建只读取不可变的图层快照并调用 mapshaper 子进程，不触碰
    地图文档；完成后由主线程按图层修订号校验并提交结果。
    """

    completed = Signal(object)  # LodPyramid
    failed = Signal(object)  # Exception

    def __init__(self, task: LodBuildTask, parent: object | None = None) -> None:
        """保存只读的 LOD 构建任务。"""
        super().__init__(parent)  # type: ignore[arg-type]
        self._task = task

    def run(self) -> None:
        """执行 LOD 构建，并把金字塔或原始异常传回主线程。"""
        try:
            pyramid = self._task()
        except Exception as error:  # noqa: BLE001 线程边界必须转发异常。
            self.failed.emit(error)
            return
        self.completed.emit(pyramid)
