"""显示载荷与显示缓存键模型测试。"""

from dataclasses import FrozenInstanceError

import numpy as np
import pytest
from affine import Affine
from pyproj import CRS
from shapely.geometry import Point

from app.application.display_models import (
    DisplayCacheKey,
    RasterDisplayPayload,
    VectorDisplayPayload,
)
from app.domain.feature import Feature
from app.domain.layer_style import LayerStyle
from app.domain.symbology import VectorRendererType, VectorSymbology
from app.domain.vector_layer import VectorLayer

BOUNDS: tuple[float, float, float, float] = (0.0, 0.0, 2.0, 2.0)
TRANSFORM: Affine = Affine(1.0, 0.0, 0.0, 0.0, -1.0, 2.0)


def make_rgba() -> np.ndarray:
    """创建与 TRANSFORM 和 BOUNDS 一致的两行两列 RGBA 显示数据。"""
    image: np.ndarray = np.zeros((2, 2, 4), dtype=np.uint8)
    image[:, :, 3] = 255
    return image


def make_vector_layer(layer_id: str = "roads") -> VectorLayer:
    """创建包含单个点要素的测试矢量图层。"""
    feature: Feature = Feature(fid=1, geometry=Point(0, 0), attributes={"名称": layer_id})
    return VectorLayer.create(
        layer_id=layer_id,
        name=layer_id,
        features=(feature,),
        crs=CRS.from_epsg(4326),
    )


def make_vector_layer_with_color(layer_id: str, color: str) -> VectorLayer:
    """创建使用指定单一符号颜色的测试矢量图层。"""
    layer: VectorLayer = make_vector_layer(layer_id)
    symbology: VectorSymbology = VectorSymbology(
        renderer_type=VectorRendererType.SIMPLE,
        base_symbol=LayerStyle(
            stroke_color=color,
            fill_color=color,
            line_width=1.0,
            point_size=8.0,
            opacity=1.0,
        ),
    )
    return VectorLayer.create(
        layer_id=layer.layer_id,
        name=layer.name,
        features=layer.features,
        crs=layer.crs,
        symbology=symbology,
    )


# ── VectorDisplayPayload ──────────────────────────────


def test_vector_payload_keeps_features_and_bounds() -> None:
    """矢量显示载荷应原样保留要素、编号和显示范围。"""
    features = (Feature(fid=1, geometry=Point(0, 0), attributes={}),)

    payload = VectorDisplayPayload(layer_id="roads", features=features, bounds=BOUNDS)

    assert payload.layer_id == "roads"
    assert payload.features == features
    assert payload.bounds == BOUNDS


def test_vector_payload_is_immutable() -> None:
    """矢量显示载荷创建后不应允许修改字段。"""
    payload = VectorDisplayPayload(
        layer_id="roads", features=(), bounds=(0.0, 0.0, 0.0, 0.0)
    )

    with pytest.raises(FrozenInstanceError):
        payload.bounds = BOUNDS  # type: ignore[misc]


def test_vector_payload_rejects_invalid_bounds() -> None:
    """矢量显示载荷应拒绝最小角大于最大角或非有限的范围。"""
    with pytest.raises(ValueError, match="显示范围无效"):
        VectorDisplayPayload(layer_id="roads", features=(), bounds=(2.0, 0.0, 1.0, 3.0))
    with pytest.raises(ValueError, match="有限"):
        VectorDisplayPayload(
            layer_id="roads",
            features=(),
            bounds=(float("nan"), 0.0, 1.0, 1.0),
        )


def test_vector_payload_equality() -> None:
    """字段相同的矢量显示载荷应判为相等。"""
    features = (Feature(fid=1, geometry=Point(0, 0), attributes={}),)
    first = VectorDisplayPayload(layer_id="roads", features=features, bounds=BOUNDS)
    second = VectorDisplayPayload(layer_id="roads", features=features, bounds=BOUNDS)

    assert first == second
    assert first != VectorDisplayPayload(layer_id="rivers", features=features, bounds=BOUNDS)


# ── RasterDisplayPayload ──────────────────────────────


def test_raster_payload_accepts_consistent_rgba_and_transform() -> None:
    """范围与仿射变换一致的 RGBA 显示数据应能创建载荷。"""
    payload = RasterDisplayPayload(
        layer_id="dem",
        image_data=make_rgba(),
        transform=TRANSFORM,
        bounds=BOUNDS,
    )

    assert payload.image_data.shape == (2, 2, 4)
    assert payload.transform == TRANSFORM
    assert payload.bounds == BOUNDS


def test_raster_payload_is_immutable() -> None:
    """栅格显示载荷创建后不应允许修改字段。"""
    payload = RasterDisplayPayload(
        layer_id="dem",
        image_data=make_rgba(),
        transform=TRANSFORM,
        bounds=BOUNDS,
    )

    with pytest.raises(FrozenInstanceError):
        payload.bounds = (0.0, 0.0, 9.0, 9.0)  # type: ignore[misc]


def test_raster_payload_rejects_invalid_display_data() -> None:
    """栅格显示载荷应拒绝非 RGBA、非 uint8 或空显示数据。"""
    with pytest.raises(ValueError, match="RGBA"):
        RasterDisplayPayload(
            layer_id="dem",
            image_data=np.zeros((2, 2, 3), dtype=np.uint8),
            transform=TRANSFORM,
            bounds=BOUNDS,
        )
    with pytest.raises(ValueError, match="uint8"):
        RasterDisplayPayload(
            layer_id="dem",
            image_data=np.zeros((2, 2, 4), dtype=np.float32),
            transform=TRANSFORM,
            bounds=BOUNDS,
        )
    with pytest.raises(ValueError, match="不能为空"):
        RasterDisplayPayload(
            layer_id="dem",
            image_data=np.zeros((0, 2, 4), dtype=np.uint8),
            transform=TRANSFORM,
            bounds=BOUNDS,
        )


def test_raster_payload_rejects_degenerate_transform() -> None:
    """栅格显示载荷应拒绝像元尺寸为零的仿射变换。"""
    with pytest.raises(ValueError, match="像元尺寸"):
        RasterDisplayPayload(
            layer_id="dem",
            image_data=make_rgba(),
            transform=Affine(0.0, 0.0, 0.0, 0.0, -1.0, 2.0),
            bounds=BOUNDS,
        )


def test_raster_payload_rejects_inconsistent_bounds() -> None:
    """栅格显示载荷应拒绝与仿射变换推导范围不一致的显示范围。"""
    with pytest.raises(ValueError, match="不一致"):
        RasterDisplayPayload(
            layer_id="dem",
            image_data=make_rgba(),
            transform=TRANSFORM,
            bounds=(0.0, 0.0, 9.0, 9.0),
        )


def test_raster_payload_accepts_rotated_transform_bounds() -> None:
    """旋转栅格应按四个角点计算完整显示范围。"""
    transform = Affine(1.0, 1.0, 0.0, -1.0, 1.0, 0.0)
    payload = RasterDisplayPayload(
        layer_id="rotated",
        image_data=make_rgba(),
        transform=transform,
        bounds=(-0.0, -2.0, 4.0, 2.0),
    )

    assert payload.bounds == (0.0, -2.0, 4.0, 2.0)


# ── DisplayCacheKey ───────────────────────────────────


def test_cache_key_includes_revision_crs_and_resampling() -> None:
    """缓存键应覆盖版本、CRS、符号和重采样设置等全部关键要素。"""
    display_crs: CRS = CRS.from_epsg(3857)
    key = DisplayCacheKey.for_layer(
        make_vector_layer(),
        layer_revision=3,
        display_crs=display_crs,
    )

    assert key.layer_id == "roads"
    assert key.layer_revision == 3
    assert key.source_crs == CRS.from_epsg(4326)
    assert key.display_crs == display_crs
    assert key.raster_resampling is None


def test_cache_key_changes_with_revision_and_display_crs() -> None:
    """版本号或显示 CRS 变化时缓存键应随之变化。"""
    layer: VectorLayer = make_vector_layer()
    base: DisplayCacheKey = DisplayCacheKey.for_layer(layer, 1, CRS.from_epsg(3857))

    assert base != DisplayCacheKey.for_layer(layer, 2, CRS.from_epsg(3857))
    assert base != DisplayCacheKey.for_layer(layer, 1, CRS.from_epsg(4326))


def test_cache_key_changes_with_symbology() -> None:
    """符号配置变化时缓存键应随之变化。"""
    display_crs: CRS = CRS.from_epsg(3857)
    default_key: DisplayCacheKey = DisplayCacheKey.for_layer(
        make_vector_layer(), 1, display_crs
    )
    colored_key: DisplayCacheKey = DisplayCacheKey.for_layer(
        make_vector_layer_with_color("roads", "#FF0000"), 1, display_crs
    )

    assert default_key != colored_key


def test_cache_key_changes_with_raster_resampling() -> None:
    """栅格显示重采样设置变化时缓存键应随之变化。"""
    key: DisplayCacheKey = DisplayCacheKey.for_layer(
        make_vector_layer(), 1, CRS.from_epsg(3857)
    )
    resampled_key: DisplayCacheKey = DisplayCacheKey.for_layer(
        make_vector_layer(), 1, CRS.from_epsg(3857), raster_resampling="nearest"
    )

    assert key != resampled_key


def test_cache_key_is_immutable() -> None:
    """缓存键创建后不应允许修改字段。"""
    key: DisplayCacheKey = DisplayCacheKey.for_layer(
        make_vector_layer(), 1, CRS.from_epsg(3857)
    )

    with pytest.raises(FrozenInstanceError):
        key.layer_revision = 2  # type: ignore[misc]
