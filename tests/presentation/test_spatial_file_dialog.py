"""空间数据专用文件选择窗口测试。"""

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QItemSelectionModel, QSettings
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from app.infrastructure.file_io.auto_reader import AutoDataReader
from app.presentation.widgets import spatial_file_dialog
from app.presentation.widgets.directory_scan_worker import DirectoryEntry
from app.presentation.widgets.spatial_file_dialog import (
    ALL_SUFFIXES,
    AUXILIARY_SUFFIXES,
    FILE_TYPE_FILTERS,
    RASTER_SUFFIXES,
    VECTOR_SUFFIXES,
    SpatialFileDialog,
    file_type_name,
    format_file_size,
    format_modified_time,
    is_auxiliary_file,
    is_supported_file,
    load_last_directory,
    save_last_directory,
    select_spatial_data_files,
)


def _pump_until_scan_idle(
    application: QApplication, dialog: SpatialFileDialog, timeout: float = 5.0
) -> None:
    """处理 Qt 事件直到当前目录扫描线程发出完成信号。"""
    completed: list[bool] = []
    worker = dialog._scan_worker
    if worker is not None:
        worker.scan_completed.connect(lambda: completed.append(True))
    deadline = time.monotonic() + timeout
    while not completed and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.005)
    application.processEvents()


def _row_names(dialog: SpatialFileDialog) -> list[str]:
    """返回文件列表第一列的名称，按当前显示顺序。"""
    names: list[str] = []
    for row in range(dialog._file_table.rowCount()):
        item = dialog._file_table.item(row, 0)
        assert item is not None
        names.append(item.text())
    return names


def _make_dialog(
    monkeypatch, application: QApplication, directory: Path
) -> SpatialFileDialog:
    """构造对话框并从指定目录开始浏览，等待扫描结束。"""
    monkeypatch.setattr(spatial_file_dialog, "load_last_directory", lambda: directory)
    dialog = SpatialFileDialog()
    _pump_until_scan_idle(application, dialog)
    return dialog


def _capture_warnings(monkeypatch) -> list[tuple[str, str]]:
    """拦截 QMessageBox.warning，避免测试被模态提示阻塞。"""
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )
    return warnings


def _select_rows(table, *rows: int) -> None:
    """模拟 Ctrl 追加选择多个行（Select 不会清除既有选择）。"""
    selection_model = table.selectionModel()
    for row in rows:
        selection_model.select(
            table.model().index(row, 0),
            QItemSelectionModel.SelectionFlag.Select
            | QItemSelectionModel.SelectionFlag.Rows,
        )


def test_filter_suffixes_match_auto_reader_support() -> None:
    """对话框筛选扩展名必须与主窗口读取器支持范围一致。"""
    assert VECTOR_SUFFIXES == AutoDataReader.VECTOR_SUFFIXES
    assert RASTER_SUFFIXES == AutoDataReader.RASTER_SUFFIXES
    assert ALL_SUFFIXES == AutoDataReader.VECTOR_SUFFIXES | AutoDataReader.RASTER_SUFFIXES


def test_filter_suffixes_follow_spec() -> None:
    """筛选项扩展名必须与规范第 6.1 节完全一致。"""
    assert VECTOR_SUFFIXES == frozenset({".shp", ".geojson", ".json", ".gpkg", ".kml"})
    assert RASTER_SUFFIXES == frozenset({".tif", ".tiff", ".img", ".dem"})
    assert [file_filter.label for file_filter in FILE_TYPE_FILTERS] == [
        "全部支持的数据",
        "矢量数据",
        "栅格数据",
    ]


def test_supported_check_is_case_insensitive() -> None:
    """扩展名匹配不区分大小写。"""
    assert is_supported_file(Path("ELEVATION.TIF"))
    assert is_supported_file(Path("roads.SHP"))
    assert is_supported_file(Path("boundary.GeoJSON"))
    assert not is_supported_file(Path("notes.txt"))
    assert not is_supported_file(Path("no-extension"))


def test_auxiliary_files_are_hidden_by_rule() -> None:
    """Shapefile 与栅格伴随文件、元数据文件都应判定为辅助文件。"""
    for suffix in AUXILIARY_SUFFIXES:
        assert is_auxiliary_file(Path(f"roads{suffix}")), suffix
    assert is_auxiliary_file(Path("elevation.aux.xml"))
    assert is_auxiliary_file(Path("roads.shp.xml"))
    assert is_auxiliary_file(Path("elevation.tif.xml"))
    for name in ("roads.shp", "roads.geojson", "elevation.tif", "elevation.dem"):
        assert not is_auxiliary_file(Path(name)), name


def test_format_file_size_uses_readable_units() -> None:
    """大小应格式化为易读单位。"""
    assert format_file_size(0) == "0 B"
    assert format_file_size(1023) == "1023 B"
    assert format_file_size(1024) == "1 KiB"
    assert format_file_size(320 * 1024 * 1024) == "320 MiB"
    assert format_file_size(2 * 1024 * 1024 * 1024) == "2 GiB"


def test_format_modified_time_uses_local_format() -> None:
    """修改时间应使用本地时间格式。"""
    text = format_modified_time(1_700_000_000.0)
    assert len(text) == 16
    assert text[4] == "-" and text[7] == "-" and text[10] == " " and text[13] == ":"


def test_file_type_name_maps_extension_to_fixed_name() -> None:
    """类型列按扩展名映射固定中文类型名称。"""
    assert file_type_name(Path("roads.shp")) == "Shapefile"
    assert file_type_name(Path("roads.geojson")) == "GeoJSON"
    assert file_type_name(Path("elevation.TIF")) == "TIFF"
    assert file_type_name(Path("model.dem")) == "DEM"
    assert file_type_name(Path("data.xyz")) == "文件"


def test_dialog_lists_directories_then_supported_files(
    tmp_path: Path, monkeypatch
) -> None:
    """文件列表应目录在前、支持文件在后，辅助与未知扩展名隐藏。"""
    application = QApplication.instance() or QApplication([])
    (tmp_path / "subdir").mkdir()
    (tmp_path / "roads.shp").write_bytes(b"x")
    (tmp_path / "elevation.tif").write_bytes(b"y")
    (tmp_path / "roads.dbf").write_bytes(b"z")
    (tmp_path / "notes.txt").write_bytes(b"w")
    dialog = _make_dialog(monkeypatch, application, tmp_path)
    assert _row_names(dialog) == ["subdir", "elevation.tif", "roads.shp"]
    assert dialog._current_path == tmp_path
    dialog.close()


def test_filter_combo_changes_visible_files(tmp_path: Path, monkeypatch) -> None:
    """更换筛选项后文件列表应只显示对应类别，目录始终保留。"""
    application = QApplication.instance() or QApplication([])
    (tmp_path / "roads.shp").write_bytes(b"x")
    (tmp_path / "elevation.tif").write_bytes(b"y")
    dialog = _make_dialog(monkeypatch, application, tmp_path)
    dialog._filter_combo.setCurrentIndex(1)  # 矢量数据
    assert _row_names(dialog) == ["roads.shp"]
    dialog._filter_combo.setCurrentIndex(2)  # 栅格数据
    assert _row_names(dialog) == ["elevation.tif"]
    dialog._filter_combo.setCurrentIndex(0)  # 全部支持的数据
    assert _row_names(dialog) == ["elevation.tif", "roads.shp"]
    dialog.close()


def test_selection_returns_paths_in_table_order(tmp_path: Path, monkeypatch) -> None:
    """多选应只返回当前目录中的受支持文件，并按列表顺序排列。"""
    application = QApplication.instance() or QApplication([])
    for name in ("b.tif", "a.shp", "c.geojson"):
        (tmp_path / name).write_bytes(b"x")
    dialog = _make_dialog(monkeypatch, application, tmp_path)
    table = dialog._file_table
    _select_rows(table, 0, 2)  # 模拟 Ctrl 追加选择 a.shp 与 c.geojson
    assert dialog.selected_paths() == ()
    assert dialog._selected_file_paths() == [
        str(tmp_path / "a.shp"),
        str(tmp_path / "c.geojson"),
    ]
    assert dialog._open_button.isEnabled()
    dialog.close()


def test_open_button_disabled_without_file_selection(
    tmp_path: Path, monkeypatch
) -> None:
    """未选中文件时“打开”按钮应禁用，仅选中目录行不算。"""
    application = QApplication.instance() or QApplication([])
    (tmp_path / "folder").mkdir()
    (tmp_path / "a.tif").write_bytes(b"x")
    dialog = _make_dialog(monkeypatch, application, tmp_path)
    table = dialog._file_table
    assert not dialog._open_button.isEnabled()
    table.selectRow(0)  # folder 目录
    assert not dialog._open_button.isEnabled()
    table.selectRow(1)  # a.tif 文件
    assert dialog._open_button.isEnabled()
    dialog.close()


def test_double_click_file_accepts_with_that_path(tmp_path: Path, monkeypatch) -> None:
    """双击文件应等同于选择该文件并点击“打开”。"""
    application = QApplication.instance() or QApplication([])
    (tmp_path / "roads.shp").write_bytes(b"x")
    dialog = _make_dialog(monkeypatch, application, tmp_path)
    table = dialog._file_table
    table.selectRow(0)
    item = table.item(0, 0)
    assert item is not None
    dialog._on_item_double_clicked(item)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.selected_paths() == (str(tmp_path / "roads.shp"),)
    dialog.close()


def test_double_click_directory_navigates_and_clears_selection(
    tmp_path: Path, monkeypatch
) -> None:
    """双击目录应进入该目录并清空此前的文件选择。"""
    application = QApplication.instance() or QApplication([])
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (tmp_path / "a.tif").write_bytes(b"x")
    (subdir / "b.shp").write_bytes(b"y")
    dialog = _make_dialog(monkeypatch, application, tmp_path)
    table = dialog._file_table
    table.selectRow(1)  # a.tif
    assert dialog._open_button.isEnabled()
    item = table.item(0, 0)
    assert item is not None and item.text() == "subdir"
    dialog._on_item_double_clicked(item)
    _pump_until_scan_idle(application, dialog)
    assert dialog._current_path == subdir
    assert dialog.selected_paths() == ()
    assert not dialog._open_button.isEnabled()
    assert _row_names(dialog) == ["b.shp"]
    dialog.close()


def test_switching_directory_clears_selection(tmp_path: Path, monkeypatch) -> None:
    """切换目录必须清空此前选择，不保留跨目录选择。"""
    application = QApplication.instance() or QApplication([])
    other = tmp_path / "other"
    other.mkdir()
    (tmp_path / "a.tif").write_bytes(b"x")
    (other / "b.shp").write_bytes(b"y")
    dialog = _make_dialog(monkeypatch, application, tmp_path)
    table = dialog._file_table
    table.selectRow(1)  # a.tif（第 0 行是目录 other）
    assert dialog._selected_file_paths()
    dialog._set_directory(other)
    _pump_until_scan_idle(application, dialog)
    assert dialog._selected_file_paths() == []
    assert not dialog._open_button.isEnabled()
    dialog.close()


def test_cancel_returns_empty_result(monkeypatch) -> None:
    """取消应返回空结果，不启动任何文件读取任务。"""
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        SpatialFileDialog, "exec", lambda self: QDialog.DialogCode.Rejected
    )
    assert select_spatial_data_files() == []


def test_accepted_returns_selected_paths(monkeypatch) -> None:
    """确认后应返回校验通过的只读路径列表。"""
    QApplication.instance() or QApplication([])

    def fake_exec(self: SpatialFileDialog) -> int:
        self._selected_paths = (str(Path("a.tif")), str(Path("b.shp")))
        return int(QDialog.DialogCode.Accepted)

    monkeypatch.setattr(SpatialFileDialog, "exec", fake_exec)
    assert select_spatial_data_files() == ["a.tif", "b.shp"]


def test_last_directory_restored_and_missing_falls_back_to_home(
    tmp_path: Path, monkeypatch
) -> None:
    """上次目录可跨窗口实例恢复；记录不存在时回退到用户目录。"""
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(spatial_file_dialog, "_get_settings", lambda: settings)
    saved = tmp_path / "saved"
    saved.mkdir()
    save_last_directory(saved)
    assert load_last_directory() == saved
    settings.clear()
    assert load_last_directory() == Path.home()


def test_dialog_restores_last_directory(tmp_path: Path, monkeypatch) -> None:
    """打开窗口应优先恢复上次成功浏览的目录。"""
    QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(spatial_file_dialog, "_get_settings", lambda: settings)
    saved = tmp_path / "saved"
    saved.mkdir()
    save_last_directory(saved)
    dialog = SpatialFileDialog()
    assert dialog._current_path == saved
    dialog.close()


def test_deleted_file_is_skipped_on_confirm(tmp_path: Path, monkeypatch) -> None:
    """确认前被删除的文件不得进入加载队列，并给出中文提示。"""
    application = QApplication.instance() or QApplication([])
    (tmp_path / "a.tif").write_bytes(b"x")
    (tmp_path / "b.shp").write_bytes(b"y")
    warnings = _capture_warnings(monkeypatch)
    dialog = _make_dialog(monkeypatch, application, tmp_path)
    table = dialog._file_table
    _select_rows(table, 0, 1)  # 模拟 Ctrl 追加选择 a.tif 与 b.shp
    (tmp_path / "a.tif").unlink()
    dialog._on_accept()
    assert dialog.selected_paths() == (str(tmp_path / "b.shp"),)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert any(title == "文件已不存在" for title, _message in warnings)
    dialog.close()


def test_extension_change_is_revalidated_on_confirm(
    tmp_path: Path, monkeypatch
) -> None:
    """扫描后扩展名变为不支持格式时，确认应重新校验并跳过。"""
    application = QApplication.instance() or QApplication([])
    (tmp_path / "elevation.tif").write_bytes(b"x")
    warnings = _capture_warnings(monkeypatch)
    dialog = _make_dialog(monkeypatch, application, tmp_path)
    dialog._file_table.selectRow(0)
    # 模拟扫描后扩展名不再受支持（文件被替换为其他类型但仍在原位）。
    monkeypatch.setattr(spatial_file_dialog, "is_supported_file", lambda path: False)
    dialog._on_accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert any("不是支持的空间数据格式" in message for _title, message in warnings)
    dialog.close()


def test_scan_never_reads_file_contents(tmp_path: Path, monkeypatch) -> None:
    """扫描阶段不得打开文件内容，只读取文件系统属性。"""
    application = QApplication.instance() or QApplication([])
    (tmp_path / "a.tif").write_bytes(b"x" * 100)

    def forbid_open(*args: object, **kwargs: object) -> object:
        raise AssertionError("扫描阶段不应打开文件内容")

    monkeypatch.setattr(Path, "open", forbid_open)
    monkeypatch.setattr(Path, "read_bytes", forbid_open)
    monkeypatch.setattr(Path, "read_text", forbid_open)
    dialog = _make_dialog(monkeypatch, application, tmp_path)
    assert "a.tif" in _row_names(dialog)
    dialog.close()


def test_dialog_and_worker_do_not_depend_on_gis_readers() -> None:
    """扫描与选择模块不得依赖 Rasterio/GDAL/GeoPandas 等读取库。"""
    from app.presentation.widgets import directory_scan_worker

    for module in (spatial_file_dialog, directory_scan_worker):
        assert "rasterio" not in vars(module)
        assert "geopandas" not in vars(module)
        assert "fiona" not in vars(module)
        assert "gdal" not in vars(module)


def test_stale_scan_results_are_discarded(tmp_path: Path, monkeypatch) -> None:
    """旧扫描的迟到批次不得混入当前目录的列表。"""
    application = QApplication.instance() or QApplication([])
    (tmp_path / "current.tif").write_bytes(b"x")
    dialog = _make_dialog(monkeypatch, application, tmp_path)
    stale_worker = dialog._scan_worker
    assert stale_worker is not None

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    (fresh / "fresh.shp").write_bytes(b"y")
    dialog._set_directory(fresh)
    _pump_until_scan_idle(application, dialog)

    late_entry = DirectoryEntry(
        path=tmp_path / "stale.tif",
        name="stale.tif",
        is_dir=False,
        size=1,
        modified=0.0,
    )
    dialog._on_scan_batch(stale_worker, [late_entry])
    assert "stale.tif" not in _row_names(dialog)
    dialog.close()


def test_empty_directory_shows_empty_state(tmp_path: Path, monkeypatch) -> None:
    """空目录或筛选结果为空应显示空状态，不视为错误。"""
    application = QApplication.instance() or QApplication([])
    dialog = _make_dialog(monkeypatch, application, tmp_path)
    assert "没有可打开的空间数据" in dialog._status_label.text()
    assert _row_names(dialog) == []
    dialog.close()


def test_invalid_directory_shows_warning_and_keeps_window(
    tmp_path: Path, monkeypatch
) -> None:
    """导航到不存在或无权限的目录应提示并保留当前目录。"""
    application = QApplication.instance() or QApplication([])
    dialog = _make_dialog(monkeypatch, application, tmp_path)
    warnings = _capture_warnings(monkeypatch)
    missing = tmp_path / "missing"
    dialog._set_directory(missing)
    assert dialog._current_path == tmp_path
    assert any(title == "无法打开目录" for title, _message in warnings)
    dialog.close()


def test_back_and_up_navigate_directory_history(tmp_path: Path, monkeypatch) -> None:
    """后退应回到上次目录，上一级应进入父目录。"""
    application = QApplication.instance() or QApplication([])
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    dialog = _make_dialog(monkeypatch, application, parent)
    assert not dialog._back_button.isEnabled()
    assert dialog._up_button.isEnabled()
    dialog._set_directory(child)
    assert dialog._current_path == child
    assert dialog._back_button.isEnabled()
    dialog._on_back()
    assert dialog._current_path == parent
    dialog._on_up()
    assert dialog._current_path == tmp_path
    dialog.close()
