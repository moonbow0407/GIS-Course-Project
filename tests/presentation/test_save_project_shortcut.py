"""Ctrl+S 保存工程快捷键的注册与触发测试。"""

import os
import warnings

# 必须在导入 Qt 前启用无界面平台，测试才能在没有显示器的环境运行。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.presentation.main_window import MainWindow


def _make_window() -> MainWindow:
    QApplication.instance() or QApplication([])
    return MainWindow()


def test_ctrl_s_shortcut_is_registered_and_triggers_save() -> None:
    """主窗口应注册 Ctrl+S 快捷键，按下时触发工程保存入口。"""
    window: MainWindow = _make_window()
    save_sequence: QKeySequence = QKeySequence(QKeySequence.StandardKey.Save)
    save_shortcuts: list[QShortcut] = [
        shortcut
        for shortcut in window.findChildren(QShortcut)
        if shortcut.key() == save_sequence
    ]
    assert save_shortcuts, "主窗口应注册 Ctrl+S 保存工程快捷键"
    assert all(shortcut.isEnabled() for shortcut in save_shortcuts)

    calls: list[int] = []

    def fake_save() -> bool:
        calls.append(1)
        return True

    window._save_project = fake_save  # type: ignore[method-assign]
    window.show()
    with warnings.catch_warnings():
        # offscreen 平台下激活窗口的唯一手段，Qt 标记弃用但功能仍可用。
        warnings.simplefilter("ignore", DeprecationWarning)
        QApplication.setActiveWindow(window)
    window.setFocus()
    QTest.keyClick(window, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert calls == [1]
