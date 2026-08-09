"""布局视图浮动工具栏 —— 添加制图元素、撤销重做和导出操作。"""

from PySide6.QtCore import Signal
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
        add_scale_bar: 添加比例尺。
        add_legend: 添加图例。
        add_north_arrow: 添加指北针。
        delete_selected: 删除选中元素。
        undo: 撤销。
        redo: 重做。
    """

    add_map_frame = Signal()
    add_scale_bar = Signal()
    add_legend = Signal()
    add_north_arrow = Signal()
    add_text = Signal()
    page_setup = Signal()
    edit_properties = Signal()
    export_layout = Signal()
    close_requested = Signal()
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
        btn_frame: QPushButton = QPushButton("▣ 地图框")
        btn_frame.setObjectName("layoutToolBtn")
        btn_frame.clicked.connect(self.add_map_frame.emit)
        layout.addWidget(btn_frame)

        # 添加比例尺
        btn_scale: QPushButton = QPushButton("━ 比例尺")
        btn_scale.setObjectName("layoutAddBtn")
        btn_scale.clicked.connect(self.add_scale_bar.emit)
        layout.addWidget(btn_scale)

        # 添加图例
        btn_legend: QPushButton = QPushButton("≡ 图例")
        btn_legend.setObjectName("layoutAddBtn")
        btn_legend.clicked.connect(self.add_legend.emit)
        layout.addWidget(btn_legend)

        # 添加指北针
        btn_north: QPushButton = QPushButton("↑ 指北针")
        btn_north.setObjectName("layoutAddBtn")
        btn_north.clicked.connect(self.add_north_arrow.emit)
        layout.addWidget(btn_north)

        # 添加文本
        btn_text: QPushButton = QPushButton("T 文本")
        btn_text.setObjectName("layoutAddBtn")
        btn_text.clicked.connect(self.add_text.emit)
        layout.addWidget(btn_text)

        # 分隔
        sep: QFrame = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("background: #d1d5db; max-width: 1px; min-width: 1px;")
        layout.addWidget(sep)

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

        # 页面设置
        btn_page: QPushButton = QPushButton("⚙ 页面设置")
        btn_page.setObjectName("layoutEditBtn")
        btn_page.clicked.connect(self.page_setup.emit)
        layout.addWidget(btn_page)

        # 属性
        btn_props: QPushButton = QPushButton("✎ 属性")
        btn_props.setObjectName("layoutEditBtn")
        btn_props.clicked.connect(self.edit_properties.emit)
        layout.addWidget(btn_props)

        # 导出
        btn_export: QPushButton = QPushButton("⤓ 导出")
        btn_export.setObjectName("layoutEditBtn")
        btn_export.clicked.connect(self.export_layout.emit)
        layout.addWidget(btn_export)

        layout.addStretch()

        # 关闭按钮
        btn_close: QPushButton = QPushButton("✕")
        btn_close.setObjectName("layoutCloseBtn")
        btn_close.setFixedSize(24, 24)
        btn_close.clicked.connect(self.close_requested.emit)
        layout.addWidget(btn_close)

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
            #layoutAddBtn {
                background: #f3f4f6;
                color: #374151;
                border: 1px solid #d1d5db;
                border-radius: 4px;
                padding: 6px 12px;
            }
            #layoutAddBtn:hover {
                background: #e5e7eb;
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
            #layoutCloseBtn {
                background: transparent;
                color: #9ca3af;
                border: none;
                border-radius: 4px;
                font-size: 14px;
            }
            #layoutCloseBtn:hover {
                background: #fee2e2;
                color: #dc2626;
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
