"""分块流式栅格重投影适配器测试。"""

from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from pyproj import CRS

from app.application.errors import LayerReprojectionFailed, WorkspaceOperationCancelled
from app.infrastructure.projection.rasterio_raster_projector import (
    RasterioRasterProjector,
)
from app.infrastructure.projection.windowed_raster_projector import (
    WindowedRasterProjector,
)

_HEIGHT = 8
_WIDTH = 10
_NODATA = -9999
_SOURCE_TRANSFORM = Affine.translation(0, 0) * Affine.scale(1000, -1000)


def _source_values() -> tuple[np.ndarray, np.ndarray]:
    """生成带 NoData 区域的 int16 单波段源像元。"""
    values = np.arange(_HEIGHT * _WIDTH, dtype=np.int16).reshape(_HEIGHT, _WIDTH)
    values[1:3, 2:4] = _NODATA
    return values, values != _NODATA


def _write_source(
    path: Path,
    values: np.ndarray,
    crs: str | None = "EPSG:3857",
    nodata: int | None = _NODATA,
) -> None:
    """写一个测试源 GeoTIFF，返回仿射变换。"""
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=values.shape[1],
        height=values.shape[0],
        count=1,
        dtype=values.dtype,
        crs=crs,
        transform=_SOURCE_TRANSFORM,
        nodata=nodata,
    ) as dataset:
        dataset.write(values[np.newaxis])


def _read_output(path: Path) -> tuple[np.ndarray, np.ndarray, CRS]:
    """读取输出文件的像元和有效掩膜。"""
    with rasterio.open(path) as dataset:
        return dataset.read(1), dataset.read_masks(1) > 0, CRS.from_user_input(dataset.crs)


def _project_full(values: np.ndarray, resampling: str) -> np.ndarray:
    """用全数组投影器生成对照结果（返回目标像元和掩膜）。"""
    return RasterioRasterProjector().project(
        values[np.newaxis],
        values != _NODATA,
        _SOURCE_TRANSFORM,
        CRS.from_epsg(3857),
        CRS.from_epsg(4326),
        nodata=_NODATA,
        resampling=resampling,
    )


def test_streamed_result_matches_full_array_projection_for_nearest(
    tmp_path: Path,
) -> None:
    """最近邻重采样下，流式输出应与全数组投影逐像元一致。"""
    values, _valid = _source_values()
    source = tmp_path / "source.tif"
    output = tmp_path / "out" / "projected.tif"
    output.parent.mkdir()
    _write_source(source, values)

    grid = WindowedRasterProjector(block_size=3).project_to_file(
        source, CRS.from_epsg(4326), output, resampling="nearest"
    )
    full = _project_full(values, "nearest")
    out_data, out_valid, out_crs = _read_output(output)

    assert (grid.height, grid.width) == full.data.shape[1:]
    assert out_crs.equals(CRS.from_epsg(4326))
    np.testing.assert_array_equal(out_data[out_valid], full.data[0][out_valid])
    np.testing.assert_array_equal(out_valid, full.valid_mask)


def test_streamed_result_matches_full_array_projection_for_bilinear(
    tmp_path: Path,
) -> None:
    """双线性重采样下，流式输出应与全数组投影在容差内一致。"""
    values, _valid = _source_values()
    source = tmp_path / "source.tif"
    output = tmp_path / "out" / "projected.tif"
    output.parent.mkdir()
    _write_source(source, values)

    WindowedRasterProjector(block_size=3).project_to_file(
        source, CRS.from_epsg(4326), output, resampling="bilinear"
    )
    full = _project_full(values, "bilinear")
    out_data, out_valid, _out_crs = _read_output(output)

    comparable = out_valid & full.valid_mask
    np.testing.assert_allclose(
        out_data[comparable], full.data[0][comparable], rtol=1e-3
    )


def test_block_size_does_not_change_streamed_result(tmp_path: Path) -> None:
    """窗口大小只影响分块方式，不应改变输出网格和像元值。"""
    values, _valid = _source_values()
    source = tmp_path / "source.tif"
    _write_source(source, values)
    small_output = tmp_path / "small.tif"
    large_output = tmp_path / "large.tif"

    WindowedRasterProjector(block_size=3).project_to_file(
        source, CRS.from_epsg(4326), small_output, resampling="nearest"
    )
    WindowedRasterProjector(block_size=100).project_to_file(
        source, CRS.from_epsg(4326), large_output, resampling="nearest"
    )

    small_data, small_valid, small_crs = _read_output(small_output)
    large_data, large_valid, large_crs = _read_output(large_output)
    np.testing.assert_array_equal(small_data, large_data)
    np.testing.assert_array_equal(small_valid, large_valid)
    assert small_crs.equals(large_crs)


def test_progress_is_monotonic_and_covers_all_windows(tmp_path: Path) -> None:
    """进度回调应从小到大覆盖全部窗口且单调递增。"""
    values, _valid = _source_values()
    source = tmp_path / "source.tif"
    output = tmp_path / "out" / "projected.tif"
    output.parent.mkdir()
    _write_source(source, values)
    progress: list[tuple[int, int]] = []

    grid = WindowedRasterProjector(block_size=3).project_to_file(
        source,
        CRS.from_epsg(4326),
        output,
        resampling="nearest",
        progress_callback=lambda done, total: progress.append((done, total)) or True,
    )

    expected_total = (grid.width + 2) // 3 * ((grid.height + 2) // 3)
    assert progress == [(index, expected_total) for index in range(1, expected_total + 1)]


def test_cancel_midway_leaves_no_temp_or_sidecar_files(tmp_path: Path) -> None:
    """中途取消应抛取消异常，并清理临时 TIFF 与掩膜伴生文件。"""
    values, _valid = _source_values()
    source = tmp_path / "source.tif"
    output_dir = tmp_path / "out"
    output = output_dir / "projected.tif"
    output_dir.mkdir()
    _write_source(source, values)
    cancelled = {"count": 0}

    def cancel_on_second(done: int, total: int) -> bool:
        cancelled["count"] += 1
        return cancelled["count"] < 2

    with pytest.raises(WorkspaceOperationCancelled, match="已取消重投影"):
        WindowedRasterProjector(block_size=3).project_to_file(
            source,
            CRS.from_epsg(4326),
            output,
            resampling="nearest",
            progress_callback=cancel_on_second,
        )

    assert not output.exists()
    leftovers = [
        path for path in output_dir.iterdir()
        if path.suffix in {".tif", ".msk"} or path.name.startswith(".")
    ]
    assert leftovers == []


def test_cancel_after_last_window_before_replace_leaves_no_output(
    tmp_path: Path,
) -> None:
    """全部窗口写完后、原子替换前取消，也不能留下输出或临时文件。"""
    values, _valid = _source_values()
    source = tmp_path / "source.tif"
    output_dir = tmp_path / "out"
    output = output_dir / "projected.tif"
    output_dir.mkdir()
    _write_source(source, values)

    def cancel_at_last(done: int, total: int) -> bool:
        return done < total

    with pytest.raises(WorkspaceOperationCancelled, match="已取消重投影"):
        WindowedRasterProjector(block_size=3).project_to_file(
            source,
            CRS.from_epsg(4326),
            output,
            resampling="nearest",
            progress_callback=cancel_at_last,
        )

    assert not output.exists()
    assert not list(output_dir.iterdir())


def test_output_exists_is_rejected(tmp_path: Path) -> None:
    """输出文件已存在时应直接报错，避免静默覆盖。"""
    values, _valid = _source_values()
    source = tmp_path / "source.tif"
    output = tmp_path / "projected.tif"
    _write_source(source, values)
    output.write_bytes(b"occupied")

    with pytest.raises(LayerReprojectionFailed, match="已存在"):
        WindowedRasterProjector().project_to_file(
            source, CRS.from_epsg(4326), output
        )


def test_missing_source_is_rejected(tmp_path: Path) -> None:
    """源文件不存在时应给出明确错误。"""
    with pytest.raises(LayerReprojectionFailed, match="不存在"):
        WindowedRasterProjector().project_to_file(
            tmp_path / "missing.tif", CRS.from_epsg(4326), tmp_path / "out.tif"
        )


def test_source_without_declared_crs_requires_override(tmp_path: Path) -> None:
    """文件未声明 CRS 时必须有工程覆盖，否则报错且不产生输出。"""
    values, _valid = _source_values()
    source = tmp_path / "source.tif"
    output = tmp_path / "out" / "projected.tif"
    output.parent.mkdir()
    _write_source(source, values, crs=None)

    with pytest.raises(LayerReprojectionFailed, match="未声明坐标参考系统"):
        WindowedRasterProjector().project_to_file(
            source, CRS.from_epsg(4326), output
        )
    assert not output.exists()

    grid = WindowedRasterProjector().project_to_file(
        source,
        CRS.from_epsg(4326),
        output,
        source_crs_override=CRS.from_epsg(3857),
        resampling="nearest",
    )
    assert grid.crs.equals(CRS.from_epsg(4326))
    assert output.is_file()


def test_multi_band_streaming_preserves_all_bands(tmp_path: Path) -> None:
    """多波段源逐窗口写出后，各波段像元都应保留。"""
    values = np.stack(
        [
            np.arange(_HEIGHT * _WIDTH, dtype=np.int16).reshape(_HEIGHT, _WIDTH),
            np.full((_HEIGHT, _WIDTH), 7, dtype=np.int16),
        ]
    )
    source = tmp_path / "source.tif"
    output = tmp_path / "out" / "projected.tif"
    output.parent.mkdir()
    with rasterio.open(
        source,
        "w",
        driver="GTiff",
        width=_WIDTH,
        height=_HEIGHT,
        count=2,
        dtype=np.int16,
        crs="EPSG:3857",
        transform=_SOURCE_TRANSFORM,
        nodata=_NODATA,
    ) as dataset:
        dataset.write(values)

    grid = WindowedRasterProjector(block_size=3).project_to_file(
        source, CRS.from_epsg(4326), output, resampling="nearest"
    )
    full = RasterioRasterProjector().project(
        values,
        np.all(values != _NODATA, axis=0),
        _SOURCE_TRANSFORM,
        CRS.from_epsg(3857),
        CRS.from_epsg(4326),
        nodata=_NODATA,
        resampling="nearest",
    )
    with rasterio.open(output) as dataset:
        out_data = dataset.read()
        out_valid = np.all(dataset.read_masks() > 0, axis=0)

    assert dataset.count == 2
    assert (grid.height, grid.width) == full.data.shape[1:]
    for band in range(2):
        np.testing.assert_array_equal(
            out_data[band][out_valid], full.data[band][out_valid]
        )
    np.testing.assert_array_equal(out_valid, full.valid_mask)
