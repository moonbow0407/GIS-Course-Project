"""Rasterio 栅格读取适配器测试。"""

from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.transform import array_bounds

from app.infrastructure.file_io.rasterio_raster_reader import RasterioRasterReader


def test_read_preserves_all_analysis_bands_dtype_nodata_and_mask(tmp_path: Path) -> None:
    """读取栅格时应在显示缓存之外保留全部真实分析数据。"""
    path: Path = tmp_path / "source.tif"
    values = np.array(
        [
            [[100, 200], [-9999, 400]],
            [[10, 20], [-9999, 40]],
        ],
        dtype=np.int16,
    )
    transform = Affine.translation(100, 200) * Affine.scale(10, -10)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=2,
        dtype=values.dtype,
        crs="EPSG:3857",
        transform=transform,
        nodata=-9999,
    ) as dataset:
        dataset.write(values)

    layer = RasterioRasterReader().read(path)

    np.testing.assert_array_equal(layer.raster_data, values)
    np.testing.assert_array_equal(
        layer.valid_mask,
        np.array([[True, True], [False, True]], dtype=np.bool_),
    )
    assert layer.raster_data.dtype == np.int16
    assert layer.nodata == -9999
    assert layer.band_count == 2


def test_large_raster_uses_bounded_preview_and_defers_analysis_pixels(tmp_path: Path) -> None:
    """大栅格首读只生成有限尺寸预览，完整像元在分析访问时才加载。"""
    path: Path = tmp_path / "large.tif"
    height, width = 4096, 5000
    values = np.stack(
        [
            np.arange(height * width, dtype=np.int16).reshape(height, width),
            np.full((height, width), 100, dtype=np.int16),
        ]
    )
    transform = Affine.translation(100, 200) * Affine.scale(10, -10)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=2,
        dtype=values.dtype,
        crs="EPSG:3857",
        transform=transform,
        nodata=-9999,
    ) as dataset:
        dataset.write(values)

    layer = RasterioRasterReader().read(path)

    assert layer.analysis_data_loaded is False
    assert max(layer.image_data.shape[:2]) <= RasterioRasterReader.MAX_DISPLAY_DIMENSION
    assert layer.image_data.shape[:2] != (height, width)
    preview_bounds = array_bounds(
        layer.image_data.shape[0],
        layer.image_data.shape[1],
        layer.display_transform,
    )
    np.testing.assert_allclose(
        preview_bounds,
        (layer.bounds[0], layer.bounds[1], layer.bounds[2], layer.bounds[3]),
    )

    np.testing.assert_array_equal(layer.raster_data, values)
    assert layer.analysis_data_loaded is True
