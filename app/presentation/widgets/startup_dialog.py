"""启动对话框 — 历史工程列表与新建工程入口。"""

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_RECENT_FILE: Path = Path.home() / ".gis_desktop_recent.json"
_MAX_RECENT: int = 10


def load_recent_projects() -> list[Path]:
    """加载历史工程路径列表。"""
    try:
        if _RECENT_FILE.exists():
            data = json.loads(_RECENT_FILE.read_text(encoding="utf-8"))
            return [Path(p) for p in data if Path(p).exists()]
    except (json.JSONDecodeError, OSError):
        pass
    return []


def save_recent_project(path: Path) -> None:
    """将工程路径添加到历史记录并保存。"""
    recent: list[Path] = load_recent_projects()
    if path in recent:
        recent.remove(path)
    recent.insert(0, path)
    recent = recent[:_MAX_RECENT]
    _RECENT_FILE.write_text(
        json.dumps([str(p) for p in recent], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class StartupDialog(QDialog):
    """启动时展示历史工程并提供新建入口。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("GIS 桌面通用平台")
        self.setMinimumWidth(500)
        self.setMinimumHeight(380)

        # 选中的工程路径。
        self.selected_path: Path | None = None
        self.action: str = "cancel"  # "open" | "browse" | "new" | "cancel"

        layout: QVBoxLayout = QVBoxLayout(self)

        title: QLabel = QLabel("欢迎使用 GIS 桌面通用平台")
        title.setObjectName("startupTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addSpacing(8)

        # ── 历史工程 ──
        recent_group: QGroupBox = QGroupBox("最近打开的工程")
        self._recent_list: QListWidget = QListWidget()
        self._recent_list.itemDoubleClicked.connect(self._open_selected)
        recent_layout: QVBoxLayout = QVBoxLayout(recent_group)
        recent_layout.addWidget(self._recent_list)

        browse_btn: QPushButton = QPushButton("浏览其他工程…")
        browse_btn.clicked.connect(self._browse_project)
        recent_layout.addWidget(browse_btn)
        layout.addWidget(recent_group, 1)

        # ── 历史记录为空时的提示 ──
        self._empty_label: QLabel = QLabel(
            "暂无历史记录。\n请打开一个工程文件（.gisproj）或新建空白工程。"
        )
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        recent_layout.addWidget(self._empty_label)

        # ── 底部按钮 ──
        self._new_btn: QPushButton = QPushButton("新建空白工程")
        self._new_btn.setObjectName("startupNewButton")
        self._new_btn.clicked.connect(self._new_project)

        btn_layout: QVBoxLayout = QVBoxLayout()
        btn_layout.addWidget(self._new_btn)
        layout.addLayout(btn_layout)

        self._refresh_recent_list()

    def _refresh_recent_list(self) -> None:
        """刷新历史工程列表。"""
        self._recent_list.clear()
        recent: list[Path] = load_recent_projects()
        for path in recent:
            item: QListWidgetItem = QListWidgetItem(path.name)
            item.setToolTip(str(path))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self._recent_list.addItem(item)
        has_recent: bool = len(recent) > 0
        self._recent_list.setVisible(has_recent)
        self._empty_label.setVisible(not has_recent)
        if has_recent:
            self._recent_list.setCurrentRow(0)

    def _open_selected(self, item: QListWidgetItem) -> None:
        """双击历史项打开工程。"""
        path_str: str | None = item.data(Qt.ItemDataRole.UserRole)
        if path_str:
            self.selected_path = Path(path_str)
            self.action = "open"
            self.accept()

    def _browse_project(self) -> None:
        """浏览其他工程文件。"""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "打开工程",
            str(Path.home()),
            "GIS 工程文件 (*.gisproj);;All Files (*)",
        )
        if path:
            self.selected_path = Path(path)
            self.action = "open"
            self.accept()

    def _new_project(self) -> None:
        """新建空白工程。"""
        self.action = "new"
        self.accept()
