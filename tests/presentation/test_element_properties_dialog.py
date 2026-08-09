"""元素属性对话框测试。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox, QDoubleSpinBox, QLineEdit

from app.domain.layout import (
    MapFrameElement,
    NorthArrowElement,
    TextElement,
)
from app.presentation.widgets.element_properties_dialog import (
    ElementPropertiesDialog,
)


def _collect(dialog: ElementPropertiesDialog) -> dict[str, object]:
    """触发对话框的值收集逻辑并返回结果。"""
    dialog._on_accept()
    return dialog.changes()


def test_properties_dialog_text_element_changes() -> None:
    """文本元素属性对话框应返回修改后的值。"""
    QApplication.instance() or QApplication([])
    elem = TextElement(text="原始", font_size_mm=3.0, color="#000000")
    dialog = ElementPropertiesDialog()
    dialog.set_element(elem)

    text_edit = dialog._changes["text"]
    assert isinstance(text_edit, QLineEdit)
    text_edit.setText("修改后")

    font_spin = dialog._changes["font_size_mm"]
    assert isinstance(font_spin, QDoubleSpinBox)
    font_spin.setValue(5.0)

    changes = _collect(dialog)
    assert changes["text"] == "修改后"
    assert changes["font_size_mm"] == 5.0


def test_properties_dialog_map_frame_changes() -> None:
    """地图框属性对话框应返回边框宽度修改。"""
    QApplication.instance() or QApplication([])
    elem = MapFrameElement(
        border_color="#333333", border_width_mm=0.5, background_color="#ffffff",
    )
    dialog = ElementPropertiesDialog()
    dialog.set_element(elem)

    spin = dialog._changes["border_width_mm"]
    assert isinstance(spin, QDoubleSpinBox)
    spin.setValue(2.0)

    changes = _collect(dialog)
    assert changes["border_width_mm"] == 2.0


def test_properties_dialog_north_arrow_style() -> None:
    """指北针属性对话框应返回样式修改。"""
    QApplication.instance() or QApplication([])
    elem = NorthArrowElement(style="compass")
    dialog = ElementPropertiesDialog()
    dialog.set_element(elem)

    combo = dialog._changes["style"]
    assert isinstance(combo, QComboBox)
    idx = combo.findData("arrow")
    combo.setCurrentIndex(idx)

    changes = _collect(dialog)
    assert changes["style"] == "arrow"


def test_properties_dialog_common_fields() -> None:
    """所有元素通用的位置和尺寸字段应可修改。"""
    QApplication.instance() or QApplication([])
    elem = TextElement(x_mm=10, y_mm=20, width_mm=40, height_mm=8, rotation=0)
    dialog = ElementPropertiesDialog()
    dialog.set_element(elem)

    x_spin = dialog._changes["x_mm"]
    assert isinstance(x_spin, QDoubleSpinBox)
    x_spin.setValue(50.0)

    rot_spin = dialog._changes["rotation"]
    assert isinstance(rot_spin, QDoubleSpinBox)
    rot_spin.setValue(90.0)

    changes = _collect(dialog)
    assert changes["x_mm"] == 50.0
    assert changes["rotation"] == 90.0
