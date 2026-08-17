"""编辑几何要素悬浮工具栏。"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QWidget,
)


class GeometryEditToolbar(QWidget):
    """顶点编辑模式下的悬浮操作工具栏。"""

    # 模式切换： "drag_vertex" / "delete_vertex"
    mode_changed = Signal(str)
    # 提交 / 取消 / 全选
    commit_requested = Signal()
    cancel_requested = Signal()
    select_all_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("geometryEditToolbar")
        # 独立顶层 QWidget 在深色系统主题下不会自动绘制 QSS 背景；显式
        # 启用样式背景，保证与应用内浅色对话框保持一致。
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWindowTitle("编辑几何要素")
        self.setWindowFlags(
            Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
        )

        layout: QHBoxLayout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self._drag_btn: QPushButton = QPushButton("拖拽顶点")
        self._drag_btn.setCheckable(True)
        self._drag_btn.setChecked(True)
        self._drag_btn.clicked.connect(lambda: self._set_mode("drag_vertex"))

        self._delete_btn: QPushButton = QPushButton("删除顶点")
        self._delete_btn.setCheckable(True)
        self._delete_btn.clicked.connect(lambda: self._set_mode("delete_vertex"))

        self._select_all_btn: QPushButton = QPushButton("全选")
        self._select_all_btn.clicked.connect(self.select_all_requested.emit)

        self._commit_btn: QPushButton = QPushButton("✓ 提交")
        self._commit_btn.setObjectName("commitButton")
        self._commit_btn.clicked.connect(self.commit_requested.emit)

        self._cancel_btn: QPushButton = QPushButton("✗ 取消")
        self._cancel_btn.clicked.connect(self.cancel_requested.emit)

        layout.addWidget(self._drag_btn)
        layout.addWidget(self._delete_btn)
        layout.addWidget(self._select_all_btn)
        layout.addSpacing(12)
        layout.addWidget(self._commit_btn)
        layout.addWidget(self._cancel_btn)

        self._mode: str = "drag_vertex"

    def _set_mode(self, mode: str) -> None:
        """切换编辑模式并同步按钮状态（用户点击按钮时调用）。"""
        self._mode = mode
        self._sync_buttons(mode)
        self.mode_changed.emit(mode)

    def set_mode(self, mode: str) -> None:
        """程序化切换编辑模式（不触发 mode_changed，避免信号循环）。"""
        self._mode = mode
        self._sync_buttons(mode)

    def _sync_buttons(self, mode: str) -> None:
        """仅同步按钮选中状态，不发射信号。"""
        self._drag_btn.setChecked(mode == "drag_vertex")
        self._delete_btn.setChecked(mode == "delete_vertex")

    @property
    def edit_mode(self) -> str:
        return self._mode

    def show_at(self, x: int, y: int) -> None:
        """在指定屏幕位置显示工具栏。"""
        self.adjustSize()
        self.move(x, y)
        self.show()
