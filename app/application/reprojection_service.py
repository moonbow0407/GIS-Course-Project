"""独立的矢量和栅格重投影图层工具。"""

from pathlib import Path

import numpy as np
from pyproj import CRS
from rasterio.transform import array_bounds

from app.application.display_projection_service import DisplayProjectionService
from app.application.errors import LayerReprojectionFailed
from app.application.ports import DataWriter
from app.application.ports.raster_projector import RasterProjectionResult
from app.application.results import ReprojectionMetadata
from app.application.symbology_service import apply_raster_symbology
from app.domain.raster_layer import RasterLayer
from app.domain.spatial_layer import SpatialLayer
from app.domain.vector_layer import VectorLayer


class ReprojectionService:
    """创建独立的新图层，绝不替换输入领域图层。"""

    def __init__(
        self,
        display_projection_service: DisplayProjectionService,
        data_writer: DataWriter | None = None,
    ) -> None:
        """注入投影服务和可选输出写入器。"""
        self._projection = display_projection_service
        self._writer = data_writer
        self._metadata: ReprojectionMetadata | None = None

    @property
    def metadata(self) -> ReprojectionMetadata | None:
        """返回最近一次重投影的操作和输出网格摘要。"""
        return self._metadata

    def execute(
        self,
        layer: SpatialLayer,
        target_crs: CRS,
        output_path: Path | None = None,
        raster_resampling: str | None = None,
    ) -> SpatialLayer:
        """重投影输入图层，输出路径为空时返回临时内存图层。"""
        if layer.crs is None:
            raise LayerReprojectionFailed(f"图层“{layer.name}”没有 CRS。")
        resolved_path: Path | None = (
            output_path.expanduser().resolve() if output_path is not None else None
        )
        try:
            if isinstance(layer, VectorLayer):
                features = self._projection.transform_features(
                    layer.features, layer.crs, target_crs
                )
                projected_vector_layer = VectorLayer.create(
                    name=layer.name,
                    features=features,
                    crs=target_crs,
                    source_path=resolved_path,
                    source_layer_name=layer.source_layer_name,
                    symbology=layer.symbology,
                    labeling=layer.labeling,
                    crs_override=False,
                )
                self._metadata = ReprojectionMetadata(
                    source_crs=layer.crs.to_string(),
                    target_crs=target_crs.to_string(),
                    operation=self._projection.describe_operation(layer.crs, target_crs),
                    resampling=None,
                    output_shape=None,
                    output_transform=None,
                    output_bounds=projected_vector_layer.bounds,
                    feature_count=len(projected_vector_layer.features),
                )
                return projected_vector_layer
            if isinstance(layer, RasterLayer):
                projected: RasterProjectionResult = self._projection.project_raster_data(
                    layer.raster_data,
                    layer.valid_mask,
                    layer.transform,
                    layer.crs,
                    target_crs,
                    nodata=layer.nodata,
                    resampling=raster_resampling or "bilinear",
                )
                height, width = projected.data.shape[1:]
                bounds = array_bounds(height, width, projected.transform)
                resolved_resampling = raster_resampling or "bilinear"
                placeholder = RasterLayer.create(
                    name=layer.name,
                    raster_data=projected.data,
                    image_data=np.zeros((height, width, 4), dtype=np.uint8),
                    valid_mask=projected.valid_mask,
                    transform=projected.transform,
                    crs=target_crs,
                    bounds=(
                        min(bounds[0], bounds[2]),
                        min(bounds[1], bounds[3]),
                        max(bounds[0], bounds[2]),
                        max(bounds[1], bounds[3]),
                    ),
                    nodata=layer.nodata,
                    source_path=resolved_path,
                    symbology=layer.symbology,
                )
                projected_raster_layer = (
                    apply_raster_symbology(placeholder, layer.symbology)
                    if layer.symbology
                    else placeholder
                )
                self._metadata = ReprojectionMetadata(
                    source_crs=layer.crs.to_string(),
                    target_crs=target_crs.to_string(),
                    operation=self._projection.describe_operation(layer.crs, target_crs),
                    resampling=resolved_resampling,
                    output_shape=(height, width),
                    output_transform=(
                        float(projected.transform.a),
                        float(projected.transform.b),
                        float(projected.transform.c),
                        float(projected.transform.d),
                        float(projected.transform.e),
                        float(projected.transform.f),
                    ),
                    output_bounds=projected_raster_layer.bounds,
                )
                return projected_raster_layer
        except LayerReprojectionFailed:
            raise
        except Exception as error:
            raise LayerReprojectionFailed(
                f"图层“{layer.name}”无法转换到目标 CRS。"
            ) from error
        raise LayerReprojectionFailed(f"图层“{layer.name}”类型不支持重投影。")

    def persist(self, layer: SpatialLayer, output_path: Path, layer_name: str | None = None) -> None:
        """将已重投影图层写入用户指定的新数据源。"""
        if self._writer is None:
            raise LayerReprojectionFailed("空间数据写出服务尚未配置。")
        self._writer.write(layer, output_path.expanduser().resolve(), (), layer_name)
