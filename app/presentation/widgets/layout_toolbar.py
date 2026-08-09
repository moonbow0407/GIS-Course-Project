"""布局视图浮动工具栏 —— 添加制图元素、撤销重做和导出操作。"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QPushButton,
    QWidget,
)


class LayoutToolbar(QFrame):
    """悬浮在布局视图上方的工具栏。

    信号:
        add_map_frame: 添加地图框。
        delete_selected: 删除选中元素。
        undo: 撤销。
        redo: 重做。
    """

    add_map_frame = Signal()
    delete_selected = Signal()
    undo = Signal()
    redo = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("layoutToolbar")
        self._create_ui()

    def _create_ui(self) -> None:
        """创建按钮组。"""
        layout: QHBoxLayout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        # 添加地图框
        btn_frame: QPushButton = QPushButton("▣ 添加地图框")
        btn_frame.setObjectName("layoutToolBtn")
        btn_frame.clicked.connect(self.add_map_frame.emit)
        layout.addWidget(btn_frame)

        # 删除
        self._delete_btn: QPushButton = QPushButton("✕ 删除")
        self._delete_btn.setObjectName("layoutDeleteBtn")
        self._delete_btn.clicked.connect(self.delete_selected.emit)
        self._delete_btn.setEnabled(False)
        layout.addWidget(self._delete_btn)

        # 撤销
        self._undo_btn: QPushButton = QPushButton("↶ 撤销")
        self._undo_btn.setObjectName("layoutEditBtn")
        self._undo_btn.clicked.connect(self.undo.emit)
        self._undo_btn.setEnabled(False)
        layout.addWidget(self._undo_btn)

        # 重做
        self._redo_btn: QPushButton = QPushButton("↷ 重做")
        self._redo_btn.setObjectName("layoutEditBtn")
        self._redo_btn.clicked.connect(self.redo.emit)
        self._redo_btn.setEnabled(False)
        layout.addWidget(self._redo_btn)

        layout.addStretch()

        # 样式
        self.setStyleSheet("""
            #layoutToolbar {
                background: #ffffff;
                border: 1px solid #d1d5db;
                border-radius: 6px;
            }
            #layoutToolBtn {
                background: #16a34a;
                color: #ffffff;
                border: none;
                border-radius: 4px;
                padding: 6px 14px;
            }
            #layoutToolBtn:hover {
                background: #15803d;
            }
            #layoutDeleteBtn {
                background: #f3f4f6;
                color: #dc2626;
                border: 1px solid #d1d5db;
                border-radius: 4px;
                padding: 6px 14px;
            }
            #layoutDeleteBtn:hover {
                background: #fef2f2;
            }
            #layoutDeleteBtn:disabled {
                color: #d1d5db;
            }
            #layoutEditBtn {
                background: #f3f4f6;
                color: #475569;
                border: 1px solid #d1d5db;
                border-radius: 4px;
                padding: 6px 12px;
            }
            #layoutEditBtn:hover {
                background: #e5e7eb;
            }
            #layoutEditBtn:disabled {
                color: #d1d5db;
            }
        """)

    def set_delete_enabled(self, enabled: bool) -> None:
        """启用/禁用删除按钮。"""
        self._delete_btn.setEnabled(enabled)

    def set_undo_enabled(self, enabled: bool) -> None:
        """启用/禁用撤销按钮。"""
        self._undo_btn.setEnabled(enabled)

    def set_redo_enabled(self, enabled: bool) -> None:
        """启用/禁用重做按钮。"""
        self._redo_btn.setEnabled(enabled)
