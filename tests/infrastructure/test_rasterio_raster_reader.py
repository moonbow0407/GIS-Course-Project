"""Rasterio 栅格读取适配器测试。"""

from pathlib import Path

import numpy as np
import rasterio
from affine import Affine

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
