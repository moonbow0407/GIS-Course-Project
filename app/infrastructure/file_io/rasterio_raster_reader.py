"""基于 Rasterio 的栅格文件读取适配器。"""

from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from numpy.typing import NDArray
from pyproj import CRS
from rasterio.enums import Resampling
from rasterio.io import DatasetReader
from rasterio.transform import array_bounds
from rasterio.vrt import WarpedVRT

from app.application.errors import (
    IncompatibleCoordinateReferenceSystem,
    RasterFileNotFound,
    RasterReadFailed,
    UnsupportedRasterFormat,
)
from app.domain.raster_layer import RasterDataLoader, RasterLayer
from app.domain.vector_layer import Bounds


class RasterioRasterReader:
    """读取常见栅格文件并生成带地理定位的显示预览。"""

    # 支持扩展名：限定当前交由 Rasterio 读取的常见栅格格式。
    SUPPORTED_SUFFIXES: frozenset[str] = frozenset({".tif", ".tiff", ".img", ".dem"})

    # 首屏预览的最长边上限；显示不需要保留源文件的每个像元。
    MAX_DISPLAY_DIMENSION: int = 2048

    # 小栅格直接缓存完整数组，大栅格首读只缓存预览，避免一次性占用大量内存。
    MAX_EAGER_ANALYSIS_BYTES: int = 64 * 1024 * 1024

    def read(self, path: Path, target_crs: CRS | None = None) -> RasterLayer:
        """读取栅格，按需重投影，并以预览级别生成显示影像。

        参数:
            path: 待读取的本地栅格文件路径。
            target_crs: 地图显示坐标系；为空时保留源栅格坐标系。

        返回:
            包含显示预览、完整像元元数据、仿射变换和空间范围的栅格图层。

        异常:
            RasterFileNotFound: 文件不存在时抛出。
            UnsupportedRasterFormat: 文件扩展名不受支持时抛出。
            IncompatibleCoordinateReferenceSystem: 无法转换到目标坐标系时抛出。
            RasterReadFailed: Rasterio 无法打开或读取数据时抛出。
        """
        resolved_path: Path = path.expanduser().resolve()
        if not resolved_path.is_file():
            raise RasterFileNotFound(f"栅格文件不存在：{resolved_path}")
        suffix: str = resolved_path.suffix.lower()
        if suffix not in self.SUPPORTED_SUFFIXES:
            raise UnsupportedRasterFormat(f"暂不支持该栅格文件格式：{suffix or '无扩展名'}")

        try:
            with rasterio.open(resolved_path) as source:
                source_crs: CRS | None = (
                    CRS.from_user_input(source.crs) if source.crs is not None else None
                )
                if target_crs is not None and source_crs is None:
                    raise IncompatibleCoordinateReferenceSystem(
                        "源栅格未声明坐标参考系统，无法转换到地图显示坐标系。"
                    )
                if target_crs is not None and source_crs != target_crs:
                    # WarpedVRT 在读取阶段完成重投影，预览只读取降采样后的像元。
                    with WarpedVRT(source, crs=target_crs) as projected:
                        return self._read_dataset(projected, resolved_path, target_crs)
                return self._read_dataset(source, resolved_path, source_crs)
        except IncompatibleCoordinateReferenceSystem:
            raise
        except Exception as error:
            raise RasterReadFailed(f"栅格文件读取失败：{resolved_path.name}") from error

    def _read_dataset(
        self,
        dataset: DatasetReader,
        path: Path,
        crs: CRS | None,
    ) -> RasterLayer:
        """读取预览并创建完整分析像元的延迟加载器。"""
        indexes: tuple[int, ...] = self._display_band_indexes(dataset.count)
        preview_height, preview_width = self._preview_shape(dataset.height, dataset.width)
        display_values: NDArray[np.generic] = dataset.read(
            indexes=indexes,
            out_shape=(len(indexes), preview_height, preview_width),
            resampling=Resampling.nearest,
        )
        display_masks: NDArray[np.uint8] = dataset.read_masks(
            indexes=indexes,
            out_shape=(len(indexes), preview_height, preview_width),
            resampling=Resampling.nearest,
        )
        rgba: NDArray[np.uint8] = self._to_rgba(display_values, display_masks)
        display_valid_mask = self._display_valid_mask(display_values, display_masks)
        display_band_indexes: tuple[int, ...] = tuple(index - 1 for index in indexes)
        transform: Affine = dataset.transform
        display_transform: Affine = transform * Affine.scale(
            dataset.width / preview_width,
            dataset.height / preview_height,
        )
        raw_bounds: tuple[float, float, float, float] = array_bounds(
            dataset.height, dataset.width, transform
        )
        bounds: Bounds = (
            min(raw_bounds[0], raw_bounds[2]),
            min(raw_bounds[1], raw_bounds[3]),
            max(raw_bounds[0], raw_bounds[2]),
            max(raw_bounds[1], raw_bounds[3]),
        )
        analysis_loader: RasterDataLoader | None = None
        if self._analysis_byte_size(dataset) > self.MAX_EAGER_ANALYSIS_BYTES:
            def load_analysis_data() -> tuple[NDArray[np.generic], NDArray[np.bool_]]:
                return self._read_analysis_data(path, crs)

            analysis_loader = load_analysis_data
        if analysis_loader is None:
            raster_data, valid_mask = self._read_full_analysis_data(dataset)
            return RasterLayer.create(
                name=path.stem,
                raster_data=raster_data,
                image_data=rgba,
                valid_mask=valid_mask,
                transform=transform,
                display_transform=display_transform,
                crs=crs,
                bounds=bounds,
                nodata=dataset.nodata,
                source_path=path,
                display_values=display_values,
                display_valid_mask=display_valid_mask,
                display_band_indexes=display_band_indexes,
            )
        return RasterLayer.create_lazy(
            name=path.stem,
            image_data=rgba,
            transform=transform,
            display_transform=display_transform,
            crs=crs,
            bounds=bounds,
            raster_shape=(dataset.height, dataset.width),
            band_count=dataset.count,
            analysis_loader=analysis_loader,
            nodata=dataset.nodata,
            source_path=path,
            display_values=display_values,
            display_valid_mask=display_valid_mask,
            display_band_indexes=display_band_indexes,
        )

    def _read_analysis_data(
        self,
        path: Path,
        target_crs: CRS | None,
    ) -> tuple[NDArray[np.generic], NDArray[np.bool_]]:
        """重新打开源文件并读取完整分析数组和有效掩膜。"""
        try:
            with rasterio.open(path) as source:
                source_crs: CRS | None = (
                    CRS.from_user_input(source.crs) if source.crs is not None else None
                )
                if target_crs is not None and source_crs is None:
                    raise IncompatibleCoordinateReferenceSystem(
                        "源栅格未声明坐标参考系统，无法转换到地图显示坐标系。"
                    )
                if target_crs is not None and source_crs != target_crs:
                    with WarpedVRT(source, crs=target_crs) as projected:
                        return self._read_full_analysis_data(projected)
                return self._read_full_analysis_data(source)
        except IncompatibleCoordinateReferenceSystem:
            raise
        except Exception as error:
            raise RasterReadFailed(f"栅格文件读取失败：{path.name}") from error

    @staticmethod
    def _read_full_analysis_data(
        dataset: DatasetReader,
    ) -> tuple[NDArray[np.generic], NDArray[np.bool_]]:
        """从数据集读取全部波段，并根据 Rasterio 掩膜生成有效像元。"""
        raster_data: NDArray[np.generic] = dataset.read()
        band_masks: NDArray[np.uint8] = dataset.read_masks()
        valid_mask: NDArray[np.bool_] = np.all(band_masks > 0, axis=0)
        return raster_data, valid_mask

    @classmethod
    def _preview_shape(cls, height: int, width: int) -> tuple[int, int]:
        """按最长边上限计算保持范围不变的预览尺寸。"""
        scale: float = min(1.0, cls.MAX_DISPLAY_DIMENSION / max(height, width))
        return max(1, round(height * scale)), max(1, round(width * scale))

    @staticmethod
    def _analysis_byte_size(dataset: DatasetReader) -> int:
        """估算完整分析数组的内存大小，用于选择立即或延迟加载策略。"""
        pixel_count: int = dataset.width * dataset.height
        bytes_per_pixel: int = sum(np.dtype(dtype).itemsize for dtype in dataset.dtypes)
        return pixel_count * bytes_per_pixel

    @staticmethod
    def _display_band_indexes(count: int) -> tuple[int, ...]:
        """选择显示波段；四波段以上数据优先按遥感真彩色 4-3-2 合成。"""
        if count >= 4:
            return (4, 3, 2)
        if count >= 3:
            return (1, 2, 3)
        return (1,)

    @staticmethod
    def _to_rgba(
        values: NDArray[np.generic], masks: NDArray[np.uint8]
    ) -> NDArray[np.uint8]:
        """对预览有效像元做百分位拉伸，并生成透明无效区的 RGBA 数组。"""
        valid: NDArray[np.bool_] = RasterioRasterReader._display_valid_mask(values, masks)
        stretched_bands: list[NDArray[np.uint8]] = []
        band_index: int
        for band_index in range(values.shape[0]):
            band: NDArray[np.float64] = values[band_index].astype(np.float64, copy=False)
            samples: NDArray[np.float64] = band[valid]
            if samples.size == 0:
                stretched_bands.append(np.zeros(band.shape, dtype=np.uint8))
                continue
            # 舍弃两端少量极值，避免异常亮点或暗点压缩整体显示对比度。
            lower: float = float(np.percentile(samples, 2.0))
            upper: float = float(np.percentile(samples, 98.0))
            if upper <= lower:
                upper = lower + 1.0
            scaled_valid = np.clip(
                (band[valid] - lower) / (upper - lower), 0.0, 1.0
            )
            stretched = np.zeros(band.shape, dtype=np.uint8)
            stretched[valid] = np.asarray(scaled_valid * 255.0, dtype=np.uint8)
            stretched_bands.append(stretched)
        if len(stretched_bands) == 1:
            stretched_bands = stretched_bands * 3
        rgb: NDArray[np.uint8] = np.stack(stretched_bands[:3], axis=2)
        alpha: NDArray[np.uint8] = np.where(valid, 255, 0).astype(np.uint8)
        # 连续内存便于 Qt 按行读取 RGBA 像素。
        return np.ascontiguousarray(np.dstack((rgb, alpha)))

    @staticmethod
    def _display_valid_mask(
        values: NDArray[np.generic], masks: NDArray[np.uint8]
    ) -> NDArray[np.bool_]:
        """按显示预览规则生成有效掩膜，供后续低分辨率符号重建复用。"""
        valid: NDArray[np.bool_] = np.all(masks > 0, axis=0)
        # 部分遥感产品未声明 nodata，但会用所有显示波段均为零表示覆盖区外背景。
        valid &= np.any(values != 0, axis=0)
        valid &= np.all(np.isfinite(values), axis=0)
        return valid
