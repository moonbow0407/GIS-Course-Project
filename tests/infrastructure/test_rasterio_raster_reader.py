"""Rasterio 栅格读取适配器测试。"""

import os
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from pyproj import CRS
from rasterio.transform import array_bounds

from app.infrastructure.file_io.raster_overview_service import (
    RasterOverviewPolicy,
    RasterOverviewService,
)
from app.infrastructure.file_io.rasterio_raster_reader import RasterioRasterReader

_PREVIEW_CRS = CRS.from_epsg(32650)


def test_preview_alpha_is_transparent_for_nodata_pixels(tmp_path: Path) -> None:
    """单波段预览必须把 NoData 画成透明，即使像元值不是 0。"""
    path: Path = tmp_path / "clip_like.tif"
    values = np.array([[85.0, 200.0], [-9999.0, 400.0]], dtype=np.float32)
    transform = Affine.translation(100, 200) * Affine.scale(10, -10)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=1,
        dtype=values.dtype,
        crs=_PREVIEW_CRS,
        transform=transform,
        nodata=-9999,
    ) as dataset:
        dataset.write(values, 1)

    layer = RasterioRasterReader().read(path)

    assert layer.image_data[1, 0, 3] == 0
    assert layer.image_data[0, 0, 3] == 255
    assert layer.image_data[0, 1, 3] == 255


def test_display_mask_rejects_nodata_even_when_resampled_mask_reports_valid() -> None:
    """降采样掩膜误报有效时仍应按声明的 NoData 排除哨兵像元。"""
    values = np.array([[[100, 32767]]], dtype=np.int16)
    masks = np.full(values.shape, 255, dtype=np.uint8)

    valid = RasterioRasterReader._display_valid_mask(values, masks, 32767)

    np.testing.assert_array_equal(valid, np.array([[True, False]]))


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


def test_downsampled_preview_defers_analysis_below_eager_byte_limit(tmp_path: Path) -> None:
    """只要预览发生降采样，就应延迟分析数组以保持领域尺寸一致。"""
    path = tmp_path / "medium-dem.tif"
    size = RasterioRasterReader.MAX_DISPLAY_DIMENSION + 1
    values = np.arange(size * size, dtype=np.int16).reshape(1, size, size)
    transform = Affine.translation(0, size) * Affine.scale(1, -1)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=size,
        height=size,
        count=1,
        dtype=values.dtype,
        crs="EPSG:3857",
        transform=transform,
    ) as dataset:
        dataset.write(values)

    layer = RasterioRasterReader().read(path)

    assert layer.analysis_data_loaded is False
    assert layer.image_data.shape[:2] == (size - 1, size - 1)
    np.testing.assert_array_equal(layer.raster_data, values)


def test_overview_plan_skips_small_and_already_optimized_rasters(tmp_path: Path) -> None:
    """小栅格及已有适用金字塔的栅格不得重复构建。"""
    small_path = tmp_path / "small.tif"
    with rasterio.open(
        small_path,
        "w",
        driver="GTiff",
        width=512,
        height=512,
        count=1,
        dtype="uint8",
        transform=Affine.identity(),
    ):
        pass
    service = RasterOverviewService(cache_root=tmp_path / "cache")

    small_plan = service.plan(small_path)

    assert small_plan.should_build is False
    assert small_plan.reason == "small_raster"

    large_path = tmp_path / "large-with-overviews.tif"
    with rasterio.open(
        large_path,
        "w",
        driver="GTiff",
        width=8192,
        height=8192,
        count=1,
        dtype="uint8",
        transform=Affine.identity(),
        tiled=True,
    ):
        pass

    automatic_plan = service.plan(large_path)

    assert automatic_plan.should_build is True
    assert automatic_plan.reason == "automatic_threshold"
    assert 4 in automatic_plan.factors

    with rasterio.open(large_path, "r+") as dataset:
        dataset.build_overviews([2, 4], rasterio.enums.Resampling.average)

    optimized_plan = service.plan(large_path)

    assert optimized_plan.should_build is False
    assert optimized_plan.reason == "source_overviews"


def test_overview_service_builds_versioned_cache_without_modifying_source(
    tmp_path: Path,
) -> None:
    """自动优化应生成可失效的 VRT Overview 缓存，不得改写源 TIFF。"""
    source_path = tmp_path / "dem.tif"
    values = np.arange(512 * 512, dtype=np.int16).reshape(512, 512)
    with rasterio.open(
        source_path,
        "w",
        driver="GTiff",
        width=512,
        height=512,
        count=1,
        dtype=values.dtype,
        crs="EPSG:3857",
        transform=Affine.translation(0, 512) * Affine.scale(1, -1),
        tiled=True,
    ) as dataset:
        dataset.write(values, 1)
    source_stat = source_path.stat()
    policy = RasterOverviewPolicy(
        target_dimension=128,
        conditional_dimension=256,
        automatic_dimension=8192,
        minimum_overview_dimension=32,
        automatic_pixels=1_000_000_000,
        automatic_file_bytes=1,
    )
    service = RasterOverviewService(tmp_path / "cache", policy)

    first = service.optimize(source_path)

    assert first.built is True
    assert first.display_path != source_path
    assert first.display_path.suffix == ".vrt"
    assert first.display_path.is_file()
    assert Path(f"{first.display_path}.ovr").is_file()
    assert source_path.stat().st_size == source_stat.st_size
    assert source_path.stat().st_mtime_ns == source_stat.st_mtime_ns
    with rasterio.open(first.display_path) as cached:
        assert cached.overviews(1) == list(first.factors)

    second = service.optimize(source_path)

    assert second.built is False
    assert second.reason == "cache_valid"
    assert service.display_path(source_path) == first.display_path

    rebuilt = service.optimize(source_path, resampling=rasterio.enums.Resampling.nearest)

    assert rebuilt.built is True
    assert source_path.stat().st_mtime_ns == source_stat.st_mtime_ns

    newer_mtime = source_stat.st_mtime_ns + 1_000_000_000
    os.utime(source_path, ns=(newer_mtime, newer_mtime))

    assert service.display_path(source_path) == source_path.resolve()


def test_auto_reader_prepares_raster_display_and_skips_vectors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """打开数据前只应对栅格构建金字塔，矢量路径必须跳过。"""
    from app.infrastructure.file_io.auto_reader import AutoDataReader
    from app.infrastructure.file_io.raster_overview_service import RasterOverviewResult

    reader = AutoDataReader()
    optimized: list[Path] = []

    def record_optimize(path: Path, *, resampling=None) -> RasterOverviewResult:
        del resampling
        optimized.append(path)
        return RasterOverviewResult(
            source_path=path,
            display_path=path,
            factors=(),
            built=False,
            reason="small_raster",
        )

    monkeypatch.setattr(reader._raster_reader._overview_service, "optimize", record_optimize)

    raster_path = tmp_path / "dem.tif"
    raster_path.write_bytes(b"not-a-real-tif")
    reader.prepare_raster_display(raster_path)
    reader.prepare_raster_display(tmp_path / "roads.shp")

    assert optimized == [raster_path.resolve()]


def test_reader_resolves_overview_cache_for_display_reads(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """首屏预览和视口读取都应通过 Overview 服务解析显示数据源。"""
    source_path = tmp_path / "display-source.tif"
    values = np.arange(64 * 64, dtype=np.int16).reshape(64, 64)
    transform = Affine.translation(0, 64) * Affine.scale(1, -1)
    with rasterio.open(
        source_path,
        "w",
        driver="GTiff",
        width=64,
        height=64,
        count=1,
        dtype=values.dtype,
        crs="EPSG:3857",
        transform=transform,
    ) as dataset:
        dataset.write(values, 1)
    service = RasterOverviewService(cache_root=tmp_path / "cache")
    resolved_paths: list[Path] = []
    original_display_path = service.display_path

    def record_display_path(path: Path) -> Path:
        resolved_paths.append(path)
        return original_display_path(path)

    monkeypatch.setattr(service, "display_path", record_display_path)
    reader = RasterioRasterReader(overview_service=service)

    reader.read(source_path)
    reader.read_view(
        source_path,
        bounds=(0.0, 0.0, 64.0, 64.0),
        viewport_size=(32, 32),
        band_indexes=(0,),
    )

    assert resolved_paths == [source_path.resolve(), source_path.resolve()]


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
