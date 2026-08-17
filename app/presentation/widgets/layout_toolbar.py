"""布局视图浮动工具栏 —— 添加制图元素、撤销清空和导出操作。"""

from PySide6.QtCore import Signal, SignalInstance
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QMenu,
    QPushButton,
    QToolButton,
    QWidget,
)


class LayoutToolbar(QFrame):
    """悬浮在布局视图上方的工具栏。

    信号:
        add_map_frame: 添加地图框。
        add_scale_bar: 添加比例尺。
        add_legend: 添加图例。
        add_north_arrow: 添加指北针。
        add_text: 添加一个文本（支持多实例）。
        apply_template: 补齐专题图默认排版。
        clear_all: 清空图幅中的全部元素。
        delete_selected: 删除选中元素。
        undo: 撤销。
    """

    add_map_frame = Signal()
    add_scale_bar = Signal()
    add_legend = Signal()
    add_north_arrow = Signal()
    add_text = Signal()
    apply_template = Signal()
    page_setup = Signal()
    edit_properties = Signal()
    zoom_in = Signal()
    zoom_out = Signal()
    zoom_fit = Signal()
    export_layout = Signal()
    close_requested = Signal()
    delete_selected = Signal()
    undo = Signal()
    clear_all = Signal()
    shortcut_help = Signal()
    align_to_page = Signal(object, object)

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

        # 添加文本（普通按钮：点一次加一个，不做高亮切换）
        self._btn_text: QPushButton = QPushButton("T 文本")
        self._btn_text.setObjectName("layoutEditBtn")
        self._btn_text.clicked.connect(self.add_text.emit)
        layout.addWidget(self._btn_text)

        self._btn_template: QPushButton = QPushButton("默认排版")
        self._btn_template.setObjectName("layoutEditBtn")
        self._btn_template.clicked.connect(self.apply_template.emit)
        layout.addWidget(self._btn_template)

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

        self._align_btn: QToolButton = QToolButton()
        self._align_btn.setText("对齐")
        self._align_btn.setObjectName("layoutEditBtn")
        self._align_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._align_btn.setEnabled(False)
        align_menu: QMenu = QMenu(self._align_btn)
        for label, horizontal, vertical in (
            ("页面居中", "center", "middle"),
            ("水平居中", "center", None),
            ("垂直居中", None, "middle"),
            ("左对齐", "left", None),
            ("右对齐", "right", None),
            ("顶对齐", None, "top"),
            ("底对齐", None, "bottom"),
        ):
            action = align_menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, h=horizontal, v=vertical: self.align_to_page.emit(h, v)
            )
        self._align_btn.setMenu(align_menu)
        layout.addWidget(self._align_btn)

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

        # 清空图幅
        self._clear_btn: QPushButton = QPushButton("⌫ 清空")
        self._clear_btn.setObjectName("layoutDeleteBtn")
        self._clear_btn.clicked.connect(self.clear_all.emit)
        self._clear_btn.setEnabled(False)
        layout.addWidget(self._clear_btn)

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

        # 快捷键一览（独立一栏，分隔线隔开）
        sep_shortcut: QFrame = QFrame()
        sep_shortcut.setFrameShape(QFrame.Shape.VLine)
        sep_shortcut.setStyleSheet("background: #d1d5db; max-width: 1px; min-width: 1px;")
        layout.addWidget(sep_shortcut)

        btn_shortcut: QPushButton = QPushButton("⌨ 快捷键")
        btn_shortcut.setObjectName("layoutEditBtn")
        btn_shortcut.clicked.connect(self.shortcut_help.emit)
        layout.addWidget(btn_shortcut)

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
        """创建可切换的添加按钮。

        按钮的选中（高亮）状态反映对应元素是否已存在于图幅中，
        由 sync_add_buttons 根据布局文档状态统一同步。
        """
        btn: QPushButton = QPushButton(text)
        btn.setObjectName("layoutAddBtn")
        btn.setCheckable(True)
        return btn

    def _on_add_clicked(self, btn: QPushButton, signal: SignalInstance) -> None:
        """处理添加按钮点击：切换对应元素在布局中的显示状态。

        按钮的选中（高亮）状态由布局视图通过 sync_add_buttons 同步，
        此处只负责转发点击，具体的添加/选中/删除切换在 LayoutView 中完成。
        """
        signal.emit()

    def sync_add_buttons(self, present_types: set[str]) -> None:
        """根据图幅中已存在的元素类型同步添加按钮的高亮状态。

        文本按钮为普通添加按钮（支持多实例），不参与高亮同步，故不在
        此映射中。

        参数:
            present_types: 布局文档中已存在元素类型的名称集合。
        """
        mapping = {
            "MapFrameElement": self._btn_frame,
            "ScaleBarElement": self._btn_scale,
            "LegendElement": self._btn_legend,
            "NorthArrowElement": self._btn_north,
        }
        for type_name, btn in mapping.items():
            btn.setChecked(type_name in present_types)

    def set_align_enabled(self, enabled: bool) -> None:
        """启用/禁用页面对齐按钮。"""
        self._align_btn.setEnabled(enabled)

    def set_delete_enabled(self, enabled: bool) -> None:
        """启用/禁用删除按钮。"""
        self._delete_btn.setEnabled(enabled)

    def set_undo_enabled(self, enabled: bool) -> None:
        """启用/禁用撤销按钮。"""
        self._undo_btn.setEnabled(enabled)

    def set_clear_enabled(self, enabled: bool) -> None:
        """启用/禁用清空按钮。"""
        self._clear_btn.setEnabled(enabled)
