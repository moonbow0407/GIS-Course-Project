"""点选/框选 STRtree 索引与全量遍历的等价性测试。"""

from pathlib import Path

from pyproj import CRS
from shapely.affinity import translate as translate_geometry
from shapely.geometry import LineString, Point, Polygon, box

from app.application.gis_application import GisApplication, _geometry_priority
from app.application.ports import VectorReader
from app.domain.feature import Feature
from app.domain.vector_layer import VectorLayer


class _NullReader(VectorReader):
    """不执行任何读取的空读取器，仅为满足应用服务构造签名。"""

    def read(
        self,
        path: Path,
        target_crs: CRS | None = None,
        layer_name: str | None = None,
        source_crs_override: CRS | None = None,
    ) -> VectorLayer:
        """本读取器不应被调用。"""
        raise AssertionError("选择索引测试不应触发数据读取。")


def make_dense_layer(layer_id: str = "dense") -> VectorLayer:
    """创建混合几何类型、含空几何的确定性网格图层。"""
    features: list[Feature] = []
    fid: int = 0
    for row in range(10):
        for column in range(10):
            x: float = column * 3.0
            y: float = row * 3.0
            kind: int = (row + column) % 4
            if kind == 0:
                geometry = Point(x, y)
            elif kind == 1:
                geometry = LineString([(x, y), (x + 1.5, y + 1.0)])
            elif kind == 2:
                geometry = Polygon(
                    [(x, y), (x + 2.0, y), (x + 2.0, y + 2.0), (x, y + 2.0), (x, y)]
                )
            else:
                geometry = Point()  # 空几何必须被两种路径同时忽略。
            features.append(Feature(fid=fid, geometry=geometry, attributes={}))
            fid += 1
    return VectorLayer.create(
        layer_id=layer_id,
        name=layer_id,
        features=tuple(features),
        crs=CRS.from_epsg(4326),
    )


def brute_force_point_candidates(
    layer: VectorLayer,
    point: Point,
    tolerance: float,
) -> list[tuple[float, int]]:
    """按旧全量遍历逻辑计算 (有效距离, fid) 候选并排序。"""
    type_penalty: float = tolerance * 0.01
    hits: list[tuple[float, int]] = []
    for feature in layer.features:
        if feature.geometry.is_empty:
            continue
        distance: float = float(feature.geometry.distance(point))
        if distance <= tolerance:
            effective: float = (
                distance + _geometry_priority(feature.geometry.geom_type) * type_penalty
            )
            hits.append((effective, feature.fid))  # type: ignore[arg-type]
    hits.sort()
    return hits


def brute_force_intersects(layer: VectorLayer, rectangle: Polygon) -> list[int]:
    """按旧全量遍历逻辑返回与矩形相交的要素编号。"""
    return [
        feature.fid  # type: ignore[arg-type]
        for feature in layer.features
        if not feature.geometry.is_empty and feature.geometry.intersects(rectangle)
    ]


def test_identify_features_matches_brute_force_order() -> None:
    """索引路径的候选项和排序应与逐要素遍历完全一致。"""
    layer: VectorLayer = make_dense_layer()
    application: GisApplication = GisApplication(data_reader=_NullReader())
    application.add_layer(layer)
    query_point: Point = Point(6.2, 6.1)
    tolerance: float = 2.5

    result = application.identify_features(query_point, tolerance)

    expected: list[tuple[float, int]] = brute_force_point_candidates(
        layer, query_point, tolerance
    )
    assert [selected.feature.fid for selected in result] == [fid for _, fid in expected]


def test_select_point_matches_brute_force_nearest() -> None:
    """索引路径选中的最近要素应与逐要素遍历一致。"""
    layer: VectorLayer = make_dense_layer()
    application: GisApplication = GisApplication(data_reader=_NullReader())
    application.add_layer(layer)

    for query_point, tolerance in [
        (Point(6.2, 6.1), 2.5),
        (Point(0.0, 0.0), 0.0),  # 零容差：只命中恰好落在查询点上的要素。
        (Point(50.0, 50.0), 1.0),  # 无命中区域。
    ]:
        result = application.select_point(query_point, tolerance)
        expected: list[tuple[float, int]] = brute_force_point_candidates(
            layer, query_point, tolerance
        )
        if expected:
            assert result.features[0].feature.fid == expected[0][1]
        else:
            assert result.features == ()


def test_select_rectangle_matches_brute_force_intersects() -> None:
    """框选结果应与逐要素 intersects 遍历一致，含边界相触要素。"""
    layer: VectorLayer = make_dense_layer()
    application: GisApplication = GisApplication(data_reader=_NullReader())
    application.add_layer(layer)
    # 矩形边界恰好压在网格线上，验证边界相触不被索引遗漏。
    rectangle: Polygon = box(2.5, 2.5, 8.5, 8.5)

    result = application.select_rectangle(rectangle)

    expected: list[int] = brute_force_intersects(layer, rectangle)
    assert [selected.feature.fid for selected in result.features] == expected


def test_selection_index_invalidated_when_layer_object_replaced() -> None:
    """图层编辑生成新对象后，索引必须重建并反映新几何。"""
    layer: VectorLayer = make_dense_layer("movable")
    application: GisApplication = GisApplication(data_reader=_NullReader())
    application.add_layer(layer)
    query_point: Point = Point(6.2, 6.1)
    first = application.select_point(query_point, 2.5)
    assert first.features

    # 以同编号新对象替换图层：几何整体平移 100 单位，原位置不再命中。
    moved: VectorLayer = VectorLayer.create(
        layer_id="movable",
        name="movable",
        features=tuple(
            Feature(
                fid=feature.fid,
                geometry=(
                    feature.geometry
                    if feature.geometry.is_empty
                    else translate_geometry(feature.geometry, 100.0, 100.0)
                ),
                attributes={},
            )
            for feature in layer.features
        ),
        crs=layer.crs,
    )
    application.remove_layer("movable")
    application.add_layer(moved)

    second = application.select_point(query_point, 2.5)

    assert second.features == ()
