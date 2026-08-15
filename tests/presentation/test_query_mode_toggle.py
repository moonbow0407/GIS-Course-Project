"""查询模式功能区状态回归测试。"""

import os
from pathlib import Path

# 必须在导入 Qt 前启用无界面平台，测试才能稳定检查功能区按钮状态。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pyproj import CRS
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel
from shapely.geometry import Polygon

from app.application.gis_application import GisApplication
from app.domain.feature import Feature
from app.domain.map_document import MapDocument
from app.domain.vector_layer import VectorLayer
from app.infrastructure.file_io.auto_reader import AutoDataReader
from app.infrastructure.file_io.auto_writer import AutoDataWriter
from app.presentation.main_window import MainWindow
from app.presentation.widgets.attribute_query_dialog import AttributeQueryRequest
from app.presentation.widgets.ribbon_bar import RibbonBar


def _make_query_window() -> tuple[QApplication, MainWindow]:
    """创建包含一个可查询面图层的主窗口。"""
    qt_application: QApplication = QApplication.instance() or QApplication([])
    layer = VectorLayer.create(
        layer_id="zones",
        name="管理区",
        features=(
            Feature(
                fid=1,
                geometry=Polygon([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]),
                attributes={"名称": "甲"},
            ),
        ),
        crs=CRS.from_epsg(4326),
    )
    document = MapDocument()
    document.add_layer(layer)
    window = MainWindow()
    window._application = GisApplication(AutoDataReader(), AutoDataWriter(), document)
    window._refresh_workspace()
    window.resize(800, 600)
    window.show()
    qt_application.processEvents()
    return qt_application, window


def test_query_ribbon_buttons_support_persistent_checked_state() -> None:
    """三种查询按钮都应支持由主窗口控制的持续高亮状态。"""
    qt_application: QApplication = QApplication.instance() or QApplication([])
    ribbon = RibbonBar()

    for action_id in ("point_query", "rectangle_query", "attribute_query"):
        button = ribbon._checkable_buttons.get(action_id)
        assert button is not None
        assert button.isCheckable()
        ribbon.set_action_checked(action_id, True)
        assert button.isChecked()
        ribbon.set_action_checked(action_id, False)
        assert not button.isChecked()

    assert qt_application is not None


def test_ribbon_starts_with_tabs_without_title_row() -> None:
    """功能区顶部应直接显示标签页，不再展示平台标题和右上角“关于”。"""
    qt_application: QApplication = QApplication.instance() or QApplication([])
    ribbon = RibbonBar()
    layout = ribbon.layout()

    assert layout is not None
    assert layout.itemAt(0).widget() is ribbon._tabs
    assert ribbon.findChild(QLabel, "applicationBrand") is None
    assert ribbon.findChild(QLabel, "ribbonAbout") is None
    assert qt_application is not None


def test_unclicked_point_query_menu_area_does_not_look_selected() -> None:
    """未点击点选查询时，下拉箭头区域不应单独显示成浅蓝激活块。"""
    qt_application: QApplication = QApplication.instance() or QApplication([])
    previous_style = qt_application.styleSheet()
    style_path = Path(__file__).parents[2] / "app" / "resources" / "styles" / "main.qss"
    try:
        qt_application.setStyleSheet(style_path.read_text(encoding="utf-8"))
        ribbon = RibbonBar()
        ribbon.resize(1000, 180)
        ribbon.show()
        qt_application.processEvents()
        button = ribbon._checkable_buttons["point_query"]
        assert not button.isChecked()

        QTest.mouseMove(button, QPoint(button.width() - 3, 8))
        qt_application.processEvents()
        image = button.grab().toImage()
        body_color = image.pixelColor(8, 8)
        menu_color = image.pixelColor(button.width() - 4, 8)

        assert menu_color == body_color

        button.setChecked(True)
        qt_application.processEvents()
        checked_image = button.grab().toImage()
        checked_body_color = checked_image.pixelColor(8, 8)
        checked_menu_color = checked_image.pixelColor(button.width() - 4, 8)

        assert checked_body_color.name() == "#dcecf9"
        assert checked_menu_color == checked_body_color
    finally:
        qt_application.setStyleSheet(previous_style)


def test_spatial_query_buttons_toggle_and_switch_exclusively() -> None:
    """点选和框选应可二次点击退出，并在相互切换时保持单一高亮。"""
    qt_application, window = _make_query_window()
    point_button = window._ribbon._checkable_buttons["point_query"]
    rectangle_button = window._ribbon._checkable_buttons["rectangle_query"]

    point_button.click()
    assert window._map_canvas._point_query_active is True
    assert point_button.isChecked()

    point_button.click()
    assert window._map_canvas._point_query_active is False
    assert not point_button.isChecked()

    rectangle_button.click()
    assert window._map_canvas._rectangle_query_active is True
    assert rectangle_button.isChecked()

    point_button.click()
    assert window._map_canvas._point_query_active is True
    assert window._map_canvas._rectangle_query_active is False
    assert point_button.isChecked()
    assert not rectangle_button.isChecked()

    QTest.keyClick(window._map_canvas, Qt.Key.Key_Escape)
    qt_application.processEvents()
    assert window._map_canvas._point_query_active is False
    assert not point_button.isChecked()
    window.close()


def test_attribute_query_button_second_click_exits_without_reopening_dialog(
    monkeypatch,
) -> None:
    """属性查询首次点击执行查询并保持高亮，第二次点击只退出模式。"""
    _qt_application, window = _make_query_window()
    opened_count = 0

    class AcceptedAttributeQueryDialog:
        """返回固定属性条件并记录打开次数。"""

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            nonlocal opened_count
            opened_count += 1

        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Accepted

        def request(self) -> AttributeQueryRequest:
            return AttributeQueryRequest("zones", "名称", "=", "甲")

    monkeypatch.setattr(
        "app.presentation.main_window.AttributeQueryDialog",
        AcceptedAttributeQueryDialog,
    )

    attribute_button = window._ribbon._checkable_buttons["attribute_query"]
    attribute_button.click()
    assert opened_count == 1
    assert attribute_button.isChecked()

    attribute_button.click()
    assert opened_count == 1
    assert not attribute_button.isChecked()
    window.close()
