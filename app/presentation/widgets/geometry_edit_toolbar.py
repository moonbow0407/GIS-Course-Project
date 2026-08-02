"""编辑几何悬浮工具栏。"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QWidget,
)


class GeometryEditToolbar(QWidget):
    """顶点编辑模式下的悬浮操作工具栏。"""

    # 模式切换： "drag_vertex" / "delete_vertex" / "move_feature"
    mode_changed = Signal(str)
    # 提交 / 取消
    commit_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("geometryEditToolbar")
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

        self._move_btn: QPushButton = QPushButton("平移要素")
        self._move_btn.setCheckable(True)
        self._move_btn.clicked.connect(lambda: self._set_mode("move_feature"))

        self._commit_btn: QPushButton = QPushButton("✓ 提交")
        self._commit_btn.setObjectName("commitButton")
        self._commit_btn.clicked.connect(self.commit_requested.emit)

        self._cancel_btn: QPushButton = QPushButton("✗ 取消")
        self._cancel_btn.clicked.connect(self.cancel_requested.emit)

        layout.addWidget(self._drag_btn)
        layout.addWidget(self._delete_btn)
        layout.addWidget(self._move_btn)
        layout.addSpacing(12)
        layout.addWidget(self._commit_btn)
        layout.addWidget(self._cancel_btn)

        self._mode: str = "drag_vertex"

    def _set_mode(self, mode: str) -> None:
        """切换编辑模式并同步按钮状态。"""
        self._mode = mode
        self._drag_btn.setChecked(mode == "drag_vertex")
        self._delete_btn.setChecked(mode == "delete_vertex")
        self._move_btn.setChecked(mode == "move_feature")
        self.mode_changed.emit(mode)

    @property
    def edit_mode(self) -> str:
        return self._mode

    def show_at(self, x: int, y: int) -> None:
        """在指定屏幕位置显示工具栏。"""
        self.adjustSize()
        self.move(x, y)
        self.show()
