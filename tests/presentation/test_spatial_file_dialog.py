"""空间数据文件选择框测试。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFileDialog

from app.presentation.widgets.spatial_file_dialog import build_spatial_file_dialog


def test_spatial_file_dialog_uses_native_system_picker() -> None:
    """打开数据必须使用系统原生对话框，不得改成 Qt 自制文件框。"""
    QApplication.instance() or QApplication([])
    dialog = build_spatial_file_dialog()

    assert not dialog.testOption(QFileDialog.Option.DontUseNativeDialog)
    assert dialog.fileMode() == QFileDialog.FileMode.ExistingFiles
    dialog.close()
