"""基于 Rasterio 的分块流式栅格重投影适配器。

大栅格无法整幅载入内存时，把重投影转换为"单次打开源数据 + 单次创建
WarpedVRT + 逐目标窗口读取写出"的流式流程，内存峰值只与窗口大小和
波段数相关，与整幅输出像元数无关。

输出先写入目标同目录的唯一临时 GeoTIFF（BigTIFF 自动按需启用），
完整写入并校验网格、波段数和 CRS 后原子替换目标文件；掩膜伴生文件
（``.msk``）与主文件同生共死，取消或失败时一并清理，不留半成品。
"""

from pathlib import Path
from shutil import disk_usage
from uuid import uuid4

import numpy as np
import rasterio
from numpy.typing import NDArray
from pyproj import CRS
from rasterio.enums import Resampling
from rasterio.io import DatasetReader
from rasterio.transform import array_bounds
from rasterio.vrt import WarpedVRT

from app.application.errors import LayerReprojectionFailed, WorkspaceOperationCancelled
from app.application.ports.windowed_raster_projector import ProjectionProgressCallback
from app.domain.raster_grid import RasterGrid
from app.infrastructure.file_io.raster_window_io import (
    DEFAULT_BLOCK_SIZE,
    RasterBlockWriter,
    iter_windows,
)


class WindowedRasterProjector:
    """把源栅格文件按目标网格窗口流式重投影并写出 GeoTIFF。

    窗口读取复用 WarpedVRT 的重投影能力：整个任务只打开一次源数据集、
    只创建一次 VRT，逐窗口读取全部波段后写入输出，避免为每个窗口重建
    转换环境，也避免把整幅影像载入内存。
    """

    def __init__(self, block_size: int = DEFAULT_BLOCK_SIZE) -> None:
        """创建投影器；block_size 用于测试注入更小的窗口。"""
        self._block_size: int = max(1, block_size)

    def project_to_file(
        self,
        source_path: Path,
        target_crs: CRS,
        output_path: Path,
        *,
        source_crs_override: CRS | None = None,
        resampling: str = "bilinear",
        progress_callback: ProjectionProgressCallback | None = None,
    ) -> RasterGrid:
        """流式重投影源文件并原子写入输出文件（见端口文档）。"""
        resolved_source: Path = source_path.expanduser().resolve()
        if not resolved_source.is_file():
            raise LayerReprojectionFailed(f"栅格文件不存在：{resolved_source}")
        try:
            method: Resampling = Resampling[resampling]
        except KeyError as error:
            raise LayerReprojectionFailed(f"不支持的重采样方法：{resampling}") from error
        resolved_output: Path = output_path.expanduser().resolve()
        if resolved_output == resolved_source:
            raise LayerReprojectionFailed("重投影输出不能覆盖输入数据源。")
        try:
            source: DatasetReader = rasterio.open(resolved_source)
        except Exception as error:
            raise LayerReprojectionFailed(
                f"栅格文件打开失败：{resolved_source.name}"
            ) from error
        try:
            return self._project_dataset(
                source,
                resolved_source,
                target_crs,
                resolved_output,
                source_crs_override,
                method,
                progress_callback,
            )
        finally:
            source.close()

    def _project_dataset(
        self,
        source: DatasetReader,
        source_path: Path,
        target_crs: CRS,
        output_path: Path,
        source_crs_override: CRS | None,
        method: Resampling,
        progress_callback: ProjectionProgressCallback | None,
    ) -> RasterGrid:
        """使用已打开的源数据集执行流式投影并返回目标网格。"""
        declared_crs: CRS | None = (
            CRS.from_user_input(source.crs) if source.crs is not None else None
        )
        effective_source_crs: CRS | None = source_crs_override or declared_crs
        if effective_source_crs is None:
            raise LayerReprojectionFailed(
                f"栅格「{source_path.name}」未声明坐标参考系统，且工程未覆盖源 CRS。"
            )
        dst_transform, dst_width, dst_height = self._target_grid(
            source, effective_source_crs, target_crs
        )
        target_grid: RasterGrid = RasterGrid(
            crs=target_crs,
            transform=dst_transform,
            width=dst_width,
            height=dst_height,
        )
        self._check_output_capacity(output_path, source, dst_width, dst_height)
        temp_path: Path = output_path.with_name(
            f".{output_path.stem}.{uuid4().hex}.tif"
        )
        writer = RasterBlockWriter(
            temp_path,
            dst_width,
            dst_height,
            source.count,
            source.dtypes[0],
            target_crs,
            dst_transform,
            source.nodata,
        )
        try:
            with WarpedVRT(
                source,
                src_crs=effective_source_crs,
                crs=target_crs,
                transform=dst_transform,
                width=dst_width,
                height=dst_height,
                resampling=method,
            ) as vrt:
                band_indexes: tuple[int, ...] = tuple(
                    range(1, source.count + 1)
                )
                windows: list[tuple[object, object]] = iter_windows(
                    dst_width, dst_height, self._block_size
                )
                total: int = len(windows)
                for index, (_read_window, write_window) in enumerate(
                    windows, start=1
                ):
                    data: NDArray[np.generic] = vrt.read(
                        indexes=band_indexes, window=write_window
                    )
                    masks: NDArray[np.uint8] = vrt.read_masks(
                        indexes=band_indexes,
                        window=write_window,
                        resampling=Resampling.nearest,
                    )
                    valid: NDArray[np.bool_] = np.all(masks > 0, axis=0)
                    writer.write_window(data, valid, write_window)
                    if (
                        progress_callback is not None
                        and not progress_callback(index, total)
                    ):
                        raise WorkspaceOperationCancelled("已取消重投影。")
            overview_factors = self._overview_factors(dst_width, dst_height)
            if overview_factors:
                overview_resampling = (
                    Resampling.nearest if method is Resampling.nearest else Resampling.average
                )
                writer.build_overviews(overview_factors, overview_resampling)
            writer.close()
            self._validate_output(
                temp_path, dst_width, dst_height, source.count, target_crs
            )
            self._commit_output(temp_path, output_path)
        except BaseException:
            # 取消、校验或写入失败都不能留下临时文件和伴生掩膜。
            writer.abort()
            raise
        return target_grid

    @staticmethod
    def _overview_factors(width: int, height: int) -> tuple[int, ...]:
        """生成让最小 Overview 仍至少含一个像元的二倍层级。"""
        factors: list[int] = []
        factor = 2
        while width // factor >= 1 and height // factor >= 1:
            factors.append(factor)
            factor *= 2
        return tuple(factors)

    @staticmethod
    def _target_grid(
        source: DatasetReader,
        source_crs: CRS,
        target_crs: CRS,
    ) -> tuple[object, int, int]:
        """按默认分辨率推导目标坐标系下的输出网格。"""
        from rasterio.warp import calculate_default_transform

        transform, width, height = calculate_default_transform(
            source_crs,
            target_crs,
            source.width,
            source.height,
            *array_bounds(source.height, source.width, source.transform),
        )
        return transform, width, height

    @staticmethod
    def _check_output_capacity(
        output_path: Path,
        source: DatasetReader,
        dst_width: int,
        dst_height: int,
    ) -> None:
        """写前估算输出字节数并核对磁盘剩余空间。"""
        bytes_per_pixel: int = sum(
            np.dtype(dtype).itemsize for dtype in source.dtypes
        )
        estimated_bytes: int = dst_width * dst_height * bytes_per_pixel
        try:
            free_bytes: int = disk_usage(output_path.parent).free
        except OSError:
            return
        if estimated_bytes > free_bytes:
            raise LayerReprojectionFailed(
                "磁盘空间不足：重投影输出约需 "
                f"{estimated_bytes / 2**20:.0f} MiB，"
                f"当前可用 {free_bytes / 2**20:.0f} MiB。"
            )

    @staticmethod
    def _validate_output(
        path: Path,
        width: int,
        height: int,
        band_count: int,
        target_crs: CRS,
    ) -> None:
        """重新打开临时文件，校验网格、波段数和坐标系与目标一致。"""
        try:
            with rasterio.open(path) as dataset:
                if (
                    dataset.width != width
                    or dataset.height != height
                    or dataset.count != band_count
                ):
                    raise LayerReprojectionFailed(
                        "重投影输出校验失败：网格或波段数与目标不一致。"
                    )
                written_crs: CRS | None = (
                    CRS.from_user_input(dataset.crs)
                    if dataset.crs is not None
                    else None
                )
                if written_crs is None or not written_crs.equals(
                    target_crs, ignore_axis_order=True
                ):
                    raise LayerReprojectionFailed(
                        "重投影输出校验失败：坐标系与目标不一致。"
                    )
        except LayerReprojectionFailed:
            raise
        except Exception as error:
            raise LayerReprojectionFailed(
                f"重投影输出校验失败：{path.name}"
            ) from error

    @staticmethod
    def _commit_output(temp_path: Path, output_path: Path) -> None:
        """校验通过后把临时文件原子替换为目标文件，并同步掩膜伴生文件。"""
        mask_path: Path = _mask_path(temp_path)
        destination_mask: Path = _mask_path(output_path)
        temp_path.replace(output_path)
        if mask_path.exists():
            mask_path.replace(destination_mask)
        elif destination_mask.exists():
            destination_mask.unlink()


def _mask_path(tiff_path: Path) -> Path:
    """返回 GDAL 为 GeoTIFF 掩膜生成的伴生文件路径。"""
    return Path(str(tiff_path) + ".msk")
