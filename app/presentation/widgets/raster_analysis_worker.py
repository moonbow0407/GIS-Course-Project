"""栅格分析后台工作线程：在工作线程执行分块计算并报告进度。

计算（文件 I/O + NumPy 算法）在工作线程执行，避免大栅格窗口循环
阻塞主线程；结果图层通过信号传回主线程注册。支持用户取消。
"""

import threading
from collections.abc import Callable

from PySide6.QtCore import QThread, Signal

from app.application.raster_analysis_service import RasterAnalysisService
from app.domain.raster_layer import RasterLayer

# 分析任务：接收一个分析服务，返回计算完成的结果图层。
RasterAnalysisTask = Callable[[RasterAnalysisService], RasterLayer]


class RasterAnalysisWorker(QThread):
    """在后台线程执行栅格分析任务。

    信号:
        completed(object): 计算成功，携带结果 RasterLayer。
        failed(str): 计算失败或被取消，携带中文错误信息。
        progress_changed(int, int): 已处理窗口数、总窗口数。
    """

    completed = Signal(object)
    failed = Signal(str)
    progress_changed = Signal(int, int)

    def __init__(
        self,
        task: RasterAnalysisTask,
        parent: object | None = None,
    ) -> None:
        """初始化工作线程。

        参数:
            task: 接收分析服务并返回结果图层的可调用对象；
                任务内只允许使用分析服务和只读输入图层。
            parent: Qt 父对象。
        """
        super().__init__(parent)  # type: ignore[arg-type]
        self._task: RasterAnalysisTask = task
        self._cancel_event: threading.Event = threading.Event()

    def run(self) -> None:
        """执行分析任务；异常和取消统一转换为 failed 信号。"""
        service = RasterAnalysisService(progress_callback=self._on_progress)
        try:
            result_layer = self._task(service)
        except Exception as error:  # noqa: BLE001  工作线程需捕获全部异常。
            if self._cancel_event.is_set():
                self.failed.emit("已取消栅格分析。")
            else:
                self.failed.emit(str(error))
            return
        if self._cancel_event.is_set():
            self.failed.emit("已取消栅格分析。")
            return
        self.completed.emit(result_layer)

    def request_cancel(self) -> None:
        """请求取消当前分析；正在处理的窗口完成后停止。"""
        self._cancel_event.set()

    @property
    def cancel_requested(self) -> bool:
        """返回是否已经请求取消。"""
        return self._cancel_event.is_set()

    def _on_progress(self, done: int, total: int) -> bool:
        """向主线程转发进度，并返回是否继续执行。"""
        self.progress_changed.emit(done, total)
        return not self._cancel_event.is_set()
