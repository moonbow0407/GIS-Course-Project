"""基于 Rasterio 的栅格文件写入适配器。"""

from pathlib import Path

import numpy as np
import rasterio

from app.application.errors import DataWriteFailed, UnsupportedExportFormat
from app.domain.raster_layer import RasterLayer


class RasterioRasterWriter:
    """把栅格真实分析像元写出为 GeoTIFF。"""

    _SUPPORTED_SUFFIXES: frozenset[str] = frozenset({".tif", ".tiff"})

    def write(self, layer: RasterLayer, path: Path) -> None:
        """保留波段值、类型、掩膜、NoData、仿射变换和当前坐标系。"""
        resolved_path: Path = path.expanduser().resolve()
        suffix: str = resolved_path.suffix.lower()
        if suffix not in self._SUPPORTED_SUFFIXES:
            raise UnsupportedExportFormat(
                f"暂不支持该栅格导出格式：{suffix or '无扩展名'}"
            )
        if not resolved_path.parent.is_dir():
            raise DataWriteFailed(f"输出目录不存在：{resolved_path.parent}")

        try:
            with rasterio.open(
                resolved_path,
                "w",
                driver="GTiff",
                width=layer.raster_data.shape[2],
                height=layer.raster_data.shape[1],
                count=layer.band_count,
                dtype=layer.raster_data.dtype,
                crs=layer.crs,
                transform=layer.transform,
                nodata=layer.nodata,
            ) as dataset:
                dataset.write(layer.raster_data)
                dataset.write_mask(np.where(layer.valid_mask, 255, 0).astype(np.uint8))
        except Exception as error:
            raise DataWriteFailed(f"栅格数据导出失败：{resolved_path.name}") from error
