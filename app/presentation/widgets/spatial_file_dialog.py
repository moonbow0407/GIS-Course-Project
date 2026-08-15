"""打开空间数据时使用的文件选择对话框。

必须使用系统原生对话框：用户熟悉 Windows 资源管理器的浏览与多选，
不得为规避大 TIFF 预览而改成 Qt 自制文件框。
"""

from PySide6.QtWidgets import QFileDialog, QWidget

SPATIAL_DATA_FILTER = (
    "空间数据 (*.shp *.geojson *.json *.gpkg *.kml *.tif *.tiff *.img *.dem);;"
    "所有文件 (*.*)"
)


def build_spatial_file_dialog(parent: QWidget | None = None) -> QFileDialog:
    """构造支持单击、Ctrl、Shift 多选的系统原生空间数据选择框。"""
    dialog = QFileDialog(parent, "打开空间数据")
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog, False)
    dialog.setFileMode(QFileDialog.FileMode.ExistingFiles)
    dialog.setNameFilter(SPATIAL_DATA_FILTER)
    return dialog


def select_spatial_data_files(parent: QWidget | None = None) -> list[str]:
    """弹出系统原生空间数据选择框，返回用户确认的文件路径。"""
    path_strings, _selected_filter = QFileDialog.getOpenFileNames(
        parent,
        "打开空间数据",
        "",
        SPATIAL_DATA_FILTER,
    )
    return list(path_strings)
