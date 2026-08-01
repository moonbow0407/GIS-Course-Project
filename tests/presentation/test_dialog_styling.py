"""对话框浅色主题测试。"""

import os

# 必须在导入 Qt 前启用无界面平台，测试才能在没有显示器的环境运行。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pyproj import CRS
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QInputDialog,
    QLineEdit,
    QMenu,
    QMessageBox,
    QTableWidget,
    QWidget,
)
from shapely.geometry import Point

from app.application.results import LayerSnapshot
from app.domain.feature import Feature
from app.domain.vector_layer import VectorLayer
from app.presentation.widgets.attribute_table import AttributeTablePanel
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
    attribute_panel: AttributeTablePanel = AttributeTablePanel()
    attribute_panel.set_layer(
        LayerSnapshot(layer=layer, visible=True, selected_feature_ids=())
    )
    message_box: QMessageBox = QMessageBox(QMessageBox.Icon.Information, "提示", "接口已预留")
    attribute_panel.show()
    message_box.show()
    application.processEvents()

    table: QTableWidget = attribute_panel.findChild(QTableWidget)
    assert table is not None
    assert _pixel_is_light(attribute_panel, QPoint(5, 5))
    assert _pixel_is_light(table, table.rect().center())
    assert _pixel_is_light(message_box, QPoint(8, 8))

    message_box.close()
    attribute_panel.close()
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


def test_combo_box_popup_keeps_light_readable_background_with_dark_system_palette() -> None:
    """系统深色调色板下，所有下拉列表都应保持浅色背景和深色文字。"""
    application: QApplication = QApplication.instance() or QApplication([])
    original_palette: QPalette = application.palette()
    dark_palette: QPalette = QPalette(original_palette)
    dark_palette.setColor(QPalette.ColorRole.Window, QColor("#202020"))
    dark_palette.setColor(QPalette.ColorRole.WindowText, QColor("#eeeeee"))
    dark_palette.setColor(QPalette.ColorRole.Base, QColor("#2b2b2b"))
    dark_palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#333333"))
    dark_palette.setColor(QPalette.ColorRole.Text, QColor("#eeeeee"))
    dark_palette.setColor(QPalette.ColorRole.Highlight, QColor("#555555"))
    dark_palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    application.setPalette(dark_palette)
    load_style(application)

    combo: QComboBox = QComboBox()
    combo.addItems(["圆角", "平头", "方头"])
    combo.show()
    combo.showPopup()
    application.processEvents()

    popup: QWidget = combo.view()
    assert popup.isVisible()
    assert popup.palette().color(QPalette.ColorRole.Base).lightness() >= 180
    assert popup.palette().color(QPalette.ColorRole.Text).lightness() <= 120
    assert popup.palette().color(QPalette.ColorRole.Highlight).lightness() >= 180
    assert popup.palette().color(QPalette.ColorRole.HighlightedText).lightness() <= 120

    combo.hide()
    application.setPalette(original_palette)


def test_standard_dialog_controls_keep_light_background_with_dark_system_palette() -> None:
    """标准输入框和文件选择器的列表控件应沿用统一浅色主题。"""
    application: QApplication = QApplication.instance() or QApplication([])
    original_palette: QPalette = application.palette()
    dark_palette: QPalette = QPalette(original_palette)
    dark_palette.setColor(QPalette.ColorRole.Window, QColor("#202020"))
    dark_palette.setColor(QPalette.ColorRole.WindowText, QColor("#eeeeee"))
    dark_palette.setColor(QPalette.ColorRole.Base, QColor("#2b2b2b"))
    dark_palette.setColor(QPalette.ColorRole.Text, QColor("#eeeeee"))
    application.setPalette(dark_palette)
    load_style(application)

    input_dialog: QInputDialog = QInputDialog()
    input_dialog.setInputMode(QInputDialog.InputMode.TextInput)
    input_dialog.show()

    file_dialog: QFileDialog = QFileDialog()
    file_dialog.setOption(QFileDialog.Option.DontUseNativeDialog)
    file_dialog.show()
    application.processEvents()

    input_edit: QLineEdit | None = input_dialog.findChild(QLineEdit)
    file_views: list[QAbstractItemView] = file_dialog.findChildren(QAbstractItemView)
    assert input_edit is not None
    assert input_edit.palette().color(QPalette.ColorRole.Base).lightness() >= 180
    assert file_views
    view_backgrounds: list[tuple[str, int]] = [
        (type(view).__name__, view.palette().color(QPalette.ColorRole.Base).lightness())
        for view in file_views
    ]
    assert all(lightness >= 180 for _, lightness in view_backgrounds), view_backgrounds

    file_dialog.close()
    input_dialog.close()
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
    panel: AttributeTablePanel = AttributeTablePanel()
    panel.set_layer(
        LayerSnapshot(layer=layer, visible=True, selected_feature_ids=())
    )
    table: QTableWidget = panel.findChild(QTableWidget)
    panel.show()
    application.processEvents()

    assert application is not None
    assert table is not None
    assert table.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    assert table.verticalScrollMode() == QTableWidget.ScrollMode.ScrollPerPixel
    assert table.verticalScrollBar().isVisible()

    panel.close()


def _pixel_is_light(widget: QWidget, point: QPoint) -> bool:
    """判断控件截图指定位置是否为浅色背景。"""
    color: QColor = widget.grab().toImage().pixelColor(point)
    return color.lightness() >= 180
