"""RasterGrid 网格值对象测试。"""

import pytest
from affine import Affine
from pyproj import CRS

from app.domain.raster_grid import RasterGrid

_CRS_32650 = CRS.from_epsg(32650)
_CRS_4326 = CRS.from_epsg(4326)
_TRANSFORM = Affine.translation(100.0, 200.0) * Affine.scale(10.0, -10.0)


def _grid(
    crs: CRS = _CRS_32650,
    transform: Affine = _TRANSFORM,
    width: int = 4,
    height: int = 3,
) -> RasterGrid:
    """构建一个默认对齐的测试网格。"""
    return RasterGrid(crs=crs, transform=transform, width=width, height=height)


class TestGridValidation:
    """网格构造校验。"""

    def test_zero_or_negative_dimensions_rejected(self) -> None:
        """行列数必须为正数。"""
        with pytest.raises(ValueError):
            RasterGrid(crs=_CRS_32650, transform=_TRANSFORM, width=0, height=3)
        with pytest.raises(ValueError):
            RasterGrid(crs=_CRS_32650, transform=_TRANSFORM, width=4, height=-1)

    def test_zero_pixel_size_rejected(self) -> None:
        """像元尺寸为零的仿射变换应被拒绝。"""
        with pytest.raises(ValueError):
            RasterGrid(
                crs=_CRS_32650,
                transform=Affine.translation(0, 0) * Affine.scale(0.0, -10.0),
                width=4,
                height=3,
            )


class TestGridDerivedProperties:
    """派生属性。"""

    def test_pixel_size_is_positive(self) -> None:
        """像元宽高应为正值，无论变换方向。"""
        grid = _grid()
        assert grid.pixel_width == 10.0
        assert grid.pixel_height == 10.0

    def test_bounds_cover_full_extent(self) -> None:
        """范围应覆盖整个网格。"""
        grid = _grid()
        min_x, min_y, max_x, max_y = grid.bounds
        assert min_x == 100.0
        assert max_x == 140.0
        assert min_y == 170.0
        assert max_y == 200.0

    def test_rotation_detection(self) -> None:
        """旋转项应被识别。"""
        assert not _grid().has_rotation
        rotated = Affine(10.0, 1.0, 0.0, 1.0, -10.0, 0.0)
        assert RasterGrid(
            crs=_CRS_32650, transform=rotated, width=2, height=2
        ).has_rotation


class TestGridAlignment:
    """网格一致性判断。"""

    def test_identical_grids_match(self) -> None:
        """完全相同的网格判断为对齐。"""
        assert _grid().matches(_grid())

    def test_different_crs_does_not_match(self) -> None:
        """CRS 不同不能视为对齐。"""
        assert not _grid().matches(_grid(crs=_CRS_4326))

    def test_different_origin_does_not_match(self) -> None:
        """原点不同不能视为对齐，即使像元大小和行列数相同。"""
        shifted = Affine.translation(105.0, 200.0) * Affine.scale(10.0, -10.0)
        assert not _grid().matches(_grid(transform=shifted))

    def test_different_pixel_size_does_not_match(self) -> None:
        """像元大小不同不能视为对齐。"""
        resized = Affine.translation(100.0, 200.0) * Affine.scale(20.0, -20.0)
        assert not _grid().matches(_grid(transform=resized))

    def test_different_dimensions_do_not_match(self) -> None:
        """行列数不同不能视为对齐。"""
        assert not _grid().matches(_grid(width=5))
        assert not _grid().matches(_grid(height=5))

    def test_rotation_difference_does_not_match(self) -> None:
        """旋转项不同不能视为对齐。"""
        rotated = Affine(10.0, 1.0, 100.0, 0.0, -10.0, 200.0)
        assert not _grid().matches(_grid(transform=rotated))

    def test_tiny_float_jitter_still_matches(self) -> None:
        """浮点微小抖动不应判定为未对齐。"""
        jittered = Affine.translation(100.0 + 1e-12, 200.0) * Affine.scale(10.0, -10.0)
        assert _grid().matches(_grid(transform=jittered))

    def test_non_grid_object_returns_not_implemented(self) -> None:
        """与非网格对象比较返回 NotImplemented。"""
        assert _grid().matches("not a grid") is NotImplemented
