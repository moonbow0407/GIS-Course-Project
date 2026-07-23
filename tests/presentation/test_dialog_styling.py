"""对话框浅色主题测试。"""

import os

# 必须在导入 Qt 前启用无界面平台，测试才能在没有显示器的环境运行。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pyproj import CRS
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QTableWidget, QWidget
from shapely.geometry import Point

from app.application.results import LayerSnapshot
from app.domain.feature import Feature
from app.domain.vector_layer import VectorLayer
from app.presentation.widgets.attribute_table import AttributeTableDialog
from main import load_style


def test_dialogs_keep_light_readable_background_with_dark_system_palette() -> None:
    """系统深色调色板下，属性表和提示框仍应保持浅色可读背景。"""
    application: QApplication = QApplication.instance() or QApplication([])
    original_palette: QPalette = application.palette()
    dark_palette: QPalette = QPalette(original_palette)
    dark_palette.setColor(QPalette.ColorRole.Window, QColor("#202020"))
    dark_palette.setColor(QPalette.ColorRole.WindowText, QColor("#eeeeee"))
    dark_palette.setColor(QPalette.ColorRole.Base, QColor("#2b2b2b"))
    dark_palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#333333"))
    dark_palette.setColor(QPalette.ColorRole.Text, QColor("#eeeeee"))
    application.setPalette(dark_palette)
    load_style(application)

    layer: VectorLayer = VectorLayer.create(
        layer_id="roads",
        name="道路",
        features=(Feature(fid=1, geometry=Point(0, 0), attributes={"名称": "主路"}),),
        crs=CRS.from_epsg(4326),
    )
    attribute_dialog: AttributeTableDialog = AttributeTableDialog(
        LayerSnapshot(layer=layer, visible=True, selected_feature_ids=())
    )
    message_box: QMessageBox = QMessageBox(QMessageBox.Icon.Information, "提示", "接口已预留")
    attribute_dialog.show()
    message_box.show()
    application.processEvents()

    table: QTableWidget = attribute_dialog.findChild(QTableWidget)
    assert table is not None
    assert _pixel_is_light(attribute_dialog, QPoint(5, 5))
    assert _pixel_is_light(table, table.rect().center())
    assert _pixel_is_light(message_box, QPoint(8, 8))

    message_box.close()
    attribute_dialog.close()
    application.setPalette(original_palette)


def test_layer_context_menu_keeps_light_readable_background_with_dark_system_palette() -> None:
    """系统深色调色板下，图层右键菜单仍应保持浅色可读背景。"""
    application: QApplication = QApplication.instance() or QApplication([])
    original_palette: QPalette = application.palette()
    dark_palette: QPalette = QPalette(original_palette)
    dark_palette.setColor(QPalette.ColorRole.Window, QColor("#202020"))
    dark_palette.setColor(QPalette.ColorRole.WindowText, QColor("#eeeeee"))
    dark_palette.setColor(QPalette.ColorRole.Base, QColor("#2b2b2b"))
    dark_palette.setColor(QPalette.ColorRole.Text, QColor("#eeeeee"))
    application.setPalette(dark_palette)
    load_style(application)

    menu: QMenu = QMenu()
    menu.addAction("打开属性表")
    menu.addAction("删除图层")
    menu.show()
    application.processEvents()

    assert _pixel_is_light(menu, QPoint(8, 8))

    menu.close()
    application.setPalette(original_palette)


def test_attribute_table_keeps_vertical_scrollbar_visible() -> None:
    """属性表即使数据行数较少，也应显示可拖动的右侧垂直滚动条。"""
    application: QApplication = QApplication.instance() or QApplication([])
    load_style(application)
    layer: VectorLayer = VectorLayer.create(
        layer_id="roads",
        name="道路",
        features=(Feature(fid=1, geometry=Point(0, 0), attributes={"名称": "主路"}),),
        crs=CRS.from_epsg(4326),
    )
    dialog: AttributeTableDialog = AttributeTableDialog(
        LayerSnapshot(layer=layer, visible=True, selected_feature_ids=())
    )
    table: QTableWidget = dialog.findChild(QTableWidget)
    dialog.show()
    application.processEvents()

    assert application is not None
    assert table is not None
    assert table.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    assert table.verticalScrollMode() == QTableWidget.ScrollMode.ScrollPerPixel
    assert table.verticalScrollBar().isVisible()

    dialog.close()


def _pixel_is_light(widget: QWidget, point: QPoint) -> bool:
    """判断控件截图指定位置是否为浅色背景。"""
    color: QColor = widget.grab().toImage().pixelColor(point)
    return color.lightness() >= 180
