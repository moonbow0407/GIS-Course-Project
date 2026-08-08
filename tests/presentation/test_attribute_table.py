"""地图下方属性表的布局、工具栏和滚动交互测试。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pyproj import CRS
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QToolButton
from shapely.geometry import Point

from app.application.results import LayerSnapshot
from app.domain.feature import Feature
from app.domain.vector_layer import VectorLayer
from app.presentation.main_window import MainWindow
from app.presentation.widgets.attribute_table import AttributeTablePanel
from main import load_style


def _make_snapshot(field_count: int = 13) -> LayerSnapshot:
    """构造字段较多的测试图层，模拟属性表需要横向滚动的场景。"""
    attributes = {
        f"字段_{index}_long_name": f"value-{index}-with-long-content"
        for index in range(field_count)
    }
    layer = VectorLayer.create(
        layer_id="roads",
        name="道路",
        features=(
            Feature(fid=1, geometry=Point(0, 0), attributes=attributes),
            Feature(fid=2, geometry=Point(1, 1), attributes=attributes),
        ),
        crs=CRS.from_epsg(4326),
    )
    return LayerSnapshot(layer=layer, visible=True, selected_feature_ids=())


def test_attribute_table_is_placed_below_map_and_has_visible_controls() -> None:
    """属性表应作为可停靠窗口浮动显示，右侧工作面板标题按钮必须清晰可见。"""
    application: QApplication = QApplication.instance() or QApplication([])
    window = MainWindow()

    assert window._attribute_table_dock.widget() is window._attribute_table_panel
    assert window._attribute_table_dock.isHidden()

    window._attribute_table_panel.set_layer(_make_snapshot())
    window._show_attribute_table_panel()
    window.show()
    application.processEvents()

    assert window._attribute_table_dock.isVisible()
    title_buttons = {
        button.objectName(): button
        for button in window.findChildren(QToolButton)
        if button.objectName()
        in {"workspacePanelFloatButton", "workspacePanelCloseButton"}
    }
    assert not title_buttons["workspacePanelFloatButton"].icon().isNull()
    assert not title_buttons["workspacePanelCloseButton"].icon().isNull()
    assert title_buttons["workspacePanelFloatButton"].accessibleName() == "浮动/停靠"
    assert title_buttons["workspacePanelCloseButton"].accessibleName() == "关闭工作面板"
    window.close()


def test_attribute_table_toolbar_emits_crud_and_query_requests() -> None:
    """属性表工具栏应分别发出查询、新增、编辑、删除和关闭请求。"""
    application: QApplication = QApplication.instance() or QApplication([])
    panel = AttributeTablePanel()
    panel.set_layer(_make_snapshot(field_count=3))
    events: list[tuple[str, object]] = []
    panel.query_requested.connect(lambda layer_id: events.append(("query", layer_id)))
    panel.add_feature_requested.connect(lambda layer_id: events.append(("add", layer_id)))
    panel.edit_feature_requested.connect(
        lambda layer_id, fid: events.append(("edit", (layer_id, fid)))
    )
    panel.delete_features_requested.connect(
        lambda layer_id, fids: events.append(("delete", (layer_id, fids)))
    )
    panel.close_requested.connect(lambda: events.append(("close", "")))

    panel._query_button.click()
    panel._add_button.click()
    panel._table.selectRow(0)
    panel._edit_button.click()
    panel._delete_button.click()
    panel._close_button.click()
    application.processEvents()

    assert events == [
        ("query", "roads"),
        ("add", "roads"),
        ("edit", ("roads", 1)),
        ("delete", ("roads", (1,))),
        ("close", ""),
    ]
    assert panel._edit_button.isEnabled()
    assert panel._delete_button.isEnabled()


def test_attribute_table_shows_horizontal_scrollbar_for_many_fields() -> None:
    """字段超出面板宽度时应显示有底色和滑块的横向滚动条。"""
    application: QApplication = QApplication.instance() or QApplication([])
    load_style(application)
    panel = AttributeTablePanel()
    panel.set_layer(_make_snapshot(field_count=13))
    panel.resize(480, 300)
    panel.show()
    application.processEvents()

    table = panel._table
    assert table.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert table.horizontalScrollBar().isVisible()
    style_sheet: str = application.styleSheet()
    assert "QTableWidget QScrollBar:horizontal" in style_sheet
    assert "QTableWidget QScrollBar::handle:horizontal" in style_sheet
    assert "background: #9eb5ca" in style_sheet
    panel.close()
