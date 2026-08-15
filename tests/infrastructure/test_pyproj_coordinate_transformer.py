"""PyProj 矢量坐标转换适配器测试。"""

import pytest
from pyproj import CRS
from shapely.geometry import Point

from app.domain.feature import Feature
from app.domain.vector_layer import Bounds
from app.infrastructure.projection.pyproj_coordinate_transformer import (
    PyprojCoordinateTransformer,
)


def make_features() -> tuple[Feature, ...]:
    """创建带属性和编号的测试要素。"""
    return (
        Feature(fid=1, geometry=Point(10, 20), attributes={"名称": "站点"}),
        Feature(fid=2, geometry=Point(0, 0), attributes={"名称": "原点"}),
    )


def test_transform_features_projects_geometry_to_target_crs() -> None:
    """要素几何应转换到目标坐标系，编号和属性保持不变。"""
    transformer: PyprojCoordinateTransformer = PyprojCoordinateTransformer()

    result: tuple[Feature, ...] = transformer.transform_features(
        make_features(),
        CRS.from_epsg(4326),
        CRS.from_epsg(3857),
    )

    projected_x, projected_y = result[0].geometry.x, result[0].geometry.y
    # 已知的 Web 墨卡托投影值：东经 10 度、北纬 20 度。
    assert projected_x == pytest.approx(1113194.9079, abs=1e-3)
    assert projected_y == pytest.approx(2273030.9270, abs=1e-3)
    assert result[0].fid == 1
    assert result[0].attributes == {"名称": "站点"}
    # 原点在两个坐标系中重合。
    assert result[1].geometry.x == pytest.approx(0.0, abs=1e-6)
    assert result[1].geometry.y == pytest.approx(0.0, abs=1e-6)


def test_transform_features_returns_same_tuple_for_equivalent_crs() -> None:
    """源与目标坐标系等价时不应重新转换要素。"""
    transformer: PyprojCoordinateTransformer = PyprojCoordinateTransformer()
    features: tuple[Feature, ...] = make_features()

    result: tuple[Feature, ...] = transformer.transform_features(
        features,
        CRS.from_epsg(4326),
        CRS.from_epsg(4326),
    )

    assert result is features


def test_transform_bounds_returns_enclosing_rectangle() -> None:
    """范围转换后的包围矩形应覆盖非线性投影的边界。"""
    transformer: PyprojCoordinateTransformer = PyprojCoordinateTransformer()

    result: Bounds = transformer.transform_bounds(
        (10.0, 20.0, 12.0, 22.0),
        CRS.from_epsg(4326),
        CRS.from_epsg(3857),
    )

    # Web 墨卡托为单调映射，角点转换后取最小/最大值即可得到精确外接矩形。
    assert result[0] == pytest.approx(1113194.9079, abs=1e-3)
    assert result[1] == pytest.approx(2273030.9270, abs=1e-3)
    assert result[2] == pytest.approx(1335833.8895, abs=1e-3)
    assert result[3] == pytest.approx(2511525.2348, abs=1e-3)


def test_transform_bounds_returns_same_bounds_for_equivalent_crs() -> None:
    """源与目标坐标系等价时范围应原样返回。"""
    transformer: PyprojCoordinateTransformer = PyprojCoordinateTransformer()
    bounds: Bounds = (10.0, 20.0, 12.0, 22.0)

    result: Bounds = transformer.transform_bounds(
        bounds,
        CRS.from_epsg(4326),
        CRS.from_epsg(4326),
    )

    assert result is bounds
