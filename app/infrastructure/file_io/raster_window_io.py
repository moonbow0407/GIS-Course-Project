"""栅格分析窗口 I/O：按窗口读取、重投影重采样和分块写出。

本模块为栅格分析服务提供低层 Rasterio 操作能力，不进入领域模型。
- 窗口读取支持按目标网格重投影和重采样；
- 分块写出支持流式写入像元和有效掩膜，避免一次性占用整幅内存。
"""

from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from numpy.typing import NDArray
from pyproj import CRS
from rasterio.enums import Resampling
from rasterio.io import DatasetReader, DatasetWriter
from rasterio.transform import array_bounds
from rasterio.windows import Window
from shapely.geometry.base import BaseGeometry

from app.application.errors import RasterReadFailed, RasterWindowReadFailed
from app.domain.raster_grid import RasterGrid

DEFAULT_TIFF_BLOCK_SIZE: int = 256
DEFAULT_TIFF_COMPRESSION: str = "deflate"
DEFAULT_BIGTIFF_POLICY: str = "IF_SAFER"


class RasterWindowReader:
    """按窗口读取栅格分析像元，支持按目标网格重投影重采样。"""

    def __init__(self, path: Path) -> None:
        """打开栅格源文件并读取元数据。"""
        self._path: Path = path.expanduser().resolve()
        if not self._path.is_file():
            raise RasterReadFailed(f"栅格文件不存在：{self._path}")
        try:
            self._dataset: DatasetReader = rasterio.open(self._path)
        except Exception as error:
            raise RasterReadFailed(
                f"栅格文件打开失败：{self._path.name}"
            ) from error

    @property
    def band_count(self) -> int:
        """返回栅格波段数量。"""
        return self._dataset.count

    @property
    def width(self) -> int:
        """返回栅格列数。"""
        return self._dataset.width

    @property
    def height(self) -> int:
        """返回栅格行数。"""
        return self._dataset.height

    @property
    def transform(self) -> Affine:
        """返回栅格仿射变换。"""
        return self._dataset.transform

    @property
    def crs(self) -> CRS | None:
        """返回栅格坐标参考系统（pyproj CRS）；未声明时为 None。"""
        if self._dataset.crs is None:
            return None
        return CRS.from_user_input(self._dataset.crs)

    def read_band_window(
        self,
        band_index: int,
        window: Window,
        resampling: Resampling = Resampling.nearest,
        out_shape: tuple[int, int] | None = None,
        boundless: bool = False,
    ) -> tuple[NDArray[np.generic], NDArray[np.bool_]]:
        """读取指定波段在窗口内的像元和有效掩膜。

        参数:
            band_index: 1-based 波段编号。
            window: 读取窗口。
            resampling: 重采样方式。
            out_shape: 输出像元形状；省略时与窗口形状一致。
            boundless: 是否允许读取超出源栅格范围的窗口。

        返回:
            (窗口像元数组, 窗口有效掩膜)。
        """
        try:
            target_shape = out_shape or (int(window.height), int(window.width))
            data = self._dataset.read(
                band_index,
                window=window,
                out_shape=target_shape,
                boundless=boundless,
                resampling=resampling,
            )
            # 掩膜始终使用最近邻，避免把有效/无效状态插值成中间值。
            mask = self._dataset.read_masks(
                band_index,
                window=window,
                out_shape=target_shape,
                boundless=boundless,
                resampling=Resampling.nearest,
            )
            valid: NDArray[np.bool_] = mask > 0
            return data, valid
        except Exception as error:
            raise RasterWindowReadFailed(
                f"读取窗口失败：{self._path.name} 波段 {band_index}"
            ) from error

    def read_window_reprojected(
        self,
        band_index: int,
        target_grid: RasterGrid,
        window: Window,
        resampling: Resampling = Resampling.nearest,
    ) -> tuple[NDArray[np.generic], NDArray[np.bool_]]:
        """读取整波段并重投影/重采样到目标网格。

        参数:
            band_index: 1-based 波段编号。
            target_grid: 目标空间网格。
            window: 目标栅格中的读取窗口。
            resampling: 重采样方式。

        返回:
            (目标网格尺寸的像元数组, 有效掩膜)。
        """
        from rasterio.vrt import WarpedVRT

        try:
            target_transform = target_grid.transform
            target_crs = target_grid.crs
            with WarpedVRT(
                self._dataset,
                crs=target_crs,
                transform=target_transform,
                width=target_grid.width,
                height=target_grid.height,
                resampling=resampling,
            ) as vrt:
                data = vrt.read(band_index, window=window)
                mask = vrt.read_masks(
                    band_index,
                    window=window,
                    resampling=Resampling.nearest,
                )
                valid: NDArray[np.bool_] = mask > 0
                return data, valid
        except Exception as error:
            raise RasterWindowReadFailed(
                f"重投影读取失败：{self._path.name} 波段 {band_index}"
            ) from error

    def close(self) -> None:
        """关闭底层数据集。"""
        if not self._dataset.closed:
            self._dataset.close()

    def __enter__(self) -> "RasterWindowReader":
        """支持上下文管理。"""
        return self

    def __exit__(self, *args: object) -> None:
        """退出上下文时关闭数据集。"""
        self.close()


class RasterBlockWriter:
    """流式分块写入 GeoTIFF，支持掩膜和自定义 NoData。

    先创建输出文件，逐窗口写入像元和有效掩膜，最后关闭并校验。
    异常时关闭并删除临时文件，不留下半成品。
    """

    def __init__(
        self,
        path: Path,
        width: int,
        height: int,
        band_count: int,
        dtype: str,
        crs: object,
        transform: Affine,
        nodata: float | None = None,
        *,
        block_size: int = DEFAULT_TIFF_BLOCK_SIZE,
        compression: str = DEFAULT_TIFF_COMPRESSION,
        bigtiff: str = DEFAULT_BIGTIFF_POLICY,
    ) -> None:
        """创建并打开输出 GeoTIFF。

        参数:
            path: 输出路径。
            width, height: 输出栅格列数和行数。
            band_count: 输出波段数量。
            dtype: 输出数据类型字符串。
            crs: 输出坐标参考系统。
            transform: 输出仿射变换。
            nodata: 输出 NoData 值。
        """
        resolved_path: Path = path.expanduser().resolve()
        self._path: Path = resolved_path
        self._width: int = width
        self._height: int = height
        self._band_count: int = band_count
        self._closed: bool = False
        if block_size <= 0 or block_size % 16 != 0:
            raise ValueError("GeoTIFF 块大小必须是大于零的 16 倍数。")
        try:
            self._dataset: DatasetWriter = rasterio.open(
                resolved_path,
                "w",
                driver="GTiff",
                width=width,
                height=height,
                count=band_count,
                dtype=dtype,
                crs=crs,
                transform=transform,
                nodata=nodata,
                tiled=True,
                blockxsize=block_size,
                blockysize=block_size,
                compress=compression,
                bigtiff=bigtiff,
            )
        except Exception as error:
            self._remove_output_files()
            raise RasterReadFailed(
                f"输出 GeoTIFF 创建失败：{resolved_path.name}"
            ) from error

    def write_window(
        self,
        data: NDArray[np.generic],
        valid_mask: NDArray[np.bool_],
        window: Window,
    ) -> None:
        """写入一个窗口的像元和有效掩膜。

        参数:
            data: 单波段 2D 数组，形状与窗口一致。
            valid_mask: 有效像元掩膜，True 表示有效。
            window: 目标写入窗口。
        """
        if self._closed:
            raise RasterReadFailed("写入器已关闭，不能继续写入。")
        try:
            if data.ndim == 2:
                if self._band_count != 1:
                    raise ValueError("多波段输出需要提供三维数组")
                bands = data[np.newaxis, ...]
            elif data.ndim == 3:
                if data.shape[0] != self._band_count:
                    raise ValueError("输出数组波段数与 GeoTIFF 元数据不一致")
                bands = data
            else:
                raise ValueError("输出数组必须是二维或三维")

            if valid_mask.ndim == 2:
                output_mask = valid_mask
            elif valid_mask.ndim == 3:
                if valid_mask.shape != bands.shape:
                    raise ValueError("有效掩膜与输出数组形状不一致")
                output_mask = np.all(valid_mask, axis=0)
            else:
                raise ValueError("有效掩膜必须是二维或三维")

            self._dataset.write(
                bands.astype(self._dataset.dtypes[0]), window=window
            )
            self._dataset.write_mask(
                np.where(output_mask, 255, 0).astype(np.uint8), window=window
            )
        except Exception as error:
            self._abort()
            raise RasterReadFailed(
                f"写入窗口失败：{self._path.name}"
            ) from error

    def close(self) -> None:
        """关闭写入句柄并标记为已关闭。"""
        if self._closed:
            return
        try:
            self._dataset.close()
        finally:
            self._closed = True

    def build_overviews(
        self,
        factors: tuple[int, ...],
        resampling: Resampling = Resampling.average,
    ) -> None:
        """在全部窗口写出后按需构建内部 Overview。

        调用方决定是否及何时构建；本方法不会被 ``close`` 自动触发。
        """
        if self._closed:
            raise RasterReadFailed("写入器已关闭，不能构建 Overview。")
        normalized = tuple(sorted({factor for factor in factors if factor > 1}))
        if not normalized:
            return
        try:
            self._dataset.build_overviews(list(normalized), resampling)
            self._dataset.update_tags(ns="rio_overview", resampling=resampling.name)
        except Exception as error:
            self._abort()
            raise RasterReadFailed(f"构建 Overview 失败：{self._path.name}") from error

    def _abort(self) -> None:
        """异常时关闭并删除 TIFF 及常见 GDAL 伴生临时文件。"""
        self.close()
        self._remove_output_files()

    def _remove_output_files(self) -> None:
        """删除输出文件及 Rasterio/GDAL 可能创建的伴生文件。"""
        companion_paths = (
            self._path,
            Path(f"{self._path}.msk"),
            Path(f"{self._path}.ovr"),
            Path(f"{self._path}.aux.xml"),
        )
        for candidate in companion_paths:
            try:
                if candidate.exists():
                    candidate.unlink()
            except OSError:
                pass

    def __enter__(self) -> "RasterBlockWriter":
        """支持上下文管理。"""
        return self

    def __exit__(self, exc_type: object, *args: object) -> None:
        """异常时中止，正常时关闭。"""
        if exc_type is not None:
            self._abort()
        else:
            self.close()


# ---------------------------------------------------------------------------
# 窗口迭代辅助
# ---------------------------------------------------------------------------

DEFAULT_BLOCK_SIZE: int = 512


def iter_windows(
    width: int,
    height: int,
    block_size: int = DEFAULT_BLOCK_SIZE,
    halo: int = 0,
) -> list[tuple[Window, Window]]:
    """生成输出网格的分块窗口列表。

    参数:
        width, height: 输出栅格列数和行数。
        block_size: 每个窗口的边长（像元）。
        halo: 窗口边缘扩展像元数，用于邻域算法。

    返回:
        (读取窗口, 写入窗口) 元组列表。读取窗口包含 halo，
        写入窗口为不含 halo 的中心区域。
    """
    windows: list[tuple[Window, Window]] = []
    for row_start in range(0, height, block_size):
        for col_start in range(0, width, block_size):
            write_h = min(block_size, height - row_start)
            write_w = min(block_size, width - col_start)
            read_row = max(0, row_start - halo)
            read_col = max(0, col_start - halo)
            read_h = min(height - read_row, write_h + 2 * halo)
            read_w = min(width - read_col, write_w + 2 * halo)
            read_window = Window(read_col, read_row, read_w, read_h)
            write_window = Window(
                col_start, row_start, write_w, write_h
            )
            windows.append((read_window, write_window))
    return windows


def build_geometry_mask(
    shapes: list[BaseGeometry],
    transform: Affine,
    height: int,
    width: int,
    all_touched: bool = False,
    invert: bool = False,
) -> NDArray[np.bool_]:
    """根据矢量几何生成栅格掩膜。

    参数:
        shapes: 矢量几何列表（已转换到栅格 CRS）。
        transform: 栅格仿射变换。
        height, width: 栅格行列数。
        all_touched: 为 True 时所有被几何边界触碰的像元视为有效。
        invert: 为 True 时反转掩膜。

    返回:
        布尔掩膜，True 表示在矢量范围内。
    """
    from rasterio.features import geometry_mask

    if not shapes:
        # 无几何时全部为 False（或反转后全部 True）。
        result = np.zeros((height, width), dtype=bool)
        return ~result if invert else result
    mask = geometry_mask(
        shapes,
        out_shape=(height, width),
        transform=transform,
        all_touched=all_touched,
        invert=invert,
    )
    # geometry_mask 返回 True 表示在几何外，需要反转。
    return ~mask


def bounds_for_window(
    window: Window, transform: Affine
) -> tuple[float, float, float, float]:
    """返回窗口在地图坐标系中的范围。"""
    raw = array_bounds(window.height, window.width, transform * Affine.translation(
        window.col_off, window.row_off
    ))
    return (
        min(raw[0], raw[2]),
        min(raw[1], raw[3]),
        max(raw[0], raw[2]),
        max(raw[1], raw[3]),
    )
