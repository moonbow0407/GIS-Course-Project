"""栅格输出图层名与文件名联动测试。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLineEdit

from app.presentation.widgets.raster_output_fields import RasterOutputNameBinder


def test_output_name_and_path_stay_in_sync() -> None:
    """修改图层名或选择输出路径时，两者应保持同一文件名。"""
    app = QApplication.instance() or QApplication([])
    name_edit = QLineEdit()
    path_edit = QLineEdit()
    binder = RasterOutputNameBinder(
        name_edit,
        path_edit,
        os.path.abspath("D:/results/dem_result.tif"),
    )

    assert name_edit.text() == "dem_result"
    assert path_edit.text().endswith("dem_result.tif")

    name_edit.setText("slope_result")
    assert path_edit.text().endswith("slope_result.tif")

    binder.set_path(os.path.abspath("D:/outputs/aspect.tiff"))
    assert name_edit.text() == "aspect"
    assert path_edit.text().endswith("aspect.tiff")

    name_edit.deleteLater()
    path_edit.deleteLater()
    app.processEvents()
