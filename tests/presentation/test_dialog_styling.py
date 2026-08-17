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
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QInputDialog,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QStyle,
    QStyleOptionButton,
    QTableView,
    QToolButton,
    QWidget,
)
from shapely.geometry import Point

from app.application.results import LayerSnapshot
from app.domain.feature import Feature
from app.domain.vector_layer import VectorLayer
from app.presentation.main_window import MainWindow
from app.presentation.widgets.attribute_table import AttributeTablePanel
from app.presentation.widgets.geometry_edit_toolbar import GeometryEditToolbar
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

    # 属性表使用 QTableView（QTableWidget 的基类）；深色系统调色板下
    # 仍依赖 QSS 提供浅色背景。
    table: QTableView = attribute_panel.findChild(QTableView)
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


def test_geometry_edit_toolbar_keeps_light_dialog_style_with_dark_system_palette() -> None:
    """几何编辑浮动工具条在深色系统主题下仍应使用统一的浅色弹窗样式。"""
    application: QApplication = QApplication.instance() or QApplication([])
    original_palette: QPalette = application.palette()
    dark_palette: QPalette = QPalette(original_palette)
    dark_palette.setColor(QPalette.ColorRole.Window, QColor("#202020"))
    dark_palette.setColor(QPalette.ColorRole.WindowText, QColor("#eeeeee"))
    dark_palette.setColor(QPalette.ColorRole.Button, QColor("#202020"))
    dark_palette.setColor(QPalette.ColorRole.ButtonText, QColor("#eeeeee"))
    application.setPalette(dark_palette)
    load_style(application)

    toolbar = GeometryEditToolbar()
    disabled_buttons: tuple[QPushButton, ...] = (
        toolbar._delete_btn,
        toolbar._commit_btn,
    )
    for button in disabled_buttons:
        button.setEnabled(False)
    toolbar.show()
    application.processEvents()

    style_sheet: str = application.styleSheet()
    assert "QWidget#geometryEditToolbar" in style_sheet
    assert "QWidget#geometryEditToolbar QPushButton:checked" in style_sheet
    assert "QWidget#geometryEditToolbar QPushButton:disabled" in style_sheet
    # 顶部内边距属于工具条背景；按钮区域另行检查禁用态。
    assert _pixel_is_light(toolbar, QPoint(toolbar.width() // 2, 2))
    assert all(
        _pixel_is_light(button, QPoint(5, button.height() // 2))
        for button in disabled_buttons
    )

    toolbar.close()
    application.setPalette(original_palette)


def test_dock_title_buttons_follow_light_workspace_palette() -> None:
    """工作面板和属性表的停靠标题栏应使用一致的浅色按钮。"""
    application: QApplication = QApplication.instance() or QApplication([])
    load_style(application)
    style_sheet: str = application.styleSheet()

    assert "QWidget#workspacePanelTitleBar" in style_sheet
    assert "QWidget#attributeTableTitleBar" in style_sheet
    assert "QToolButton#workspacePanelCloseButton:hover" in style_sheet
    assert "QToolButton#attributeTableCloseButton:hover" in style_sheet
    assert "QDockWidget::close-button" not in style_sheet
    assert "QDockWidget::float-button" not in style_sheet

    window: MainWindow = MainWindow()
    title_bars = [
        window._panel_dock.titleBarWidget(),
        window._attribute_table_dock.titleBarWidget(),
    ]
    assert all(title_bar is not None for title_bar in title_bars)

    expected_buttons = {
        "workspacePanelFloatButton",
        "workspacePanelCloseButton",
        "attributeTableFloatButton",
        "attributeTableCloseButton",
    }
    title_buttons: list[QToolButton] = [
        button
        for title_bar in title_bars
        for button in title_bar.findChildren(QToolButton)
    ]
    application.processEvents()

    assert {button.objectName() for button in title_buttons} == expected_buttons
    assert all(not button.icon().isNull() for button in title_buttons)
    assert all(
        button.palette().color(QPalette.ColorRole.Button).name() == "#eef4fa"
        for button in title_buttons
    )
    assert all(
        button.palette().color(QPalette.ColorRole.ButtonText).lightness() < 180
        for button in title_buttons
    )
    window.close()


def test_workspace_panel_tabs_use_explicit_light_contrast() -> None:
    """统一工作面板和三个标签页必须使用明确的浅色背景与深色文字。"""
    application: QApplication = QApplication.instance() or QApplication([])
    load_style(application)
    style_sheet: str = application.styleSheet()

    assert "QDockWidget#workspacePanelDock::title" in style_sheet
    assert "QDockWidget#workspacePanelDock" in style_sheet
    assert "QTabBar#workspacePanelTabBar::tab:selected" in style_sheet
    assert "background: #ffffff" in style_sheet
    assert "color: #0f5f9f" in style_sheet


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
    table: QTableView = panel.findChild(QTableView)
    panel.show()
    application.processEvents()

    assert application is not None
    assert table is not None
    assert table.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    assert table.verticalScrollMode() == QTableView.ScrollMode.ScrollPerPixel
    assert table.verticalScrollBar().isVisible()

    panel.close()


def _pixel_is_light(widget: QWidget, point: QPoint) -> bool:
    """判断控件截图指定位置是否为浅色背景。"""
    color: QColor = widget.grab().toImage().pixelColor(point)
    return color.lightness() >= 180


def _indicator_rect(button: QCheckBox | QRadioButton) -> tuple[int, int, int, int, object]:
    """返回复选/单选按钮指示块的像素矩形和所属控件截图。"""
    option = QStyleOptionButton()
    button.initStyleOption(option)
    element = (
        QStyle.SubElement.SE_RadioButtonIndicator
        if isinstance(button, QRadioButton)
        else QStyle.SubElement.SE_CheckBoxIndicator
    )
    rect = button.style().subElementRect(element, option, button)
    image = button.grab().toImage()
    return (
        max(rect.left(), 0),
        max(rect.top(), 0),
        min(rect.right(), image.width() - 1),
        min(rect.bottom(), image.height() - 1),
        image,
    )


def _indicator_contrast(button: QCheckBox | QRadioButton) -> float:
    """返回指示块像素相对背景的最大亮度对比（边框或内部标记）。

    用逐像素最大偏差而非区域均值：圆形指示块的采样线在四角会取到
    背景白，均值会低估真实描边对比度。
    """
    left, top, right, bottom, image = _indicator_rect(button)

    def luminance(x: int, y: int) -> float:
        color = image.pixelColor(x, y)
        return 0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()

    border_values = [luminance(x, top) for x in range(left, right + 1)]
    border_values += [luminance(left, y) for y in range(top, bottom + 1)]
    inner_values = [
        luminance(x, y)
        for x in range(left + 2, max(right - 1, left + 3))
        for y in range(top + 2, max(bottom - 1, top + 3))
    ]
    background = luminance(min(right + 6, image.width() - 1), (top + bottom) // 2)
    border_max = max(abs(value - background) for value in border_values)
    inner_max = max(abs(value - background) for value in inner_values)
    return max(border_max, inner_max)


def _indicator_min_inner_luminance(button: QCheckBox | QRadioButton) -> float:
    """返回指示块内部区域的最暗像素亮度，用于检测勾选态的蓝色标记。"""
    left, top, right, bottom, image = _indicator_rect(button)

    def luminance(x: int, y: int) -> float:
        color = image.pixelColor(x, y)
        return 0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()

    values = [
        luminance(x, y)
        for x in range(left + 2, max(right - 1, left + 3))
        for y in range(top + 2, max(bottom - 1, top + 3))
    ]
    return min(values)


def _indicator_max_inner_luminance(button: QCheckBox | QRadioButton) -> float:
    """返回指示块内部区域的最亮像素亮度，用于检测对勾笔画。"""
    left, top, right, bottom, image = _indicator_rect(button)

    def luminance(x: int, y: int) -> float:
        color = image.pixelColor(x, y)
        return 0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()

    values = [
        luminance(x, y)
        for x in range(left + 2, max(right - 1, left + 3))
        for y in range(top + 2, max(bottom - 1, top + 3))
    ]
    return max(values)


def test_checkbox_and_radio_indicators_stay_visible_when_checked() -> None:
    """对话框浅色规则下，勾选与未勾选的指示块都应清晰可辨。

    QDialog QWidget 背景规则命中 QCheckBox/QRadioButton 后，Qt 不再用
    原生样式绘制指示块；缺少显式 indicator 规则时勾选态会画成空白，
    用户无法分辨是否勾选（白底白框）。
    """
    application: QApplication = QApplication.instance() or QApplication([])
    load_style(application)

    dialog = QDialog()
    checked_box = QCheckBox("显示 NoData")
    checked_box.setChecked(True)
    unchecked_box = QCheckBox("融合相互重叠的缓冲结果")
    radio_checked = QRadioButton("所有可见图层")
    radio_unchecked = QRadioButton("仅活动图层")

    layout = QFormLayout(dialog)
    layout.addRow(checked_box)
    layout.addRow(unchecked_box)
    layout.addRow(radio_checked)
    layout.addRow(radio_unchecked)
    radio_checked.setChecked(True)
    dialog.show()
    application.processEvents()

    assert application is not None
    # 勾选与未勾选复选框：白底描边框始终可见。
    assert _indicator_contrast(checked_box) >= 40.0
    assert _indicator_contrast(unchecked_box) >= 40.0
    # 勾选态白底框内出现深色对勾，且大部分区域仍为白底。
    assert _indicator_min_inner_luminance(checked_box) <= 140.0
    assert _indicator_max_inner_luminance(checked_box) >= 240.0
    # 未勾选框内部为空白白底，不能出现对勾或色块。
    assert _indicator_min_inner_luminance(unchecked_box) >= 200.0
    # 已勾选与未勾选的单选按钮：圆环均可见。
    assert _indicator_contrast(radio_checked) >= 40.0
    assert _indicator_contrast(radio_unchecked) >= 40.0
    # 只有已勾选的单选按钮内部出现深色中心点。
    assert (
        _indicator_min_inner_luminance(radio_checked)
        < _indicator_min_inner_luminance(radio_unchecked) - 40.0
    )

    dialog.close()
