"""布局视图浮动工具栏 —— 添加制图元素、撤销重做和导出操作。"""

from PySide6.QtCore import Signal, SignalInstance
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
    zoom_in = Signal()
    zoom_out = Signal()
    zoom_fit = Signal()
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

        self._add_buttons: list[QPushButton] = []

        # 添加地图框
        self._btn_frame: QPushButton = self._make_add_button("▣ 地图框")
        self._btn_frame.clicked.connect(
            lambda: self._on_add_clicked(self._btn_frame, self.add_map_frame)
        )
        layout.addWidget(self._btn_frame)

        # 添加比例尺
        self._btn_scale: QPushButton = self._make_add_button("━ 比例尺")
        self._btn_scale.clicked.connect(
            lambda: self._on_add_clicked(self._btn_scale, self.add_scale_bar)
        )
        layout.addWidget(self._btn_scale)

        # 添加图例
        self._btn_legend: QPushButton = self._make_add_button("≡ 图例")
        self._btn_legend.clicked.connect(
            lambda: self._on_add_clicked(self._btn_legend, self.add_legend)
        )
        layout.addWidget(self._btn_legend)

        # 添加指北针
        self._btn_north: QPushButton = self._make_add_button("↑ 指北针")
        self._btn_north.clicked.connect(
            lambda: self._on_add_clicked(self._btn_north, self.add_north_arrow)
        )
        layout.addWidget(self._btn_north)

        # 添加文本
        self._btn_text: QPushButton = self._make_add_button("T 文本")
        self._btn_text.clicked.connect(
            lambda: self._on_add_clicked(self._btn_text, self.add_text)
        )
        layout.addWidget(self._btn_text)

        # 缩放分隔
        sep_zoom: QFrame = QFrame()
        sep_zoom.setFrameShape(QFrame.Shape.VLine)
        sep_zoom.setStyleSheet("background: #d1d5db; max-width: 1px; min-width: 1px;")
        layout.addWidget(sep_zoom)

        # 放大
        btn_zoom_in: QPushButton = QPushButton("⊕ 放大")
        btn_zoom_in.setObjectName("layoutEditBtn")
        btn_zoom_in.clicked.connect(self.zoom_in.emit)
        layout.addWidget(btn_zoom_in)

        # 缩小
        btn_zoom_out: QPushButton = QPushButton("⊖ 缩小")
        btn_zoom_out.setObjectName("layoutEditBtn")
        btn_zoom_out.clicked.connect(self.zoom_out.emit)
        layout.addWidget(btn_zoom_out)

        # 适配页面
        btn_fit: QPushButton = QPushButton("⊡ 适配")
        btn_fit.setObjectName("layoutEditBtn")
        btn_fit.clicked.connect(self.zoom_fit.emit)
        layout.addWidget(btn_fit)

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
            #layoutAddBtn {
                background: #f3f4f6;
                color: #9ca3af;
                border: 1px solid #e5e7eb;
                border-radius: 4px;
                padding: 6px 12px;
            }
            #layoutAddBtn:hover {
                background: #e5e7eb;
                color: #6b7280;
            }
            #layoutAddBtn:checked {
                background: #1e40af;
                color: #ffffff;
                border-color: #1e40af;
            }
            #layoutAddBtn:checked:hover {
                background: #1e3a8a;
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

    def _make_add_button(self, text: str) -> QPushButton:
        """创建可切换的添加按钮。"""
        btn: QPushButton = QPushButton(text)
        btn.setObjectName("layoutAddBtn")
        btn.setCheckable(True)
        self._add_buttons.append(btn)
        return btn

    def _on_add_clicked(self, btn: QPushButton, signal: SignalInstance) -> None:
        """处理添加按钮点击：互斥切换 + 允许取消选中。"""
        if btn.isChecked():
            # 刚被选中 → 取消其他按钮，保持当前选中
            for other in self._add_buttons:
                if other is not btn:
                    other.setChecked(False)
            signal.emit()
        # 已选中的按钮再次点击变成未选中 → 不做任何操作

    def set_delete_enabled(self, enabled: bool) -> None:
        """启用/禁用删除按钮。"""
        self._delete_btn.setEnabled(enabled)

    def set_undo_enabled(self, enabled: bool) -> None:
        """启用/禁用撤销按钮。"""
        self._undo_btn.setEnabled(enabled)

    def set_redo_enabled(self, enabled: bool) -> None:
        """启用/禁用重做按钮。"""
        self._redo_btn.setEnabled(enabled)
