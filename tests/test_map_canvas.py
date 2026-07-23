"""地图画布初始状态测试。"""

import os

# 必须在导入 Qt 前启用无界面平台，测试才能在没有显示器的环境运行。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pyproj import CRS
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shapely.geometry import Polygon

from app.application.results import LayerSnapshot, WorkspaceSnapshot
from app.domain.feature import Feature
from app.domain.vector_layer import VectorLayer
from app.presentation.widgets.map_canvas import MapCanvas


def test_canvas_starts_without_mock_map_items() -> None:
    """未加载数据时，画布不应包含演示底图要素。"""
    application = QApplication.instance() or QApplication([])
    canvas = MapCanvas()

    assert application is not None
    assert canvas.scene().items() == []
    assert canvas.scene().items() == []


def test_canvas_can_pan_when_imported_layer_does_not_fill_viewport() -> None:
    """导入图层未占满画幅时，从空白区域拖动也应改变地图视角。"""
    application = QApplication.instance() or QApplication([])
    canvas = MapCanvas()
    canvas.resize(800, 600)
    canvas.show()
    layer = VectorLayer.create(
        layer_id="small-layer",
        name="小范围图层",
        features=(
            Feature(
                fid=1,
                geometry=Polygon([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]),
                attributes={},
            ),
        ),
        crs=CRS.from_epsg(4326),
    )
    canvas.set_snapshot(
        WorkspaceSnapshot(
            layers=(LayerSnapshot(layer=layer, visible=True, selected_feature_ids=()),),
            active_layer_id=layer.layer_id,
            display_crs=layer.crs,
        )
    )
    application.processEvents()

    drag_start = QPoint(canvas.viewport().width() - 24, canvas.viewport().height() // 2)
    drag_end = QPoint(drag_start.x() - 80, drag_start.y())
    initial_center = canvas.mapToScene(canvas.viewport().rect().center())
    QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=drag_start)
    QTest.mouseMove(canvas.viewport(), drag_end, delay=10)
    QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=drag_end)
    moved_center = canvas.mapToScene(canvas.viewport().rect().center())

    assert application is not None
    assert moved_center != initial_center
