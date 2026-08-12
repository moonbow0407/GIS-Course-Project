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


def test_read_view_reads_only_visible_window_at_viewport_resolution(tmp_path: Path) -> None:
    """视口读取应裁剪空间范围，并将输出限制在屏幕分辨率附近。"""
    path = tmp_path / "pyramid.tif"
    values = np.arange(1024 * 1024, dtype=np.float32).reshape(1024, 1024)
    transform = Affine.translation(0, 1024) * Affine.scale(1, -1)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=1024,
        height=1024,
        count=1,
        dtype="float32",
        crs="EPSG:3857",
        transform=transform,
        tiled=True,
        blockxsize=256,
        blockysize=256,
    ) as dataset:
        dataset.write(values, 1)
        dataset.build_overviews([2, 4, 8], rasterio.enums.Resampling.average)

    view = RasterioRasterReader().read_view(
        path,
        bounds=(256, 256, 768, 768),
        viewport_size=(128, 128),
        band_indexes=(0,),
    )

    assert view is not None
    assert view.data.shape == (1, 160, 160)
    assert view.valid_mask.shape == (160, 160)
    np.testing.assert_allclose(view.bounds, (256, 256, 768, 768))
    assert view.source_window.width == 512
    assert view.source_window.height == 512


def test_read_view_returns_none_when_viewport_does_not_intersect(tmp_path: Path) -> None:
    """视口与栅格无交集时不应执行无界读取。"""
    path = tmp_path / "source.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=16,
        height=16,
        count=1,
        dtype="uint8",
        transform=Affine.translation(0, 16) * Affine.scale(1, -1),
    ) as dataset:
        dataset.write(np.ones((16, 16), dtype=np.uint8), 1)

    result = RasterioRasterReader().read_view(
        path,
        bounds=(100, 100, 120, 120),
        viewport_size=(200, 200),
        band_indexes=(0,),
    )

    assert result is None
