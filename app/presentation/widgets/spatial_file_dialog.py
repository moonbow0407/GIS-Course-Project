"""打开空间数据时使用的文件选择对话框。

Windows 原生文件框会调用 Shell 的 TIFF 缩略图/属性处理器；仅选中
300MB 级 DEM 就会让对话框进入“未响应”。这里使用 Qt 对话框，并用
通用图标，避免为选中文件解码影像。
"""

from PySide6.QtCore import QFileInfo
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFileDialog, QFileIconProvider, QWidget

SPATIAL_DATA_FILTER = (
    "空间数据 (*.shp *.geojson *.json *.gpkg *.kml *.tif *.tiff *.img *.dem);;"
    "所有文件 (*.*)"
)


class GenericFileIconProvider(QFileIconProvider):
    """只返回文件夹/普通文件图标，不向 Windows 索取类型缩略图。"""

    def __init__(self) -> None:
        """关闭自定义目录图标，避免触发 Shell 扩展。"""
        super().__init__()
        self.setOptions(QFileIconProvider.Option.DontUseCustomDirectoryIcons)

    def icon(self, info_or_type: QFileIconProvider.IconType | QFileInfo) -> QIcon:
        """目录用文件夹图标，其余一律用普通文件图标。"""
        if isinstance(info_or_type, QFileInfo):
            if info_or_type.isDir():
                return super().icon(QFileIconProvider.IconType.Folder)
            return super().icon(QFileIconProvider.IconType.File)
        return super().icon(info_or_type)


def build_spatial_file_dialog(parent: QWidget | None = None) -> QFileDialog:
    """构造支持多选、且不会触发系统预览的空间数据选择框。"""
    dialog = QFileDialog(parent, "打开空间数据")
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    dialog.setOption(QFileDialog.Option.DontUseCustomDirectoryIcons, True)
    dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
    dialog.setViewMode(QFileDialog.ViewMode.Detail)
    dialog.setNameFilter(SPATIAL_DATA_FILTER)
    dialog.setIconProvider(GenericFileIconProvider())
    return dialog


def select_spatial_data_files(parent: QWidget | None = None) -> list[str]:
    """弹出空间数据选择框，返回用户确认的文件路径。"""
    dialog = build_spatial_file_dialog(parent)
    if dialog.exec() != QFileDialog.DialogCode.Accepted:
        return []
    return list(dialog.selectedFiles())
