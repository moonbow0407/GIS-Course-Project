"""地图画布初始状态测试。"""

import os
from dataclasses import replace
from pathlib import Path

# 必须在导入 Qt 前启用无界面平台，测试才能在没有显示器的环境运行。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from affine import Affine
from pyproj import CRS
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QMouseEvent, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGraphicsPathItem
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

from app.application.display_models import RasterDisplayPayload
from app.application.project_models import MapViewState
from app.application.results import LayerSnapshot, WorkspaceSnapshot
from app.domain.feature import Feature
from app.domain.labeling import LabelClass, LabelingConfig, LabelPlacement
from app.domain.raster_layer import RasterLayer
from app.domain.vector_layer import VectorLayer
from app.infrastructure.file_io.auto_reader import AutoDataReader
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


def test_canvas_extent_ignores_hidden_layers() -> None:
    """隐藏大范围矢量图层后，画布全图范围应收缩到可见栅格。"""
    application = QApplication.instance() or QApplication([])
    canvas = MapCanvas()
    canvas.resize(800, 600)
    raster = RasterLayer.create(
        layer_id="small-raster",
        name="小范围栅格",
        raster_data=np.ones((1, 2, 2), dtype=np.uint8),
        image_data=np.full((2, 2, 4), 255, dtype=np.uint8),
        valid_mask=np.ones((2, 2), dtype=np.bool_),
        transform=Affine(10, 0, 100, 0, -10, 120),
        crs=CRS.from_epsg(4326),
        bounds=(100, 100, 120, 120),
    )
    large_vector = VectorLayer.create(
        layer_id="large-vector",
        name="大范围矢量",
        features=(
            Feature(
                fid=1,
                geometry=Polygon(
                    [(-500000, -500000), (2500000, -500000), (2500000, 2200000),
                     (-500000, 2200000), (-500000, -500000)]
                ),
                attributes={},
            ),
        ),
        crs=CRS.from_epsg(4326),
    )

    canvas.set_snapshot(
        WorkspaceSnapshot(
            layers=(
                LayerSnapshot(layer=raster, visible=True, selected_feature_ids=()),
                LayerSnapshot(layer=large_vector, visible=False, selected_feature_ids=()),
            ),
            active_layer_id=raster.layer_id,
            display_crs=raster.crs,
        )
    )

    assert application is not None
    assert canvas._map_scene_rect is not None
    assert canvas._map_scene_rect.width() == pytest.approx(21.0)
    assert canvas._map_scene_rect.height() == pytest.approx(21.0)


def test_update_raster_viewport_replaces_only_target_raster_item() -> None:
    """金字塔载荷应原位替换栅格图元，不改变视图范围。"""
    application = QApplication.instance() or QApplication([])
    canvas = MapCanvas()
    raster = RasterLayer.create(
        layer_id="raster",
        name="栅格",
        raster_data=np.ones((1, 4, 4), dtype=np.uint8),
        image_data=np.full((4, 4, 4), 255, dtype=np.uint8),
        valid_mask=np.ones((4, 4), dtype=np.bool_),
        transform=Affine(1, 0, 0, 0, -1, 4),
        crs=CRS.from_epsg(3857),
        bounds=(0, 0, 4, 4),
    )
    canvas.set_snapshot(
        WorkspaceSnapshot(
            layers=(LayerSnapshot(raster, True, ()),),
            active_layer_id=raster.layer_id,
            display_crs=raster.crs,
        )
    )
    old_item = canvas._layer_items[raster.layer_id][0]
    old_scene_rect = QRectF(canvas.sceneRect())
    payload = RasterDisplayPayload(
        layer_id=raster.layer_id,
        image_data=np.full((8, 8, 4), 255, dtype=np.uint8),
        transform=Affine(0.25, 0, 1, 0, -0.25, 3),
        bounds=(1, 1, 3, 3),
    )

    canvas.update_raster_viewport(payload)

    assert application is not None
    assert canvas._layer_items[raster.layer_id][0] is not old_item
    assert canvas.sceneRect() == old_scene_rect
    assert canvas._last_snapshot is not None
    assert canvas._last_snapshot.layers[0].display_payload is payload


def test_transparent_raster_viewport_does_not_replace_existing_preview() -> None:
    """NoData 视口不能覆盖仍可用的栅格首屏预览。"""
    application = QApplication.instance() or QApplication([])
    canvas = MapCanvas()
    raster = RasterLayer.create(
        layer_id="raster",
        name="栅格",
        raster_data=np.ones((1, 4, 4), dtype=np.uint8),
        image_data=np.full((4, 4, 4), 255, dtype=np.uint8),
        valid_mask=np.ones((4, 4), dtype=np.bool_),
        transform=Affine(1, 0, 0, 0, -1, 4),
        crs=CRS.from_epsg(3857),
        bounds=(0, 0, 4, 4),
    )
    canvas.set_snapshot(
        WorkspaceSnapshot(
            layers=(LayerSnapshot(raster, True, ()),),
            active_layer_id=raster.layer_id,
            display_crs=raster.crs,
        )
    )
    old_item = canvas._layer_items[raster.layer_id][0]
    old_payload = canvas._last_snapshot.layers[0].display_payload
    transparent_payload = RasterDisplayPayload(
        layer_id=raster.layer_id,
        image_data=np.zeros((8, 8, 4), dtype=np.uint8),
        transform=Affine(0.25, 0, 1, 0, -0.25, 3),
        bounds=(1, 1, 3, 3),
    )

    canvas.update_raster_viewport(transparent_payload)

    assert application is not None
    assert canvas._layer_items[raster.layer_id][0] is old_item
    assert canvas._last_snapshot is not None
    assert canvas._last_snapshot.layers[0].display_payload is old_payload


def test_zoom_to_layer_forces_viewport_refresh() -> None:
    """图层定位后应强制刷新视口，即使缩放比例没有变化。"""
    application = QApplication.instance() or QApplication([])
    canvas = MapCanvas()
    canvas.resize(800, 600)
    canvas.show()
    raster = RasterLayer.create(
        layer_id="raster",
        name="栅格",
        raster_data=np.ones((1, 4, 4), dtype=np.uint8),
        image_data=np.full((4, 4, 4), 255, dtype=np.uint8),
        valid_mask=np.ones((4, 4), dtype=np.bool_),
        transform=Affine(1, 0, 0, 0, -1, 4),
        crs=CRS.from_epsg(3857),
        bounds=(0, 0, 4, 4),
    )
    canvas.set_snapshot(
        WorkspaceSnapshot(
            layers=(LayerSnapshot(raster, True, ()),),
            active_layer_id=raster.layer_id,
            display_crs=raster.crs,
        )
    )
    canvas._viewport_timer.stop()
    canvas._last_viewport_key = (1.0,)

    canvas.zoom_to_layer(raster.bounds)

    assert application is not None
    assert canvas._viewport_timer.isActive()
    assert canvas._last_viewport_key is None


def test_scale_hidden_layers_are_excluded_from_query_and_snapping() -> None:
    """超出显示比例范围的图层不应参与画布查询或顶点捕捉。"""
    application = QApplication.instance() or QApplication([])
    visible_layer = VectorLayer.create(
        layer_id="visible-layer",
        name="可见图层",
        features=(Feature(fid=1, geometry=Point(0, 0), attributes={}),),
        crs=CRS.from_epsg(4326),
    )
    scale_hidden_layer = VectorLayer.create(
        layer_id="scale-hidden-layer",
        name="比例隐藏图层",
        features=(Feature(fid=2, geometry=Point(100, 100), attributes={}),),
        crs=CRS.from_epsg(4326),
    )
    canvas = MapCanvas()
    canvas.set_snapshot(
        WorkspaceSnapshot(
            layers=(
                LayerSnapshot(
                    layer=visible_layer,
                    visible=True,
                    selected_feature_ids=(),
                ),
                LayerSnapshot(
                    layer=scale_hidden_layer,
                    visible=True,
                    selected_feature_ids=(),
                    min_scale_percent=200.0,
                ),
            ),
            active_layer_id=visible_layer.layer_id,
            display_crs=visible_layer.crs,
        )
    )

    assert application is not None
    assert canvas.queryable_layer_ids() == (visible_layer.layer_id,)
    # 捕捉引擎仅索引可见且未超比例范围的图层；尺度受限图层不在索引中。
    assert canvas.snap_engine.indexed_layer_ids == frozenset({visible_layer.layer_id})


def test_snapping_engine_accepts_3d_geometry() -> None:
    """带 Z 坐标（三维）的图层不应导致捕捉索引构建或查询崩溃。"""
    application = QApplication.instance() or QApplication([])
    layer = VectorLayer.create(
        layer_id="z-layer",
        name="三维线图层",
        features=(
            Feature(
                fid=1,
                geometry=LineString([(0, 0, 10), (1, 1, 10), (2, 0, 10)]),
                attributes={},
            ),
        ),
        crs=CRS.from_epsg(4326),
    )
    canvas = MapCanvas()
    canvas.set_snapshot(
        WorkspaceSnapshot(
            layers=(LayerSnapshot(layer=layer, visible=True, selected_feature_ids=()),),
            active_layer_id=layer.layer_id,
            display_crs=layer.crs,
        )
    )

    assert application is not None
    # 索引构建成功，3D 线的顶点可按平面坐标参与捕捉。
    assert layer.layer_id in canvas.snap_engine.indexed_layer_ids
    canvas.snap_engine.enabled = True
    snap = canvas.snap_engine.find_snap(Point(1, 1), map_units_per_pixel=1.0)
    assert snap is not None
    # 捕捉位置回到二维平面坐标，不含 Z 分量。
    assert (snap.map_point.x, snap.map_point.y) == (1.0, 1.0)


def test_canvas_full_extent_uses_relative_margin_for_small_geographic_bounds() -> None:
    """小范围经纬度数据全图显示时，应按包络比例留白并充分利用视口。"""
    application: QApplication = QApplication.instance() or QApplication([])
    canvas = MapCanvas()
    canvas.resize(1680, 900)
    canvas.show()
    minimum_x, minimum_y = 116.3620, 39.9745
    maximum_x, maximum_y = 116.3941, 39.9970
    layer = VectorLayer.create(
        layer_id="small-geographic",
        name="小范围经纬度图层",
        features=(
            Feature(
                fid=1,
                geometry=Polygon(
                    [
                        (minimum_x, minimum_y),
                        (maximum_x, minimum_y),
                        (maximum_x, maximum_y),
                        (minimum_x, maximum_y),
                        (minimum_x, minimum_y),
                    ]
                ),
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
    canvas.zoom_to_full_extent()
    application.processEvents()

    assert canvas._map_scene_rect is not None
    assert canvas._map_scene_rect.width() == pytest.approx(
        (maximum_x - minimum_x) * 1.05
    )
    assert canvas._map_scene_rect.height() == pytest.approx(
        (maximum_y - minimum_y) * 1.05
    )
    data_scene_rect = QRectF(
        minimum_x,
        -maximum_y,
        maximum_x - minimum_x,
        maximum_y - minimum_y,
    )
    mapped_data_rect = canvas.mapFromScene(data_scene_rect).boundingRect()
    height_fill_ratio = mapped_data_rect.height() / canvas.viewport().height()
    assert height_fill_ratio >= 0.9


def test_first_sample_data_load_keeps_point_symbols_at_screen_size() -> None:
    """首次加载小范围经纬度示例数据时，点符号不能放大到覆盖整个画布。"""
    application: QApplication = QApplication.instance() or QApplication([])
    canvas = MapCanvas()
    canvas.resize(1680, 900)
    canvas.show()
    data_directory = Path(__file__).parents[1] / "sample_data" / "postgis"
    reader = AutoDataReader()
    layers = tuple(
        reader.read(data_directory / filename)
        for filename in (
            "management_zones.geojson",
            "monitoring_sites.geojson",
            "survey_routes.geojson",
        )
    )
    point_layer = layers[1]
    canvas.set_snapshot(
        WorkspaceSnapshot(
            layers=tuple(
                LayerSnapshot(layer=layer, visible=True, selected_feature_ids=())
                for layer in layers
            ),
            active_layer_id=point_layer.layer_id,
            display_crs=point_layer.crs,
        )
    )
    application.processEvents()

    assert canvas._map_scene_rect is not None
    point_items = [
        item
        for item in canvas.scene().items()
        if isinstance(item, QGraphicsPathItem) and item.data(0) == point_layer.layer_id
    ]
    assert point_items
    assert max(item.path().boundingRect().width() for item in point_items) < (
        canvas._map_scene_rect.width() * 0.1
    )


def test_first_small_geographic_point_layer_uses_fitted_view_scale() -> None:
    """全新画布只加载一个小范围点图层时，也应按适配后的视图计算符号尺寸。"""
    application: QApplication = QApplication.instance() or QApplication([])
    canvas = MapCanvas()
    canvas.resize(1680, 900)
    canvas.show()
    point_layer = VectorLayer.create(
        layer_id="small-points",
        name="小范围点图层",
        features=(
            Feature(fid=1, geometry=Point(116.36, 39.98), attributes={}),
            Feature(fid=2, geometry=Point(116.39, 39.99), attributes={}),
        ),
        crs=CRS.from_epsg(4326),
    )
    canvas.set_snapshot(
        WorkspaceSnapshot(
            layers=(
                LayerSnapshot(
                    layer=point_layer,
                    visible=True,
                    selected_feature_ids=(),
                ),
            ),
            active_layer_id=point_layer.layer_id,
            display_crs=point_layer.crs,
        )
    )
    application.processEvents()

    assert canvas._map_scene_rect is not None
    point_items = [
        item
        for item in canvas.scene().items()
        if isinstance(item, QGraphicsPathItem) and item.data(0) == point_layer.layer_id
    ]
    assert max(item.path().boundingRect().width() for item in point_items) < (
        canvas._map_scene_rect.width() * 0.1
    )


def test_restoring_view_recalculates_point_symbol_scale() -> None:
    """恢复工程视图后，点符号应按恢复后的屏幕比例重新计算尺寸。"""
    application: QApplication = QApplication.instance() or QApplication([])
    canvas = MapCanvas()
    canvas.resize(800, 600)
    canvas.show()
    point_layer = VectorLayer.create(
        layer_id="restored-points",
        name="恢复视图点图层",
        features=(
            Feature(fid=1, geometry=Point(0, 0), attributes={}),
            Feature(fid=2, geometry=Point(10, 10), attributes={}),
        ),
        crs=CRS.from_epsg(4326),
    )
    snapshot = WorkspaceSnapshot(
        layers=(LayerSnapshot(layer=point_layer, visible=True, selected_feature_ids=()),),
        active_layer_id=point_layer.layer_id,
        display_crs=point_layer.crs,
    )
    canvas.set_snapshot(snapshot)
    # 恢复中心放在要素位置：深缩放视野外的要素会被视域裁剪，不生成图元。
    canvas.restore_view_state(
        MapViewState(center_x=0.0, center_y=0.0, zoom_percent=5000.0)
    )
    application.processEvents()

    visible_rect = canvas._visible_scene_rect()
    expected_units_per_pixel = max(
        visible_rect.width() / canvas.viewport().width(),
        visible_rect.height() / canvas.viewport().height(),
    )
    assert canvas._map_units_per_pixel == pytest.approx(
        expected_units_per_pixel, rel=1e-4
    )
    point_items = [
        item
        for item in canvas.scene().items()
        if isinstance(item, QGraphicsPathItem) and item.data(0) == point_layer.layer_id
    ]
    assert point_items
    assert {item.data(1) for item in point_items} == {1}
    assert max(item.path().boundingRect().width() for item in point_items) < (
        visible_rect.width() * 0.25
    )


def test_canvas_extent_keeps_degenerate_bounds_navigable() -> None:
    """单点和单轴范围应使用独立兜底，不能生成无法适配的空矩形。"""
    origin_rect = MapCanvas._scene_rect_from_bounds((0.0, 0.0, 0.0, 0.0))
    point_rect = MapCanvas._scene_rect_from_bounds((116.38, 39.98, 116.38, 39.98))
    horizontal_line_rect = MapCanvas._scene_rect_from_bounds((10.0, 20.0, 30.0, 20.0))

    assert origin_rect.width() == pytest.approx(2.1)
    assert origin_rect.height() == pytest.approx(2.1)
    assert point_rect.width() > 0.0
    assert point_rect.height() > 0.0
    assert horizontal_line_rect.width() > 0.0
    assert horizontal_line_rect.height() > 0.0


def test_canvas_can_zoom_to_one_layer_extent() -> None:
    """图层级全图显示应定位到指定范围，而不是全部可见图层的联合范围。"""
    application: QApplication = QApplication.instance() or QApplication([])
    canvas = MapCanvas()
    canvas.resize(800, 600)
    canvas.show()
    small_layer = VectorLayer.create(
        layer_id="small",
        name="小范围图层",
        features=(
            Feature(
                fid=1,
                geometry=Polygon([(10, 20), (20, 20), (20, 30), (10, 30), (10, 20)]),
                attributes={},
            ),
        ),
        crs=CRS.from_epsg(4326),
    )
    large_layer = VectorLayer.create(
        layer_id="large",
        name="大范围图层",
        features=(
            Feature(
                fid=1,
                geometry=Polygon([(-100, -100), (200, -100), (200, 200), (-100, 200)]),
                attributes={},
            ),
        ),
        crs=CRS.from_epsg(4326),
    )
    canvas.set_snapshot(
        WorkspaceSnapshot(
            layers=(
                LayerSnapshot(layer=large_layer, visible=True, selected_feature_ids=()),
                LayerSnapshot(layer=small_layer, visible=True, selected_feature_ids=()),
            ),
            active_layer_id=small_layer.layer_id,
            display_crs=small_layer.crs,
        )
    )
    application.processEvents()

    canvas.zoom_to_layer(small_layer.bounds)
    view_state = canvas.capture_view_state()

    assert view_state.center_x == pytest.approx(15.0, abs=0.5)
    assert view_state.center_y == pytest.approx(25.0, abs=0.5)
    assert view_state.zoom_percent > 100.0


def test_canvas_queries_recover_after_geometry_edit_refresh() -> None:
    """几何编辑提交并刷新场景后，点选和框选查询仍应接收左键事件。"""
    application: QApplication = QApplication.instance() or QApplication([])
    canvas = MapCanvas()
    canvas.resize(800, 600)
    canvas.show()
    feature = Feature(
        fid=1,
        geometry=Polygon([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]),
        attributes={},
    )
    layer = VectorLayer.create(
        layer_id="editable-layer",
        name="可编辑图层",
        features=(feature,),
        crs=CRS.from_epsg(4326),
    )
    snapshot = WorkspaceSnapshot(
        layers=(LayerSnapshot(layer=layer, visible=True, selected_feature_ids=(1,)),),
        active_layer_id=layer.layer_id,
        display_crs=layer.crs,
    )
    canvas.set_snapshot(snapshot)
    application.processEvents()

    canvas.set_vertex_edit_tool(feature.geometry, layer.layer_id, feature.fid)
    # 几何提交会先刷新工作区；刷新会清空场景中的旧顶点标记。
    canvas.set_snapshot(snapshot)
    canvas.set_pan_tool()

    queried_points: list[object] = []
    canvas.point_queried.connect(lambda point, _add: queried_points.append(point))
    canvas.set_point_query_tool()
    center = canvas.viewport().rect().center()
    QTest.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=center)

    queried_rectangles: list[object] = []
    canvas.rectangle_queried.connect(
        lambda polygon, _add: queried_rectangles.append(polygon)
    )
    canvas.set_rectangle_query_tool()
    drag_start = QPoint(center.x() - 30, center.y() - 30)
    drag_end = QPoint(center.x() + 30, center.y() + 30)
    QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=drag_start)
    QTest.mouseMove(canvas.viewport(), drag_end, delay=10)
    QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=drag_end)

    assert len(queried_points) == 1
    assert len(queried_rectangles) == 1


def test_line_vertex_edit_accepts_left_click_on_blank_canvas() -> None:
    """线顶点编辑时左键点击空白处应只清除顶点选择，不应中断鼠标交互。"""
    application: QApplication = QApplication.instance() or QApplication([])
    canvas = MapCanvas()
    canvas.resize(800, 600)
    canvas.show()
    canvas.set_vertex_edit_tool(LineString([(0, 0), (10, 0)]), "line-layer", 1)
    application.processEvents()
    blank_position = QPoint(700, 500)
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(blank_position),
        QPointF(canvas.viewport().mapToGlobal(blank_position)),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    canvas.mousePressEvent(event)

    assert event.isAccepted()
    assert canvas._vertex_edit_active is True
    assert canvas._vertex_drag_idx == -1


def test_map_canvas_paints_label_text_on_real_viewport() -> None:
    """真实 QGraphicsView 视口中，白字白光晕配置也必须显示深色文字。"""
    application = QApplication.instance() or QApplication([])
    canvas = MapCanvas()
    canvas.resize(800, 600)
    canvas.show()
    layer = VectorLayer.create(
        layer_id="viewport-label-contrast",
        name="行政区",
        features=(
            Feature(
                fid=1,
                geometry=Polygon(
                    [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
                ),
                attributes={"name": "合肥"},
            ),
        ),
        crs=CRS.from_epsg(4326),
        labeling=LabelingConfig(
            enabled=True,
            classes=(
                LabelClass(
                    name="名称",
                    field_name="name",
                    placement=LabelPlacement.CENTER,
                    font_size=18.0,
                    text_color="#FFFFFF",
                    halo_color="#FFFFFF",
                    halo_width=3.0,
                ),
            ),
        ),
    )
    canvas.set_snapshot(
        WorkspaceSnapshot(
            layers=(LayerSnapshot(layer=layer, visible=True, selected_feature_ids=()),),
            active_layer_id=layer.layer_id,
            display_crs=layer.crs,
        )
    )
    application.processEvents()

    label_items = [
        item for item in canvas.scene().items() if item.data(2) == "label"
    ]
    assert len(label_items) == 1
    image = QImage(canvas.size(), QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    canvas.render(painter)
    painter.end()
    label_rect = canvas.mapFromScene(label_items[0].sceneBoundingRect()).boundingRect()
    dark_pixels = 0
    for y in range(max(0, label_rect.top()), min(image.height(), label_rect.bottom() + 1)):
        for x in range(max(0, label_rect.left()), min(image.width(), label_rect.right() + 1)):
            color = image.pixelColor(x, y)
            if color.red() < 100 and color.green() < 120 and color.blue() < 140:
                dark_pixels += 1

    assert dark_pixels >= 10
    assert application is not None


def test_map_canvas_declutters_labels_when_zoomed_out() -> None:
    """缩小到多个标签争用同一屏幕区域时，应自动避让而不是叠成一团。"""
    application = QApplication.instance() or QApplication([])
    canvas = MapCanvas()
    canvas.resize(800, 600)
    canvas.show()
    extent_layer = VectorLayer.create(
        layer_id="zoom-extent",
        name="范围",
        features=(
            Feature(
                fid=1,
                geometry=Polygon(
                    [(-5000, -5000), (5000, -5000), (5000, 5000), (-5000, 5000), (-5000, -5000)]
                ),
                attributes={},
            ),
        ),
        crs=CRS.from_epsg(4326),
    )
    labeled_layer = VectorLayer.create(
        layer_id="zoom-labels",
        name="省份",
        features=tuple(
            Feature(
                fid=index,
                geometry=Point(index * 16.0, 0.0),
                attributes={"name": name},
            )
            for index, name in enumerate(("山东", "安徽", "江苏", "浙江"), start=1)
        ),
        crs=CRS.from_epsg(4326),
        labeling=LabelingConfig(
            enabled=True,
            classes=(
                LabelClass(
                    name="省名",
                    field_name="name",
                    placement=LabelPlacement.CENTER,
                    font_size=18.0,
                ),
            ),
        ),
    )
    canvas.set_snapshot(
        WorkspaceSnapshot(
            layers=(
                LayerSnapshot(layer=extent_layer, visible=True, selected_feature_ids=()),
                LayerSnapshot(layer=labeled_layer, visible=True, selected_feature_ids=()),
            ),
            active_layer_id=labeled_layer.layer_id,
            display_crs=labeled_layer.crs,
        )
    )
    application.processEvents()

    label_items = [
        item
        for item in canvas.scene().items()
        if item.data(0) == labeled_layer.layer_id and item.data(2) == "label"
    ]
    assert 1 <= len(label_items) < 4
    screen_rects = [
        canvas.mapFromScene(item.sceneBoundingRect()).boundingRect()
        for item in label_items
    ]
    overlapping_pairs = [
        (left, right)
        for index, left in enumerate(screen_rects)
        for right in screen_rects[index + 1 :]
        if left.intersects(right)
    ]

    assert not overlapping_pairs
    assert application is not None


def test_map_canvas_places_labels_on_polygons_in_geographic_display_crs() -> None:
    """显示坐标系为经纬度时，标注屏幕位置应落在对应面要素范围内。"""
    application = QApplication.instance() or QApplication([])
    canvas = MapCanvas()
    canvas.resize(800, 600)
    canvas.show()
    geometry = Polygon(
        [
            (115.0, 28.0),
            (122.0, 28.0),
            (122.0, 35.0),
            (115.0, 35.0),
            (115.0, 28.0),
        ]
    )
    layer = VectorLayer.create(
        layer_id="east-china-4490",
        name="华东行政区",
        features=(
            Feature(
                fid=1,
                geometry=geometry,
                attributes={"name": "安徽"},
            ),
        ),
        crs=CRS.from_epsg(4490),
        labeling=LabelingConfig(
            enabled=True,
            classes=(
                LabelClass(
                    name="省名",
                    field_name="name",
                    placement=LabelPlacement.CENTER,
                    font_size=18.0,
                ),
            ),
        ),
    )
    canvas.set_snapshot(
        WorkspaceSnapshot(
            layers=(LayerSnapshot(layer=layer, visible=True, selected_feature_ids=()),),
            active_layer_id=layer.layer_id,
            display_crs=layer.crs,
        )
    )
    application.processEvents()

    polygon_item = next(
        item
        for item in canvas.scene().items()
        if isinstance(item, QGraphicsPathItem) and item.data(1) == 1
    )
    label_item = next(
        item for item in canvas.scene().items() if item.data(2) == "label"
    )
    polygon_rect = canvas.mapFromScene(polygon_item.sceneBoundingRect()).boundingRect()
    # ItemIgnoresTransformations 下 sceneBoundingRect 会混用像素与地图单位，
    # 不能用来判断视觉位置；标注原点才是贴在要素上的场景锚点。
    label_origin = canvas.mapFromScene(label_item.pos())

    assert polygon_rect.contains(label_origin)
    assert application is not None


def test_measurement_tools_emit_temporary_geometry_without_editing_layers() -> None:
    """长度/面积工具应只发出临时几何，不触发要素写回信号。"""
    application = QApplication.instance() or QApplication([])
    canvas = MapCanvas()
    measurements: list[tuple[str, BaseGeometry]] = []
    canvas.measurement_completed.connect(
        lambda kind, geometry: measurements.append((kind, geometry))
    )

    canvas.set_measure_length_tool()
    canvas._emit_completed_sketch(LineString([(0.0, 0.0), (10.0, 0.0)]))
    canvas.set_measure_area_tool()
    canvas._emit_completed_sketch(
        Polygon([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 0.0)])
    )

    assert [kind for kind, _geometry in measurements] == ["length", "area"]
    assert canvas._digitize_mode == "none"
    assert application is not None


def test_set_snapshot_reuses_unchanged_layer_items() -> None:
    """未变化图层的图元应在快照刷新时原样复用，不做重建。"""
    application: QApplication = QApplication.instance() or QApplication([])
    canvas = MapCanvas()
    canvas.resize(800, 600)
    canvas.show()
    first = VectorLayer.create(
        layer_id="first",
        name="第一层",
        features=(
            Feature(fid=1, geometry=Point(0, 0), attributes={}),
            Feature(fid=2, geometry=Point(1, 1), attributes={}),
        ),
        crs=CRS.from_epsg(4326),
    )
    second = VectorLayer.create(
        layer_id="second",
        name="第二层",
        features=(Feature(fid=1, geometry=Point(2, 2), attributes={}),),
        crs=CRS.from_epsg(4326),
    )
    first_snapshot = LayerSnapshot(layer=first, visible=True, selected_feature_ids=())
    second_snapshot = LayerSnapshot(layer=second, visible=True, selected_feature_ids=())
    canvas.set_snapshot(
        WorkspaceSnapshot(
            layers=(first_snapshot, second_snapshot),
            active_layer_id=first.layer_id,
            display_crs=first.crs,
        )
    )
    first_items = list(canvas._layer_items[first.layer_id])
    second_items = list(canvas._layer_items[second.layer_id])
    assert first_items and second_items

    # 模拟一次普通刷新：外层快照对象全新，但图层与载荷身份不变。
    canvas.set_snapshot(
        WorkspaceSnapshot(
            layers=(replace(first_snapshot), replace(second_snapshot)),
            active_layer_id=first.layer_id,
            display_crs=first.crs,
        )
    )

    assert canvas._layer_items[first.layer_id] == first_items
    assert canvas._layer_items[second.layer_id] == second_items
    assert application is not None
    canvas.close()


def test_set_snapshot_rebuilds_layer_when_selection_changes() -> None:
    """选中集变化只重建该图层，其余图层继续复用旧图元。"""
    application: QApplication = QApplication.instance() or QApplication([])
    canvas = MapCanvas()
    canvas.resize(800, 600)
    canvas.show()
    first = VectorLayer.create(
        layer_id="first",
        name="第一层",
        features=(Feature(fid=1, geometry=Point(0, 0), attributes={}),),
        crs=CRS.from_epsg(4326),
    )
    second = VectorLayer.create(
        layer_id="second",
        name="第二层",
        features=(Feature(fid=1, geometry=Point(2, 2), attributes={}),),
        crs=CRS.from_epsg(4326),
    )
    first_snapshot = LayerSnapshot(layer=first, visible=True, selected_feature_ids=())
    second_snapshot = LayerSnapshot(layer=second, visible=True, selected_feature_ids=())
    canvas.set_snapshot(
        WorkspaceSnapshot(
            layers=(first_snapshot, second_snapshot),
            active_layer_id=first.layer_id,
            display_crs=first.crs,
        )
    )
    first_items = list(canvas._layer_items[first.layer_id])
    second_items = list(canvas._layer_items[second.layer_id])

    canvas.set_snapshot(
        WorkspaceSnapshot(
            layers=(
                replace(first_snapshot, selected_feature_ids=(1,)),
                replace(second_snapshot),
            ),
            active_layer_id=first.layer_id,
            display_crs=first.crs,
        )
    )

    # 选中要素带光晕层，重建后图元对象应全部更换。
    rebuilt_items = canvas._layer_items[first.layer_id]
    assert rebuilt_items != first_items
    assert not any(old is new for old in first_items for new in rebuilt_items)
    # 未变化图层仍复用旧图元。
    assert canvas._layer_items[second.layer_id] == second_items
    assert application is not None
    canvas.close()


def test_set_snapshot_removes_items_of_deleted_layers() -> None:
    """快照中消失的图层，其图元应从场景中移除。"""
    application: QApplication = QApplication.instance() or QApplication([])
    canvas = MapCanvas()
    canvas.resize(800, 600)
    canvas.show()
    first = VectorLayer.create(
        layer_id="first",
        name="第一层",
        features=(Feature(fid=1, geometry=Point(0, 0), attributes={}),),
        crs=CRS.from_epsg(4326),
    )
    second = VectorLayer.create(
        layer_id="second",
        name="第二层",
        features=(Feature(fid=1, geometry=Point(2, 2), attributes={}),),
        crs=CRS.from_epsg(4326),
    )
    first_snapshot = LayerSnapshot(layer=first, visible=True, selected_feature_ids=())
    second_snapshot = LayerSnapshot(layer=second, visible=True, selected_feature_ids=())
    canvas.set_snapshot(
        WorkspaceSnapshot(
            layers=(first_snapshot, second_snapshot),
            active_layer_id=first.layer_id,
            display_crs=first.crs,
        )
    )

    canvas.set_snapshot(
        WorkspaceSnapshot(
            layers=(replace(second_snapshot),),
            active_layer_id=second.layer_id,
            display_crs=second.crs,
        )
    )

    assert first.layer_id not in canvas._layer_items
    assert [
        item for item in canvas.scene().items() if item.data(0) == first.layer_id
    ] == []
    assert canvas._layer_items[second.layer_id]
    assert application is not None
    canvas.close()


def test_refresh_keeps_raster_viewport_item_instead_of_downgrading() -> None:
    """工作区刷新不得把画布上的高清视口图元替换回低分辨率预览。

    应用服务的显示缓存对同一图层返回同一预览载荷对象；画布据此在
    渲染签名中复用现有图元，避免"预览降级再等待高清"的往返闪烁。
    """
    application: QApplication = QApplication.instance() or QApplication([])
    canvas = MapCanvas()
    raster = RasterLayer.create(
        layer_id="raster",
        name="栅格",
        raster_data=np.ones((1, 4, 4), dtype=np.uint8),
        image_data=np.full((4, 4, 4), 255, dtype=np.uint8),
        valid_mask=np.ones((4, 4), dtype=np.bool_),
        transform=Affine(1, 0, 0, 0, -1, 4),
        crs=CRS.from_epsg(3857),
        bounds=(0, 0, 4, 4),
    )
    preview_payload = RasterDisplayPayload(
        layer_id=raster.layer_id,
        image_data=np.full((4, 4, 4), 255, dtype=np.uint8),
        transform=Affine(1, 0, 0, 0, -1, 4),
        bounds=(0, 0, 4, 4),
    )
    canvas.set_snapshot(
        WorkspaceSnapshot(
            layers=(
                LayerSnapshot(
                    layer=raster,
                    visible=True,
                    selected_feature_ids=(),
                    display_payload=preview_payload,
                ),
            ),
            active_layer_id=raster.layer_id,
            display_crs=raster.crs,
        )
    )
    canvas.update_raster_viewport(
        RasterDisplayPayload(
            layer_id=raster.layer_id,
            image_data=np.full((8, 8, 4), 200, dtype=np.uint8),
            transform=Affine(0.25, 0, 1, 0, -0.25, 3),
            bounds=(1, 1, 3, 3),
        )
    )
    hi_res_item = canvas._layer_items[raster.layer_id][0]

    # 模拟 _refresh_workspace：全新外层快照，预览载荷仍是缓存中的同一对象。
    canvas.set_snapshot(
        WorkspaceSnapshot(
            layers=(
                LayerSnapshot(
                    layer=raster,
                    visible=True,
                    selected_feature_ids=(),
                    display_payload=preview_payload,
                ),
            ),
            active_layer_id=raster.layer_id,
            display_crs=raster.crs,
        )
    )

    assert application is not None
    assert canvas._layer_items[raster.layer_id][0] is hi_res_item
