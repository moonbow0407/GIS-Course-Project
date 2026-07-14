"""Qt 矢量图层渲染器测试。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pyproj import CRS
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QGraphicsItem, QGraphicsPathItem, QGraphicsScene
from shapely.geometry import LineString, Point, Polygon

from app.application.results import LayerSnapshot
from app.domain.feature import Feature
from app.domain.vector_layer import VectorLayer
from app.presentation.renderers.qt_vector_renderer import QtVectorRenderer


def test_renderer_creates_selectable_items_for_point_line_and_polygon() -> None:
    """渲染器应为点、线、面几何创建带领域编号的可选择图元。"""
    application: QApplication = QApplication.instance() or QApplication([])
    scene: QGraphicsScene = QGraphicsScene()
    features: tuple[Feature, ...] = (
        Feature(fid=1, geometry=Point(0, 0), attributes={}),
        Feature(fid=2, geometry=LineString([(0, 0), (10, 10)]), attributes={}),
        Feature(
            fid=3,
            geometry=Polygon(
                [(0, 0), (10, 0), (10, 10), (0, 0)],
                holes=[[(2, 2), (4, 2), (4, 4), (2, 2)]],
            ),
            attributes={},
        ),
    )
    layer: VectorLayer = VectorLayer.create(
        layer_id="mixed",
        name="混合图层",
        features=features,
        crs=CRS.from_epsg(4326),
    )
    snapshot: LayerSnapshot = LayerSnapshot(layer=layer, visible=True, selected_feature_ids=(2,))
    renderer: QtVectorRenderer = QtVectorRenderer()

    items: list[QGraphicsItem] = renderer.render_layer(scene, snapshot, z_value=3.0)

    assert application is not None
    assert len(items) == 3
    assert {item.data(0) for item in items} == {"mixed"}
    assert {item.data(1) for item in items} == {1, 2, 3}
    assert all(item.flags() & item.GraphicsItemFlag.ItemIsSelectable for item in items)
    polygon_item: QGraphicsPathItem = next(
        item for item in items if isinstance(item, QGraphicsPathItem) and item.data(1) == 3
    )
    assert polygon_item.path().fillRule() is Qt.FillRule.OddEvenFill
