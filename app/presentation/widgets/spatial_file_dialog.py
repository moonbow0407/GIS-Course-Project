"""打开空间数据时使用的专用文件选择对话框。

使用不依赖 Windows Shell 预览的自制对话框：目录扫描只读取文件系统
属性（枚举、目录判断、大小、修改时间），不解析栅格或矢量内容，避免
选中大 TIFF 时触发 Shell 预览处理器导致界面长时间无响应。其他打开和
保存操作仍使用系统原生文件对话框。
"""

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast

from PySide6.QtCore import QDir, QSettings, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.presentation.widgets.directory_scan_worker import DirectoryEntry, DirectoryScanWorker

# 矢量扩展名：与 GeoPandasVectorReader / KmlVectorReader 支持范围一致。
VECTOR_SUFFIXES: frozenset[str] = frozenset(
    {".shp", ".geojson", ".json", ".gpkg", ".kml"}
)
# 栅格扩展名：与 RasterioRasterReader 支持范围一致。
RASTER_SUFFIXES: frozenset[str] = frozenset({".tif", ".tiff", ".img", ".dem"})
# 全部受支持的空间数据扩展名，集中定义以保持与主窗口读取格式一致。
ALL_SUFFIXES: frozenset[str] = VECTOR_SUFFIXES | RASTER_SUFFIXES

# GIS 辅助文件扩展名：默认不显示也不能被选中。
AUXILIARY_SUFFIXES: frozenset[str] = frozenset(
    {
        ".dbf",
        ".shx",
        ".prj",
        ".cpg",
        ".sbn",
        ".sbx",
        ".qix",
        ".ovr",
        ".msk",
        ".tfw",
        ".rrd",
    }
)

# 固定中文类型名称：只按扩展名映射，不读取文件内容推断类型。
TYPE_NAMES: dict[str, str] = {
    ".shp": "Shapefile",
    ".geojson": "GeoJSON",
    ".json": "GeoJSON",
    ".gpkg": "GeoPackage",
    ".kml": "KML",
    ".tif": "TIFF",
    ".tiff": "TIFF",
    ".img": "ERDAS IMG",
    ".dem": "DEM",
}

_SETTINGS_GROUP: str = "SpatialFileDialog"
_settings: QSettings | None = None

_PATH_ROLE: int = int(Qt.ItemDataRole.UserRole)
_IS_DIR_ROLE: int = int(Qt.ItemDataRole.UserRole) + 1
_LOADED_ROLE: int = int(Qt.ItemDataRole.UserRole) + 2


@dataclass(frozen=True, slots=True)
class FileTypeFilter:
    """文件类型筛选项：显示标签和允许的扩展名集合。"""

    label: str
    suffixes: frozenset[str]


# 只提供三个数据类别筛选项，不提供“所有文件 (*.*)”。
FILE_TYPE_FILTERS: tuple[FileTypeFilter, ...] = (
    FileTypeFilter("全部支持的数据", ALL_SUFFIXES),
    FileTypeFilter("矢量数据", VECTOR_SUFFIXES),
    FileTypeFilter("栅格数据", RASTER_SUFFIXES),
)


def is_supported_file(path: Path) -> bool:
    """返回文件扩展名是否为受支持的空间数据格式（不区分大小写）。"""
    return path.suffix.lower() in ALL_SUFFIXES


def is_auxiliary_file(path: Path) -> bool:
    """返回文件是否属于默认隐藏的 GIS 辅助文件。"""
    name: str = path.name.lower()
    return (
        path.suffix.lower() in AUXILIARY_SUFFIXES
        or name.endswith(".aux.xml")
        or name.endswith(".shp.xml")
        or name.endswith(".tif.xml")
    )


def format_file_size(size: int) -> str:
    """把字节数格式化为易读单位（1024 进制，如 320 MiB）。"""
    if size < 1024:
        return f"{size} B"
    value: float = float(size)
    units: tuple[str, ...] = ("KiB", "MiB", "GiB", "TiB")
    for unit in units:
        value /= 1024.0
        if value < 1024.0:
            return f"{value:g} {unit}"
    return f"{value:g} TiB"


def format_modified_time(timestamp: float) -> str:
    """把文件修改时间格式化为本地时间字符串。"""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


def file_type_name(path: Path) -> str:
    """根据扩展名映射为固定中文类型名称，不读取文件内容。"""
    return TYPE_NAMES.get(path.suffix.lower(), "文件")


def _get_settings() -> QSettings:
    """延迟初始化 QSettings，确保在 QApplication 创建之后才构造。

    无参构造依赖 QApplication 的应用名，模块导入时（早于 QApplication
    实例化）创建会导致存储路径为空；延迟到首次读写时创建可避免此问题。
    """
    global _settings
    if _settings is None:
        _settings = QSettings()
    return _settings


def load_last_directory() -> Path:
    """返回上次成功浏览的目录；记录缺失或不可用时回退到用户目录。"""
    raw: object = _get_settings().value(f"{_SETTINGS_GROUP}/last_directory", "")
    if isinstance(raw, str) and raw:
        path: Path = Path(raw)
        if path.is_dir():
            return path
    return Path.home()


def save_last_directory(path: Path) -> None:
    """持久化最近一次成功进入的可访问目录，不影响工程文件内容。"""
    settings: QSettings = _get_settings()
    settings.setValue(f"{_SETTINGS_GROUP}/last_directory", str(path))
    settings.sync()


class SpatialFileDialog(QDialog):
    """不依赖 Windows Shell 预览的空间数据专用文件选择窗口。"""

    _HEADERS: tuple[str, ...] = ("名称", "类型", "大小", "修改时间")
    _SCAN_BATCH_SIZE: int = 200
    _MAX_HISTORY: int = 50

    def __init__(self, parent: QWidget | None = None) -> None:
        """构造窗口并从上次目录或用户目录开始浏览。"""
        super().__init__(parent)
        self.setWindowTitle("打开空间数据")
        self.setMinimumSize(780, 500)
        self._selected_paths: tuple[str, ...] = ()
        self._current_path: Path = Path.home()
        self._history: list[Path] = []
        self._cached_entries: list[DirectoryEntry] = []
        self._scan_worker: DirectoryScanWorker | None = None
        self._retired_workers: list[DirectoryScanWorker] = []

        self._build_ui()
        self._populate_directory_tree()
        self._set_directory(load_last_directory(), record_history=False)

    def selected_paths(self) -> tuple[str, ...]:
        """返回确认时校验通过的只读路径集合。"""
        return self._selected_paths

    def _build_ui(self) -> None:
        """构造顶栏、目录树、文件列表和底部操作区。"""
        style: QStyle = self.style()
        self._folder_icon = style.standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        self._file_icon = style.standardIcon(QStyle.StandardPixmap.SP_FileIcon)

        self._back_button: QToolButton = QToolButton(self)
        self._back_button.setText("后退")
        self._back_button.setToolTip("返回上一次访问的目录")
        self._back_button.clicked.connect(self._on_back)
        self._up_button: QToolButton = QToolButton(self)
        self._up_button.setText("上一级")
        self._up_button.setToolTip("进入当前目录的上一级")
        self._up_button.clicked.connect(self._on_up)
        self._path_edit: QLineEdit = QLineEdit(self)
        self._path_edit.setPlaceholderText("输入目录路径后回车")
        self._path_edit.returnPressed.connect(self._on_path_entered)

        top_layout: QHBoxLayout = QHBoxLayout()
        top_layout.addWidget(self._back_button)
        top_layout.addWidget(self._up_button)
        top_layout.addWidget(self._path_edit, 1)

        self._dir_tree: QTreeWidget = QTreeWidget(self)
        self._dir_tree.setHeaderHidden(True)
        self._dir_tree.itemClicked.connect(self._on_tree_item_clicked)
        self._dir_tree.itemExpanded.connect(self._on_tree_item_expanded)

        self._file_table: QTableWidget = QTableWidget(0, len(self._HEADERS), self)
        self._file_table.setHorizontalHeaderLabels(list(self._HEADERS))
        self._file_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._file_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self._file_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._file_table.setSortingEnabled(False)
        self._file_table.verticalHeader().setVisible(False)
        self._file_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        for column in (1, 2, 3):
            self._file_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        self._file_table.itemSelectionChanged.connect(self._on_selection_changed)
        self._file_table.itemDoubleClicked.connect(self._on_item_double_clicked)

        splitter: QSplitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self._dir_tree)
        splitter.addWidget(self._file_table)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([220, 560])

        self._filter_combo: QComboBox = QComboBox(self)
        for file_filter in FILE_TYPE_FILTERS:
            self._filter_combo.addItem(file_filter.label)
        self._filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        self._status_label: QLabel = QLabel(self)
        self._cancel_button: QPushButton = QPushButton("取消", self)
        self._cancel_button.clicked.connect(self.reject)
        self._open_button: QPushButton = QPushButton("打开", self)
        self._open_button.setDefault(True)
        self._open_button.setEnabled(False)
        self._open_button.clicked.connect(self._on_accept)

        bottom_layout: QHBoxLayout = QHBoxLayout()
        bottom_layout.addWidget(QLabel("文件类型：", self))
        bottom_layout.addWidget(self._filter_combo)
        bottom_layout.addWidget(self._status_label, 1)
        bottom_layout.addWidget(self._cancel_button)
        bottom_layout.addWidget(self._open_button)

        layout: QVBoxLayout = QVBoxLayout(self)
        layout.addLayout(top_layout)
        layout.addWidget(splitter, 1)
        layout.addLayout(bottom_layout)

    def _populate_directory_tree(self) -> None:
        """创建“此电脑”根节点和磁盘节点，子目录在展开时才加载。"""
        root: QTreeWidgetItem = QTreeWidgetItem(["此电脑"])
        root.setData(0, _LOADED_ROLE, True)
        self._dir_tree.addTopLevelItem(root)
        for drive in QDir.drives():
            drive_path: Path = Path(drive.absoluteFilePath())
            node: QTreeWidgetItem = QTreeWidgetItem(
                [str(drive_path).rstrip("/\\") or "/"]
            )
            node.setData(0, _PATH_ROLE, str(drive_path))
            # 占位子节点让展开箭头可见；展开时才替换为真实子目录。
            node.addChild(QTreeWidgetItem([""]))
            root.addChild(node)
        root.setExpanded(True)

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        """单击目录树节点时切换到该目录。"""
        raw: object = item.data(0, _PATH_ROLE)
        if isinstance(raw, str):
            self._set_directory(Path(raw))

    def _on_tree_item_expanded(self, item: QTreeWidgetItem) -> None:
        """展开目录节点时只加载该层子目录，避免递归预扫描整棵子树。"""
        if item.data(0, _LOADED_ROLE) is not None:
            return
        item.setData(0, _LOADED_ROLE, True)
        raw: object = item.data(0, _PATH_ROLE)
        if not isinstance(raw, str):
            return
        item.takeChildren()
        subdirectories: list[Path] = []
        try:
            with os.scandir(raw) as scanner:
                for entry in scanner:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            subdirectories.append(Path(entry.path))
                    except OSError:
                        continue
        except OSError:
            pass
        subdirectories.sort(key=lambda path: path.name.casefold())
        for directory in subdirectories:
            node: QTreeWidgetItem = QTreeWidgetItem([directory.name])
            node.setData(0, _PATH_ROLE, str(directory))
            node.addChild(QTreeWidgetItem([""]))
            item.addChild(node)

    def _set_directory(self, path: Path, *, record_history: bool = True) -> None:
        """切换当前目录并启动后台扫描；无效目录给出中文提示。"""
        if not path.is_dir():
            QMessageBox.warning(self, "无法打开目录", f"目录不存在或不可访问：\n{path}")
            return
        if record_history and self._current_path != path:
            self._history.append(self._current_path)
            if len(self._history) > self._MAX_HISTORY:
                self._history.pop(0)
        self._current_path = path
        self._path_edit.setText(str(path))
        self._file_table.setRowCount(0)
        self._cached_entries = []
        self._status_label.setText("正在扫描目录…")
        self._open_button.setEnabled(False)
        self._start_scan(path)
        self._update_navigation_buttons()
        save_last_directory(path)

    def _update_navigation_buttons(self) -> None:
        """按当前目录和历史栈更新后退、上一级按钮可用性。"""
        self._back_button.setEnabled(bool(self._history))
        self._up_button.setEnabled(self._current_path.parent != self._current_path)

    def _start_scan(self, path: Path) -> None:
        """启动新一轮目录扫描；旧扫描的结果会被身份守卫丢弃。"""
        previous: DirectoryScanWorker | None = self._scan_worker
        if previous is not None and previous.isRunning():
            # 旧线程尽快停止，并登记到滞留列表，窗口关闭时统一回收。
            previous.request_stop()
            self._retired_workers.append(previous)
            previous.finished.connect(lambda w=previous: self._release_worker(w))
        worker: DirectoryScanWorker = DirectoryScanWorker(
            path,
            ALL_SUFFIXES,
            batch_size=self._SCAN_BATCH_SIZE,
            parent=self,
        )
        self._scan_worker = worker
        worker.batch.connect(lambda entries, w=worker: self._on_scan_batch(w, entries))
        worker.scan_failed.connect(
            lambda message, w=worker: self._on_scan_failed(w, message)
        )
        worker.scan_completed.connect(lambda w=worker: self._on_scan_completed(w))
        worker.start()

    def _on_scan_batch(self, worker: DirectoryScanWorker, payload: object) -> None:
        """接收一批扫描结果；只接受当前扫描线程投递的批次。"""
        if worker is not self._scan_worker:
            return
        entries: list[DirectoryEntry] = cast(list[DirectoryEntry], payload)
        self._cached_entries.extend(entries)
        self._append_rows(self._visible_entries(entries))

    def _on_scan_failed(self, worker: DirectoryScanWorker, message: str) -> None:
        """目录整体读取失败时给出可理解提示，窗口保持可导航。"""
        if worker is not self._scan_worker:
            return
        self._status_label.setText(message)
        self._status_label.setToolTip(message)

    def _on_scan_completed(self, worker: DirectoryScanWorker) -> None:
        """扫描完成时更新状态文字，显示文件数量或空状态。"""
        if worker is not self._scan_worker:
            return
        self._update_status(self._visible_entries())

    def _on_filter_changed(self) -> None:
        """筛选项变化时按缓存重排可见文件，不重新扫描目录。"""
        visible: list[DirectoryEntry] = self._visible_entries()
        self._file_table.setRowCount(0)
        self._open_button.setEnabled(False)
        self._append_rows(visible)
        if self._scan_worker is None or not self._scan_worker.isRunning():
            self._update_status(visible)

    def _visible_entries(
        self, entries: list[DirectoryEntry] | None = None
    ) -> list[DirectoryEntry]:
        """按当前筛选项过滤目录项，目录始终保留。"""
        filter_index: int = self._filter_combo.currentIndex()
        allowed: frozenset[str] = FILE_TYPE_FILTERS[filter_index].suffixes
        source: list[DirectoryEntry] = self._cached_entries if entries is None else entries
        return [
            entry
            for entry in source
            if entry.is_dir or entry.path.suffix.lower() in allowed
        ]

    def _append_rows(self, entries: list[DirectoryEntry]) -> None:
        """批量追加目录项行，期间暂停重绘避免逐行刷屏。"""
        table: QTableWidget = self._file_table
        start_row: int = table.rowCount()
        table.setUpdatesEnabled(False)
        try:
            table.setRowCount(start_row + len(entries))
            for offset, entry in enumerate(entries):
                self._set_row(start_row + offset, entry)
        finally:
            table.setUpdatesEnabled(True)

    def _set_row(self, row: int, entry: DirectoryEntry) -> None:
        """把单个目录项写入一行，路径和目录标记存入 UserRole。"""
        values: tuple[str, str, str, str] = (
            entry.name,
            "文件夹" if entry.is_dir else file_type_name(entry.path),
            "" if entry.is_dir else format_file_size(entry.size),
            "" if entry.is_dir else format_modified_time(entry.modified),
        )
        for column, value in enumerate(values):
            item: QTableWidgetItem = QTableWidgetItem(value)
            if column == 0:
                item.setIcon(self._folder_icon if entry.is_dir else self._file_icon)
                item.setData(_PATH_ROLE, str(entry.path))
                item.setData(_IS_DIR_ROLE, entry.is_dir)
            self._file_table.setItem(row, column, item)

    def _on_selection_changed(self) -> None:
        """选中项变化时，仅在包含文件时启用“打开”按钮。"""
        self._open_button.setEnabled(bool(self._selected_file_paths()))

    def _selected_file_paths(self) -> list[str]:
        """按表格行顺序返回当前选中的文件路径（目录不参与打开）。"""
        paths: list[str] = []
        for row in range(self._file_table.rowCount()):
            item: QTableWidgetItem | None = self._file_table.item(row, 0)
            if item is None or not item.isSelected():
                continue
            if item.data(_IS_DIR_ROLE):
                continue
            raw: object = item.data(_PATH_ROLE)
            if isinstance(raw, str):
                paths.append(raw)
        return paths

    def _on_accept(self) -> None:
        """校验选中文件仍存在且格式受支持，通过后关闭窗口。"""
        selected: list[str] = []
        for path_string in self._selected_file_paths():
            path: Path = Path(path_string)
            if not path.is_file():
                QMessageBox.warning(
                    self, "文件已不存在", f"{path.name} 已被删除或移动，已跳过。"
                )
                continue
            if not is_supported_file(path):
                QMessageBox.warning(
                    self,
                    "文件类型无效",
                    f"{path.name} 不是支持的空间数据格式，已跳过。",
                )
                continue
            selected.append(str(path))
        if not selected:
            return
        self._selected_paths = tuple(selected)
        self.accept()

    def _on_item_double_clicked(self, item: QTableWidgetItem) -> None:
        """双击目录进入该目录；双击文件等同于选择并打开。"""
        if item.data(_IS_DIR_ROLE):
            raw: object = item.data(_PATH_ROLE)
            if isinstance(raw, str):
                self._set_directory(Path(raw))
            return
        self._on_accept()

    def _on_back(self) -> None:
        """后退到历史栈中的上一个目录。"""
        if not self._history:
            return
        target: Path = self._history.pop()
        self._set_directory(target, record_history=False)

    def _on_up(self) -> None:
        """进入当前目录的上一级。"""
        parent: Path = self._current_path.parent
        if parent != self._current_path:
            self._set_directory(parent)

    def _on_path_entered(self) -> None:
        """按路径栏输入的内容切换目录。"""
        text: str = self._path_edit.text().strip()
        if text:
            self._set_directory(Path(text))

    def _update_status(self, visible: list[DirectoryEntry]) -> None:
        """按可见文件数量显示状态或空状态提示。"""
        file_count: int = sum(1 for entry in visible if not entry.is_dir)
        if file_count == 0:
            self._status_label.setText("此目录没有可打开的空间数据文件。")
        else:
            self._status_label.setText(f"共 {file_count} 个文件")

    def done(self, result: int) -> None:
        """关闭窗口前停止后台扫描，防止迟到结果访问已销毁控件。"""
        self._stop_scan()
        super().done(result)

    def _stop_scan(self) -> None:
        """停止当前及滞留的扫描线程并释放其信号连接。"""
        workers: list[DirectoryScanWorker] = []
        if self._scan_worker is not None:
            workers.append(self._scan_worker)
        workers.extend(self._retired_workers)
        for worker in workers:
            worker.request_stop()
            worker.batch.disconnect()
            worker.scan_failed.disconnect()
            worker.scan_completed.disconnect()
            worker.wait(3000)
            if worker.isRunning():
                # 极端慢盘下线程未及时结束：保留引用，线程结束后再销毁。
                worker.finished.connect(lambda w=worker: self._release_worker(w))
            else:
                worker.deleteLater()
        self._retired_workers.clear()
        self._scan_worker = None

    def _release_worker(self, worker: DirectoryScanWorker) -> None:
        """在线程结束后移除滞留引用并销毁扫描对象。"""
        if worker in self._retired_workers:
            self._retired_workers.remove(worker)
        worker.deleteLater()


def select_spatial_data_files(parent: QWidget | None = None) -> list[str]:
    """弹出专用空间数据选择窗口，返回用户确认的文件路径。"""
    dialog: SpatialFileDialog = SpatialFileDialog(parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return []
    return list(dialog.selected_paths())
