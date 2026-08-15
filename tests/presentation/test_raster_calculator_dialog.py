"""栅格计算器对话框主题测试。"""

import os

# 必须在导入 Qt 前启用无界面平台，测试才能在没有显示器的环境运行。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from affine import Affine
from pyproj import CRS
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QTextEdit

from app.application.results import LayerSnapshot
from app.domain.raster_layer import RasterLayer
from app.presentation.widgets.raster_calculator_dialog import RasterCalculatorDialog
from main import load_style


def test_expression_editor_keeps_light_contrast_with_dark_system_palette() -> None:
    """系统深色调色板下，表达式编辑框仍应使用浅色背景和深色文字。"""
    application: QApplication = QApplication.instance() or QApplication([])
    original_palette: QPalette = application.palette()
    dark_palette: QPalette = QPalette(original_palette)
    dark_palette.setColor(QPalette.ColorRole.Window, QColor("#202020"))
    dark_palette.setColor(QPalette.ColorRole.WindowText, QColor("#eeeeee"))
    dark_palette.setColor(QPalette.ColorRole.Base, QColor("#2b2b2b"))
    dark_palette.setColor(QPalette.ColorRole.Text, QColor("#eeeeee"))
    application.setPalette(dark_palette)
    load_style(application)

    data: np.ndarray = np.ones((1, 2, 2), dtype=np.float32)
    image: np.ndarray = np.full((2, 2, 4), 255, dtype=np.uint8)
    layer: RasterLayer = RasterLayer.create(
        name="dem",
        raster_data=data,
        image_data=image,
        valid_mask=np.ones((2, 2), dtype=bool),
        transform=Affine.identity(),
        crs=CRS.from_epsg(4326),
        bounds=(0.0, 0.0, 2.0, 2.0),
    )
    dialog: RasterCalculatorDialog = RasterCalculatorDialog(
        (LayerSnapshot(layer=layer, visible=True, selected_feature_ids=()),)
    )
    dialog.show()
    application.processEvents()

    editor: QTextEdit | None = dialog.findChild(QTextEdit, "rasterCalculatorExpression")
    assert editor is not None
    assert editor.palette().color(QPalette.ColorRole.Base).lightness() >= 180
    assert editor.palette().color(QPalette.ColorRole.Text).lightness() <= 120

    dialog.close()
    application.setPalette(original_palette)
