"""图层管理面板行为测试。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pyproj import CRS
from PySide6.QtWidgets import QApplication
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
