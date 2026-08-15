"""栅格窗口 I/O 测试：窗口读取、分块写出和窗口迭代。"""

from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from pyproj import CRS
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.windows import Window
from shapely.geometry import Polygon

from app.application.errors import RasterReadFailed, RasterWindowReadFailed
from app.domain.raster_grid import RasterGrid
from app.infrastructure.file_io.raster_window_io import (
    RasterBlockWriter,
    RasterWindowReader,
    build_geometry_mask,
    iter_windows,
)

_CRS = CRS.from_epsg(32650)
_TRANSFORM = Affine.translation(0.0, 100.0) * Affine.scale(10.0, -10.0)


def _write_tif(path: Path, data: np.ndarray, nodata: float | None = -9999.0) -> Path:
    """写出单波段测试 GeoTIFF。"""
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=data.shape[1],
        height=data.shape[0],
        count=1,
        dtype=data.dtype,
        crs=_CRS,
        transform=_TRANSFORM,
        nodata=nodata,
    ) as dst:
        dst.write(data, 1)
    return path


class TestWindowReader:
    """窗口读取器。"""

    def test_read_window_returns_correct_slice(self, tmp_path: Path) -> None:
        """窗口读取应返回对应区域的像元和掩膜。"""
        data = np.arange(100, dtype=np.float32).reshape(10, 10)
        path = _write_tif(tmp_path / "a.tif", data, nodata=None)
        with RasterWindowReader(path) as reader:
            assert reader.width == 10
            assert reader.height == 10
            assert reader.band_count == 1
            assert reader.crs == _CRS
            values, valid = reader.read_band_window(1, Window(2, 3, 4, 2))
        np.testing.assert_array_equal(values, data[3:5, 2:6])
        assert valid.all()

    def test_nodata_mask_propagates(self, tmp_path: Path) -> None:
        """NoData 像元应在窗口掩膜中标记为无效。"""
        data = np.full((4, 4), 1.0, dtype=np.float32)
        data[1, 1] = -9999.0
        path = _write_tif(tmp_path / "b.tif", data)
        with RasterWindowReader(path) as reader:
            _, valid = reader.read_band_window(1, Window(0, 0, 4, 4))
        assert not valid[1, 1]
        assert valid.sum() == 15

    def test_missing_file_rejected(self, tmp_path: Path) -> None:
        """不存在的文件应抛出明确异常。"""
        with pytest.raises(RasterReadFailed):
            RasterWindowReader(tmp_path / "missing.tif")

    def test_invalid_band_rejected(self, tmp_path: Path) -> None:
        """超出范围的波段应抛出窗口读取异常。"""
        path = _write_tif(tmp_path / "c.tif", np.ones((2, 2), dtype=np.float32))
        with RasterWindowReader(path) as reader:
            with pytest.raises(RasterWindowReadFailed):
                reader.read_band_window(9, Window(0, 0, 2, 2))

    def test_read_window_resamples_to_requested_shape(self, tmp_path: Path) -> None:
        """窗口读取应按目标形状和重采样方式返回数组。"""
        data = np.arange(16, dtype=np.float32).reshape(4, 4)
        path = _write_tif(tmp_path / "resample.tif", data, nodata=None)
        with RasterWindowReader(path) as reader:
            values, valid = reader.read_band_window(
                1,
                Window(0, 0, 4, 4),
                resampling=Resampling.average,
                out_shape=(2, 2),
            )

        assert values.shape == (2, 2)
        assert valid.shape == (2, 2)
        np.testing.assert_allclose(values, [[2.5, 4.5], [10.5, 12.5]])

    def test_reprojected_read_returns_requested_window_shape(self, tmp_path: Path) -> None:
        """重投影读取应只返回当前目标窗口，而不是整幅目标栅格。"""
        path = tmp_path / "reproject.tif"
        source_transform = from_bounds(0.0, 0.0, 1.0, 1.0, 10, 10)
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=10,
            height=10,
            count=1,
            dtype="float32",
            crs="EPSG:4326",
            transform=source_transform,
            nodata=-9999.0,
        ) as dataset:
            dataset.write(np.ones((1, 10, 10), dtype=np.float32))

        target_grid = RasterGrid(
            crs=CRS.from_epsg(3857),
            transform=from_bounds(0.0, 0.0, 111319.5, 111325.1, 10, 10),
            width=10,
            height=10,
        )
        with RasterWindowReader(path) as reader:
            values, valid = reader.read_window_reprojected(
                1, target_grid, Window(0, 0, 4, 4)
            )

        assert values.shape == (4, 4)
        assert valid.shape == (4, 4)
        assert valid.all()


class TestBlockWriter:
    """分块写出器。"""

    def test_invalid_pixels_are_written_as_nodata(self, tmp_path: Path) -> None:
        """无效像元应写入 NoData 值，重新打开后显示掩膜为无效。"""
        path = tmp_path / "masked.tif"
        writer = RasterBlockWriter(
            path, width=2, height=2, band_count=1, dtype="float32",
            crs=_CRS, transform=_TRANSFORM, nodata=-9999.0,
        )
        values = np.array([[10.0, 20.0], [30.0, 40.0]], dtype=np.float32)
        valid = np.array([[True, False], [True, False]], dtype=bool)
        writer.write_window(values, valid, Window(0, 0, 2, 2))
        writer.close()

        with rasterio.open(path) as dst:
            written = dst.read(1)
            mask = dst.dataset_mask()
        np.testing.assert_array_equal(written[valid], [10.0, 30.0])
        np.testing.assert_array_equal(written[~valid], [-9999.0, -9999.0])
        np.testing.assert_array_equal(mask > 0, valid)

    def test_write_windows_roundtrip(self, tmp_path: Path) -> None:
        """分窗口写入后整体读回应与输入一致。"""
        path = tmp_path / "out.tif"
        writer = RasterBlockWriter(
            path, width=4, height=4, band_count=1, dtype="float32",
            crs=_CRS, transform=_TRANSFORM, nodata=-9999.0,
        )
        block_a = np.full((2, 4), 1.0, dtype=np.float32)
        block_b = np.full((2, 4), 2.0, dtype=np.float32)
        writer.write_window(block_a, np.ones((2, 4), dtype=bool), Window(0, 0, 4, 2))
        writer.write_window(block_b, np.ones((2, 4), dtype=bool), Window(0, 2, 4, 2))
        writer.close()

        with rasterio.open(path) as dst:
            values = dst.read(1)
            assert dst.width == 4 and dst.height == 4
            assert CRS.from_user_input(dst.crs) == _CRS
            assert dst.transform == _TRANSFORM
        np.testing.assert_array_equal(values[:2, :], 1.0)
        np.testing.assert_array_equal(values[2:, :], 2.0)

    def test_write_multiband_window_roundtrip(self, tmp_path: Path) -> None:
        """多波段窗口写入应保留全部波段。"""
        path = tmp_path / "multiband.tif"
        writer = RasterBlockWriter(
            path, width=2, height=2, band_count=2, dtype="float32",
            crs=_CRS, transform=_TRANSFORM, nodata=-9999.0,
        )
        data = np.stack(
            [np.full((2, 2), 1.0), np.full((2, 2), 2.0)], axis=0
        ).astype(np.float32)
        writer.write_window(data, np.ones((2, 2), dtype=bool), Window(0, 0, 2, 2))
        writer.close()

        with rasterio.open(path) as dst:
            assert dst.count == 2
            np.testing.assert_array_equal(dst.read(1), 1.0)
            np.testing.assert_array_equal(dst.read(2), 2.0)

    def test_abort_removes_temp_file(self, tmp_path: Path) -> None:
        """异常中止时应删除临时输出文件。"""
        path = tmp_path / "abort.tif"
        writer = RasterBlockWriter(
            path, width=2, height=2, band_count=1, dtype="float32",
            crs=_CRS, transform=_TRANSFORM,
        )
        writer._abort()
        assert not path.exists()

    def test_context_manager_aborts_on_exception(self, tmp_path: Path) -> None:
        """上下文异常退出应删除文件。"""
        path = tmp_path / "ctx.tif"
        with pytest.raises(RuntimeError):
            with RasterBlockWriter(
                path, width=2, height=2, band_count=1, dtype="float32",
                crs=_CRS, transform=_TRANSFORM,
            ):
                raise RuntimeError("boom")
        assert not path.exists()


class TestIterWindows:
    """窗口迭代。"""

    def test_windows_cover_full_grid(self) -> None:
        """窗口应无重叠、无遗漏地覆盖整个网格。"""
        windows = iter_windows(10, 8, block_size=4, halo=0)
        covered = np.zeros((8, 10), dtype=bool)
        for _read, write in windows:
            covered[
                write.row_off:write.row_off + write.height,
                write.col_off:write.col_off + write.width,
            ] = True
        assert covered.all()

    def test_halo_expands_read_windows(self) -> None:
        """halo 应扩展读取窗口但不影响写入窗口。"""
        windows = iter_windows(12, 12, block_size=4, halo=1)
        first_read, first_write = windows[0]
        # 首窗口位于原点，halo 只能向右下扩展。
        assert first_read.width == first_write.width + 2
        assert first_read.height == first_write.height + 2
        assert first_read.col_off == 0 and first_read.row_off == 0
        # 中间窗口的 halo 向四周扩展。
        middle_read, middle_write = next(
            (r, w) for r, w in windows if w.col_off == 4 and w.row_off == 4
        )
        assert middle_read.width == middle_write.width + 2
        assert middle_read.height == middle_write.height + 2
        assert middle_read.col_off == middle_write.col_off - 1
        assert middle_read.row_off == middle_write.row_off - 1


class TestGeometryMask:
    """几何掩膜生成。"""

    def test_polygon_mask_marks_inside(self) -> None:
        """多边形内部像元应标记为 True。"""
        transform = Affine.translation(0.0, 10.0) * Affine.scale(1.0, -1.0)
        polygon = Polygon([(0.0, 5.0), (5.0, 5.0), (5.0, 10.0), (0.0, 10.0)])
        mask = build_geometry_mask([polygon], transform, 10, 10)
        # 左上 5×5 区域在多边形内。
        assert mask[:5, :5].all()
        assert not mask[6:, :].any()

    def test_empty_shapes_all_outside(self) -> None:
        """无几何时全部像元视为范围外。"""
        transform = Affine.identity()
        mask = build_geometry_mask([], transform, 3, 3)
        assert not mask.any()
