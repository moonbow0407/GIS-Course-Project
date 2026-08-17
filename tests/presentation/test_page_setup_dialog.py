"""页面设置对话框测试。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.domain.layout import LayoutPage, PageOrientation
from app.presentation.widgets.page_setup_dialog import PageSetupDialog


def test_page_setup_dialog_returns_configured_page() -> None:
    """对话框接受后应返回用户配置的纸张规格。"""
    QApplication.instance() or QApplication([])
    current = LayoutPage.from_preset("A4")
    dialog = PageSetupDialog(current)
    dialog._paper_combo.setCurrentText("A3")
    orient_idx = dialog._orientation_combo.findData(PageOrientation.LANDSCAPE.value)
    dialog._orientation_combo.setCurrentIndex(orient_idx)
    dialog._dpi_spin.setValue(150.0)
    dialog._margin_spin.setValue(15.0)

    dialog._on_accept()
    page = dialog.page()
    assert page is not None
    assert page.name == "A3"
    assert page.orientation == PageOrientation.LANDSCAPE
    assert page.dpi == 150.0
    assert page.margin_mm == 15.0


def test_page_setup_dialog_preserves_current_values() -> None:
    """对话框初始值应与传入的当前页面一致。"""
    QApplication.instance() or QApplication([])
    current = LayoutPage.from_preset("A5", dpi=200.0, margin_mm=5.0)
    dialog = PageSetupDialog(current)

    assert dialog._paper_combo.currentText() == "A5"
    assert dialog._dpi_spin.value() == 200.0
    assert dialog._margin_spin.value() == 5.0
