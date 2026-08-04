"""图层管理面板行为测试。"""

import os

# 必须在导入 Qt 前启用无界面平台，测试才能在没有显示器的环境运行。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pyproj import CRS
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QApplication, QMenu, QTreeWidget
from shapely.geometry import Point

from app.application.results import LayerSnapshot, WorkspaceSnapshot
from app.domain.feature import Feature
from app.domain.vector_layer import VectorLayer
from app.presentation.widgets.layer_panel import LayerPanel


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
            "缩放至图层",
            "符号系统",
            "打开属性表",
            "打开文件夹",
            "删除图层",
        ]
        return menu.actions()[0]

    monkeypatch.setattr(panel, "_execute_context_menu", select_zoom_action)
    panel._on_context_menu_requested(tree.visualItemRect(item).center())

    assert requested_layer_ids == ["target"]
    panel.close()
