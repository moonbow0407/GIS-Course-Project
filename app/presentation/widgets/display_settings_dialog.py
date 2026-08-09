"""显示设置对话框：图层属性、符号系统、显示比例、全局显示和地图书签。"""

from PySide6.QtCore import QSignalBlocker, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPalette, QPixmap
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import app.presentation.global_display_settings as global_display_settings
from app.application.project_models import MapBookmark
from app.application.results import LayerSnapshot
from app.presentation.widgets.color_wheel_picker import ColorWheelPicker
from app.presentation.widgets.symbology_panel import SymbologyPanel


class DisplaySettingsDialog(QDialog):
    """编辑活动图层显示属性、符号系统和地图书签。

    信号连接由主窗口完成：图层属性、符号变更和书签操作。
    """

    # 请求调整图层透明度：(图层编号, 目标透明度 0 到 1)。
    opacity_requested = Signal(str, float)
    # 请求调整图层混合模式：(图层编号, 混合模式键名)。
    blend_mode_requested = Signal(str, str)
    # 请求调整图层显示比例范围：(图层编号, 最小比例, 最大比例)，空值表示不限。
    scale_range_requested = Signal(str, object, object)
    # 请求添加当前视图为命名书签。
    bookmark_add_requested = Signal(str)
    # 请求定位到指定名称的书签。
    bookmark_jump_requested = Signal(str)
    # 请求删除指定名称的书签。
    bookmark_delete_requested = Signal(str)
    # 转发自嵌入的 SymbologyPanel。
    symbology_changed = Signal(str, object)
    unique_requested = Signal(str, str, str)
    graduated_requested = Signal(str, str, str, str, int)
    global_display_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建绑定工作区图层的显示设置对话框。"""
        super().__init__(parent)
        self.setWindowTitle("显示设置")
        self.setMinimumWidth(480)
        self._layers: tuple[LayerSnapshot, ...] = ()
        self._updating: bool = False
        self._opacity_timer: QTimer = QTimer(self)
        self._opacity_timer.setSingleShot(True)
        self._opacity_timer.setInterval(300)
        self._opacity_timer.timeout.connect(self._emit_opacity)
        self._scale_timer: QTimer = QTimer(self)
        self._scale_timer.setSingleShot(True)
        self._scale_timer.setInterval(300)
        self._scale_timer.timeout.connect(self._emit_scale_range)
        self._layer_combo: QComboBox = QComboBox()
        self._opacity_slider: QSlider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_value: QLabel = QLabel("100%")
        self._blend_mode_combo: QComboBox = QComboBox()
        self._BLEND_MODES: tuple[tuple[str, str], ...] = (
            ("normal", "正常 (Normal)"),
            ("multiply", "正片叠底 (Multiply)"),
            ("darken", "变暗 (Darken)"),
        )
        for mode_key, mode_label in self._BLEND_MODES:
            self._blend_mode_combo.addItem(mode_label, mode_key)
        self._opacity_slider.setRange(0, 100)
        self._opacity_slider.setValue(100)
        self._min_scale: QDoubleSpinBox = QDoubleSpinBox()
        self._max_scale: QDoubleSpinBox = QDoubleSpinBox()
        for spin in (self._min_scale, self._max_scale):
            spin.setRange(0.0, 1_000_000.0)
            spin.setDecimals(1)
            spin.setSuffix("%")
            spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            spin.setSpecialValueText("不限")
            spin.setValue(0.0)
        self._symbology_panel: SymbologyPanel = SymbologyPanel()
        self._selection_color_button: QPushButton = QPushButton("颜色 ▾")
        self._sketch_color_button: QPushButton = QPushButton("颜色 ▾")
        self._bookmark_name: QLineEdit = QLineEdit()
        self._bookmark_name.setPlaceholderText("请输入书签名称")
        self._bookmark_list: QListWidget = QListWidget()
        self._create_ui()
        self._connect_signals()
        self._update_controls()

    def _create_ui(self) -> None:
        """组装图层选择器、符号/显示双标签页和书签控件。"""
        layout: QVBoxLayout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # ── 顶部：图层选择器（两个标签页共用） ──
        layer_row: QHBoxLayout = QHBoxLayout()
        layer_row.addWidget(QLabel("图层"))
        layer_row.addWidget(self._layer_combo, 1)
        layout.addLayout(layer_row)

        # ── 双标签页：符号系统 / 显示设置 ──
        self._tabs: QTabWidget = QTabWidget()
        self._tabs.setDocumentMode(True)

        # 标签页 1：符号系统。
        self._tabs.addTab(self._symbology_panel, "符号系统")

        # 标签页 2：显示设置（图层属性 + 比例 + 全局 + 书签）。
        display_page: QWidget = QWidget()
        display_layout: QVBoxLayout = QVBoxLayout(display_page)
        display_layout.setContentsMargins(0, 8, 0, 0)
        display_layout.setSpacing(12)

        # 图层属性。
        layer_group: QGroupBox = QGroupBox("图层属性")
        layer_group_layout: QVBoxLayout = QVBoxLayout(layer_group)
        layer_group_layout.setSpacing(8)
        opacity_row: QHBoxLayout = QHBoxLayout()
        opacity_row.addWidget(QLabel("不透明度"))
        opacity_row.addWidget(self._opacity_slider, 1)
        opacity_row.addWidget(self._opacity_value)
        layer_group_layout.addLayout(opacity_row)
        blend_row: QHBoxLayout = QHBoxLayout()
        blend_row.addWidget(QLabel("混合模式"))
        blend_row.addWidget(self._blend_mode_combo, 1)
        layer_group_layout.addLayout(blend_row)
        display_layout.addWidget(layer_group)

        # 显示比例范围。
        scale_group: QGroupBox = QGroupBox("显示比例范围")
        scale_layout: QVBoxLayout = QVBoxLayout(scale_group)
        scale_layout.setSpacing(8)
        scale_hint: QLabel = QLabel(
            "视图比例低于最小范围或高于最大范围时，图层自动隐藏；0 表示不限制。"
        )
        scale_hint.setObjectName("displayScaleHint")
        scale_hint.setWordWrap(True)
        scale_layout.addWidget(scale_hint)
        scale_row: QHBoxLayout = QHBoxLayout()
        scale_row.addWidget(QLabel("最小比例"))
        scale_row.addWidget(self._min_scale, 1)
        scale_row.addWidget(QLabel("最大比例"))
        scale_row.addWidget(self._max_scale, 1)
        scale_layout.addLayout(scale_row)
        display_layout.addWidget(scale_group)

        # 全局显示。
        global_group: QGroupBox = QGroupBox("全局显示")
        global_layout: QFormLayout = QFormLayout(global_group)
        global_layout.setSpacing(8)
        self._setup_color_button(
            self._selection_color_button,
            global_display_settings.selection_color(),
        )
        self._setup_color_button(
            self._sketch_color_button,
            global_display_settings.sketch_color(),
        )
        global_layout.addRow("选择高亮", self._selection_color_button)
        global_layout.addRow("草图预览", self._sketch_color_button)
        display_layout.addWidget(global_group)

        # 地图书签。
        bookmark_group: QGroupBox = QGroupBox("地图书签")
        bookmark_layout: QVBoxLayout = QVBoxLayout(bookmark_group)
        bookmark_layout.setSpacing(8)
        name_row: QHBoxLayout = QHBoxLayout()
        name_row.addWidget(self._bookmark_name, 1)
        add_button: QPushButton = QPushButton("添加当前视图")
        add_button.clicked.connect(self._on_add_bookmark)
        name_row.addWidget(add_button)
        bookmark_layout.addLayout(name_row)
        bookmark_layout.addWidget(self._bookmark_list, 1)
        action_row: QHBoxLayout = QHBoxLayout()
        jump_button: QPushButton = QPushButton("定位到此书签")
        jump_button.clicked.connect(self._on_jump_bookmark)
        delete_button: QPushButton = QPushButton("删除书签")
        delete_button.clicked.connect(self._on_delete_bookmark)
        action_row.addWidget(jump_button)
        action_row.addWidget(delete_button)
        bookmark_layout.addLayout(action_row)
        display_layout.addWidget(bookmark_group, 1)

        self._tabs.addTab(display_page, "显示设置")
        layout.addWidget(self._tabs, 1)

        # ── 底部：确定 / 取消 ──
        button_box: QDialogButtonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self._apply_light_palette()

    def _connect_signals(self) -> None:
        """绑定自动应用、书签管理和符号系统转发信号。"""
        self._layer_combo.currentIndexChanged.connect(self._on_layer_changed)
        self._opacity_slider.valueChanged.connect(self._schedule_opacity)
        self._min_scale.valueChanged.connect(self._schedule_scale)
        self._max_scale.valueChanged.connect(self._schedule_scale)
        self._blend_mode_combo.currentIndexChanged.connect(self._emit_blend_mode)
        self._bookmark_list.itemDoubleClicked.connect(
            lambda _item: self._on_jump_bookmark()
        )
        self._selection_color_button.clicked.connect(
            lambda: self._on_global_color_clicked("selection")
        )
        self._sketch_color_button.clicked.connect(
            lambda: self._on_global_color_clicked("sketch")
        )
        # 转发 SymbologyPanel 信号。
        self._symbology_panel.symbology_changed.connect(self.symbology_changed.emit)
        self._symbology_panel.unique_requested.connect(self.unique_requested.emit)
        self._symbology_panel.graduated_requested.connect(self.graduated_requested.emit)

    def set_layers(
        self,
        layers: tuple[LayerSnapshot, ...],
        active_layer_id: str | None,
    ) -> None:
        """绑定工作区图层并加载当前显示属性。"""
        self._layers = layers
        self._updating = True
        try:
            with QSignalBlocker(self._layer_combo):
                self._layer_combo.clear()
                for index, layer in enumerate(layers):
                    self._layer_combo.addItem(layer.name, layer.layer_id)
                    if layer.layer_id == active_layer_id:
                        self._layer_combo.setCurrentIndex(index)
        finally:
            self._updating = False
        active = self.selected_layer()
        self._load_controls(active)
        self._symbology_panel.set_layer(active)
        self._update_controls()

    def _on_layer_changed(self, _index: int) -> None:
        """切换图层后同步所有图层相关控件。"""
        if self._updating:
            return
        layer = self.selected_layer()
        self._load_controls(layer)
        self._symbology_panel.set_layer(layer)
        self._update_controls()

    def set_bookmarks(self, bookmarks: tuple[MapBookmark, ...]) -> None:
        """刷新书签列表并尽量保留当前选中的名称。"""
        current_name: str | None = None
        current: QListWidgetItem | None = self._bookmark_list.currentItem()
        if current is not None:
            current_name = current.text()
        self._bookmark_list.clear()
        for bookmark in bookmarks:
            item: QListWidgetItem = QListWidgetItem(bookmark.name)
            self._bookmark_list.addItem(item)
            if bookmark.name == current_name:
                self._bookmark_list.setCurrentItem(item)
        if self._bookmark_list.currentItem() is None and self._bookmark_list.count():
            self._bookmark_list.setCurrentRow(0)

    def selected_layer(self) -> LayerSnapshot | None:
        """返回当前选中图层的快照，工作区为空时返回空值。"""
        layer_id: object = self._layer_combo.currentData()
        for layer in self._layers:
            if layer.layer_id == layer_id:
                return layer
        return None

    def _load_controls(self, layer: LayerSnapshot | None) -> None:
        """将选中图层的显示属性同步到控件。"""
        with QSignalBlocker(self._opacity_slider), QSignalBlocker(
            self._min_scale
        ), QSignalBlocker(self._max_scale), QSignalBlocker(
            self._blend_mode_combo
        ):
            if layer is None:
                self._opacity_slider.setValue(100)
                self._min_scale.setValue(0.0)
                self._max_scale.setValue(0.0)
                self._opacity_value.setText("100%")
                self._blend_mode_combo.setCurrentIndex(0)
                return
            self._opacity_slider.setValue(int(round(layer.opacity * 100)))
            self._opacity_value.setText(f"{layer.opacity * 100:.0f}%")
            self._min_scale.setValue(layer.min_scale_percent or 0.0)
            self._max_scale.setValue(layer.max_scale_percent or 0.0)
            blend_index: int = self._blend_mode_combo.findData(layer.blend_mode)
            if blend_index >= 0:
                self._blend_mode_combo.setCurrentIndex(blend_index)

    def _schedule_opacity(self, value: int) -> None:
        """更新百分比标签并在连续拖动停止后应用不透明度。"""
        self._opacity_value.setText(f"{value}%")
        if not self._updating:
            self._opacity_timer.start()

    def _emit_opacity(self) -> None:
        """发出当前不透明度请求。"""
        layer: LayerSnapshot | None = self.selected_layer()
        if layer is None:
            return
        self.opacity_requested.emit(layer.layer_id, self._opacity_slider.value() / 100.0)

    def _emit_blend_mode(self) -> None:
        """发出当前混合模式请求。"""
        if self._updating:
            return
        layer: LayerSnapshot | None = self.selected_layer()
        if layer is None:
            return
        mode_key: str = self._blend_mode_combo.currentData()
        self.blend_mode_requested.emit(layer.layer_id, mode_key)

    def _schedule_scale(self) -> None:
        """连续数值输入停止后应用显示比例范围。"""
        if not self._updating:
            self._scale_timer.start()

    def _emit_scale_range(self) -> None:
        """发出当前显示比例范围请求，并校正最小与最大比例的关系。"""
        layer: LayerSnapshot | None = self.selected_layer()
        if layer is None:
            return
        min_scale: float | None = (
            self._min_scale.value() if self._min_scale.value() > 0.0 else None
        )
        max_scale: float | None = (
            self._max_scale.value() if self._max_scale.value() > 0.0 else None
        )
        if min_scale is not None and max_scale is not None and max_scale < min_scale:
            with QSignalBlocker(self._max_scale):
                self._max_scale.setValue(min_scale)
            max_scale = min_scale
        self.scale_range_requested.emit(layer.layer_id, min_scale, max_scale)

    def _on_add_bookmark(self) -> None:
        """请求主窗口把当前视图保存为指定名称的书签。"""
        name: str = self._bookmark_name.text().strip()
        if not name:
            return
        self.bookmark_add_requested.emit(name)
        self._bookmark_name.clear()

    def _on_jump_bookmark(self) -> None:
        """请求主窗口定位到选中书签。"""
        current: QListWidgetItem | None = self._bookmark_list.currentItem()
        if current is not None:
            self.bookmark_jump_requested.emit(current.text())

    def _on_delete_bookmark(self) -> None:
        """请求主窗口删除选中书签。"""
        current: QListWidgetItem | None = self._bookmark_list.currentItem()
        if current is not None:
            self.bookmark_delete_requested.emit(current.text())

    def _update_controls(self) -> None:
        """根据是否存在图层启用或禁用图层相关控件。"""
        has_layer: bool = self.selected_layer() is not None
        self._opacity_slider.setEnabled(has_layer)
        self._blend_mode_combo.setEnabled(has_layer)
        self._min_scale.setEnabled(has_layer)
        self._max_scale.setEnabled(has_layer)
        self._tabs.setTabEnabled(0, has_layer)

    def _on_global_color_clicked(self, kind: str) -> None:
        """弹出 HSB 色轮选择全局显示颜色。

        参数:
            kind: "selection" 或 "sketch"。
        """
        if kind == "selection":
            current = global_display_settings.selection_color()
            button = self._selection_color_button
        else:
            current = global_display_settings.sketch_color()
            button = self._sketch_color_button
        color: QColor | None = ColorWheelPicker.get_color(current, self)
        if color is None:
            return
        if kind == "selection":
            global_display_settings.set_selection_color(color)
        else:
            global_display_settings.set_sketch_color(color)
        self._setup_color_button(button, color)
        self.global_display_changed.emit()

    def _setup_color_button(self, button: QPushButton, color: QColor) -> None:
        """统一设置颜色按钮的外观：色块图标 + 边框 + hover 高亮。"""
        pixmap = QPixmap(60, 24)
        pixmap.fill(color)
        button.setIcon(QIcon(pixmap))
        button.setIconSize(QSize(60, 24))
        button.setText("颜色 ▾")
        button.setStyleSheet(
            "QPushButton {"
            "  border: 1px solid #CBD5E1;"
            "  border-radius: 4px;"
            "  padding: 4px 10px;"
            "  text-align: left;"
            "  font-size: 12px;"
            "  color: #475569;"
            "}"
            "QPushButton:hover {"
            "  border-color: #3B82F6;"
            "  background-color: #F8FAFC;"
            "}"
        )

    def _apply_light_palette(self) -> None:
        """给对话框和内部列表设置完整的浅色窗口、输入和选择颜色。"""
        palette: QPalette = QPalette(self.palette())
        palette.setColor(QPalette.ColorRole.Window, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#263548"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f8fafc"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#263548"))
        palette.setColor(QPalette.ColorRole.Button, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#263548"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#dcecf9"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#0f5f9f"))
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        self._layer_combo.setPalette(palette)
        self._layer_combo.view().setPalette(palette)
