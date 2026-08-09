"""图层动态标注与标注分类配置对话框。"""

from dataclasses import replace

from PySide6.QtCore import QSignalBlocker, QSize
from PySide6.QtGui import QColor, QIcon, QPalette, QPixmap
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.application.results import LayerSnapshot
from app.domain.labeling import (
    LabelClass,
    LabelingConfig,
    LabelPlacement,
    attribute_fields,
    default_labeling_for_features,
)
from app.domain.vector_layer import VectorLayer

_PLACEMENTS: tuple[tuple[LabelPlacement, str], ...] = (
    (LabelPlacement.ABOVE_LEFT, "左上"),
    (LabelPlacement.ABOVE, "正上方"),
    (LabelPlacement.ABOVE_RIGHT, "右上"),
    (LabelPlacement.LEFT, "左侧"),
    (LabelPlacement.CENTER, "要素中心"),
    (LabelPlacement.RIGHT, "右侧"),
    (LabelPlacement.BELOW_LEFT, "左下"),
    (LabelPlacement.BELOW, "正下方"),
    (LabelPlacement.BELOW_RIGHT, "右下"),
)


class LabelingDialog(QDialog):
    """编辑指定矢量图层的标注开关和多个标注分类。"""

    def __init__(
        self,
        snapshot: LayerSnapshot,
        parent: QWidget | None = None,
    ) -> None:
        """创建标注配置窗口，并从图层现有状态初始化控件。"""
        super().__init__(parent)
        if not isinstance(snapshot.layer, VectorLayer):
            raise TypeError("标注配置只能打开矢量图层。")
        self.setObjectName("labelingDialog")
        self.setWindowTitle(f"标注分类 · {snapshot.name}")
        self.setMinimumSize(760, 560)
        self._snapshot: LayerSnapshot = snapshot
        self._fields: tuple[str, ...] = attribute_fields(snapshot.layer.features)
        current_config: LabelingConfig | None = snapshot.layer.labeling
        if current_config is None:
            current_config = default_labeling_for_features(snapshot.layer.features)
        self._enabled_checkbox: QCheckBox = QCheckBox("启用此图层的动态标注")
        self._enabled_checkbox.setChecked(current_config.enabled)
        self._classes: list[LabelClass] = list(current_config.classes)
        self._selected_index: int = -1
        self._result_config: LabelingConfig | None = None
        self._class_list: QListWidget = QListWidget()
        self._class_name: QLineEdit = QLineEdit()
        self._field: QComboBox = QComboBox()
        self._filter_field: QComboBox = QComboBox()
        self._filter_value: QLineEdit = QLineEdit()
        self._placement: QComboBox = QComboBox()
        self._font_size: QDoubleSpinBox = QDoubleSpinBox()
        self._offset_x: QDoubleSpinBox = QDoubleSpinBox()
        self._offset_y: QDoubleSpinBox = QDoubleSpinBox()
        self._halo_width: QDoubleSpinBox = QDoubleSpinBox()
        self._halo_enabled_checkbox: QCheckBox = QCheckBox("启用晕染底框")
        self._text_color: str = "#20354A"
        self._halo_color: str = "#FFFFFF"
        self._text_color_button: QPushButton = QPushButton("文字颜色")
        self._halo_color_button: QPushButton = QPushButton("光晕颜色")
        self._add_button: QPushButton = QPushButton("＋ 添加分类")
        self._remove_button: QPushButton = QPushButton("− 删除分类")
        self._create_ui()
        self._apply_light_palette()
        self._connect_signals()
        self._populate_class_list()

    @property
    def result_config(self) -> LabelingConfig | None:
        """返回用户确认后的标注配置。"""
        return self._result_config

    def _create_ui(self) -> None:
        """创建高对比度的标注分类编辑界面。"""
        root_layout: QVBoxLayout = QVBoxLayout(self)
        root_layout.setContentsMargins(18, 16, 18, 16)
        root_layout.setSpacing(12)

        header: QFrame = QFrame()
        header.setObjectName("labelingHeaderCard")
        header_layout: QVBoxLayout = QVBoxLayout(header)
        header_layout.setContentsMargins(14, 12, 14, 12)
        header_layout.setSpacing(4)
        title: QLabel = QLabel(f"{self._snapshot.name} · 标注分类")
        title.setObjectName("labelingTitle")
        hint: QLabel = QLabel(
            "标注文本来自要素属性；可为同一图层创建多个分类，并分别设置字段、位置和文字样式。"
        )
        hint.setObjectName("labelingHint")
        hint.setWordWrap(True)
        header_layout.addWidget(title)
        header_layout.addWidget(hint)
        header_layout.addWidget(self._enabled_checkbox)
        root_layout.addWidget(header)

        body_layout: QHBoxLayout = QHBoxLayout()
        body_layout.setSpacing(12)
        class_card: QFrame = QFrame()
        class_card.setObjectName("labelingClassCard")
        class_card.setMinimumWidth(210)
        class_layout: QVBoxLayout = QVBoxLayout(class_card)
        class_layout.setContentsMargins(12, 12, 12, 12)
        class_layout.setSpacing(8)
        class_title: QLabel = QLabel("标注分类")
        class_title.setObjectName("labelingSectionTitle")
        class_layout.addWidget(class_title)
        class_layout.addWidget(self._class_list, 1)
        class_buttons: QHBoxLayout = QHBoxLayout()
        class_buttons.addWidget(self._add_button)
        class_buttons.addWidget(self._remove_button)
        class_layout.addLayout(class_buttons)
        body_layout.addWidget(class_card)

        settings_card: QFrame = QFrame()
        settings_card.setObjectName("labelingSettingsCard")
        settings_layout: QVBoxLayout = QVBoxLayout(settings_card)
        settings_layout.setContentsMargins(14, 12, 14, 12)
        settings_layout.setSpacing(8)
        settings_title: QLabel = QLabel("当前分类设置")
        settings_title.setObjectName("labelingSectionTitle")
        settings_layout.addWidget(settings_title)

        form: QFormLayout = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(9)
        form.addRow("分类名称", self._class_name)
        form.addRow("标注字段", self._field)
        form.addRow("分类字段", self._filter_field)
        form.addRow("分类值", self._filter_value)
        form.addRow("标注位置", self._placement)
        form.addRow("字号", self._font_size)
        form.addRow("X 偏移", self._offset_x)
        form.addRow("Y 偏移", self._offset_y)
        form.addRow("晕染效果", self._halo_enabled_checkbox)
        form.addRow("文字颜色", self._text_color_button)
        form.addRow("光晕颜色", self._halo_color_button)
        form.addRow("光晕宽度", self._halo_width)
        settings_layout.addLayout(form)
        settings_hint: QLabel = QLabel(
            "晕染默认关闭；开启后绘制不透明底框以提高对比度。字号和偏移量按屏幕像素计算，缩放地图时自动避让重叠标签。"
        )
        settings_hint.setObjectName("labelingHint")
        settings_hint.setWordWrap(True)
        settings_layout.addStretch(1)
        settings_layout.addWidget(settings_hint)
        body_layout.addWidget(settings_card, 1)
        root_layout.addLayout(body_layout, 1)

        button_box: QDialogButtonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.button(QDialogButtonBox.StandardButton.Ok).setText("应用")
        button_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        button_box.accepted.connect(self._accept)
        button_box.rejected.connect(self.reject)
        root_layout.addWidget(button_box)

        for field_name in self._fields:
            self._field.addItem(field_name, field_name)
        self._filter_field.addItem("全部要素", None)
        for field_name in self._fields:
            self._filter_field.addItem(field_name, field_name)
        for placement, label in _PLACEMENTS:
            self._placement.addItem(label, placement)
        for spin in (self._offset_x, self._offset_y):
            spin.setRange(-200.0, 200.0)
            spin.setDecimals(1)
            spin.setSuffix(" px")
            spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._font_size.setRange(6.0, 72.0)
        self._font_size.setDecimals(1)
        self._font_size.setSuffix(" px")
        self._font_size.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._halo_width.setRange(0.0, 12.0)
        self._halo_width.setDecimals(1)
        self._halo_width.setSuffix(" px")
        self._halo_width.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._filter_value.setPlaceholderText("留空表示不按分类值过滤")
        self._class_list.setObjectName("labelingClassList")
        self._field.setObjectName("labelingField")
        self._filter_field.setObjectName("labelingFilterField")
        self._filter_value.setObjectName("labelingFilterValue")
        self._placement.setObjectName("labelingPlacement")
        self._halo_enabled_checkbox.setObjectName("labelingHaloEnabled")

    def _connect_signals(self) -> None:
        """连接分类切换、分类增删和颜色选择事件。"""
        self._class_list.currentRowChanged.connect(self._on_class_changed)
        self._add_button.clicked.connect(self._add_class)
        self._remove_button.clicked.connect(self._remove_class)
        self._filter_field.currentIndexChanged.connect(self._update_filter_state)
        self._text_color_button.clicked.connect(lambda: self._choose_color("text"))
        self._halo_color_button.clicked.connect(lambda: self._choose_color("halo"))

    def _populate_class_list(self) -> None:
        """刷新左侧分类列表并选中第一类。"""
        with QSignalBlocker(self._class_list):
            self._class_list.clear()
            for index, label_class in enumerate(self._classes, start=1):
                item: QListWidgetItem = QListWidgetItem(f"{index}. {label_class.name}")
                self._class_list.addItem(item)
            if self._classes:
                self._class_list.setCurrentRow(0)
            else:
                self._selected_index = -1
        if self._classes:
            self._on_class_changed(0)
        else:
            self._set_editor_enabled(False)

    def _on_class_changed(self, row: int) -> None:
        """保存上一类并加载用户当前选择的分类。"""
        if self._selected_index >= 0:
            self._commit_current()
        self._selected_index = row
        if 0 <= row < len(self._classes):
            self._load_class(self._classes[row])
            self._set_editor_enabled(True)
        else:
            self._set_editor_enabled(False)

    def _load_class(self, label_class: LabelClass) -> None:
        """将一个领域标注类加载到编辑控件。"""
        with QSignalBlocker(self._field), QSignalBlocker(
            self._filter_field
        ), QSignalBlocker(self._placement):
            self._class_name.setText(label_class.name)
            self._set_combo_data(self._field, label_class.field_name)
            self._set_combo_data(self._filter_field, label_class.filter_field)
            self._set_combo_data(self._placement, label_class.placement)
        self._filter_value.setText(label_class.filter_value or "")
        self._font_size.setValue(label_class.font_size)
        self._offset_x.setValue(label_class.offset_x)
        self._offset_y.setValue(label_class.offset_y)
        self._halo_width.setValue(label_class.halo_width)
        self._halo_enabled_checkbox.setChecked(label_class.halo_enabled)
        self._text_color = label_class.text_color
        self._halo_color = label_class.halo_color
        self._set_color_button(self._text_color_button, self._text_color, "文字颜色")
        self._set_color_button(self._halo_color_button, self._halo_color, "光晕颜色")
        self._update_filter_state()

    def _commit_current(self) -> None:
        """把当前编辑控件写回内存中的标注类。"""
        if not 0 <= self._selected_index < len(self._classes):
            return
        field_name: str = str(self._field.currentData() or "")
        if not field_name:
            return
        filter_field_data: object = self._filter_field.currentData()
        filter_field: str | None = (
            str(filter_field_data) if filter_field_data is not None else None
        )
        filter_value: str | None = self._filter_value.text().strip() or None
        if filter_field is None:
            filter_value = None
        class_name: str = self._class_name.text().strip()
        if not class_name:
            class_name = f"标注分类 {self._selected_index + 1}"
        self._classes[self._selected_index] = replace(
            self._classes[self._selected_index],
            name=class_name,
            field_name=field_name,
            placement=self._placement.currentData(),
            font_size=self._font_size.value(),
            text_color=self._text_color,
            halo_color=self._halo_color,
            halo_width=self._halo_width.value(),
            offset_x=self._offset_x.value(),
            offset_y=self._offset_y.value(),
            filter_field=filter_field,
            filter_value=filter_value,
            halo_enabled=self._halo_enabled_checkbox.isChecked(),
        )
        item: QListWidgetItem | None = self._class_list.item(self._selected_index)
        if item is not None:
            item.setText(f"{self._selected_index + 1}. {class_name}")

    def _add_class(self) -> None:
        """添加一个继承当前字段的标注类，供用户配置另一种样式。"""
        self._commit_current()
        if not self._fields:
            return
        next_index: int = len(self._classes) + 1
        self._classes.append(
            LabelClass(
                name=f"标注分类 {next_index}",
                field_name=self._fields[0],
                text_color="#20354A",
                halo_color="#FFFFFF",
            )
        )
        self._populate_class_list()
        new_index: int = len(self._classes) - 1
        with QSignalBlocker(self._class_list):
            self._class_list.setCurrentRow(new_index)
        self._on_class_changed(new_index)

    def _remove_class(self) -> None:
        """删除当前标注类；允许删除到零类以关闭所有标签。"""
        if not 0 <= self._selected_index < len(self._classes):
            return
        self._commit_current()
        self._classes.pop(self._selected_index)
        self._populate_class_list()

    def _update_filter_state(self) -> None:
        """只有选择分类字段时才允许编辑分类值。"""
        self._filter_value.setEnabled(self._filter_field.currentData() is not None)

    def _set_editor_enabled(self, enabled: bool) -> None:
        """同步启用或禁用当前分类编辑器。"""
        for widget in (
            self._class_name,
            self._field,
            self._filter_field,
            self._filter_value,
            self._placement,
            self._font_size,
            self._offset_x,
            self._offset_y,
            self._halo_enabled_checkbox,
            self._text_color_button,
            self._halo_color_button,
            self._halo_width,
        ):
            widget.setEnabled(enabled)
        self._remove_button.setEnabled(enabled)

    def _choose_color(self, kind: str) -> None:
        """弹出颜色选择器并更新文字或光晕颜色。"""
        current: str = self._text_color if kind == "text" else self._halo_color
        color: QColor = QColorDialog.getColor(QColor(current), self, "选择标注颜色")
        if not color.isValid():
            return
        if kind == "text":
            self._text_color = color.name().upper()
            self._set_color_button(self._text_color_button, self._text_color, "文字颜色")
        else:
            self._halo_color = color.name().upper()
            self._set_color_button(self._halo_color_button, self._halo_color, "光晕颜色")

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: object) -> None:
        """按数据值选择组合框项目，找不到时回退到第一项。"""
        index: int = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    @staticmethod
    def _set_color_button(button: QPushButton, color_name: str, label: str) -> None:
        """设置颜色按钮的色块图标和明确文字。"""
        color: QColor = QColor(color_name)
        if not color.isValid():
            color = QColor("#FFFFFF")
        pixmap: QPixmap = QPixmap(54, 18)
        pixmap.fill(color)
        button.setIcon(QIcon(pixmap))
        button.setIconSize(QSize(54, 18))
        button.setText(label)

    def _accept(self) -> None:
        """校验当前编辑并返回不可变标注配置。"""
        self._commit_current()
        self._result_config = LabelingConfig(
            enabled=self._enabled_checkbox.isChecked() and bool(self._classes),
            classes=tuple(self._classes),
        )
        self.accept()

    def _apply_light_palette(self) -> None:
        """强制弹窗及其输入控件使用浅色高对比度调色板。"""
        palette: QPalette = QPalette(self.palette())
        palette.setColor(QPalette.ColorRole.Window, QColor("#FFFFFF"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#263548"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#FFFFFF"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#F8FAFC"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#263548"))
        palette.setColor(QPalette.ColorRole.Button, QColor("#FFFFFF"))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#263548"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#DCECF9"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#0F5F9F"))
        self.setPalette(palette)
        self.setAutoFillBackground(True)
        for widget in self.findChildren(QWidget):
            widget.setPalette(palette)
