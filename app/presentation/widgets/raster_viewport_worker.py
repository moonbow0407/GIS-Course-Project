"""栅格金字塔视口后台读取任务。"""

from dataclasses import dataclass, replace

import numpy as np
from pyproj import CRS
from PySide6.QtCore import QThread, Signal

from app.application.display_models import RasterDisplayPayload
from app.application.symbology_service import apply_raster_symbology
from app.domain.raster_layer import RasterLayer
from app.domain.symbology import RasterRendererType, RasterSymbology
from app.domain.vector_layer import Bounds
from app.infrastructure.file_io.rasterio_raster_reader import RasterioRasterReader


@dataclass(frozen=True, slots=True)
class RasterViewportRequest:
    """描述一次可丢弃的地图视口栅格读取请求。"""

    request_id: int
    layer: RasterLayer
    display_crs: CRS | None
    bounds: Bounds
    viewport_size: tuple[int, int]
    resampling: str | None = None


class RasterViewportWorker(QThread):
    """在后台读取单个图层的可见金字塔窗口并生成 RGBA 载荷。"""

    completed = Signal(int, str, object)
    failed = Signal(int, str, str)

    def __init__(
        self,
        request: RasterViewportRequest,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self._request = request

    def run(self) -> None:
        """执行窗口 I/O 和符号渲染；异常通过信号返回主线程。"""
        request = self._request
        layer = request.layer
        if layer.source_path is None:
            self.completed.emit(request.request_id, layer.layer_id, None)
            return
        symbology = layer.symbology
        assert symbology is not None
        band_indexes = self._requested_bands(layer, symbology)
        resampling = request.resampling or (
            "nearest"
            if symbology.renderer_type is RasterRendererType.CLASSIFIED
            else "bilinear"
        )
        try:
            view = RasterioRasterReader().read_view(
                layer.source_path,
                bounds=request.bounds,
                viewport_size=request.viewport_size,
                band_indexes=band_indexes,
                resampling=resampling,
                display_crs=request.display_crs,
                source_crs_override=layer.crs if layer.crs_override else None,
            )
            if view is None:
                self.completed.emit(request.request_id, layer.layer_id, None)
                return
            local_symbology = self._local_symbology(symbology)
            placeholder = np.zeros((*view.valid_mask.shape, 4), dtype=np.uint8)
            temporary = RasterLayer.create(
                name=layer.name,
                raster_data=view.data,
                image_data=placeholder,
                valid_mask=view.valid_mask,
                transform=view.transform,
                crs=request.display_crs or layer.crs,
                bounds=view.bounds,
                nodata=layer.nodata,
                symbology=local_symbology,
            )
            rendered = apply_raster_symbology(temporary, local_symbology)
            payload = RasterDisplayPayload(
                layer_id=layer.layer_id,
                image_data=rendered.image_data,
                transform=view.transform,
                bounds=view.bounds,
            )
        except Exception as error:  # noqa: BLE001 线程边界统一转换为界面错误。
            self.failed.emit(request.request_id, layer.layer_id, str(error))
            return
        self.completed.emit(request.request_id, layer.layer_id, payload)

    @staticmethod
    def _requested_bands(
        layer: RasterLayer,
        symbology: RasterSymbology,
    ) -> tuple[int, ...]:
        candidates = (
            symbology.rgb_bands
            if symbology.renderer_type is RasterRendererType.RGB
            else (symbology.stretch_band,)
        )
        return tuple(min(max(index, 0), layer.band_count - 1) for index in candidates)

    @staticmethod
    def _local_symbology(symbology: RasterSymbology) -> RasterSymbology:
        """把源波段编号映射到窗口数组内的局部编号。"""
        if symbology.renderer_type is RasterRendererType.RGB:
            return replace(symbology, rgb_bands=(0, 1, 2))
        return replace(symbology, stretch_band=0)
