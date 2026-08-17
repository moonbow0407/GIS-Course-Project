"""元素属性编辑对话框 —— 根据布局元素类型动态显示可编辑字段。"""

from typing import cast

from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.domain.layout import (
    LayoutElement,
    LegendElement,
    MapFrameElement,
    NorthArrowElement,
    ScaleBarElement,
    TextElement,
)


class _ColorButton(QPushButton):
    """点击弹出颜色选择器的按钮。"""

    def __init__(self, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = color
        self._update_style()
        self.clicked.connect(self._pick_color)

    def _update_style(self) -> None:
        self.setStyleSheet(
            f"background: {self._color}; color: transparent; "
            f"border: 1px solid #9ca3af; border-radius: 3px; "
            f"min-width: 60px; max-width: 60px;"
        )
        self.setText(self._color)
        self.setStyleSheet(
            f"background: {self._color}; "
            f"border: 1px solid #9ca3af; border-radius: 3px; "
            f"min-width: 60px; max-width: 60px; "
            f"color: {'#ffffff' if self._is_dark() else '#000000'};"
        )

    def _is_dark(self) -> bool:
        from PySide6.QtGui import QColor

        c = QColor(self._color)
        return c.lightness() < 128

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(
            initial=self._color, parent=self.parentWidget()
        )
        if color.isValid():
            self._color = color.name()
            self._update_style()

    def color(self) -> str:
        return self._color


class ElementPropertiesDialog(QDialog):
    """元素属性编辑对话框。

    根据元素类型动态构建编辑表单，用户确认后返回修改后的属性字典。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("元素属性")
        self._element: LayoutElement | None = None
        self._changes: dict[str, object] = {}
        self._form: QFormLayout = QFormLayout()
        self._create_ui()

    def _create_ui(self) -> None:
        """创建对话框骨架。"""
        main_layout: QVBoxLayout = QVBoxLayout(self)
        main_layout.addLayout(self._form)

        from PySide6.QtWidgets import QDialogButtonBox

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)

    def set_element(self, element: LayoutElement) -> None:
        """根据元素类型填充编辑字段。"""
        self._element = element
        self._clear_form()

        self._add_position_fields(element)

        if isinstance(element, MapFrameElement):
            self._add_map_frame_fields(element)
        elif isinstance(element, ScaleBarElement):
            self._add_scale_bar_fields(element)
        elif isinstance(element, LegendElement):
            self._add_legend_fields(element)
        elif isinstance(element, NorthArrowElement):
            self._add_north_arrow_fields(element)
        elif isinstance(element, TextElement):
            self._add_text_fields(element)

    def _clear_form(self) -> None:
        """清空表单中的所有行。"""
        while self._form.rowCount() > 0:
            self._form.removeRow(0)

    def _add_position_fields(self, element: LayoutElement) -> None:
        """添加通用位置/尺寸/旋转字段。"""
        self._add_spin("X (mm)", "x_mm", element.x_mm, -500.0, 2000.0, 1)
        self._add_spin("Y (mm)", "y_mm", element.y_mm, -500.0, 2000.0, 1)
        self._add_spin("宽度 (mm)", "width_mm", element.width_mm, 5.0, 2000.0, 1)
        self._add_spin("高度 (mm)", "height_mm", element.height_mm, 5.0, 2000.0, 1)
        self._add_spin("旋转 (°)", "rotation", element.rotation, -360.0, 360.0, 1)

    def _add_map_frame_fields(self, element: MapFrameElement) -> None:
        """添加地图框特有字段。"""
        self._add_color("边框颜色", "border_color", element.border_color)
        self._add_spin(
            "边框线宽 (mm)", "border_width_mm",
            element.border_width_mm, 0.0, 10.0, 2,
        )
        self._add_color("背景颜色", "background_color", element.background_color)

    def _add_scale_bar_fields(self, element: ScaleBarElement) -> None:
        """添加比例尺特有字段。"""
        style_combo = QComboBox()
        style_combo.addItem("交替条", "alternating")
        style_combo.addItem("双层交替", "double_alternating")
        style_combo.addItem("线状刻度", "line")
        style_idx = style_combo.findData(element.style)
        if style_idx >= 0:
            style_combo.setCurrentIndex(style_idx)
        self._form.addRow("形态:", style_combo)
        self._register_combo("style", style_combo)

        unit_combo = QComboBox()
        unit_combo.addItem("千米", "km")
        unit_combo.addItem("米", "m")
        idx = unit_combo.findData(element.unit)
        if idx >= 0:
            unit_combo.setCurrentIndex(idx)
        self._form.addRow("单位:", unit_combo)
        self._register_combo("unit", unit_combo)

        self._add_spin(
            "分段数", "num_segments",
            float(element.num_segments), 1.0, 20.0, 0,
        )
        self._add_color("颜色", "color", element.color)
        self._add_spin(
            "标签字号 (mm)", "label_font_size_mm",
            element.label_font_size_mm, 0.5, 20.0, 1,
        )

    def _add_legend_fields(self, element: LegendElement) -> None:
        """添加图例特有字段。"""
        title_edit = QLineEdit(element.title)
        self._form.addRow("标题:", title_edit)
        self._register_text("title", title_edit)

        self._add_spin(
            "标题字号 (mm)", "title_font_size_mm",
            element.title_font_size_mm, 0.5, 20.0, 1,
        )
        self._add_spin(
            "条目字号 (mm)", "item_font_size_mm",
            element.item_font_size_mm, 0.5, 20.0, 1,
        )
        self._add_int_spin("列数", "column_count", element.column_count, 1, 10)

    def _add_north_arrow_fields(self, element: NorthArrowElement) -> None:
        """添加指北针特有字段。"""
        style_combo = QComboBox()
        style_combo.addItem("罗盘", "compass")
        style_combo.addItem("箭头", "arrow")
        style_combo.addItem("简单", "simple")
        idx = style_combo.findData(element.style)
        if idx >= 0:
            style_combo.setCurrentIndex(idx)
        self._form.addRow("样式:", style_combo)
        self._register_combo("style", style_combo)

        self._add_color("颜色", "color", element.color)

    def _add_text_fields(self, element: TextElement) -> None:
        """添加文本元素特有字段。"""
        text_edit = QLineEdit(element.text)
        self._form.addRow("文本:", text_edit)
        self._register_text("text", text_edit)

        self._add_spin(
            "字号 (mm)", "font_size_mm",
            element.font_size_mm, 0.5, 50.0, 1,
        )
        self._add_color("颜色", "color", element.color)

        bold_check = QCheckBox()
        bold_check.setChecked(element.bold)
        self._form.addRow("粗体:", bold_check)
        self._register_check("bold", bold_check)

        italic_check = QCheckBox()
        italic_check.setChecked(element.italic)
        self._form.addRow("斜体:", italic_check)
        self._register_check("italic", italic_check)

        align_combo = QComboBox()
        align_combo.addItem("左对齐", "left")
        align_combo.addItem("居中", "center")
        align_combo.addItem("右对齐", "right")
        idx = align_combo.findData(element.alignment)
        if idx >= 0:
            align_combo.setCurrentIndex(idx)
        self._form.addRow("框内对齐:", align_combo)
        self._register_combo("alignment", align_combo)

    # --- 字段注册辅助 ---

    def _add_spin(
        self,
        label: str,
        key: str,
        value: float,
        minimum: float,
        maximum: float,
        decimals: int,
    ) -> None:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setValue(value)
        self._form.addRow(label + ":", spin)
        self._register_spin(key, spin)

    def _add_int_spin(
        self, label: str, key: str, value: int, minimum: int, maximum: int,
    ) -> None:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        self._form.addRow(label + ":", spin)
        self._register_int_spin(key, spin)

    def _add_color(self, label: str, key: str, value: str) -> None:
        btn = _ColorButton(value)
        self._form.addRow(label + ":", btn)
        self._register_color(key, btn)

    def _register_spin(self, key: str, spin: QDoubleSpinBox) -> None:
        self._changes[key] = spin

    def _register_int_spin(self, key: str, spin: QSpinBox) -> None:
        self._changes[key] = spin

    def _register_combo(self, key: str, combo: QComboBox) -> None:
        self._changes[key] = combo

    def _register_text(self, key: str, edit: QLineEdit) -> None:
        self._changes[key] = edit

    def _register_check(self, key: str, check: QCheckBox) -> None:
        self._changes[key] = check

    def _register_color(self, key: str, btn: _ColorButton) -> None:
        self._changes[key] = btn

    # --- 结果收集 ---

    def _on_accept(self) -> None:
        """收集修改值并关闭。"""
        collected: dict[str, object] = {}
        for key, widget in self._changes.items():
            if isinstance(widget, QDoubleSpinBox):
                collected[key] = widget.value()
            elif isinstance(widget, QSpinBox):
                collected[key] = widget.value()
            elif isinstance(widget, QComboBox):
                collected[key] = widget.currentData()
            elif isinstance(widget, QLineEdit):
                collected[key] = widget.text()
            elif isinstance(widget, QCheckBox):
                collected[key] = widget.isChecked()
            elif isinstance(widget, _ColorButton):
                collected[key] = widget.color()
        # num_segments 需要转为 int
        if "num_segments" in collected:
            collected["num_segments"] = int(
                cast(str | int | float, collected["num_segments"])
            )
        self._changes = collected
        self.accept()

    def changes(self) -> dict[str, object]:
        """返回用户修改的属性字典。"""
        return dict(self._changes)
