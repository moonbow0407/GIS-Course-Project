"""矢量图层 LOD 领域模型测试。"""

import pytest
from shapely.geometry import Point

from app.domain.feature import Feature
from app.domain.lod import LodLevel, LodPyramid


def make_feature(fid: int) -> Feature:
    """构造带编号的测试要素。"""
    return Feature(fid=fid, geometry=Point(fid, fid), attributes={})


def make_pyramid() -> LodPyramid:
    """构造容差为 0、1、4 的三级金字塔。"""
    return LodPyramid((
        LodLevel(0.0, (make_feature(1), make_feature(2))),
        LodLevel(1.0, (make_feature(1), make_feature(2))),
        LodLevel(4.0, (make_feature(1), make_feature(2))),
    ))


def test_lod_level_rejects_negative_tolerance() -> None:
    """简化容差不能为负数。"""
    with pytest.raises(ValueError, match="不能为负数"):
        LodLevel(-0.1, (make_feature(1),))


def test_lod_pyramid_requires_at_least_one_level() -> None:
    """空金字塔应被拒绝。"""
    with pytest.raises(ValueError, match="至少包含一个级别"):
        LodPyramid(())


def test_lod_pyramid_requires_strictly_increasing_tolerance() -> None:
    """容差相等或递减的级别应被拒绝。"""
    with pytest.raises(ValueError, match="严格递增"):
        LodPyramid((
            LodLevel(0.0, (make_feature(1),)),
            LodLevel(0.0, (make_feature(1),)),
        ))


def test_select_returns_coarsest_level_within_screen_scale() -> None:
    """选择应返回容差不超过每像素地图单位的最粗级别。"""
    pyramid = make_pyramid()

    assert pyramid.select(10.0) is pyramid.levels[2].features
    assert pyramid.select(4.0) is pyramid.levels[2].features
    assert pyramid.select(1.0) is pyramid.levels[1].features
    assert pyramid.select(0.001) is pyramid.levels[0].features


def test_select_falls_back_to_finest_when_no_level_matches() -> None:
    """任何级别都无法匹配时回退到最细级别。"""
    pyramid = LodPyramid((
        LodLevel(1.0, (make_feature(1),)),
        LodLevel(2.0, (make_feature(1),)),
    ))

    assert pyramid.select(0.5) is pyramid.levels[0].features


def test_select_fade_interpolates_between_adjacent_levels() -> None:
    """每像素地图单位落在相邻两级之间时，应返回两级并线性插值。"""
    pyramid = make_pyramid()  # 容差 0、1、4

    fine, coarse, t = pyramid.select_fade(2.5)

    assert fine is pyramid.levels[1].features  # 容差 1
    assert coarse is pyramid.levels[2].features  # 容差 4
    assert t == pytest.approx(0.5)  # (2.5 - 1) / (4 - 1)


def test_select_fade_returns_single_level_at_boundaries() -> None:
    """落在首级之前或末级之后时，两级相同，分别返回最细/最粗级别。"""
    pyramid = make_pyramid()

    assert pyramid.select_fade(0.0) == (
        pyramid.levels[0].features,
        pyramid.levels[0].features,
        0.0,
    )
    assert pyramid.select_fade(4.0) == (
        pyramid.levels[2].features,
        pyramid.levels[2].features,
        1.0,
    )
    assert pyramid.select_fade(100.0) == (
        pyramid.levels[2].features,
        pyramid.levels[2].features,
        1.0,
    )
