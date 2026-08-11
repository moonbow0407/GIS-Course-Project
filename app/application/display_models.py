"""地图显示载荷与显示缓存键的不可变值对象。

显示载荷保存图层在特定显示坐标系下的绘制数据，与原始领域图层分离：
领域图层保留原始 CRS 语义供分析、编辑和导出使用，显示载荷只供
渲染器、点选和缩放定位使用。显示缓存键标识一次显示缓存的有效性，
任何关键要素变化（几何版本、CRS、符号或栅格显示设置）都会生成
不同的键，从而触发缓存重建。
"""

import json
import zlib
from dataclasses import dataclass
from math import isfinite
from typing import TypeAlias

import numpy as np
from affine import Affine
from numpy.typing import NDArray
from pyproj import CRS

from app.domain.feature import Feature
from app.domain.spatial_layer import SpatialLayer
from app.domain.symbology import RasterSymbology, VectorSymbology, symbology_to_dict
from app.domain.vector_layer import Bounds

# 空间范围比较容差：浮点运算产生的微小差异不应判定为不一致。
_BOUNDS_TOLERANCE: float = 1e-9


@dataclass(frozen=True, slots=True)
class VectorDisplayPayload:
    """表示矢量图层在显示坐标系下的不可变绘制载荷。"""

    # 图层编号：用于把载荷与工作区图层关联。
    layer_id: str

    # 显示要素：几何坐标已转换到显示坐标系，属性与编号保持不变。
    features: tuple[Feature, ...]

    # 显示范围：使用显示坐标系表示的最小包围矩形。
    bounds: Bounds

    def __post_init__(self) -> None:
        """校验显示范围，保证载荷可用于渲染、点选和缩放定位。"""
        _validate_bounds(self.bounds)


@dataclass(frozen=True, slots=True)
class RasterDisplayPayload:
    """表示栅格图层在显示坐标系下的不可变 RGBA 显示载荷。"""

    # 图层编号：用于把载荷与工作区图层关联。
    layer_id: str

    # RGBA 像素：按显示预览的行列保存八位四通道数据。
    image_data: NDArray[np.uint8]

    # 仿射变换：将显示像素列行坐标转换为显示坐标系中的地图坐标。
    transform: Affine

    # 显示范围：使用显示坐标系表示的最小包围矩形。
    bounds: Bounds

    def __post_init__(self) -> None:
        """校验 RGBA 数据、仿射变换及其与空间范围的一致性。

        显示像素与仿射变换必须完整覆盖显示范围：渲染器和点选都依赖
        同一套坐标关系，两者不一致时会在画布上出现错位。
        """
        _validate_bounds(self.bounds)
        if self.image_data.ndim != 3 or self.image_data.shape[2] != 4:
            raise ValueError("栅格显示数据必须是高度×宽度×4 的 RGBA 数组。")
        if self.image_data.dtype != np.uint8:
            raise ValueError("栅格显示数据必须使用 uint8 类型。")
        if self.image_data.shape[0] == 0 or self.image_data.shape[1] == 0:
            raise ValueError("栅格显示数据不能为空。")
        if self.transform.a == 0 or self.transform.e == 0:
            raise ValueError("栅格显示仿射变换的像元尺寸不能为零。")
        derived_bounds: Bounds = bounds_from_transform(
            self.transform,
            self.image_data.shape[1],
            self.image_data.shape[0],
        )
        if not _bounds_close(self.bounds, derived_bounds):
            raise ValueError("栅格显示范围与仿射变换推导范围不一致。")


DisplayPayload: TypeAlias = VectorDisplayPayload | RasterDisplayPayload


@dataclass(frozen=True, slots=True)
class DisplayCacheKey:
    """标识一次显示缓存的有效性。

    缓存键覆盖图层编号、内容版本、源 CRS、显示 CRS、符号配置和
    栅格显示重采样设置；任一要素变化都会使键失效，从而重建缓存。
    """

    # 图层编号：区分不同图层的显示缓存。
    layer_id: str

    # 图层版本号：几何、样式或显示设置变更时由地图文档递增。
    layer_revision: int

    # 图层原始坐标系：源数据 CRS 变化时应重新生成显示缓存。
    source_crs: CRS | None

    # 地图显示坐标系：显示 CRS 变化时所有图层的缓存都会失效。
    display_crs: CRS | None

    # 符号配置版本：符号变化会改变绘制结果，必须参与缓存键。
    symbology_version: int

    # 栅格显示重采样设置：矢量和未使用重采样的栅格使用空值。
    raster_resampling: str | None = None

    @classmethod
    def for_layer(
        cls,
        layer: SpatialLayer,
        layer_revision: int,
        display_crs: CRS | None,
        raster_resampling: str | None = None,
    ) -> "DisplayCacheKey":
        """根据图层当前状态生成显示缓存键。

        参数:
            layer: 工作区中的领域图层。
            layer_revision: 地图文档维护的图层版本号。
            display_crs: 当前地图显示坐标系。
            raster_resampling: 栅格显示重采样方法；矢量图层使用空值。

        返回:
            覆盖图层编号、版本、CRS、符号和重采样设置的缓存键。
        """
        return cls(
            layer_id=layer.layer_id,
            layer_revision=layer_revision,
            source_crs=layer.crs,
            display_crs=display_crs,
            symbology_version=_symbology_version(layer.symbology),
            raster_resampling=raster_resampling,
        )


def bounds_from_transform(transform: Affine, width: int, height: int) -> Bounds:
    """根据仿射变换和行列数推导像素覆盖的空间范围。

    仿射变换允许旋转，四个角点坐标需要分别计算后再取最小和最大值。
    """
    corners: tuple[tuple[float, float], ...] = (
        transform * (0, 0),
        transform * (width, 0),
        transform * (0, height),
        transform * (width, height),
    )
    corner_xs: tuple[float, ...] = tuple(point[0] for point in corners)
    corner_ys: tuple[float, ...] = tuple(point[1] for point in corners)
    return (
        min(corner_xs),
        min(corner_ys),
        max(corner_xs),
        max(corner_ys),
    )


def _validate_bounds(bounds: Bounds) -> None:
    """拒绝非有限数值或最小角大于最大角的空间范围。"""
    if not all(isfinite(value) for value in bounds):
        raise ValueError("显示范围必须为有限数值。")
    if bounds[0] > bounds[2] or bounds[1] > bounds[3]:
        raise ValueError("显示范围无效：最小角不能大于最大角。")


def _bounds_close(left: Bounds, right: Bounds) -> bool:
    """在容差内比较两个空间范围；容差随范围尺度放大。"""
    scale: float = max(1.0, left[2] - left[0], left[3] - left[1])
    return all(
        abs(left_value - right_value) <= _BOUNDS_TOLERANCE * scale
        for left_value, right_value in zip(left, right, strict=True)
    )


def _symbology_version(symbology: VectorSymbology | RasterSymbology | None) -> int:
    """把符号配置序列化为稳定的整数版本。

    缓存键需要区分不同符号配置，但直接存放大型配置既浪费内存，
    又让键的可读性变差；使用稳定的校验和即可在符号变化时使键失效。
    """
    if symbology is None:
        return 0
    serialized: str = json.dumps(
        symbology_to_dict(symbology),
        sort_keys=True,
        ensure_ascii=False,
    )
    return zlib.crc32(serialized.encode("utf-8"))
