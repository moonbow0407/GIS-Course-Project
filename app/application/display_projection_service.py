"""为地图显示准备独立于领域图层的投影载荷。"""

import numpy as np
from affine import Affine
from numpy.typing import NDArray
from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from app.application.crs_utils import crs_equivalent
from app.application.display_models import (
    DisplayCacheKey,
    RasterDisplayPayload,
    VectorDisplayPayload,
    bounds_from_transform,
)
from app.application.ports.coordinate_transformer import CoordinateTransformer
from app.application.ports.raster_projector import RasterProjectionResult, RasterProjector
from app.application.symbology_service import apply_raster_symbology
from app.domain.feature import Feature
from app.domain.raster_layer import RasterLayer
from app.domain.spatial_layer import SpatialLayer
from app.domain.symbology import RasterRendererType, RasterSymbology
from app.domain.vector_layer import VectorLayer


class DisplayProjectionService:
    """把领域图层转换为指定显示 CRS 下的不可变显示载荷。

    该服务不返回新的领域图层。矢量属性、要素编号和栅格分析数据仍由原始
    ``SpatialLayer`` 持有，显示投影只在应用层缓存中存在。
    """

    def __init__(
        self,
        coordinate_transformer: CoordinateTransformer | None = None,
        raster_projector: RasterProjector | None = None,
    ) -> None:
        """注入矢量与栅格投影端口；空端口仅允许等价 CRS 的零转换路径。"""
        self._coordinate_transformer = coordinate_transformer
        self._raster_projector = raster_projector

    def project(
        self,
        layer: SpatialLayer,
        display_crs: CRS | None,
        raster_resampling: str | None = None,
    ) -> VectorDisplayPayload | RasterDisplayPayload:
        """生成图层在地图显示 CRS 下的绘制载荷。"""
        if isinstance(layer, VectorLayer):
            return self._project_vector(layer, display_crs)
        return self._project_raster(layer, display_crs, raster_resampling)

    def transform_geometry(
        self,
        geometry: BaseGeometry,
        source_crs: CRS,
        target_crs: CRS,
    ) -> BaseGeometry:
        """将显示 CRS 下的查询几何转换回指定图层 CRS。"""
        if crs_equivalent(source_crs, target_crs):
            return geometry
        if self._coordinate_transformer is None:
            raise ValueError("空间查询需要配置坐标转换端口。")
        return self._coordinate_transformer.transform_geometry(
            geometry,
            source_crs,
            target_crs,
        )

    def transform_features(
        self,
        features: tuple[Feature, ...],
        source_crs: CRS,
        target_crs: CRS,
    ) -> tuple[Feature, ...]:
        """通过显示投影服务转换新图层中的矢量要素。"""
        if self._coordinate_transformer is None:
            raise ValueError("矢量重投影需要配置坐标转换端口。")
        return self._coordinate_transformer.transform_features(
            features,
            source_crs,
            target_crs,
        )

    def describe_operation(self, source_crs: CRS, target_crs: CRS) -> str:
        """返回矢量/栅格转换所使用的自动操作摘要。"""
        if crs_equivalent(source_crs, target_crs):
            return "CRS 等价，无需坐标转换"
        if self._coordinate_transformer is None:
            return "由投影适配器自动选择（未提供操作详情）"
        operation = getattr(self._coordinate_transformer, "describe_operation", None)
        if callable(operation):
            return str(operation(source_crs, target_crs))
        return "由投影适配器自动选择"

    def project_raster_grid(
        self,
        data: NDArray[np.generic],
        valid_mask: NDArray[np.bool_],
        transform: Affine,
        source_crs: CRS,
        target_crs: CRS,
        target_transform: Affine,
        target_shape: tuple[int, int],
        nodata: float | int | None = None,
        resampling: str = "bilinear",
    ) -> tuple[NDArray[np.generic], NDArray[np.bool_]]:
        """按显式参考栅格临时对齐像元，不改变任何领域图层。"""
        if self._raster_projector is None:
            raise ValueError("栅格对齐需要配置栅格投影端口。")
        projected = self._raster_projector.project(
            data,
            valid_mask,
            transform,
            source_crs,
            target_crs,
            nodata=nodata,
            resampling=resampling,
            target_transform=target_transform,
            target_shape=target_shape,
        )
        return projected.data, projected.valid_mask

    def project_raster_data(
        self,
        data: NDArray[np.generic],
        valid_mask: NDArray[np.bool_],
        transform: Affine,
        source_crs: CRS,
        target_crs: CRS,
        nodata: float | int | None = None,
        resampling: str = "bilinear",
    ) -> RasterProjectionResult:
        """将完整栅格像元投影到新图层 CRS。"""
        if self._raster_projector is None:
            raise ValueError("栅格重投影需要配置栅格投影端口。")
        return self._raster_projector.project(
            data,
            valid_mask,
            transform,
            source_crs,
            target_crs,
            nodata=nodata,
            resampling=resampling,
        )

    def _project_vector(
        self,
        layer: VectorLayer,
        display_crs: CRS | None,
    ) -> VectorDisplayPayload:
        """转换矢量几何并保留 fid 与属性映射。"""
        if layer.crs is None or display_crs is None or crs_equivalent(layer.crs, display_crs):
            return VectorDisplayPayload(
                layer_id=layer.layer_id,
                features=layer.features,
                bounds=layer.bounds,
            )
        if self._coordinate_transformer is None:
            raise ValueError("显示矢量需要配置坐标转换端口。")
        features = self._coordinate_transformer.transform_features(
            layer.features,
            layer.crs,
            display_crs,
        )
        bounds = self._coordinate_transformer.transform_bounds(
            layer.bounds,
            layer.crs,
            display_crs,
        )
        return VectorDisplayPayload(layer_id=layer.layer_id, features=features, bounds=bounds)

    def _project_raster(
        self,
        layer: RasterLayer,
        display_crs: CRS | None,
        raster_resampling: str | None,
    ) -> RasterDisplayPayload:
        """按显示预览或完整分析数组投影栅格并生成 RGBA 载荷。"""
        source_transform: Affine = layer.display_transform or layer.transform
        if layer.crs is None or display_crs is None or crs_equivalent(layer.crs, display_crs):
            return RasterDisplayPayload(
                layer_id=layer.layer_id,
                image_data=layer.image_data,
                transform=source_transform,
                bounds=bounds_from_transform(
                    source_transform,
                    layer.image_data.shape[1],
                    layer.image_data.shape[0],
                ),
            )

        if self._raster_projector is None:
            raise ValueError("显示栅格需要配置栅格投影端口。")
        source_values: NDArray[np.generic]
        source_mask: NDArray[np.bool_]
        if layer.display_values is not None and layer.display_valid_mask is not None:
            source_values = layer.display_values
            source_mask = layer.display_valid_mask
        else:
            source_values = layer.raster_data
            source_mask = layer.valid_mask

        resampling = raster_resampling or self._default_raster_resampling(layer)
        projected = self._raster_projector.project(
            source_values,
            source_mask,
            source_transform,
            layer.crs,
            display_crs,
            nodata=layer.nodata,
            resampling=resampling,
        )
        height, width = projected.data.shape[1:]
        projected_bounds = bounds_from_transform(projected.transform, width, height)
        # 先构造一个只存在于本次调用中的临时栅格，复用既有符号服务把原始值
        # 重新渲染为 RGBA；这不会修改领域图层或触发源文件写回。
        placeholder = np.zeros((height, width, 4), dtype=np.uint8)
        projected_layer = RasterLayer.create(
            name=layer.name,
            raster_data=projected.data,
            image_data=placeholder,
            valid_mask=projected.valid_mask,
            transform=projected.transform,
            crs=display_crs,
            bounds=projected_bounds,
            nodata=layer.nodata,
            symbology=layer.symbology,
        )
        symbology: RasterSymbology | None = projected_layer.symbology
        assert symbology is not None
        rendered_layer = apply_raster_symbology(projected_layer, symbology)
        return RasterDisplayPayload(
            layer_id=layer.layer_id,
            image_data=rendered_layer.image_data,
            transform=projected.transform,
            bounds=projected_bounds,
        )

    @staticmethod
    def _default_raster_resampling(layer: RasterLayer) -> str:
        """分类栅格使用最近邻，连续影像使用双线性。"""
        if (
            layer.symbology is not None
            and layer.symbology.renderer_type is RasterRendererType.CLASSIFIED
        ):
            return "nearest"
        return "bilinear"


class DisplayCacheManager:
    """按显示缓存键复用载荷，并在图层状态变化后清理旧键。"""

    def __init__(self, projection_service: DisplayProjectionService) -> None:
        """创建一个与投影服务绑定的进程内显示缓存。"""
        self._projection_service = projection_service
        self._payloads: dict[
            DisplayCacheKey,
            VectorDisplayPayload | RasterDisplayPayload,
        ] = {}

    def get_or_create(
        self,
        layer: SpatialLayer,
        layer_revision: int,
        display_crs: CRS | None,
        raster_resampling: str | None = None,
    ) -> VectorDisplayPayload | RasterDisplayPayload:
        """按图层版本、CRS、符号和重采样设置读取或创建载荷。"""
        key = DisplayCacheKey.for_layer(
            layer,
            layer_revision,
            display_crs,
            raster_resampling=(
                raster_resampling if isinstance(layer, RasterLayer) else None
            ),
        )
        cached = self._payloads.get(key)
        if cached is not None:
            return cached
        payload = self._projection_service.project(layer, display_crs, raster_resampling)
        self._store(key, payload)
        return payload

    def store(
        self,
        layer: SpatialLayer,
        layer_revision: int,
        display_crs: CRS | None,
        payload: VectorDisplayPayload | RasterDisplayPayload,
        raster_resampling: str | None = None,
    ) -> None:
        """保存已准备好的载荷，供 CRS 原子提交阶段避免重复投影。"""
        key = DisplayCacheKey.for_layer(
            layer,
            layer_revision,
            display_crs,
            raster_resampling=(
                raster_resampling if isinstance(layer, RasterLayer) else None
            ),
        )
        self._store(key, payload)

    def _store(
        self,
        key: DisplayCacheKey,
        payload: VectorDisplayPayload | RasterDisplayPayload,
    ) -> None:
        """写入单个载荷并清理同一图层的旧版本。"""
        self._payloads[key] = payload
        # 一个图层只保留当前版本/显示参数的载荷，避免反复改 CRS 或样式时
        # 无限积累大型栅格数组。
        stale_keys = tuple(
            old_key
            for old_key in self._payloads
            if old_key.layer_id == key.layer_id and old_key != key
        )
        for old_key in stale_keys:
            del self._payloads[old_key]

    def invalidate_layer(self, layer_id: str) -> None:
        """清除指定图层的所有显示载荷。"""
        for key in tuple(self._payloads):
            if key.layer_id == layer_id:
                del self._payloads[key]

    def clear(self) -> None:
        """清空全部显示载荷。"""
        self._payloads.clear()

    @property
    def size(self) -> int:
        """返回当前缓存条目数，供诊断和测试使用。"""
        return len(self._payloads)
