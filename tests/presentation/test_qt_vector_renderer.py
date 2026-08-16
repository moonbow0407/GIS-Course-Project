"""Qt 矢量图层渲染器测试。"""

import math
import os

# 必须在导入 Qt 前启用无界面平台，测试才能在没有显示器的环境运行。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pyproj import CRS
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QGraphicsItem, QGraphicsPathItem, QGraphicsScene
from shapely.geometry import LineString, Point, Polygon

from app.application.results import LayerSnapshot
from app.domain.feature import Feature
from app.domain.labeling import LabelClass, LabelingConfig, LabelPlacement
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
    # 选中要素 extra 一个光晕层，含光晕共 4 个图元。
    assert len(items) == 4
    assert {item.data(0) for item in items} == {"mixed"}
    assert {item.data(1) for item in items} == {1, 2, 3}
    # 光晕层不可选择，其余图元均可选择。
    selectable: list[QGraphicsItem] = [
        item
        for item in items
        if item.flags() & item.GraphicsItemFlag.ItemIsSelectable
    ]
    assert len(selectable) == 0
    polygon_item: QGraphicsPathItem = next(
        item for item in items if isinstance(item, QGraphicsPathItem) and item.data(1) == 3
    )
    assert polygon_item.path().fillRule() is Qt.FillRule.OddEvenFill


def test_renderer_simplifies_dense_geometry_for_current_screen_scale() -> None:
    """屏幕分辨率不足以区分的密集顶点应在显示路径中合并。"""
    application: QApplication = QApplication.instance() or QApplication([])
    coordinates = tuple(
        (index * 0.01, math.sin(index / 4.0) * 0.02)
        for index in range(2000)
    )
    layer = VectorLayer.create(
        layer_id="dense-line",
        name="高密度线",
        features=(Feature(fid=1, geometry=LineString(coordinates), attributes={}),),
        crs=CRS.from_epsg(4326),
    )
    scene: QGraphicsScene = QGraphicsScene()
    renderer = QtVectorRenderer()

    items = renderer.render_layer(
        scene,
        LayerSnapshot(layer=layer, visible=True, selected_feature_ids=()),
        z_value=0.0,
        map_units_per_pixel=0.1,
    )

    item = next(item for item in items if isinstance(item, QGraphicsPathItem))
    assert item.path().elementCount() < len(coordinates) / 4
    assert application is not None


def test_renderer_creates_readable_label_items_for_enabled_label_class() -> None:
    """启用标注类后，渲染器应为非空字段创建带屏幕尺寸的标签图元。"""
    application: QApplication = QApplication.instance() or QApplication([])
    layer = VectorLayer.create(
        layer_id="cities",
        name="城市",
        features=(
            Feature(
                fid=1,
                geometry=Point(10, 20),
                attributes={"name": "合肥"},
            ),
            Feature(
                fid=2,
                geometry=Point(30, 40),
                attributes={"name": "南京"},
            ),
        ),
        crs=CRS.from_epsg(4326),
        labeling=LabelingConfig(
            enabled=True,
            classes=(
                LabelClass(
                    name="城市名",
                    field_name="name",
                    placement=LabelPlacement.ABOVE_RIGHT,
                ),
            ),
        ),
    )
    scene: QGraphicsScene = QGraphicsScene()

    items = QtVectorRenderer().render_layer(
        scene,
        LayerSnapshot(layer=layer, visible=True, selected_feature_ids=()),
        z_value=2.0,
        map_units_per_pixel=0.5,
    )

    label_items = [item for item in items if item.data(2) == "label"]
    assert len(label_items) == 2
    assert all(
        item.flags() & item.GraphicsItemFlag.ItemIgnoresTransformations
        for item in label_items
    )
    assert application is not None


def test_renderer_paints_dark_label_text_inside_white_halo() -> None:
    """标注绘制到地图后应保留深色文字，而不是只显示白色光晕。"""
    application: QApplication = QApplication.instance() or QApplication([])
    layer = VectorLayer.create(
        layer_id="label-contrast",
        name="行政区",
        features=(
            Feature(
                fid=1,
                geometry=Point(50, -50),
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
                    text_color="#20354A",
                    halo_color="#FFFFFF",
                    halo_width=3.0,
                ),
            ),
        ),
    )
    scene = QGraphicsScene(0.0, 0.0, 100.0, 100.0)
    QtVectorRenderer().render_layer(
        scene,
        LayerSnapshot(layer=layer, visible=True, selected_feature_ids=()),
        z_value=1.0,
        map_units_per_pixel=1.0,
    )

    image = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill("#A8C8F5")
    painter = QPainter(image)
    scene.render(painter, QRectF(0.0, 0.0, 100.0, 100.0), scene.sceneRect())
    painter.end()

    dark_pixels = 0
    for y in range(35, 65):
        for x in range(35, 65):
            color = image.pixelColor(x, y)
            if color.red() < 100 and color.green() < 120 and color.blue() < 140:
                dark_pixels += 1

    assert dark_pixels >= 10
    assert application is not None


def test_renderer_repairs_light_text_with_light_halo_for_readability() -> None:
    """历史配置出现白字白光晕时，渲染器也应自动恢复可读的深色文字。"""
    application: QApplication = QApplication.instance() or QApplication([])
    layer = VectorLayer.create(
        layer_id="legacy-label-contrast",
        name="行政区",
        features=(
            Feature(
                fid=1,
                geometry=Point(50, -50),
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
    scene = QGraphicsScene(0.0, 0.0, 100.0, 100.0)
    QtVectorRenderer().render_layer(
        scene,
        LayerSnapshot(layer=layer, visible=True, selected_feature_ids=()),
        z_value=1.0,
        map_units_per_pixel=1.0,
    )

    image = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill("#A8C8F5")
    painter = QPainter(image)
    scene.render(painter, QRectF(0.0, 0.0, 100.0, 100.0), scene.sceneRect())
    painter.end()

    dark_pixels = sum(
        1
        for y in range(35, 65)
        for x in range(35, 65)
        if (
            image.pixelColor(x, y).red() < 100
            and image.pixelColor(x, y).green() < 120
            and image.pixelColor(x, y).blue() < 140
        )
    )

    assert dark_pixels >= 10
    assert application is not None


def test_label_stays_near_feature_when_map_units_are_geographic_degrees() -> None:
    """地理坐标系下每像素对应远小于 1 的地图单位时，标注应仍贴在要素锚点附近。

    标注文本尺寸来自屏幕像素。若把像素宽高直接当成场景坐标去偏移，
    在 EPSG:4490 这类以度为单位的显示坐标系中，标签会被整块平移出图斑。
    """
    application: QApplication = QApplication.instance() or QApplication([])
    geometry = Polygon(
        [
            (115.0, 28.0),
            (122.0, 28.0),
            (122.0, 35.0),
            (115.0, 35.0),
            (115.0, 28.0),
        ]
    )
    anchor = geometry.representative_point()
    map_units_per_pixel = 0.02
    layer = VectorLayer.create(
        layer_id="east-china",
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
    scene: QGraphicsScene = QGraphicsScene()

    items = QtVectorRenderer().render_layer(
        scene,
        LayerSnapshot(layer=layer, visible=True, selected_feature_ids=()),
        z_value=1.0,
        map_units_per_pixel=map_units_per_pixel,
    )

    label_item = next(item for item in items if item.data(2) == "label")
    # 场景纵轴向下，地图纵轴向上；居中标注的图元原点应落在锚点附近，
    # 偏移不得超过半个文字盒换算成的地图单位再加少量空隙。
    max_offset = 40.0 * map_units_per_pixel
    assert abs(label_item.pos().x() - float(anchor.x)) < max_offset
    assert abs(label_item.pos().y() - (-float(anchor.y))) < max_offset
    assert application is not None
