"""显示投影服务和显示缓存的行为测试。"""

import numpy as np
import pytest
from affine import Affine
from pyproj import CRS
from shapely.geometry import Point

from app.application.display_models import RasterDisplayPayload, VectorDisplayPayload
from app.application.display_projection_service import (
    DisplayCacheManager,
    DisplayProjectionService,
)
from app.application.reprojection_service import ReprojectionService
from app.domain.feature import Feature
from app.domain.raster_layer import RasterLayer
from app.domain.vector_layer import VectorLayer
from app.infrastructure.projection.pyproj_coordinate_transformer import (
    PyprojCoordinateTransformer,
)
from app.infrastructure.projection.rasterio_raster_projector import (
    RasterioRasterProjector,
)


def _vector_layer() -> VectorLayer:
    """构造带属性和稳定 fid 的经纬度点图层。"""
    return VectorLayer.create(
        layer_id="roads",
        name="道路",
        crs=CRS.from_epsg(4326),
        features=(
            Feature(fid=7, geometry=Point(0.01, 0.02), attributes={"name": "A"}),
        ),
    )


def _raster_layer(crs: CRS | None = None) -> RasterLayer:
    """构造用于显示投影的单波段栅格。"""
    data = np.asarray([[[1.0, 2.0], [3.0, 4.0]]], dtype=np.float32)
    valid_mask = np.ones((2, 2), dtype=bool)
    image_data = np.zeros((2, 2, 4), dtype=np.uint8)
    image_data[..., 3] = 255
    transform = Affine.translation(0.0, 1.0) * Affine.scale(0.5, -0.5)
    return RasterLayer.create(
        layer_id="dem",
        name="高程",
        raster_data=data,
        image_data=image_data,
        valid_mask=valid_mask,
        transform=transform,
        crs=crs if crs is not None else CRS.from_epsg(4326),
        bounds=(0.0, 0.0, 1.0, 1.0),
    )


def _service() -> DisplayProjectionService:
    """构造使用真实 PyProj/Rasterio 适配器的显示服务。"""
    return DisplayProjectionService(
        PyprojCoordinateTransformer(),
        RasterioRasterProjector(),
    )


def test_vector_display_payload_transforms_geometry_but_preserves_domain_layer() -> None:
    """矢量显示投影只改变载荷，fid 和原始领域坐标保持不变。"""
    layer = _vector_layer()
    payload = _service().project(layer, CRS.from_epsg(3857))

    assert isinstance(payload, VectorDisplayPayload)
    assert payload.features[0].fid == 7
    assert payload.features[0].attributes["name"] == "A"
    assert payload.features[0].geometry.x == pytest.approx(1113.1949, rel=1e-5)
    assert layer.crs == CRS.from_epsg(4326)
    assert layer.features[0].geometry.x == 0.01


def test_raster_display_payload_projects_preview_and_uses_default_resampling() -> None:
    """栅格显示投影输出新网格，连续栅格默认使用双线性重采样。"""
    layer = _raster_layer()
    payload = _service().project(layer, CRS.from_epsg(3857))

    assert isinstance(payload, RasterDisplayPayload)
    assert payload.image_data.shape[2] == 4
    assert payload.transform != layer.transform
    assert layer.crs == CRS.from_epsg(4326)


def test_same_crs_raster_display_path_does_not_load_analysis_pixels() -> None:
    """同 CRS 显示缓存只使用预览，不应触发延迟栅格的完整读取。"""
    transform = Affine.translation(0.0, 1.0) * Affine.scale(0.5, -0.5)
    image_data = np.zeros((2, 2, 4), dtype=np.uint8)
    image_data[..., 3] = 255

    def fail_loader() -> tuple[np.ndarray, np.ndarray]:
        raise AssertionError("同 CRS 显示路径不应加载完整分析数据")

    layer = RasterLayer.create_lazy(
        name="延迟高程",
        image_data=image_data,
        transform=transform,
        display_transform=transform,
        crs=CRS.from_epsg(4326),
        bounds=(0.0, 0.0, 1.0, 1.0),
        raster_shape=(4, 4),
        band_count=1,
        analysis_loader=fail_loader,
    )
    payload = _service().project(layer, CRS.from_epsg(4326))

    assert isinstance(payload, RasterDisplayPayload)
    assert layer.analysis_data_loaded is False


def test_display_cache_reuses_payload_and_drops_stale_revision() -> None:
    """同一缓存键复用载荷，图层 revision 变化后生成新载荷并清理旧值。"""
    layer = _vector_layer()
    cache = DisplayCacheManager(_service())
    target_crs = CRS.from_epsg(3857)

    first = cache.get_or_create(layer, 1, target_crs)
    second = cache.get_or_create(layer, 1, target_crs)
    changed_layer = VectorLayer.create(
        layer_id=layer.layer_id,
        name=layer.name,
        crs=layer.crs,
        features=(
            Feature(fid=7, geometry=Point(0.02, 0.02), attributes={"name": "A"}),
        ),
    )
    third = cache.get_or_create(changed_layer, 2, target_crs)

    assert first is second
    assert third is not first
    assert cache.size == 1


def test_display_cache_supports_vector_and_raster_with_different_source_crs() -> None:
    """同一显示缓存可同时准备不同源 CRS 的矢量和栅格载荷。"""
    cache = DisplayCacheManager(_service())
    target_crs = CRS.from_epsg(3857)

    vector_payload = cache.get_or_create(_vector_layer(), 1, target_crs)
    raster_payload = cache.get_or_create(
        _raster_layer(CRS.from_epsg(3857)),
        1,
        target_crs,
    )

    assert isinstance(vector_payload, VectorDisplayPayload)
    assert isinstance(raster_payload, RasterDisplayPayload)
    assert cache.size == 2


def test_reprojection_service_creates_independent_vector_and_raster_layers() -> None:
    """重投影工具应分别创建新矢量/栅格层，不修改输入图层。"""
    service = ReprojectionService(_service())
    vector = _vector_layer()
    raster = _raster_layer()

    projected_vector = service.execute(vector, CRS.from_epsg(3857))
    projected_raster = service.execute(raster, CRS.from_epsg(3857))

    assert isinstance(projected_vector, VectorLayer)
    assert isinstance(projected_raster, RasterLayer)
    assert projected_vector.crs == CRS.from_epsg(3857)
    assert projected_raster.crs == CRS.from_epsg(3857)
    assert vector.crs == CRS.from_epsg(4326)
    assert raster.crs == CRS.from_epsg(4326)
    assert vector.features[0].geometry.x == pytest.approx(0.01)
