"""主窗口图层控件与地图场景生命周期回归测试。"""

import os

# 必须在导入 Qt 前启用无界面平台，测试才能稳定复现原生控件事件。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from affine import Affine
from pyproj import CRS
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem
from shapely.geometry import Point, Polygon

from app.application.gis_application import GisApplication
from app.domain.feature import Feature
from app.domain.map_document import MapDocument
from app.domain.raster_layer import RasterLayer
from app.domain.vector_layer import VectorLayer
from app.infrastructure.file_io.auto_reader import AutoDataReader
from app.infrastructure.file_io.auto_writer import AutoDataWriter
from app.presentation.main_window import MainWindow


def test_raster_visibility_can_be_toggled_twice_with_real_mouse_events() -> None:
    """连续点击栅格复选框时不能在控件回调中删除正在处理事件的节点。"""
    qt_application: QApplication = QApplication.instance() or QApplication([])
    crs: CRS = CRS.from_epsg(4326)
    raster_data = np.ones((1, 2, 2), dtype=np.uint8)
    image_data = np.full((2, 2, 4), 255, dtype=np.uint8)
    layer: RasterLayer = RasterLayer.create(
        layer_id="raster",
        name="测试栅格",
        raster_data=raster_data,
        image_data=image_data,
        valid_mask=np.ones((2, 2), dtype=np.bool_),
        transform=Affine(1, 0, 0, 0, -1, 2),
        crs=crs,
        bounds=(0, 0, 2, 2),
    )
    document: MapDocument = MapDocument()
    document.add_layer(layer)
    window: MainWindow = MainWindow()
    window._application = GisApplication(AutoDataReader(), AutoDataWriter(), document)
    window._refresh_workspace()
    window.resize(800, 600)
    window.show()
    qt_application.processEvents()
    tree: QTreeWidget | None = window.findChild(QTreeWidget, "layerTree")
    assert tree is not None

    _click_visibility_checkbox(tree)
    qt_application.processEvents()
    _click_visibility_checkbox(tree)
    qt_application.processEvents()

    snapshot = window._application.snapshot()
    assert snapshot.layers[0].visible is True
    raster_items = [
        item for item in window._map_canvas.scene().items() if item.data(0) == layer.layer_id
    ]
    assert len(raster_items) == 1
    assert raster_items[0].isVisible() is True
    window.close()


def test_workspace_refresh_preserves_zoom_after_buffer_layer_is_added() -> None:
    """缓冲结果加入工作区时应保留用户视图，避免小缓冲面退化成全图淡点。"""
    qt_application: QApplication = QApplication.instance() or QApplication([])
    crs: CRS = CRS.from_epsg(4549)
    source_layer: VectorLayer = VectorLayer.create(
        layer_id="points",
        name="测试点",
        features=(
            Feature(fid=1, geometry=Point(0, 0), attributes={}),
            Feature(fid=2, geometry=Point(1000, 1000), attributes={}),
        ),
        crs=crs,
    )
    buffer_layer: VectorLayer = VectorLayer.create(
        layer_id="buffers",
        name="测试缓冲",
        features=(
            Feature(
                fid=1,
                geometry=Polygon(
                    [(-10, -10), (10, -10), (10, 10), (-10, 10), (-10, -10)]
                ),
                attributes={},
            ),
        ),
        crs=crs,
    )
    document: MapDocument = MapDocument()
    document.add_layer(source_layer)
    window: MainWindow = MainWindow()
    window._application = GisApplication(AutoDataReader(), AutoDataWriter(), document)
    window.resize(800, 600)
    window.show()
    window._refresh_workspace()
    qt_application.processEvents()
    window._map_canvas.zoom_in()
    window._map_canvas.zoom_in()
    before_refresh = window._map_canvas.capture_view_state()

    document.add_layer(buffer_layer)
    window._refresh_workspace()
    qt_application.processEvents()
    after_refresh = window._map_canvas.capture_view_state()

    assert before_refresh.zoom_percent > 100.0
    assert after_refresh.zoom_percent == before_refresh.zoom_percent
    # QGraphicsView 的滚动条使用整数像素，中心点允许最多约一个屏幕像素的舍入差。
    assert after_refresh.center_x == pytest.approx(before_refresh.center_x, abs=2.0)
    assert after_refresh.center_y == pytest.approx(before_refresh.center_y, abs=2.0)
    window.close()


def _click_visibility_checkbox(tree: QTreeWidget) -> None:
    """使用真实鼠标事件点击当前唯一图层节点的复选框。"""
    item: QTreeWidgetItem | None = tree.topLevelItem(0)
    assert item is not None
    item_rect = tree.visualItemRect(item)
    checkbox_position = QPoint(item_rect.left() + 8, item_rect.center().y())
    QTest.mouseClick(
        tree.viewport(),
        Qt.MouseButton.LeftButton,
        pos=checkbox_position,
    )
