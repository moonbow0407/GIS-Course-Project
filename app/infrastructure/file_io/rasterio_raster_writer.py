"""基于 Rasterio 的栅格文件写入适配器。"""

from pathlib import Path

from rasterio.enums import Resampling
from rasterio.windows import Window

from app.application.errors import DataWriteFailed, UnsupportedExportFormat
from app.domain.raster_layer import RasterLayer
from app.infrastructure.file_io.raster_window_io import RasterBlockWriter


class RasterioRasterWriter:
    """把栅格真实分析像元写出为 GeoTIFF。"""

    _SUPPORTED_SUFFIXES: frozenset[str] = frozenset({".tif", ".tiff"})

    def write(
        self,
        layer: RasterLayer,
        path: Path,
        *,
        overview_factors: tuple[int, ...] = (),
        overview_resampling: Resampling = Resampling.average,
    ) -> None:
        """写出瓦片化 GeoTIFF，并可选在完整写出后构建 Overview。"""
        resolved_path: Path = path.expanduser().resolve()
        suffix: str = resolved_path.suffix.lower()
        if suffix not in self._SUPPORTED_SUFFIXES:
            raise UnsupportedExportFormat(
                f"暂不支持该栅格导出格式：{suffix or '无扩展名'}"
            )
        if not resolved_path.parent.is_dir():
            raise DataWriteFailed(f"输出目录不存在：{resolved_path.parent}")

        try:
            with RasterBlockWriter(
                resolved_path,
                width=layer.raster_data.shape[2],
                height=layer.raster_data.shape[1],
                band_count=layer.band_count,
                dtype=str(layer.raster_data.dtype),
                crs=layer.crs,
                transform=layer.transform,
                nodata=layer.nodata,
            ) as writer:
                writer.write_window(
                    layer.raster_data,
                    layer.valid_mask,
                    Window(0, 0, layer.raster_shape[1], layer.raster_shape[0]),
                )
                writer.build_overviews(overview_factors, overview_resampling)
        except Exception as error:
            raise DataWriteFailed(f"栅格数据导出失败：{resolved_path.name}") from error
