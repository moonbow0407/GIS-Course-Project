"""图层管理面板行为测试。"""

import os
from dataclasses import replace

# 必须在导入 Qt 前启用无界面平台，测试才能在没有显示器的环境运行。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from affine import Affine
from pyproj import CRS
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QApplication, QMenu, QTreeWidget
from shapely.geometry import Point

from app.application.results import LayerSnapshot, WorkspaceSnapshot
from app.application.symbology_service import create_raster_classified_symbology
from app.domain.feature import Feature
from app.domain.raster_layer import RasterLayer
from app.domain.symbology import RasterRendererType
from app.domain.vector_layer import VectorLayer
from app.presentation.widgets.layer_panel import LayerPanel


def test_raster_classified_legend_shows_each_value_and_color() -> None:
    """分类栅格图层树应直接列出每个值及其对应颜色。"""
    application: QApplication = QApplication.instance() or QApplication([])
    raster = RasterLayer.create(
        layer_id="classified-raster",
        name="分类栅格",
        raster_data=np.ones((1, 2, 2), dtype=np.float32),
        image_data=np.full((2, 2, 4), 255, dtype=np.uint8),
        valid_mask=np.ones((2, 2), dtype=bool),
        transform=Affine.identity(),
        crs=CRS.from_epsg(4326),
        bounds=(0.0, 0.0, 2.0, 2.0),
        symbology=replace(
            create_raster_classified_symbology((1.0, 2.0, 3.0)),
            other_visible=False,
        ),
    )
    panel: LayerPanel = LayerPanel()
    panel.apply_snapshot(
        WorkspaceSnapshot(
            layers=(LayerSnapshot(layer=raster, visible=True, selected_feature_ids=()),),
            active_layer_id=raster.layer_id,
            display_crs=raster.crs,
        )
    )
    tree: QTreeWidget | None = panel.findChild(QTreeWidget, "layerTree")
    assert tree is not None
    parent = tree.topLevelItem(0)
    assert parent is not None
    assert raster.symbology is not None
    assert raster.symbology.renderer_type is RasterRendererType.CLASSIFIED
    assert parent.childCount() == 4
    assert [parent.child(index).text(0) for index in range(1, 4)] == ["1", "2", "3"]
    assert all(not parent.child(index).icon(0).isNull() for index in range(1, 4))
    panel.close()
    assert application is not None


def test_apply_snapshot_does_not_emit_user_activation_signal() -> None:
    """程序同步工作区快照时，不应发出代表用户操作的图层激活信号。"""
    application: QApplication = QApplication.instance() or QApplication([])
    layer: VectorLayer = VectorLayer.create(
        layer_id="railway",
        name="高铁网",
        features=(Feature(fid=1, geometry=Point(0, 0), attributes={}),),
        crs=CRS.from_epsg(4326),
    )
    snapshot: WorkspaceSnapshot = WorkspaceSnapshot(
        layers=(LayerSnapshot(layer=layer, visible=True, selected_feature_ids=()),),
        active_layer_id=layer.layer_id,
        display_crs=layer.crs,
    )
    panel: LayerPanel = LayerPanel()
    activated_layer_ids: list[str] = []
    panel.layer_activated.connect(activated_layer_ids.append)

    panel.apply_snapshot(snapshot)

    assert application is not None
    assert activated_layer_ids == []


def test_layer_tree_supports_flat_drag_reordering() -> None:
    """拖动图层节点后应按面板顺序换算并请求新的地图文档位置。"""
    application: QApplication = QApplication.instance() or QApplication([])
    bottom_layer: VectorLayer = VectorLayer.create(
        layer_id="bottom",
        name="底层",
        features=(Feature(fid=1, geometry=Point(0, 0), attributes={}),),
        crs=CRS.from_epsg(4326),
    )
    top_layer: VectorLayer = VectorLayer.create(
        layer_id="top",
        name="顶层",
        features=(Feature(fid=1, geometry=Point(1, 1), attributes={}),),
        crs=CRS.from_epsg(4326),
    )
    panel: LayerPanel = LayerPanel()
    panel.apply_snapshot(
        WorkspaceSnapshot(
            layers=(
                LayerSnapshot(layer=bottom_layer, visible=True, selected_feature_ids=()),
                LayerSnapshot(layer=top_layer, visible=True, selected_feature_ids=()),
            ),
            active_layer_id=top_layer.layer_id,
            display_crs=top_layer.crs,
        )
    )
    tree: QTreeWidget | None = panel.findChild(QTreeWidget, "layerTree")
    assert tree is not None
    assert tree.dragDropMode() == QAbstractItemView.DragDropMode.InternalMove
    top_item = tree.topLevelItem(0)
    assert top_item.flags() & Qt.ItemFlag.ItemIsDragEnabled
    assert top_item.flags() & Qt.ItemFlag.ItemIsDropEnabled
    requested_moves: list[tuple[str, int]] = []
    panel.layer_move_requested.connect(
        lambda layer_id, target_index: requested_moves.append((layer_id, target_index))
    )

    panel._updating = True
    moved_item = tree.takeTopLevelItem(0)
    tree.addTopLevelItem(moved_item)
    tree.setCurrentItem(moved_item)
    panel._updating = False
    panel._on_rows_moved()

    assert application is not None
    assert requested_moves == [("top", 0)]


def test_layer_context_menu_can_request_zoom_to_layer(monkeypatch) -> None:
    """右键图层的“缩放至图层”操作应携带对应图层编号。"""
    application: QApplication = QApplication.instance() or QApplication([])
    layer: VectorLayer = VectorLayer.create(
        layer_id="target",
        name="定位目标",
        features=(Feature(fid=1, geometry=Point(10, 20), attributes={}),),
        crs=CRS.from_epsg(4326),
    )
    panel: LayerPanel = LayerPanel()
    panel.apply_snapshot(
        WorkspaceSnapshot(
            layers=(LayerSnapshot(layer=layer, visible=True, selected_feature_ids=()),),
            active_layer_id=layer.layer_id,
            display_crs=layer.crs,
        )
    )
    tree: QTreeWidget | None = panel.findChild(QTreeWidget, "layerTree")
    assert tree is not None
    panel.resize(300, 400)
    panel.show()
    application.processEvents()
    item = tree.topLevelItem(0)
    requested_layer_ids: list[str] = []
    panel.layer_zoom_requested.connect(requested_layer_ids.append)

    def select_zoom_action(menu: QMenu, *args) -> object:
        assert [action.text() for action in menu.actions()] == [
            "重命名…",
            "缩放至图层",
            "图层属性",
            "符号系统",
            "打开属性表",
            "打开文件夹",
            "删除图层",
        ]
        return next(action for action in menu.actions() if action.text() == "缩放至图层")

    monkeypatch.setattr(panel, "_execute_context_menu", select_zoom_action)
    panel._on_context_menu_requested(tree.visualItemRect(item).center())

    assert requested_layer_ids == ["target"]
    panel.close()


def test_layer_context_menu_can_request_layer_properties(monkeypatch) -> None:
    """右键选择“图层属性”时应携带对应图层编号。"""
    application: QApplication = QApplication.instance() or QApplication([])
    layer: VectorLayer = VectorLayer.create(
        layer_id="properties-target",
        name="属性目标",
        features=(Feature(fid=1, geometry=Point(10, 20), attributes={}),),
        crs=CRS.from_epsg(4326),
    )
    panel: LayerPanel = LayerPanel()
    panel.apply_snapshot(
        WorkspaceSnapshot(
            layers=(LayerSnapshot(layer=layer, visible=True, selected_feature_ids=()),),
            active_layer_id=layer.layer_id,
            display_crs=layer.crs,
        )
    )
    tree: QTreeWidget | None = panel.findChild(QTreeWidget, "layerTree")
    assert tree is not None
    panel.resize(300, 400)
    panel.show()
    application.processEvents()
    item = tree.topLevelItem(0)
    requested_layer_ids: list[str] = []
    panel.layer_properties_requested.connect(requested_layer_ids.append)

    def select_properties_action(menu: QMenu, *args) -> object:
        return next(action for action in menu.actions() if action.text() == "图层属性")

    monkeypatch.setattr(panel, "_execute_context_menu", select_properties_action)
    panel._on_context_menu_requested(tree.visualItemRect(item).center())

    assert requested_layer_ids == ["properties-target"]
    panel.close()


def test_layer_context_menu_can_request_rename(monkeypatch) -> None:
    """右键选择"重命名…"时应发出携带图层编号的重命名信号。"""
    application: QApplication = QApplication.instance() or QApplication([])
    layer: VectorLayer = VectorLayer.create(
        layer_id="rename-target",
        name="重命名目标",
        features=(Feature(fid=1, geometry=Point(10, 20), attributes={}),),
        crs=CRS.from_epsg(4326),
    )
    panel: LayerPanel = LayerPanel()
    panel.apply_snapshot(
        WorkspaceSnapshot(
            layers=(LayerSnapshot(layer=layer, visible=True, selected_feature_ids=()),),
            active_layer_id=layer.layer_id,
            display_crs=layer.crs,
        )
    )
    tree: QTreeWidget | None = panel.findChild(QTreeWidget, "layerTree")
    assert tree is not None
    panel.resize(300, 400)
    panel.show()
    application.processEvents()
    item = tree.topLevelItem(0)
    requested_layer_ids: list[str] = []
    panel.layer_rename_requested.connect(requested_layer_ids.append)

    def select_rename_action(menu: QMenu, *args) -> object:
        return next(action for action in menu.actions() if action.text() == "重命名…")

    monkeypatch.setattr(panel, "_execute_context_menu", select_rename_action)
    panel._on_context_menu_requested(tree.visualItemRect(item).center())

    assert requested_layer_ids == ["rename-target"]
    panel.close()


def test_layer_context_menu_exposes_labeling_actions_for_attribute_layer(monkeypatch) -> None:
    """含属性字段的矢量图层右键菜单应提供标注开关和分类入口。"""
    application: QApplication = QApplication.instance() or QApplication([])
    layer: VectorLayer = VectorLayer.create(
        layer_id="cities",
        name="城市",
        features=(
            Feature(
                fid=1,
                geometry=Point(10, 20),
                attributes={"name": "合肥"},
            ),
        ),
        crs=CRS.from_epsg(4326),
    )
    panel: LayerPanel = LayerPanel()
    panel.apply_snapshot(
        WorkspaceSnapshot(
            layers=(LayerSnapshot(layer=layer, visible=True, selected_feature_ids=()),),
            active_layer_id=layer.layer_id,
            display_crs=layer.crs,
        )
    )
    tree: QTreeWidget | None = panel.findChild(QTreeWidget, "layerTree")
    assert tree is not None
    item = tree.topLevelItem(0)
    changes: list[tuple[str, bool]] = []
    panel.layer_labeling_changed.connect(lambda layer_id, enabled: changes.append((layer_id, enabled)))

    def select_label_action(menu: QMenu, *args) -> object:
        action = next(action for action in menu.actions() if action.text() == "标注")
        action.setChecked(True)
        return action

    monkeypatch.setattr(panel, "_execute_context_menu", select_label_action)
    panel._on_context_menu_requested(tree.visualItemRect(item).center())

    assert changes == [("cities", True)]
    assert application is not None
    panel.close()
