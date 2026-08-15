"""空间数据文件选择框测试。"""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QFileInfo
from PySide6.QtWidgets import QApplication, QFileDialog

from app.presentation.widgets.spatial_file_dialog import (
    GenericFileIconProvider,
    build_spatial_file_dialog,
)


def test_spatial_file_dialog_avoids_windows_shell_preview() -> None:
    """打开数据选择框不得使用会触发 TIFF 预览的系统原生对话框。"""
    QApplication.instance() or QApplication([])
    dialog = build_spatial_file_dialog()

    assert dialog.testOption(QFileDialog.Option.DontUseNativeDialog)
    assert dialog.testOption(QFileDialog.Option.DontUseCustomDirectoryIcons)
    assert dialog.fileMode() == QFileDialog.FileMode.ExistingFiles
    assert isinstance(dialog.iconProvider(), GenericFileIconProvider)
    dialog.close()


def test_generic_icon_provider_does_not_query_file_thumbnails(tmp_path: Path) -> None:
    """选中大栅格文件名时只应使用通用文件图标。"""
    QApplication.instance() or QApplication([])
    raster_path = tmp_path / "dem.tif"
    raster_path.write_bytes(b"not-a-decoded-tiff")
    provider = GenericFileIconProvider()

    icon = provider.icon(QFileInfo(str(raster_path)))

    assert icon.isNull() is False
