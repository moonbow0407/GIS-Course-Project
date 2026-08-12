"""Rasterio 栅格写入适配器测试。"""

from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from pyproj import CRS

from app.application.errors import RasterReadFailed
from app.domain.raster_layer import RasterLayer
from app.infrastructure.file_io.raster_window_io import RasterBlockWriter
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
        assert dataset.profile["tiled"] is True
        assert dataset.profile["compress"] == "deflate"


def test_block_writer_builds_optional_overviews(tmp_path: Path) -> None:
    """完整写出后可显式构建 Overview，默认 profile 使用稳定瓦片参数。"""
    path = tmp_path / "overview.tif"
    values = np.ones((512, 512), dtype=np.uint16)
    valid = np.ones((512, 512), dtype=np.bool_)
    with RasterBlockWriter(
        path,
        width=512,
        height=512,
        band_count=1,
        dtype="uint16",
        crs="EPSG:3857",
        transform=Affine.identity(),
    ) as writer:
        writer.write_window(values, valid, rasterio.windows.Window(0, 0, 512, 512))
        writer.build_overviews((2, 4), rasterio.enums.Resampling.average)

    with rasterio.open(path) as dataset:
        assert dataset.profile["tiled"] is True
        assert dataset.block_shapes == [(256, 256)]
        assert dataset.profile["compress"] == "deflate"
        assert dataset.overviews(1) == [2, 4]


def test_block_writer_removes_tiff_and_companions_after_write_failure(tmp_path: Path) -> None:
    """窗口写入失败时应清理 TIFF、掩膜、Overview 与辅助元数据。"""
    path = tmp_path / "broken.tif"
    writer = RasterBlockWriter(
        path,
        width=256,
        height=256,
        band_count=1,
        dtype="uint8",
        crs=None,
        transform=Affine.identity(),
    )
    companions = tuple(Path(f"{path}{suffix}") for suffix in (".msk", ".ovr", ".aux.xml"))
    for companion in companions:
        companion.touch()

    with pytest.raises(RasterReadFailed):
        writer.write_window(
            np.ones((2, 2, 2, 2), dtype=np.uint8),
            np.ones((2, 2), dtype=np.bool_),
            rasterio.windows.Window(0, 0, 2, 2),
        )

    assert not path.exists()
    assert all(not companion.exists() for companion in companions)
