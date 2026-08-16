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


def test_attribute_table_uses_explicit_light_title_bar_controls() -> None:
    """属性表停靠栏应复用可控的浅色标题栏，而不是依赖系统原生按钮。"""
    application: QApplication = QApplication.instance() or QApplication([])
    window = MainWindow()

    title_bar = window._attribute_table_dock.titleBarWidget()
    assert title_bar is not None
    assert title_bar.objectName() == "attributeTableTitleBar"
    title_buttons = {
        button.objectName(): button
        for button in title_bar.findChildren(QToolButton)
    }

    assert set(title_buttons) == {
        "attributeTableFloatButton",
        "attributeTableCloseButton",
    }
    assert all(not button.icon().isNull() for button in title_buttons.values())
    assert title_buttons["attributeTableFloatButton"].accessibleName() == "浮动/停靠"
    assert title_buttons["attributeTableCloseButton"].accessibleName() == "关闭属性表"
    application.processEvents()
    window._attribute_table_dock.show()
    title_buttons["attributeTableCloseButton"].click()
    assert window._attribute_table_dock.isHidden()
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
    # 属性表为 QTableView；QTableWidget 是其子类，规则同时覆盖两者。
    assert "QTableView QScrollBar:horizontal" in style_sheet
    assert "QTableView QScrollBar::handle:horizontal" in style_sheet
    assert "background: #9eb5ca" in style_sheet
    panel.close()


def test_attribute_table_model_supplies_values_and_fid_mapping() -> None:
    """模型应按需提供单元格文本，FID 列在 UserRole 携带原始编号。"""
    application: QApplication = QApplication.instance() or QApplication([])
    layer = VectorLayer.create(
        layer_id="roads",
        name="道路",
        features=(
            Feature(fid=1, geometry=Point(0, 0), attributes={"名称": "甲", "等级": 3}),
            Feature(fid="a-2", geometry=Point(1, 1), attributes={"名称": "乙"}),
        ),
        crs=CRS.from_epsg(4326),
    )
    panel = AttributeTablePanel()
    panel.set_layer(
        LayerSnapshot(layer=layer, visible=True, selected_feature_ids=())
    )

    model = panel._model
    assert model.columnCount() == 3  # FID + 名称 + 等级
    assert model.rowCount() == 2
    assert model.index(0, 0).data(Qt.ItemDataRole.DisplayRole) == "1"
    assert model.index(0, 0).data(Qt.ItemDataRole.UserRole) == 1
    assert model.index(1, 0).data(Qt.ItemDataRole.UserRole) == "a-2"
    assert model.index(0, 2).data(Qt.ItemDataRole.DisplayRole) == "3"
    assert model.index(1, 2).data(Qt.ItemDataRole.DisplayRole) == ""
    assert model.row_for_fid("a-2") == 1
    assert application is not None


def test_attribute_table_sorts_numeric_strings_numerically() -> None:
    """点击表头排序时应按数值而非字典序比较可解析文本。"""
    application: QApplication = QApplication.instance() or QApplication([])
    layer = VectorLayer.create(
        layer_id="sort-check",
        name="排序",
        features=(
            Feature(fid=1, geometry=Point(0, 0), attributes={"值": "10"}),
            Feature(fid=2, geometry=Point(1, 1), attributes={"值": "2"}),
            Feature(fid=3, geometry=Point(2, 2), attributes={"值": "文本"}),
        ),
        crs=CRS.from_epsg(4326),
    )
    panel = AttributeTablePanel()
    panel.set_layer(
        LayerSnapshot(layer=layer, visible=True, selected_feature_ids=())
    )

    panel._table.sortByColumn(1, Qt.SortOrder.AscendingOrder)

    fids = [
        panel._model.index(row, 0).data(Qt.ItemDataRole.UserRole)
        for row in range(panel._model.rowCount())
    ]
    assert fids == [2, 1, 3]  # 数值 2 < 10，非数值文本排在最后。
    assert panel._model.row_for_fid(1) == 1
    assert application is not None


def test_highlight_features_selects_mapped_rows_without_full_scan() -> None:
    """地图侧高亮应通过 fid 映射直接定位行，而不是逐行扫描。"""
    application: QApplication = QApplication.instance() or QApplication([])
    layer = VectorLayer.create(
        layer_id="highlight",
        name="高亮",
        features=tuple(
            Feature(fid=index, geometry=Point(index, index), attributes={})
            for index in range(50)
        ),
        crs=CRS.from_epsg(4326),
    )
    panel = AttributeTablePanel()
    panel.set_layer(
        LayerSnapshot(layer=layer, visible=True, selected_feature_ids=())
    )

    panel.highlight_features({7, 41})

    assert panel.selected_feature_ids() == (7, 41)
    panel.highlight_features(set())
    assert panel.selected_feature_ids() == ()
    assert application is not None


def test_refresh_layer_restores_selection_after_attribute_edit() -> None:
    """要素属性编辑刷新后，先前选中的行应按 fid 恢复高亮。"""
    application: QApplication = QApplication.instance() or QApplication([])
    features: tuple[Feature, ...] = tuple(
        Feature(fid=index, geometry=Point(index, index), attributes={"名称": f"行{index}"})
        for index in range(5)
    )
    layer = VectorLayer.create(
        layer_id="editable", name="可编辑", features=features, crs=CRS.from_epsg(4326)
    )
    panel = AttributeTablePanel()
    panel.set_layer(
        LayerSnapshot(layer=layer, visible=True, selected_feature_ids=(3,))
    )
    panel.highlight_features({3})

    edited_layer = VectorLayer.create(
        layer_id="editable",
        name="可编辑",
        features=tuple(
            Feature(
                fid=feature.fid,
                geometry=feature.geometry,
                attributes={"名称": "已改"} if feature.fid == 3 else feature.attributes,
            )
            for feature in features
        ),
        crs=CRS.from_epsg(4326),
    )
    panel.refresh_layer(
        LayerSnapshot(
            layer=edited_layer, visible=True, selected_feature_ids=(3,)
        )
    )

    assert panel.selected_feature_ids() == (3,)
    assert panel._model.index(
        panel._model.row_for_fid(3), 1
    ).data(Qt.ItemDataRole.DisplayRole) == "已改"
    assert application is not None
