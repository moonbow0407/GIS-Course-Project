"""跟随活动图层的符号系统侧边面板。"""

from collections.abc import Callable
from dataclasses import replace

from PySide6.QtCore import QSignalBlocker, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.application.results import LayerSnapshot
from app.domain.layer_style import GeometryFamily, LayerStyle
from app.domain.raster_layer import RasterLayer
from app.domain.symbology import (
    CATEGORICAL_SCHEMES,
    COLOR_RAMPS,
    GraduatedClass,
    RasterRendererType,
    RasterSymbology,
    StretchType,
    UniqueValueClass,
    VectorRendererType,
    VectorSymbology,
)
from app.domain.vector_layer import VectorLayer
from app.presentation.widgets.color_wheel_picker import ColorWheelPicker


class SymbologyPanel(QWidget):
    """编辑活动图层符号并通过信号请求自动应用。"""

    symbology_changed = Signal(str, object)
    unique_requested = Signal(str, str, str)
    graduated_requested = Signal(str, str, str, str, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建初始为空的符号系统面板。"""
        super().__init__(parent)
        self.setObjectName("symbologyPanel")
        self._snapshot: LayerSnapshot | None = None
        self._updating: bool = False
        self._raster_timer: QTimer = QTimer(self)
        self._raster_timer.setSingleShot(True)
        self._raster_timer.setInterval(300)
        self._raster_timer.timeout.connect(self._emit_raster)
        self._title: QLabel = QLabel("请选择图层")
        self._metadata: QLabel = QLabel("打开图层后可配置显示方式")
        self._preview: QLabel = QLabel()
        self._class_count_label: QLabel = QLabel("0 类")
        self._auto_apply_checkbox: QCheckBox = QCheckBox("自动应用")
        self._apply_button: QPushButton = QPushButton("应用")
        self._pending_action: Callable[[], None] | None = None
        self._settings_card: QFrame = QFrame()
        self._classes_card: QFrame = QFrame()
        self._renderer: QComboBox = QComboBox()
        self._field: QComboBox = QComboBox()
        self._scheme: QComboBox = QComboBox()
        self._method: QComboBox = QComboBox()
        self._class_count: QSpinBox = QSpinBox()
        self._simple_color_button: QPushButton = QPushButton("选择颜色…")
        self._current_simple_color: str = "#2F7DE1"
        self._red_band: QSpinBox = QSpinBox()
        self._green_band: QSpinBox = QSpinBox()
        self._blue_band: QSpinBox = QSpinBox()
        self._stretch_band: QSpinBox = QSpinBox()
        self._stretch_type: QComboBox = QComboBox()
        self._lower_percent: QDoubleSpinBox = QDoubleSpinBox()
        self._upper_percent: QDoubleSpinBox = QDoubleSpinBox()
        self._invert: QCheckBox = QCheckBox("反转色带")
        self._classes: QTableWidget = QTableWidget(0, 3)
        self._form: QFormLayout = QFormLayout()
        self._create_ui()
        self._apply_light_palette()
        self._connect_signals()
        self._update_control_visibility()

    def set_layer(self, snapshot: LayerSnapshot | None) -> None:
        """切换面板绑定图层并加载其当前符号配置。"""
        self._snapshot = snapshot
        self._updating = True
        try:
            if snapshot is None:
                self._title.setText("请选择图层")
                self._metadata.setText("打开图层后可配置显示方式")
                for combo in (
                    self._renderer,
                    self._field,
                    self._scheme,
                ):
                    with QSignalBlocker(combo):
                        combo.clear()
                self._classes.setRowCount(0)
                self._class_count_label.setText("0 类")
                self._preview.clear()
                self._update_control_visibility()
                return
            self._title.setText(snapshot.name)
            if isinstance(snapshot.layer, VectorLayer):
                self._load_vector(snapshot.layer)
            else:
                self._load_raster(snapshot.layer)
            self._update_layer_summary(snapshot.layer)
            self._update_preview(snapshot.layer)
        finally:
            self._updating = False
        self._update_control_visibility()

    def _create_ui(self) -> None:
        """组装通用、矢量和栅格符号控件。"""
        self._title.setObjectName("symbologyTitle")
        self._metadata.setObjectName("symbologyLayerMetadata")
        self._preview.setObjectName("symbologyPreview")
        self._preview.setMinimumHeight(64)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._auto_apply_checkbox.setObjectName("symbologyAutoApplyCheck")
        self._auto_apply_checkbox.setChecked(True)
        self._apply_button.setObjectName("symbologyApplyButton")
        self._apply_button.setVisible(False)
        self._apply_button.setFixedWidth(72)
        self._class_count_label.setObjectName("symbologyClassCount")
        self._settings_card.setObjectName("symbologySettingsCard")
        self._classes_card.setObjectName("symbologyClassesCard")
        self._renderer.setObjectName("symbologyRenderer")
        self._simple_color_button.setObjectName("symbologySimpleColorButton")
        self._scheme.setIconSize(QSize(86, 16))
        self._field.setObjectName("symbologyField")
        self._scheme.setObjectName("symbologyScheme")
        # 允许用户输入超过当前样本数的值，由应用层给出具体校验提示，
        # 避免 QSpinBox 静默截断输入后让用户误以为分类已应用。
        self._class_count.setRange(3, 999)
        self._class_count.setValue(5)
        self._method.addItem("等间隔", "equal_interval")
        self._method.addItem("分位数", "quantile")
        self._set_simple_color_button(QColor("#2F7DE1"))
        for spin in (self._red_band, self._green_band, self._blue_band, self._stretch_band):
            spin.setMinimum(1)
            spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._class_count.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._stretch_type.addItem("最小值—最大值", StretchType.MIN_MAX.value)
        self._stretch_type.addItem("百分比截断", StretchType.PERCENT_CLIP.value)
        for percent_spin, value in (
            (self._lower_percent, 2.0),
            (self._upper_percent, 98.0),
        ):
            percent_spin.setRange(0.0, 100.0)
            percent_spin.setDecimals(1)
            percent_spin.setValue(value)
            percent_spin.setSuffix("%")
            percent_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._classes.setHorizontalHeaderLabels(["显示", "标签", "颜色"])
        self._classes.verticalHeader().hide()
        self._classes.setAlternatingRowColors(True)
        self._classes.setShowGrid(False)
        self._classes.setIconSize(QSize(42, 14))
        self._classes.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._classes.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._classes.horizontalHeader().setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self._classes.horizontalHeader().setSectionResizeMode(
            1,
            QHeaderView.ResizeMode.Stretch,
        )
        self._classes.horizontalHeader().setSectionResizeMode(
            2,
            QHeaderView.ResizeMode.ResizeToContents,
        )
        self._form.addRow("符号类型", self._renderer)
        self._form.addRow("字段", self._field)
        self._form.addRow("配色方案", self._scheme)
        self._form.addRow("单一颜色", self._simple_color_button)
        self._form.addRow("分级方法", self._method)
        self._form.addRow("级数", self._class_count)
        self._form.addRow("红色波段", self._red_band)
        self._form.addRow("绿色波段", self._green_band)
        self._form.addRow("蓝色波段", self._blue_band)
        self._form.addRow("拉伸波段", self._stretch_band)
        self._form.addRow("拉伸方式", self._stretch_type)
        self._form.addRow("下限", self._lower_percent)
        self._form.addRow("上限", self._upper_percent)
        self._form.addRow("", self._invert)
        header_card = QFrame()
        header_card.setObjectName("symbologyHeaderCard")
        header_layout = QVBoxLayout(header_card)
        header_layout.setContentsMargins(16, 14, 16, 14)
        header_layout.setSpacing(3)
        header_layout.addWidget(self._title)
        header_layout.addWidget(self._metadata)
        header_layout.addSpacing(8)
        header_layout.addWidget(self._preview)

        settings_title = QLabel("主要符号系统")
        settings_title.setObjectName("symbologySectionTitle")
        settings_layout = QVBoxLayout(self._settings_card)
        settings_layout.setContentsMargins(14, 12, 14, 14)
        settings_layout.setSpacing(10)
        settings_layout.addWidget(settings_title)
        settings_layout.addLayout(self._form)

        classes_title = QLabel("符号类别")
        classes_title.setObjectName("symbologySectionTitle")
        classes_header = QHBoxLayout()
        classes_header.addWidget(classes_title)
        classes_header.addStretch()
        classes_header.addWidget(self._class_count_label)
        classes_hint = QLabel("双击颜色可单独编辑，修改会立即显示在地图中")
        classes_hint.setObjectName("symbologyHint")
        classes_layout = QVBoxLayout(self._classes_card)
        classes_layout.setContentsMargins(14, 12, 14, 14)
        classes_layout.setSpacing(8)
        classes_layout.addLayout(classes_header)
        classes_layout.addWidget(classes_hint)
        classes_layout.addWidget(self._classes)

        scroll_content = QWidget()
        scroll_content.setObjectName("symbologyScrollContent")
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(10, 10, 10, 10)
        scroll_layout.setSpacing(10)
        scroll_layout.addWidget(header_card)
        scroll_layout.addWidget(self._settings_card)
        scroll_layout.addWidget(self._classes_card, 1)
        scroll_layout.addStretch()
        scroll = QScrollArea()
        scroll.setObjectName("symbologyScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(scroll_content)

        layout: QVBoxLayout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(scroll, 1)
        # 底部：自动应用开关 + 手动应用按钮。
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(14, 6, 14, 6)
        bottom_row.addWidget(self._auto_apply_checkbox)
        bottom_row.addStretch()
        bottom_row.addWidget(self._apply_button)
        layout.addLayout(bottom_row)
        self.setMinimumWidth(350)

    def _connect_signals(self) -> None:
        """连接自动应用和类别编辑信号。"""
        self._renderer.currentIndexChanged.connect(self._on_vector_or_raster_change)
        self._field.currentIndexChanged.connect(self._on_vector_or_raster_change)
        self._scheme.currentIndexChanged.connect(self._on_vector_or_raster_change)
        self._method.currentIndexChanged.connect(self._on_vector_or_raster_change)
        self._class_count.valueChanged.connect(self._on_vector_or_raster_change)
        self._simple_color_button.clicked.connect(self._on_simple_color_clicked)
        for spin in (self._red_band, self._green_band, self._blue_band, self._stretch_band):
            spin.valueChanged.connect(self._emit_raster)
        self._stretch_type.currentIndexChanged.connect(self._emit_raster)
        self._invert.toggled.connect(self._emit_raster)
        self._lower_percent.valueChanged.connect(self._schedule_raster)
        self._upper_percent.valueChanged.connect(self._schedule_raster)
        self._classes.itemChanged.connect(self._on_class_item_changed)
        self._classes.cellDoubleClicked.connect(self._edit_class_color)
        self._auto_apply_checkbox.toggled.connect(self._on_auto_apply_toggled)
        self._apply_button.clicked.connect(self._on_apply_clicked)

    def _on_auto_apply_toggled(self, checked: bool) -> None:
        """自动应用关闭时显示手动应用按钮并隐藏待定操作。"""
        self._apply_button.setVisible(not checked)
        if checked and self._pending_action is not None:
            self._pending_action()
            self._pending_action = None

    def _on_apply_clicked(self) -> None:
        """手动应用当前待定的符号变更。"""
        if self._pending_action is not None:
            self._pending_action()
            self._pending_action = None
        self._apply_button.setVisible(False)

    def _emit_or_defer(self, action: Callable[[], None]) -> None:
        """自动应用开启时直接执行，否则暂存待手动应用。"""
        if self._auto_apply_checkbox.isChecked():
            action()
        else:
            self._pending_action = action
            self._apply_button.setVisible(True)

    def _load_vector(self, layer: VectorLayer) -> None:
        """加载矢量字段、类型、方案和类别表。"""
        symbology: VectorSymbology = layer.symbology  # type: ignore[assignment]
        with QSignalBlocker(self._renderer):
            self._renderer.clear()
            self._renderer.addItem("单一符号", VectorRendererType.SIMPLE.value)
            self._renderer.addItem("唯一值", VectorRendererType.UNIQUE.value)
            self._renderer.addItem("分级颜色", VectorRendererType.GRADUATED.value)
            self._set_combo_data(self._renderer, symbology.renderer_type.value)
        fields = sorted({name for feature in layer.features for name in feature.attributes})
        with QSignalBlocker(self._field):
            self._field.clear()
            self._field.addItems(fields)
            if symbology.field_name:
                self._field.setCurrentText(symbology.field_name)
        with QSignalBlocker(self._scheme):
            self._scheme.clear()
            if symbology.renderer_type is VectorRendererType.UNIQUE:
                for name in CATEGORICAL_SCHEMES:
                    self._add_scheme_item(name)
            else:
                for name in COLOR_RAMPS:
                    self._add_scheme_item(name)
            self._set_combo_data(self._scheme, symbology.color_scheme)
        self._set_simple_color_button(QColor(self._symbol_color(symbology.base_symbol)))
        self._set_combo_data(self._method, symbology.classification_method)
        stored_class_count: int = (
            len(symbology.graduated_classes)
            if symbology.renderer_type is VectorRendererType.GRADUATED
            else 5
        )
        self._class_count.setValue(
            min(
                max(stored_class_count, self._class_count.minimum()),
                self._class_count.maximum(),
            )
        )
        self._fill_vector_classes(symbology)

    def _load_raster(self, layer: RasterLayer) -> None:
        """加载栅格模式、波段和拉伸参数。"""
        symbology: RasterSymbology = layer.symbology  # type: ignore[assignment]
        with QSignalBlocker(self._renderer):
            self._renderer.clear()
            if layer.band_count >= 3:
                self._renderer.addItem("RGB合成", RasterRendererType.RGB.value)
            self._renderer.addItem("单波段拉伸", RasterRendererType.STRETCH.value)
            self._set_combo_data(self._renderer, symbology.renderer_type.value)
        with QSignalBlocker(self._scheme):
            self._scheme.clear()
            for name in COLOR_RAMPS:
                self._add_scheme_item(name)
            self._set_combo_data(self._scheme, symbology.color_scheme)
        for spin, value in zip(
            (self._red_band, self._green_band, self._blue_band),
            symbology.rgb_bands,
            strict=True,
        ):
            spin.setMaximum(layer.band_count)
            spin.setValue(min(value + 1, layer.band_count))
        self._stretch_band.setMaximum(layer.band_count)
        self._stretch_band.setValue(min(symbology.stretch_band + 1, layer.band_count))
        self._set_combo_data(self._stretch_type, symbology.stretch_type.value)
        self._lower_percent.setValue(symbology.lower_percent)
        self._upper_percent.setValue(symbology.upper_percent)
        self._invert.setChecked(symbology.inverted)
        self._classes.setRowCount(0)

    def _on_vector_or_raster_change(self) -> None:
        """按当前图层类型自动生成或应用主符号。"""
        if self._updating or self._snapshot is None:
            return
        snapshot: LayerSnapshot = self._snapshot
        self._update_control_visibility()
        if isinstance(snapshot.layer, RasterLayer):
            self._emit_raster()
            return
        renderer = VectorRendererType(str(self._renderer.currentData()))
        layer = snapshot.layer
        if isinstance(layer, VectorLayer) and renderer is not VectorRendererType.SIMPLE:
            all_fields = sorted(
                {name for feature in layer.features for name in feature.attributes}
            )
            fields = (
                [
                    name
                    for name in all_fields
                    if any(
                        isinstance(feature.attributes.get(name), (int, float))
                        and not isinstance(feature.attributes.get(name), bool)
                        for feature in layer.features
                    )
                ]
                if renderer is VectorRendererType.GRADUATED
                else all_fields
            )
            existing_fields = [
                self._field.itemText(index) for index in range(self._field.count())
            ]
            if fields != existing_fields:
                with QSignalBlocker(self._field):
                    self._field.clear()
                    self._field.addItems(fields)
        scheme_names = (
            tuple(CATEGORICAL_SCHEMES)
            if renderer is VectorRendererType.UNIQUE
            else tuple(COLOR_RAMPS)
        )
        if str(self._scheme.currentData()) not in scheme_names:
            with QSignalBlocker(self._scheme):
                self._scheme.clear()
                for name in scheme_names:
                    self._add_scheme_item(name)
        if renderer is VectorRendererType.SIMPLE:
            self._emit_simple()
            return
        field_name: str = self._field.currentText()
        if not field_name:
            return
        scheme: str = str(self._scheme.currentData())
        if renderer is VectorRendererType.UNIQUE:
            self._emit_or_defer(
                lambda: self.unique_requested.emit(
                    snapshot.layer_id, field_name, scheme
                )
            )
        else:
            self._emit_or_defer(
                lambda: self.graduated_requested.emit(
                    snapshot.layer_id,
                    field_name,
                    scheme,
                    str(self._method.currentData()),
                    self._class_count.value(),
                )
            )

    def _set_simple_color_button(self, color: QColor) -> None:
        """更新单一颜色按钮的图标和文字。

        参数:
            color: 要显示在按钮上的当前颜色。
        """
        self._current_simple_color = color.name()
        self._setup_color_button(self._simple_color_button, color)

    def _setup_color_button(self, button: QPushButton, color: QColor) -> None:
        """统一设置颜色按钮的外观：色块图标 + 边框 + hover 高亮。

        参数:
            button: 目标按钮。
            color: 要显示的色块颜色。
        """
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

    def _on_simple_color_clicked(self) -> None:
        """弹出 HSB 色轮选择单一符号颜色。"""
        color: QColor | None = ColorWheelPicker.get_color(
            QColor(self._current_simple_color), self
        )
        if color is None:
            return
        self._set_simple_color_button(color)
        self._emit_simple()

    def _emit_simple(self) -> None:
        """应用当前单一颜色并保留符号尺寸。"""
        if self._updating or self._snapshot is None:
            return
        layer = self._snapshot.layer
        if not isinstance(layer, VectorLayer):
            return
        color: str = self._current_simple_color
        base: LayerStyle = layer.style
        symbol = (
            replace(base, stroke_color=color)
            if base.fill_color == "transparent"
            else replace(base, fill_color=color)
        )
        self._emit_or_defer(
            lambda: self.symbology_changed.emit(
                layer.layer_id,
                VectorSymbology(VectorRendererType.SIMPLE, symbol),
            )
        )

    def _emit_raster(self) -> None:
        """应用当前 RGB 或拉伸栅格配置。"""
        if self._updating or self._snapshot is None:
            return
        layer = self._snapshot.layer
        if not isinstance(layer, RasterLayer):
            return
        lower: float = self._lower_percent.value()
        upper: float = self._upper_percent.value()
        if lower >= upper:
            return
        config = RasterSymbology(
            renderer_type=RasterRendererType(str(self._renderer.currentData())),
            rgb_bands=(
                self._red_band.value() - 1,
                self._green_band.value() - 1,
                self._blue_band.value() - 1,
            ),
            stretch_band=self._stretch_band.value() - 1,
            stretch_type=StretchType(str(self._stretch_type.currentData())),
            lower_percent=lower,
            upper_percent=upper,
            color_scheme=str(self._scheme.currentData()),
            inverted=self._invert.isChecked(),
        )
        self._emit_or_defer(
            lambda: self.symbology_changed.emit(layer.layer_id, config)
        )

    def _schedule_raster(self) -> None:
        """在连续数值输入停止三百毫秒后重算栅格。"""
        if not self._updating:
            self._raster_timer.start()

    def _fill_vector_classes(self, symbology: VectorSymbology) -> None:
        """把唯一值或分级类别写入可编辑表格。"""
        classes: tuple[UniqueValueClass | GraduatedClass, ...] = (
            tuple(symbology.unique_classes)
            if symbology.unique_classes
            else tuple(symbology.graduated_classes)
        )
        include_other: bool = (
            symbology.renderer_type is VectorRendererType.UNIQUE
            and symbology.other_symbol is not None
        )
        self._classes.setRowCount(len(classes) + int(include_other))
        self._class_count_label.setText(
            f"{len(classes) + int(include_other)} 类"
        )
        display_rows: list[tuple[str, LayerStyle, bool]] = [
            (category.label, category.symbol, category.visible) for category in classes
        ]
        if include_other and symbology.other_symbol is not None:
            display_rows.append(
                ("其他值", symbology.other_symbol, symbology.other_visible)
            )
        for row, (label, symbol, visible) in enumerate(display_rows):
            visible_item = QTableWidgetItem()
            visible_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            visible_item.setCheckState(
                Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked
            )
            label_item = QTableWidgetItem(label)
            label_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsEditable
            )
            color = (
                symbol.stroke_color if symbol.fill_color == "transparent" else symbol.fill_color
            )
            color_item = QTableWidgetItem(self._color_display_name(color))
            color_item.setIcon(self._solid_color_icon(color))
            color_item.setToolTip(color.upper())
            color_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            )
            self._classes.setItem(row, 0, visible_item)
            self._classes.setItem(row, 1, label_item)
            self._classes.setItem(row, 2, color_item)
            self._classes.setRowHeight(row, 34)

    def _on_class_item_changed(self, item: QTableWidgetItem) -> None:
        """将类别标签或显隐编辑写回矢量符号配置。"""
        if self._updating or self._snapshot is None:
            return
        layer = self._snapshot.layer
        if not isinstance(layer, VectorLayer):
            return
        symbology: VectorSymbology = layer.symbology  # type: ignore[assignment]
        row: int = item.row()
        visible_item = self._classes.item(row, 0)
        label_item = self._classes.item(row, 1)
        if visible_item is None or label_item is None:
            return
        visible: bool = visible_item.checkState() == Qt.CheckState.Checked
        label: str = label_item.text()
        if symbology.renderer_type is VectorRendererType.UNIQUE:
            classes = list(symbology.unique_classes)
            if row == len(classes):
                self._emit_or_defer(
                    lambda: self.symbology_changed.emit(
                        layer.layer_id,
                        replace(symbology, other_visible=visible),
                    )
                )
                return
            classes[row] = replace(classes[row], visible=visible, label=label)
            updated = replace(symbology, unique_classes=tuple(classes))
        else:
            classes2 = list(symbology.graduated_classes)
            classes2[row] = replace(classes2[row], visible=visible, label=label)
            updated = replace(symbology, graduated_classes=tuple(classes2))
        self._emit_or_defer(
            lambda: self.symbology_changed.emit(layer.layer_id, updated)
        )

    def _edit_class_color(self, row: int, column: int) -> None:
        """双击颜色列后修改单个类别颜色。"""
        if column != 2 or self._snapshot is None:
            return
        layer = self._snapshot.layer
        if not isinstance(layer, VectorLayer):
            return
        symbology: VectorSymbology = layer.symbology  # type: ignore[assignment]
        editing_other: bool = False
        if symbology.renderer_type is VectorRendererType.UNIQUE:
            unique_classes = list(symbology.unique_classes)
            if row == len(unique_classes):
                if symbology.other_symbol is None:
                    return
                current = symbology.other_symbol
                editing_other = True
            else:
                current = unique_classes[row].symbol
        else:
            graduated_classes = list(symbology.graduated_classes)
            current = graduated_classes[row].symbol
        current_color = (
            current.stroke_color if current.fill_color == "transparent" else current.fill_color
        )
        color: QColor | None = ColorWheelPicker.get_color(QColor(current_color), self)
        if color is None:
            return
        updated_symbol = (
            replace(current, stroke_color=color.name())
            if current.fill_color == "transparent"
            else replace(current, fill_color=color.name())
        )
        if symbology.renderer_type is VectorRendererType.UNIQUE:
            if editing_other:
                updated = replace(symbology, other_symbol=updated_symbol)
            else:
                unique_classes[row] = replace(unique_classes[row], symbol=updated_symbol)
                updated = replace(symbology, unique_classes=tuple(unique_classes))
        else:
            graduated_classes[row] = replace(
                graduated_classes[row],
                symbol=updated_symbol,
            )
            updated = replace(symbology, graduated_classes=tuple(graduated_classes))
        self._emit_or_defer(
            lambda: self.symbology_changed.emit(layer.layer_id, updated)
        )

    def _update_control_visibility(self) -> None:
        """根据当前图层和主符号类型显示相关控件。"""
        snapshot = self._snapshot
        is_vector: bool = snapshot is not None and isinstance(snapshot.layer, VectorLayer)
        renderer_data: str = str(self._renderer.currentData())
        is_unique = is_vector and renderer_data == VectorRendererType.UNIQUE.value
        is_graduated = is_vector and renderer_data == VectorRendererType.GRADUATED.value
        is_raster = snapshot is not None and isinstance(snapshot.layer, RasterLayer)
        is_rgb = is_raster and renderer_data == RasterRendererType.RGB.value
        is_stretch = is_raster and renderer_data == RasterRendererType.STRETCH.value
        self._settings_card.setVisible(snapshot is not None)
        self._form.setRowVisible(self._field, is_unique or is_graduated)
        self._form.setRowVisible(self._scheme, is_unique or is_graduated or is_stretch)
        self._form.setRowVisible(
            self._simple_color_button,
            is_vector and not is_unique and not is_graduated,
        )
        self._form.setRowVisible(self._method, is_graduated)
        self._form.setRowVisible(self._class_count, is_graduated)
        self._classes_card.setVisible(is_unique or is_graduated)
        for control in (self._red_band, self._green_band, self._blue_band):
            self._form.setRowVisible(control, is_rgb)
        self._form.setRowVisible(self._stretch_band, is_stretch)
        self._form.setRowVisible(self._stretch_type, is_raster)
        percent_visible: bool = (
            is_raster and str(self._stretch_type.currentData()) == StretchType.PERCENT_CLIP.value
        )
        self._form.setRowVisible(self._lower_percent, percent_visible)
        self._form.setRowVisible(self._upper_percent, percent_visible)
        self._form.setRowVisible(self._invert, is_stretch)

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: str) -> None:
        """按用户数据选择下拉项。"""
        index: int = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    @staticmethod
    def _scheme_label(name: str) -> str:
        """把内部配色编号转换为中文显示名称。"""
        return {
            "standard": "标准分类",
            "soft": "柔和分类",
            "contrast": "高对比分类",
            "gray": "灰度",
            "blue": "蓝色渐变",
            "viridis": "Viridis",
            "terrain": "地形",
            "blue_white_red": "蓝—白—红",
        }[name]

    def _add_scheme_item(self, name: str) -> None:
        """添加带离散色块或连续渐变预览的配色方案。"""
        colors: tuple[str, ...] = CATEGORICAL_SCHEMES.get(name) or COLOR_RAMPS[name]
        icon: QIcon = (
            self._categorical_scheme_icon(colors)
            if name in CATEGORICAL_SCHEMES
            else self._gradient_icon(colors)
        )
        self._scheme.addItem(icon, self._scheme_label(name), name)

    def _apply_light_palette(self) -> None:
        """阻止符号系统及弹出列表继承系统深色调色板。"""
        self._set_light_palette(self)
        self._set_light_palette(self._classes)
        for combo in (
            self._renderer,
            self._field,
            self._scheme,
            self._method,
            self._stretch_type,
        ):
            self._set_light_palette(combo)
            self._set_light_palette(combo.view())
        self.setAutoFillBackground(True)

    @staticmethod
    def _set_light_palette(widget: QWidget) -> None:
        """给指定控件设置完整的浅色窗口、输入和选择颜色。"""
        palette = QPalette(widget.palette())
        palette.setColor(QPalette.ColorRole.Window, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#263548"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f8fafc"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#263548"))
        palette.setColor(QPalette.ColorRole.Button, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#263548"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#dcecf9"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#0f5f9f"))
        widget.setPalette(palette)

    @staticmethod
    def _solid_color_icon(color: str) -> QIcon:
        """创建带边框的单色预览图标。"""
        pixmap = QPixmap(44, 16)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setPen(QPen(QColor("#64748b"), 1))
        painter.setBrush(QBrush(QColor(color)))
        painter.drawRoundedRect(1, 1, 41, 13, 2, 2)
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _categorical_scheme_icon(colors: tuple[str, ...]) -> QIcon:
        """创建由多个离散色块组成的分类配色预览。"""
        pixmap = QPixmap(72, 16)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        visible_colors = colors[:8]
        width: float = 70.0 / len(visible_colors)
        for index, color in enumerate(visible_colors):
            painter.fillRect(
                int(1 + index * width),
                1,
                max(int(width + 0.5), 1),
                14,
                QColor(color),
            )
        painter.setPen(QPen(QColor("#64748b"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(1, 1, 69, 13)
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _gradient_icon(colors: tuple[str, ...]) -> QIcon:
        """创建连续色带渐变预览。"""
        pixmap = QPixmap(72, 16)
        pixmap.fill(Qt.GlobalColor.transparent)
        gradient = QLinearGradient(1, 0, 70, 0)
        for index, color in enumerate(colors):
            gradient.setColorAt(index / max(len(colors) - 1, 1), QColor(color))
        painter = QPainter(pixmap)
        painter.setPen(QPen(QColor("#64748b"), 1))
        painter.setBrush(QBrush(gradient))
        painter.drawRect(1, 1, 69, 13)
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _color_display_name(color: str) -> str:
        """把任意 RGB 颜色映射为最接近的中文基础色名。"""
        target = QColor(color)
        anchors: tuple[tuple[str, str], ...] = (
            ("蓝色", "#2563EB"),
            ("橙色", "#F28E2B"),
            ("绿色", "#39A96B"),
            ("红色", "#D9534F"),
            ("紫色", "#8B5CF6"),
            ("青色", "#2AA7A1"),
            ("黄色", "#E0B51B"),
            ("粉色", "#E88AA2"),
            ("棕色", "#8B5E3C"),
            ("灰色", "#7F8C8D"),
            ("白色", "#FFFFFF"),
            ("黑色", "#000000"),
        )
        return min(
            anchors,
            key=lambda item: (
                (target.red() - QColor(item[1]).red()) ** 2
                + (target.green() - QColor(item[1]).green()) ** 2
                + (target.blue() - QColor(item[1]).blue()) ** 2
            ),
        )[0]

    def _update_layer_summary(self, layer: VectorLayer | RasterLayer) -> None:
        """在面板标题下显示图层类型和数据规模。"""
        if isinstance(layer, VectorLayer):
            family = layer.geometry_family
            if family is None:
                geometry_name = "未知几何"
            else:
                geometry_name = {
                    GeometryFamily.POINT: "点",
                    GeometryFamily.LINE: "线",
                    GeometryFamily.POLYGON: "面",
                    GeometryFamily.MIXED: "混合几何",
                }[family]
            self._metadata.setText(
                f"矢量 · {geometry_name} · {len(layer.features)} 个要素"
            )
            return
        height, width = layer.raster_shape
        self._metadata.setText(
            f"栅格 · {layer.band_count} 个波段 · {width} × {height}"
        )

    def _update_preview(self, layer: VectorLayer | RasterLayer) -> None:
        """生成当前主符号的宽幅预览，帮助用户理解实际效果。"""
        pixmap = QPixmap(300, 64)
        pixmap.fill(QColor("#f8fafc"))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#d5dee8"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(0, 0, 299, 63, 5, 5)
        painter.setPen(QColor("#516274"))
        if isinstance(layer, VectorLayer):
            self._paint_vector_preview(painter, layer)
        else:
            self._paint_raster_preview(painter, layer)
        painter.end()
        self._preview.setPixmap(pixmap)

    def _paint_vector_preview(self, painter: QPainter, layer: VectorLayer) -> None:
        """绘制单一、唯一值或分级矢量符号预览。"""
        symbology = layer.symbology
        if symbology is None:
            return
        if symbology.renderer_type is VectorRendererType.UNIQUE:
            colors = tuple(
                self._symbol_color(category.symbol)
                for category in symbology.unique_classes[:10]
            )
            painter.drawText(12, 20, f"唯一值 · {len(symbology.unique_classes)} 类")
            self._paint_discrete_bar(painter, colors, 12, 31, 276, 20)
            return
        if symbology.renderer_type is VectorRendererType.GRADUATED:
            colors = tuple(
                self._symbol_color(category.symbol)
                for category in symbology.graduated_classes
            )
            painter.drawText(
                12,
                20,
                f"分级颜色 · {len(symbology.graduated_classes)} 级",
            )
            self._paint_gradient_bar(painter, colors, 12, 31, 276, 20)
            return
        color = QColor(self._symbol_color(symbology.base_symbol))
        painter.drawText(76, 37, f"单一符号 · {self._color_display_name(color.name())}")
        painter.setPen(QPen(QColor(symbology.base_symbol.stroke_color), 2))
        painter.setBrush(QBrush(color))
        if layer.geometry_family is GeometryFamily.POINT:
            painter.drawEllipse(24, 17, 32, 32)
        elif layer.geometry_family is GeometryFamily.LINE:
            painter.drawLine(18, 42, 62, 22)
        else:
            painter.drawRoundedRect(20, 18, 40, 30, 3, 3)

    def _paint_raster_preview(self, painter: QPainter, layer: RasterLayer) -> None:
        """绘制 RGB 波段组合或单波段拉伸预览。"""
        symbology = layer.symbology
        if symbology is None:
            return
        if symbology.renderer_type is RasterRendererType.RGB:
            painter.drawText(12, 20, "RGB 波段组合")
            rgb_colors = ("#ef4444", "#22c55e", "#3b82f6")
            labels = ("R", "G", "B")
            for index, (color, label, band) in enumerate(
                zip(rgb_colors, labels, symbology.rgb_bands, strict=True)
            ):
                left: int = 12 + index * 94
                painter.fillRect(left, 31, 88, 20, QColor(color))
                painter.setPen(QColor("#ffffff"))
                painter.drawText(left + 6, 46, f"{label}  波段 {band + 1}")
            return
        ramp_colors = COLOR_RAMPS[symbology.color_scheme]
        if symbology.inverted:
            ramp_colors = tuple(reversed(ramp_colors))
        painter.drawText(
            12,
            20,
            f"单波段拉伸 · 波段 {symbology.stretch_band + 1}",
        )
        self._paint_gradient_bar(painter, ramp_colors, 12, 31, 276, 20)

    @staticmethod
    def _paint_discrete_bar(
        painter: QPainter,
        colors: tuple[str, ...],
        left: int,
        top: int,
        width: int,
        height: int,
    ) -> None:
        """绘制离散分类色条。"""
        resolved = colors or ("#d1d5db",)
        item_width: float = width / len(resolved)
        for index, color in enumerate(resolved):
            painter.fillRect(
                int(left + index * item_width),
                top,
                max(int(item_width + 0.5), 1),
                height,
                QColor(color),
            )

    @staticmethod
    def _paint_gradient_bar(
        painter: QPainter,
        colors: tuple[str, ...],
        left: int,
        top: int,
        width: int,
        height: int,
    ) -> None:
        """绘制连续渐变色条。"""
        resolved = colors or ("#d1d5db", "#ffffff")
        gradient = QLinearGradient(left, 0, left + width, 0)
        for index, color in enumerate(resolved):
            gradient.setColorAt(index / max(len(resolved) - 1, 1), QColor(color))
        painter.fillRect(left, top, width, height, QBrush(gradient))

    @staticmethod
    def _symbol_color(symbol: LayerStyle) -> str:
        """返回符号用于预览的主要颜色。"""
        return (
            symbol.stroke_color
            if symbol.fill_color == "transparent"
            else symbol.fill_color
        )
