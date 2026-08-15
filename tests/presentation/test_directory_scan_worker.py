"""目录扫描后台线程测试。"""

import os
import threading
import time
from pathlib import Path
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.presentation.widgets import directory_scan_worker
from app.presentation.widgets.directory_scan_worker import DirectoryEntry, DirectoryScanWorker
from app.presentation.widgets.spatial_file_dialog import ALL_SUFFIXES


def _run_scan(
    application: QApplication, directory: Path, **kwargs
) -> tuple[list[list[DirectoryEntry]], list[DirectoryEntry]]:
    """运行一次完整扫描，返回批次列表和全部条目。"""
    batches: list[list[DirectoryEntry]] = []
    completed: list[bool] = []
    worker = DirectoryScanWorker(directory, ALL_SUFFIXES, **kwargs)
    worker.batch.connect(lambda payload: batches.append(cast(list[DirectoryEntry], payload)))
    worker.scan_completed.connect(lambda: completed.append(True))
    worker.start()
    deadline = time.monotonic() + 15.0
    while not completed and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.005)
    worker.wait(1000)
    application.processEvents()
    return batches, [entry for batch in batches for entry in batch]


def test_scan_returns_directories_and_supported_files(tmp_path: Path) -> None:
    """扫描应返回目录与受支持文件，并附带文件系统属性。"""
    application = QApplication.instance() or QApplication([])
    (tmp_path / "subdir").mkdir()
    (tmp_path / "roads.shp").write_bytes(b"a" * 2048)
    (tmp_path / "roads.dbf").write_bytes(b"x")  # 辅助文件，应被过滤
    (tmp_path / "notes.txt").write_bytes(b"y")  # 未知扩展名，应被过滤
    (tmp_path / "B.TIF").write_bytes(b"z")  # 大小写不敏感
    _batches, entries = _run_scan(application, tmp_path)
    assert [entry.name for entry in entries] == ["subdir", "B.TIF", "roads.shp"]
    shp = next(entry for entry in entries if entry.name == "roads.shp")
    assert not shp.is_dir
    assert shp.size == 2048
    assert shp.modified > 0
    folder = next(entry for entry in entries if entry.name == "subdir")
    assert folder.is_dir


def test_scan_orders_directories_first_then_files(tmp_path: Path) -> None:
    """目录应始终排在文件之前，名称不区分大小写排序。"""
    application = QApplication.instance() or QApplication([])
    for name in ("z.tif", "A.shp", "m.geojson"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "b_folder").mkdir()
    (tmp_path / "A_folder").mkdir()
    _batches, entries = _run_scan(application, tmp_path)
    assert [entry.name for entry in entries] == [
        "A_folder",
        "b_folder",
        "A.shp",
        "m.geojson",
        "z.tif",
    ]


def test_large_directory_scans_in_batches_off_main_thread(tmp_path: Path) -> None:
    """约一万个目录项应分批投递且不在主线程执行（性能验收）。"""
    application = QApplication.instance() or QApplication([])
    main_thread_id: int = threading.get_ident()
    scan_thread_ids: list[int] = []
    (tmp_path / "folder").mkdir()
    for index in range(9_800):
        (tmp_path / f"f{index:04d}.tif").touch()
    for index in range(100):
        (tmp_path / f"a{index:02d}.dbf").touch()  # 辅助文件
    for index in range(100):
        (tmp_path / f"x{index:02d}.xyz").touch()  # 未知扩展名

    class RecordingWorker(DirectoryScanWorker):
        """记录扫描所在线程，验证目录枚举不在主线程执行。"""

        def run(self) -> None:
            scan_thread_ids.append(threading.get_ident())
            super().run()

    batches: list[list[DirectoryEntry]] = []
    completed: list[bool] = []
    worker = RecordingWorker(tmp_path, ALL_SUFFIXES)
    worker.batch.connect(lambda payload: batches.append(cast(list[DirectoryEntry], payload)))
    worker.scan_completed.connect(lambda: completed.append(True))
    worker.start()
    deadline = time.monotonic() + 30.0
    while not completed and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.005)
    worker.wait(2000)
    application.processEvents()

    entries = [entry for batch in batches for entry in batch]
    assert scan_thread_ids and scan_thread_ids[0] != main_thread_id
    assert len(batches) >= 2  # 分批提交，避免一次性插入大量行
    assert len(entries) == 9_801
    assert len({entry.name for entry in entries}) == 9_801
    assert entries[0].name == "folder"  # 目录始终置顶
    worker.deleteLater()
    application.processEvents()


def test_request_stop_halts_further_emission(monkeypatch, tmp_path: Path) -> None:
    """请求停止后不得继续投递新批次，避免浪费扫描。"""
    application = QApplication.instance() or QApplication([])
    for index in range(2_000):
        (tmp_path / f"f{index:04d}.tif").touch()
    original_stat = directory_scan_worker._entry_stat

    def slow_stat(entry: os.DirEntry[str]) -> tuple[int, float]:
        time.sleep(0.001)
        return original_stat(entry)

    monkeypatch.setattr(directory_scan_worker, "_entry_stat", slow_stat)
    batch_counts: list[int] = []
    worker = DirectoryScanWorker(tmp_path, ALL_SUFFIXES)
    worker.batch.connect(
        lambda payload: batch_counts.append(len(cast(list[DirectoryEntry], payload)))
    )
    worker.start()
    time.sleep(0.05)
    worker.request_stop()
    worker.wait(2000)
    application.processEvents()
    assert worker.stop_requested
    assert sum(batch_counts) < 2_000
    worker.deleteLater()
    application.processEvents()


def test_single_entry_stat_failure_is_skipped(monkeypatch, tmp_path: Path) -> None:
    """单个目录项属性读取失败应跳过该项，不中止整个扫描。"""
    application = QApplication.instance() or QApplication([])
    (tmp_path / "broken.tif").write_bytes(b"x")
    (tmp_path / "fine.shp").write_bytes(b"y")
    original_stat = directory_scan_worker._entry_stat

    def flaky_stat(entry: os.DirEntry[str]) -> tuple[int, float]:
        if entry.name == "broken.tif":
            raise OSError("模拟属性读取失败")
        return original_stat(entry)

    monkeypatch.setattr(directory_scan_worker, "_entry_stat", flaky_stat)
    _batches, entries = _run_scan(application, tmp_path)
    assert [entry.name for entry in entries] == ["fine.shp"]
