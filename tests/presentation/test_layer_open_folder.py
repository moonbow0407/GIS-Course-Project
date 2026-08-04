"""图层右键"打开文件夹"菜单与打开行为测试。"""

import os
from pathlib import Path

# 必须在导入 Qt 前启用无界面平台，测试才能在没有显示器的环境运行。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pyproj import CRS
from PySide6.QtCore import QPoint
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QTreeWidgetItem

from app.application.results import LayerSnapshot, WorkspaceSnapshot
from app.domain.feature import Feature
from app.domain.vector_layer import VectorLayer
from app.presentation.widgets.layer_panel import LayerPanel
from shapely.geometry import Point


def _make_layer(layer_id: str, name: str, source_path: Path | None) -> VectorLayer:
    """构造带单个点要素和可选数据文件路径的测试矢量图层。"""
    return VectorLayer.create(
        layer_id=layer_id,
        name=name,
        features=(Feature(fid=1, geometry=Point(0, 0), attributes={}),),
        crs=CRS.from_epsg(4549),
        source_path=source_path,
    )


def _snapshot(layer: VectorLayer) -> WorkspaceSnapshot:
    """构造只含一个图层的完整工作区快照。"""
    return WorkspaceSnapshot(
        layers=(LayerSnapshot(layer=layer, visible=True, selected_feature_ids=()),),
        active_layer_id=None,
        display_crs=layer.crs,
    )


def test_context_menu_contains_open_folder_and_emits_layer_id(monkeypatch) -> None:
    """右键菜单应包含"打开文件夹"项，选择后发出携带图层编号的信号。"""
    QApplication.instance() or QApplication([])
    panel: LayerPanel = LayerPanel()
    panel.apply_snapshot(
        _snapshot(_make_layer("a", "图层A", Path("D:/data/roads.geojson")))
    )
    panel.resize(400, 300)
    panel.show()
    QApplication.processEvents()
    item: QTreeWidgetItem | None = panel._tree.topLevelItem(0)
    assert item is not None

    menu_texts: list[str] = []
    emitted: list[str] = []
    panel.layer_folder_requested.connect(emitted.append)

    def execute(menu: QMenu, position: QPoint) -> QAction | None:
        menu_texts.extend(action.text() for action in menu.actions())
        return menu.actions()[3]

    monkeypatch.setattr(panel, "_execute_context_menu", execute)
    rect = panel._tree.visualItemRect(item)
    panel._on_context_menu_requested(rect.topLeft() + QPoint(5, 5))

    assert menu_texts == [
        "缩放至图层",
        "符号系统",
        "打开属性表",
        "打开文件夹",
        "删除图层",
    ]
    assert emitted == ["a"]
    panel.close()
