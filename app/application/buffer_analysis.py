"""缓冲区分析用例的参数校验和几何计算。"""

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Literal

from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as transform_geometry
from shapely.ops import unary_union

from app.application.errors import (
    BufferAnalysisFailed,
    EmptyBufferResult,
    InvalidBufferParameters,
)
from app.domain.feature import Feature
from app.domain.vector_layer import VectorLayer

BufferCapStyleName = Literal["round", "flat", "square"]
BufferJoinStyleName = Literal["round", "mitre", "bevel"]

_CAP_STYLE_VALUES: dict[BufferCapStyleName, int] = {
    "round": 1,
    "flat": 2,
    "square": 3,
}
_JOIN_STYLE_VALUES: dict[BufferJoinStyleName, int] = {
    "round": 1,
    "mitre": 2,
    "bevel": 3,
}


@dataclass(frozen=True, slots=True)
class BufferRequest:
    """描述一次缓冲区分析及其输出位置。"""

    # 输入图层编号：必须引用当前地图文档中的矢量图层。
    input_layer_id: str

    # 输出文件位置：由用户在界面中指定，支持当前写入器支持的矢量格式。
    output_path: Path

    # 输出图层名称：用于工作区显示，并作为 GeoPackage 的内部图层名。
    output_layer_name: str

    # 缓冲距离：单位等于分析坐标系的坐标单位。
    distance: float

    # 圆弧近似分段数：每个四分之一圆弧使用的线段数量。
    segments: int = 8

    # 线端点样式：仅对线几何产生可见影响。
    cap_style: BufferCapStyleName = "round"

    # 折点连接样式：影响线和面边界的折点。
    join_style: BufferJoinStyleName = "round"

    # 尖角连接的最大斜接比。
    mitre_limit: float = 5.0

    # 是否将全部缓冲结果融合为一个或多个几何。
    dissolve: bool = False

    # 可选分析 CRS；为空时使用当前地图显示 CRS。
    analysis_crs: CRS | None = None

    def __post_init__(self) -> None:
        """在进入应用服务前拒绝无法执行或容易产生误解的参数。"""
        if not self.input_layer_id.strip():
            raise InvalidBufferParameters("缓冲区输入图层不能为空。")
        if not self.output_layer_name.strip():
            raise InvalidBufferParameters("缓冲区输出图层名不能为空。")
        if not str(self.output_path).strip():
            raise InvalidBufferParameters("缓冲区输出位置不能为空。")
        if not isfinite(self.distance) or self.distance <= 0:
            raise InvalidBufferParameters("缓冲距离必须是大于零的有限数值。")
        if self.segments < 1:
            raise InvalidBufferParameters("缓冲区圆弧分段数必须至少为 1。")
        if not isfinite(self.mitre_limit) or self.mitre_limit <= 0:
            raise InvalidBufferParameters("斜接比必须是大于零的有限数值。")
        if self.cap_style not in _CAP_STYLE_VALUES:
            raise InvalidBufferParameters("不支持的缓冲区端点样式。")
        if self.join_style not in _JOIN_STYLE_VALUES:
            raise InvalidBufferParameters("不支持的缓冲区连接样式。")


def buffer_features(layer: VectorLayer, request: BufferRequest) -> tuple[Feature, ...]:
    """按请求参数计算图层要素缓冲区，并保留未融合结果的属性。"""
    buffered_features: list[Feature] = []
    feature: Feature
    for feature in layer.features:
        if feature.geometry.is_empty:
            continue
        try:
            buffered_geometry: BaseGeometry = feature.geometry.buffer(
                request.distance,
                quad_segs=request.segments,
                cap_style=_CAP_STYLE_VALUES[request.cap_style],
                join_style=_JOIN_STYLE_VALUES[request.join_style],
                mitre_limit=request.mitre_limit,
            )
        except Exception as error:
            raise BufferAnalysisFailed(
                f"图层“{layer.name}”的要素缓冲计算失败。"
            ) from error
        if not buffered_geometry.is_empty:
            buffered_features.append(
                Feature(
                    fid=feature.fid,
                    geometry=buffered_geometry,
                    attributes=feature.attributes,
                )
            )

    if not buffered_features:
        raise EmptyBufferResult("缓冲区分析没有产生可用几何。")
    if not request.dissolve:
        return tuple(buffered_features)

    try:
        dissolved_geometry: BaseGeometry = unary_union(
            [feature.geometry for feature in buffered_features]
        )
    except Exception as error:
        raise BufferAnalysisFailed("缓冲区结果融合失败。") from error
    if dissolved_geometry.is_empty:
        raise EmptyBufferResult("缓冲区分析融合后没有产生可用几何。")
    return (
        Feature(
            fid="buffer_1",
            geometry=dissolved_geometry,
            attributes={"source_count": len(buffered_features)},
        ),
    )


def reproject_features(
    features: tuple[Feature, ...],
    source_crs: CRS,
    target_crs: CRS,
) -> tuple[Feature, ...]:
    """将分析结果几何转换回地图 CRS，属性和要素编号保持不变。"""
    if source_crs == target_crs:
        return features
    try:
        transformer: Transformer = Transformer.from_crs(
            source_crs,
            target_crs,
            always_xy=True,
        )
        return tuple(
            Feature(
                fid=feature.fid,
                geometry=transform_geometry(transformer.transform, feature.geometry),
                attributes=feature.attributes,
            )
            for feature in features
        )
    except Exception as error:
        raise BufferAnalysisFailed("缓冲区结果无法转换到地图显示坐标系。") from error
