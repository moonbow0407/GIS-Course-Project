"""空间数据文件选择窗口的目录扫描后台线程。

扫描只读取通用文件系统信息（枚举、目录判断、大小、修改时间），
不读取文件内容，也不调用任何 GIS 库或 Windows Shell 预览处理器。
"""

import os
import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal


@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    """目录扫描产生的一个只读目录项。"""

    path: Path
    name: str
    is_dir: bool
    size: int
    modified: float


def _entry_stat(entry: os.DirEntry[str]) -> tuple[int, float]:
    """读取单个目录项的大小和修改时间，供测试注入属性读取失败。"""
    stat_result = entry.stat()
    return stat_result.st_size, stat_result.st_mtime


class DirectoryScanWorker(QThread):
    """在后台枚举目录，并分批返回排序后的目录与受支持文件。

    结果顺序固定为：目录在前、文件在后，各自按名称（不区分大小写）
    排序，界面按批次追加行即可得到稳定的展示顺序。
    """

    batch = Signal(object)
    scan_failed = Signal(str)
    scan_completed = Signal()

    def __init__(
        self,
        directory: Path,
        supported_suffixes: frozenset[str],
        batch_size: int = 200,
        parent: object | None = None,
    ) -> None:
        """保存目录、格式规则和分批大小。

        参数:
            directory: 待扫描目录，必须为已确认存在的目录。
            supported_suffixes: 允许显示的文件扩展名集合（小写）。
            batch_size: 每次信号投递的目录项数量上限。
        """
        super().__init__(parent)  # type: ignore[arg-type]
        self._directory: Path = directory
        self._supported_suffixes: frozenset[str] = supported_suffixes
        self._batch_size: int = batch_size
        self._stop_event: threading.Event = threading.Event()

    def request_stop(self) -> None:
        """请求扫描尽快停止；已发射的信号不会再追加结果。"""
        self._stop_event.set()

    @property
    def stop_requested(self) -> bool:
        """返回是否已请求停止扫描。"""
        return self._stop_event.is_set()

    def run(self) -> None:
        """枚举目录并按批发射结果；单项失败跳过，整体失败发出提示。"""
        directory_entries: list[DirectoryEntry] = []
        try:
            with os.scandir(self._directory) as scanner:
                for entry in scanner:
                    if self._stop_event.is_set():
                        return
                    collected = self._collect(entry)
                    if collected is not None:
                        directory_entries.append(collected)
        except OSError as error:
            self.scan_failed.emit(self._failure_message(error))
            return
        if self._stop_event.is_set():
            return
        for batch in self._ordered_batches(directory_entries):
            if self._stop_event.is_set():
                return
            self.batch.emit(batch)
        self.scan_completed.emit()

    def _collect(self, entry: os.DirEntry[str]) -> DirectoryEntry | None:
        """把单个目录项转换为目录或受支持文件；失败时跳过该项。"""
        try:
            is_dir: bool = entry.is_dir(follow_symlinks=False)
            if is_dir:
                size, modified = 0, 0.0
            else:
                # 非受支持扩展名和 GIS 辅助文件不进入结果，避免投递无用项。
                if Path(entry.name).suffix.lower() not in self._supported_suffixes:
                    return None
                size, modified = _entry_stat(entry)
        except OSError:
            # 属性读取失败（如权限或竞态删除）只跳过当前项，不中止整次扫描。
            return None
        return DirectoryEntry(
            path=Path(entry.path),
            name=entry.name,
            is_dir=is_dir,
            size=size,
            modified=modified,
        )

    def _ordered_batches(self, entries: list[DirectoryEntry]) -> list[list[DirectoryEntry]]:
        """按目录优先、名称不区分大小写的顺序切分批次。"""
        directories = sorted(
            (entry for entry in entries if entry.is_dir),
            key=lambda entry: entry.name.casefold(),
        )
        files = sorted(
            (entry for entry in entries if not entry.is_dir),
            key=lambda entry: entry.name.casefold(),
        )
        ordered: list[DirectoryEntry] = directories + files
        return [
            ordered[index : index + self._batch_size]
            for index in range(0, len(ordered), self._batch_size)
        ]

    @staticmethod
    def _failure_message(error: OSError) -> str:
        """把扫描失败转换为可理解的中文提示。"""
        detail = error.strerror or str(error)
        return f"无法读取目录：{detail}"
