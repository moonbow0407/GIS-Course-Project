"""Rasterio 栅格重投影适配器测试。"""

import numpy as np
import pytest
from affine import Affine
from pyproj import CRS, Transformer
from rasterio.transform import array_bounds

from app.application.ports.raster_projector import RasterProjectionResult
from app.domain.vector_layer import Bounds
from app.infrastructure.projection.rasterio_raster_projector import (
    RasterioRasterProjector,
)

# 覆盖东经 10~13 度、北纬 20~23 度的三行三列单波段测试栅格。
SOURCE_TRANSFORM: Affine = Affine(1.0, 0.0, 10.0, 0.0, -1.0, 23.0)
TARGET_CRS: CRS = CRS.from_epsg(3857)


def make_source_data() -> tuple[np.ndarray, np.ndarray]:
    """创建与源变换一致的像元数组和全有效掩膜。"""
    data: np.ndarray = np.arange(9, dtype=np.float32).reshape(1, 3, 3)
    mask: np.ndarray = np.ones((3, 3), dtype=bool)
    return data, mask


def test_project_returns_target_crs_grid_and_mask() -> None:
    """重投影结果应包含目标坐标系的网格、像元和同步的有效掩膜。"""
    projector: RasterioRasterProjector = RasterioRasterProjector()
    data, mask = make_source_data()

    result: RasterProjectionResult = projector.project(
        data,
        mask,
        SOURCE_TRANSFORM,
        CRS.from_epsg(4326),
        TARGET_CRS,
        resolution=200000.0,
    )

    assert result.data.shape == (1, result.data.shape[1], result.data.shape[2])
    assert result.valid_mask.shape == result.data.shape[1:]
    assert result.valid_mask.dtype == np.bool_
    # 目标范围必须覆盖源范围角点转换后的区域。
    transformed_corners: Bounds = _project_bounds(SOURCE_TRANSFORM, CRS.from_epsg(4326))
    result_bounds: Bounds = _result_bounds(result)
    assert result_bounds[0] <= transformed_corners[0] + 1e-6
    assert result_bounds[1] <= transformed_corners[1] + 1e-6
    assert result_bounds[2] >= transformed_corners[2] - 1e-6
    assert result_bounds[3] >= transformed_corners[3] - 1e-6


def test_project_reprojects_invalid_pixels() -> None:
    """源无效像元在重投影后应保持无效。"""
    projector: RasterioRasterProjector = RasterioRasterProjector()
    data, mask = make_source_data()
    mask[1, 1] = False

    result: RasterProjectionResult = projector.project(
        data,
        mask,
        SOURCE_TRANSFORM,
        CRS.from_epsg(4326),
        TARGET_CRS,
        resolution=100000.0,
    )

    # 所有中心采样点落入源无效像元的目标像元都应保持无效。
    invalid_dst_pixels: list[tuple[int, int]] = _pixels_sampling_source_pixel(
        result, (1, 1)
    )
    assert invalid_dst_pixels, "目标网格应包含采样到源中心像元的像元"
    for row, col in invalid_dst_pixels:
        assert bool(result.valid_mask[row, col]) is False
    # 全有效输入下这些像元有效，说明无效区域确实来自掩膜传播。
    all_valid: RasterProjectionResult = projector.project(
        data,
        np.ones((3, 3), dtype=bool),
        SOURCE_TRANSFORM,
        CRS.from_epsg(4326),
        TARGET_CRS,
        resolution=100000.0,
    )
    for row, col in invalid_dst_pixels:
        assert bool(all_valid.valid_mask[row, col]) is True


def test_project_returns_input_for_equivalent_crs() -> None:
    """源与目标坐标系等价时不应重新采样，直接返回输入。"""
    projector: RasterioRasterProjector = RasterioRasterProjector()
    data, mask = make_source_data()

    result: RasterProjectionResult = projector.project(
        data,
        mask,
        SOURCE_TRANSFORM,
        CRS.from_epsg(4326),
        CRS.from_epsg(4326),
    )

    assert result.data is data
    assert result.transform is SOURCE_TRANSFORM
    assert result.valid_mask is mask


def test_project_resamples_equivalent_crs_when_resolution_is_requested() -> None:
    """源目标 CRS 相同时，显式输出分辨率仍应创建新的目标网格。"""
    projector: RasterioRasterProjector = RasterioRasterProjector()
    data, mask = make_source_data()

    result: RasterProjectionResult = projector.project(
        data,
        mask,
        SOURCE_TRANSFORM,
        CRS.from_epsg(4326),
        CRS.from_epsg(4326),
        resolution=0.5,
    )

    assert result.data is not data
    assert result.transform != SOURCE_TRANSFORM
    assert result.data.shape[1:] == (6, 6)
    assert result.valid_mask.shape == (6, 6)


def test_project_rejects_invalid_input_layout() -> None:
    """输入像元不是波段×高度×宽度或掩膜行列不一致时应拒绝。"""
    projector: RasterioRasterProjector = RasterioRasterProjector()
    data, mask = make_source_data()

    with pytest.raises(ValueError, match="波段"):
        projector.project(
            data[0],
            mask,
            SOURCE_TRANSFORM,
            CRS.from_epsg(4326),
            TARGET_CRS,
        )
    with pytest.raises(ValueError, match="掩膜"):
        projector.project(
            data,
            np.ones((2, 2), dtype=bool),
            SOURCE_TRANSFORM,
            CRS.from_epsg(4326),
            TARGET_CRS,
        )


def test_project_rejects_unsupported_resampling() -> None:
    """不支持的重采样方法应给出明确错误。"""
    projector: RasterioRasterProjector = RasterioRasterProjector()
    data, mask = make_source_data()

    with pytest.raises(ValueError, match="重采样"):
        projector.project(
            data,
            mask,
            SOURCE_TRANSFORM,
            CRS.from_epsg(4326),
            TARGET_CRS,
            resampling="不存在的算法",
        )


def _project_bounds(transform: Affine, crs: CRS) -> Bounds:
    """把源范围四个角点转换到目标坐标系后的最小包围矩形。"""
    min_x, min_y, max_x, max_y = array_bounds(3, 3, transform)
    transformer: Transformer = Transformer.from_crs(crs, TARGET_CRS, always_xy=True)
    xs, ys = transformer.transform(
        (min_x, max_x, min_x, max_x),
        (min_y, min_y, max_y, max_y),
    )
    return min(xs), min(ys), max(xs), max(ys)


def _result_bounds(result: RasterProjectionResult) -> Bounds:
    """根据结果仿射变换和行列数推导覆盖范围。"""
    height, width = result.data.shape[1:]
    return array_bounds(height, width, result.transform)


def _pixels_sampling_source_pixel(
    result: RasterProjectionResult,
    source_pixel: tuple[int, int],
) -> list[tuple[int, int]]:
    """返回中心采样点落入指定源像元的目标像元位置。

    掩膜重采样按目标像元中心采样源像元，因此先把每个目标像元
    中心转换回源坐标系，再计算其所在的源像元。
    """
    height, width = result.data.shape[1:]
    source_crs: CRS = CRS.from_epsg(4326)
    back_transformer: Transformer = Transformer.from_crs(
        TARGET_CRS, source_crs, always_xy=True
    )
    inverse_source: Affine = ~SOURCE_TRANSFORM
    hits: list[tuple[int, int]] = []
    for row in range(height):
        for col in range(width):
            x, y = result.transform * (col + 0.5, row + 0.5)
            lon, lat = back_transformer.transform(x, y)
            src_col, src_row = inverse_source * (lon, lat)
            if (int(src_row), int(src_col)) == source_pixel:
                hits.append((row, col))
    return hits
