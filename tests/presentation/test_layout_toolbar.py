"""布局工具栏测试 —— 快捷键一览入口。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from app.presentation.widgets.layout_shortcut_dialog import LayoutShortcutDialog
from app.presentation.widgets.layout_toolbar import LayoutToolbar


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_toolbar_has_shortcut_button_and_emits() -> None:
    """工具栏应有独立的"快捷键"栏按钮，点击发出 shortcut_help 信号。"""
    _app()
    tb = LayoutToolbar()
    fired: list[int] = []
    tb.shortcut_help.connect(lambda: fired.append(1))
    btn: QPushButton | None = None
    for b in tb.findChildren(QPushButton):
        if "快捷键" in b.text():
            btn = b
            break
    assert btn is not None, "工具栏缺少快捷键按钮"
    btn.click()
    assert fired, "点击快捷键按钮应发出 shortcut_help 信号"


def test_shortcut_dialog_lists_alt_shift_first() -> None:
    """快捷键一览应把 Alt/Shift 功能列在最前的高亮栏，并列出全部条目。"""
    _app()
    dialog = LayoutShortcutDialog()
    texts = [lbl.text() for lbl in dialog.findChildren(QLabel)]
    assert "Alt / Shift 快捷键" in texts, "应存在 Alt/Shift 分组标题"
    assert "Alt / Ctrl + 点击" in texts
    assert "Shift + 拖拽" in texts
    # 其余快捷键与鼠标操作
    assert "删除选中的元素" in texts
    assert "在叠压元素间循环选中" in texts
    assert any("平移地图框内容" in t for t in texts), "缺少 Shift+拖拽说明"
    dialog.close()


def test_text_button_is_plain_add_button() -> None:
    """文本按钮应为普通添加按钮（不可切换、无高亮）。"""
    _app()
    tb = LayoutToolbar()
    assert tb._btn_text.isCheckable() is False
    assert tb._btn_text.objectName() == "layoutEditBtn"


def test_text_button_click_emits_add_text() -> None:
    """点击文本按钮应发出 add_text 信号。"""
    _app()
    tb = LayoutToolbar()
    fired: list[int] = []
    tb.add_text.connect(lambda: fired.append(1))
    tb._btn_text.click()
    assert fired


def test_sync_add_buttons_ignores_text() -> None:
    """sync_add_buttons 不再对文本按钮高亮（TextElement 被忽略）。"""
    _app()
    tb = LayoutToolbar()
    tb.sync_add_buttons({"TextElement", "MapFrameElement"})
    assert tb._btn_text.isChecked() is False
    assert tb._btn_frame.isChecked() is True
