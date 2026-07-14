"""矢量图层领域模型测试。"""

from datetime import date
from pathlib import Path

import pytest
from pyproj import CRS
from shapely.geometry import LineString, Point, Polygon

from app.domain.feature import Feature
from app.domain.layer_style import GeometryFamily, LayerStyle
from app.domain.vector_layer import VectorLayer


def test_feature_copies_attributes_to_prevent_external_mutation() -> None:
    """要素创建后不应受到外部属性字典继续修改的影响。"""
    source_attributes: dict[str, str | int] = {"名称": "学校", "等级": 1}
    feature: Feature = Feature(
        fid=1,
        geometry=Point(118.78, 32.04),
        attributes=source_attributes,
    )

    source_attributes["名称"] = "医院"

    assert feature.attributes == {"名称": "学校", "等级": 1}


def test_vector_layer_rejects_empty_feature_collection() -> None:
    """矢量图层没有任何要素时应拒绝创建。"""
    with pytest.raises(ValueError, match="至少包含一个要素"):
        VectorLayer.create(name="空图层", features=(), crs=None)


def test_vector_layer_rejects_collection_without_usable_geometry() -> None:
    """全部几何为空时应拒绝创建矢量图层。"""
    feature: Feature = Feature(fid=1, geometry=Point(), attributes={})

    with pytest.raises(ValueError, match="可用几何"):
        VectorLayer.create(name="空几何图层", features=(feature,), crs=None)


def test_vector_layer_derives_bounds_and_geometry_family() -> None:
    """矢量图层应根据有效几何计算范围和几何类别。"""
    features: tuple[Feature, ...] = (
        Feature(fid=1, geometry=Point(10, 20), attributes={"日期": date(2026, 7, 14)}),
        Feature(fid=2, geometry=Point(30, 40), attributes={}),
    )
    layer: VectorLayer = VectorLayer.create(
        name="监测点",
        features=features,
        crs=CRS.from_epsg(4326),
        source_path=Path("monitoring.geojson"),
    )

    assert layer.bounds == (10.0, 20.0, 30.0, 40.0)
    assert layer.geometry_family is GeometryFamily.POINT
    assert layer.crs == CRS.from_epsg(4326)
    assert layer.source_path == Path("monitoring.geojson")


def test_vector_layer_reports_mixed_geometry_family() -> None:
    """包含多类几何的图层应明确标记为混合类型。"""
    features: tuple[Feature, ...] = (
        Feature(fid=1, geometry=Point(0, 0), attributes={}),
        Feature(fid=2, geometry=LineString([(0, 0), (1, 1)]), attributes={}),
        Feature(fid=3, geometry=Polygon([(0, 0), (2, 0), (2, 2), (0, 0)]), attributes={}),
    )
    layer: VectorLayer = VectorLayer.create(name="混合图层", features=features, crs=None)

    assert layer.geometry_family is GeometryFamily.MIXED


@pytest.mark.parametrize(
    ("family", "expected_fill", "expected_point_size"),
    [
        (GeometryFamily.POINT, "#2f7de1", 8.0),
        (GeometryFamily.LINE, "transparent", 0.0),
        (GeometryFamily.POLYGON, "#9ec5fe", 0.0),
    ],
)
def test_default_style_matches_geometry_family(
    family: GeometryFamily,
    expected_fill: str,
    expected_point_size: float,
) -> None:
    """默认样式应根据几何类别提供稳定的视觉参数。"""
    style: LayerStyle = LayerStyle.for_geometry_family(family)

    assert style.fill_color == expected_fill
    assert style.point_size == expected_point_size
