"""Rasterio 栅格写入适配器测试。"""

from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from pyproj import CRS

from app.domain.raster_layer import RasterLayer
from app.infrastructure.file_io.rasterio_raster_writer import RasterioRasterWriter


def test_write_geotiff_preserves_analysis_values_and_metadata(tmp_path: Path) -> None:
    """GeoTIFF 写出应使用真实波段值并保留空间元数据和 NoData。"""
    raster_data = np.array(
        [
            [[100, 200], [-9999, 400]],
            [[10, 20], [-9999, 40]],
        ],
        dtype=np.int16,
    )
    image_data = np.full((2, 2, 4), 255, dtype=np.uint8)
    valid_mask = np.array([[True, True], [False, True]], dtype=np.bool_)
    transform = Affine.translation(100, 200) * Affine.scale(10, -10)
    layer: RasterLayer = RasterLayer.create(
        name="elevation",
        raster_data=raster_data,
        image_data=image_data,
        valid_mask=valid_mask,
        transform=transform,
        crs=CRS.from_epsg(3857),
        bounds=(100, 180, 120, 200),
        nodata=-9999,
    )
    path: Path = tmp_path / "elevation.tif"

    RasterioRasterWriter().write(layer, path)

    with rasterio.open(path) as dataset:
        assert dataset.count == 2
        assert dataset.dtypes == ("int16", "int16")
        assert dataset.crs == rasterio.crs.CRS.from_epsg(3857)
        assert dataset.transform == transform
        assert dataset.nodata == -9999
        np.testing.assert_array_equal(dataset.read(), raster_data)
        np.testing.assert_array_equal(dataset.dataset_mask() > 0, valid_mask)
