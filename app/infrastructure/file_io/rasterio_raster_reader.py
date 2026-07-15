"""基于 Rasterio 的栅格文件读取适配器。"""

from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from numpy.typing import NDArray
from pyproj import CRS
from rasterio.io import DatasetReader
from rasterio.transform import array_bounds
from rasterio.vrt import WarpedVRT

from app.application.errors import (
    IncompatibleCoordinateReferenceSystem,
    RasterFileNotFound,
    RasterReadFailed,
    UnsupportedRasterFormat,
)
from app.domain.raster_layer import RasterLayer
from app.domain.vector_layer import Bounds


class RasterioRasterReader:
    """读取常见栅格文件并生成带地理定位的 RGBA 图层。"""

    # 支持扩展名：限定当前交由 Rasterio 读取的常见栅格格式。
    SUPPORTED_SUFFIXES: frozenset[str] = frozenset({".tif", ".tiff", ".img", ".dem"})

    def read(self, path: Path, target_crs: CRS | None = None) -> RasterLayer:
        """读取栅格，按需重投影，并把数值波段拉伸为屏幕显示影像。

        参数:
            path: 待读取的本地栅格文件路径。
            target_crs: 地图显示坐标系；为空时保留源栅格坐标系。

        返回:
            包含 RGBA 显示像素、仿射变换和空间范围的栅格图层。

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
                    # WarpedVRT 在读取阶段完成重投影，避免生成临时栅格文件。
                    with WarpedVRT(source, crs=target_crs) as projected:
                        return self._read_dataset(projected, resolved_path, target_crs, source.count)
                return self._read_dataset(source, resolved_path, source_crs, source.count)
        except IncompatibleCoordinateReferenceSystem:
            raise
        except Exception as error:
            raise RasterReadFailed(f"栅格文件读取失败：{resolved_path.name}") from error

    def _read_dataset(
        self,
        dataset: DatasetReader,
        path: Path,
        crs: CRS | None,
        source_band_count: int,
    ) -> RasterLayer:
        """从已确定坐标系的数据集读取显示波段和地理定位信息。

        参数:
            dataset: 原始数据集或已完成动态重投影的数据集。
            path: 用于记录来源和生成图层名称的文件路径。
            crs: 数据集当前坐标参考系统。
            source_band_count: 原始文件波段数量。

        返回:
            完成显示波段合成的栅格领域图层。
        """
        indexes: tuple[int, ...] = self._display_band_indexes(dataset.count)
        # Rasterio 返回“波段×高度×宽度”，后续再合成为 RGBA 通道。
        values: NDArray[np.float64] = dataset.read(indexes).astype(np.float64)
        masks: NDArray[np.uint8] = dataset.read_masks(indexes)
        rgba: NDArray[np.uint8] = self._to_rgba(values, masks)
        transform: Affine = dataset.transform
        raw_bounds: tuple[float, float, float, float] = array_bounds(
            dataset.height, dataset.width, transform
        )
        bounds: Bounds = (
            min(raw_bounds[0], raw_bounds[2]),
            min(raw_bounds[1], raw_bounds[3]),
            max(raw_bounds[0], raw_bounds[2]),
            max(raw_bounds[1], raw_bounds[3]),
        )
        return RasterLayer.create(
            name=path.stem,
            image_data=rgba,
            transform=transform,
            crs=crs,
            bounds=bounds,
            source_path=path,
            band_count=source_band_count,
        )

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
        values: NDArray[np.float64], masks: NDArray[np.uint8]
    ) -> NDArray[np.uint8]:
        """对有效像素做百分位拉伸，并生成透明无效区的 RGBA 数组。"""
        valid: NDArray[np.bool_] = np.all(masks > 0, axis=0)
        # 部分遥感产品未声明 nodata，但会用所有显示波段均为零表示覆盖区外背景。
        valid &= np.any(values != 0, axis=0)
        stretched_bands: list[NDArray[np.uint8]] = []
        band_index: int
        for band_index in range(values.shape[0]):
            band: NDArray[np.float64] = values[band_index]
            samples: NDArray[np.float64] = band[valid]
            if samples.size == 0:
                stretched_bands.append(np.zeros(band.shape, dtype=np.uint8))
                continue
            # 舍弃两端少量极值，避免异常亮点或暗点压缩整体显示对比度。
            lower: float = float(np.percentile(samples, 2.0))
            upper: float = float(np.percentile(samples, 98.0))
            if upper <= lower:
                upper = lower + 1.0
            scaled: NDArray[np.float64] = np.clip((band - lower) / (upper - lower), 0.0, 1.0)
            stretched_bands.append(np.asarray(scaled * 255.0, dtype=np.uint8))
        if len(stretched_bands) == 1:
            stretched_bands = stretched_bands * 3
        rgb: NDArray[np.uint8] = np.stack(stretched_bands[:3], axis=2)
        alpha: NDArray[np.uint8] = np.where(valid, 255, 0).astype(np.uint8)
        # 连续内存便于 Qt 按行读取 RGBA 像素。
        return np.ascontiguousarray(np.dstack((rgb, alpha)))
