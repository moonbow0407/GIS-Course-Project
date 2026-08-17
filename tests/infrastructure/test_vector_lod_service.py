"""矢量 LOD 生成服务测试。"""

import pytest
from pyproj import CRS
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from app.domain.feature import Feature
from app.domain.lod import LodPyramid
from app.domain.vector_layer import Bounds, VectorLayer
from app.infrastructure.lod.vector_lod_service import VectorLodService


def _mapshaper_available() -> bool:
    """mapshaper 是否可用；不可用时跳过依赖外部命令的测试。"""
    return VectorLodService._locate_mapshaper() is not None


def _adjacent_layer() -> VectorLayer:
    """两个沿 x=1 共享竖直边界的相邻矩形面。

    共享边上人为加入多个共线顶点，简化时应被两侧一致地消除，
    从而验证拓扑保持简化的“无缝隙贴合”特性。
    """
    left = Polygon([
        (0.0, 0.0), (1.0, 0.0), (1.0, 0.5), (1.0, 1.0), (1.0, 1.5),
        (1.0, 2.0), (0.0, 2.0), (0.0, 0.0),
    ])
    right = Polygon([
        (1.0, 0.0), (1.0, 0.5), (1.0, 1.0), (1.0, 1.5), (1.0, 2.0),
        (2.0, 2.0), (2.0, 0.0), (1.0, 0.0),
    ])
    return VectorLayer.create(
        layer_id="adjacent",
        name="相邻面",
        features=(
            Feature(fid=1, geometry=left, attributes={"name": "左"}),
            Feature(fid=2, geometry=right, attributes={"name": "右"}),
        ),
        crs=CRS.from_epsg(4326),
    )


def _shared_edge_vertices(geometry: BaseGeometry, x: float = 1.0) -> tuple[float, ...]:
    """返回多边形外环落在 x 竖线上（共享边）的顶点 y 坐标，升序去重。"""
    if geometry.geom_type != "Polygon":
        return ()
    coords = geometry.exterior.coords
    return tuple(sorted({round(c[1], 9) for c in coords if abs(c[0] - x) < 1e-9}))


def test_default_tolerances_are_strictly_increasing_from_zero() -> None:
    """默认容差表应以 0 开头、严格递增，并按 2 倍步进覆盖 64 倍基准。"""
    bounds: Bounds = (0.0, 0.0, 1024.0, 512.0)
    tolerances = VectorLodService.default_tolerances(bounds)

    assert tolerances[0] == 0.0
    assert all(a < b for a, b in zip(tolerances, tolerances[1:]))
    assert len(tolerances) == 8
    # 正级别按 2 倍递增：base, 2base, 4base, ... 64base。
    positive = tolerances[1:]
    assert [round(value / positive[0], 6) for value in positive] == [
        1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0,
    ]


def test_default_tolerances_collapse_to_original_for_degenerate_bounds() -> None:
    """零范围或单一级别时只返回原始级别。"""
    assert VectorLodService.default_tolerances((0.0, 0.0, 0.0, 0.0)) == (0.0,)
    assert VectorLodService.default_tolerances((0.0, 0.0, 1.0, 1.0), level_count=1) == (0.0,)


@pytest.mark.skipif(not _mapshaper_available(), reason="mapshaper 未安装")
def test_simplify_keeps_adjacent_polygons_sealed() -> None:
    """相邻面简化后共享边仍同线，并集面积不变：紧密贴合、无缝隙。"""
    layer = _adjacent_layer()
    pyramid: LodPyramid = VectorLodService().build_pyramid(layer, tolerances=(0.0, 0.5))

    simplified = next(level for level in pyramid.levels if level.tolerance > 0.0)
    by_fid = {feature.fid: feature.geometry for feature in simplified.features}
    left, right = by_fid[1], by_fid[2]

    # 共享边顶点被两侧一致地简化（共线点被消除且结果相同）。
    assert _shared_edge_vertices(left) == _shared_edge_vertices(right)
    assert len(_shared_edge_vertices(left)) < len(_shared_edge_vertices(layer.features[0].geometry))
    # 无缝隙（面积不缩水）、无重叠（面积不膨胀）。
    assert left.union(right).area == pytest.approx(4.0)


@pytest.mark.skipif(not _mapshaper_available(), reason="mapshaper 未安装")
def test_simplify_aligns_fid_and_attributes_to_original() -> None:
    """简化后要素编号与属性应与原始图层逐一对齐。"""
    layer = _adjacent_layer()
    pyramid: LodPyramid = VectorLodService().build_pyramid(layer, tolerances=(0.0, 0.5))

    simplified = next(level for level in pyramid.levels if level.tolerance > 0.0)
    assert [feature.fid for feature in simplified.features] == [1, 2]
    assert {feature.attributes["name"] for feature in simplified.features} == {"左", "右"}
    # 原始级别应直接复用原图层要素，不产生新的几何对象。
    original = next(level for level in pyramid.levels if level.tolerance == 0.0)
    assert original.features is layer.features
