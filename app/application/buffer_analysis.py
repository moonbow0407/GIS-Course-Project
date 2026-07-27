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
from app.domain.layer_style import GeometryFamily
from app.domain.vector_layer import VectorLayer

BufferCapStyleName = Literal["round", "flat", "square"]
BufferJoinStyleName = Literal["round", "mitre", "bevel"]
BufferSideTypeName = Literal["full", "left", "right", "outside"]
BufferDistanceUnitName = Literal["millimeter", "centimeter", "meter", "kilometer", "foot", "mile"]

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
_DISTANCE_UNIT_TO_METERS: dict[BufferDistanceUnitName, float] = {
    "millimeter": 0.001,
    "centimeter": 0.01,
    "meter": 1.0,
    "kilometer": 1_000.0,
    "foot": 0.3048,
    "mile": 1_609.344,
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

    # 缓冲距离：按照 distance_unit 解释，不直接使用 CRS 的坐标单位。
    distance: float

    # 用户输入距离的线性单位，内部统一换算为米。
    distance_unit: BufferDistanceUnitName = "meter"

    # 缓冲侧类型：线支持两侧/左侧/右侧，面支持包含原面/仅外侧。
    side_type: BufferSideTypeName = "full"

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

    # 可选计算 CRS；为空时自动选择适合距离单位的投影 CRS。
    analysis_crs: CRS | None = None

    def __post_init__(self) -> None:
        """在进入应用服务前拒绝无法执行或容易产生误解的参数。"""
        if not self.input_layer_id.strip():
            raise InvalidBufferParameters("缓冲区输入图层不能为空。")
        if not self.output_layer_name.strip():
            raise InvalidBufferParameters("缓冲区输出图层名不能为空。")
        if not str(self.output_path).strip():
            raise InvalidBufferParameters("缓冲区输出位置不能为空。")
        if not isfinite(self.distance) or self.distance == 0:
            raise InvalidBufferParameters(
                "缓冲距离不能为零；点、线必须大于零，面可以使用负值向内缓冲。"
            )
        if self.segments < 1:
            raise InvalidBufferParameters("缓冲区圆弧分段数必须至少为 1。")
        if not isfinite(self.mitre_limit) or self.mitre_limit <= 0:
            raise InvalidBufferParameters("斜接比必须是大于零的有限数值。")
        if self.cap_style not in _CAP_STYLE_VALUES:
            raise InvalidBufferParameters("不支持的缓冲区端点样式。")
        if self.join_style not in _JOIN_STYLE_VALUES:
            raise InvalidBufferParameters("不支持的缓冲区连接样式。")
        if self.side_type not in {"full", "left", "right", "outside"}:
            raise InvalidBufferParameters("不支持的缓冲区侧类型。")
        if self.distance_unit not in _DISTANCE_UNIT_TO_METERS:
            raise InvalidBufferParameters("不支持的缓冲距离单位。")


def buffer_features(
    layer: VectorLayer,
    request: BufferRequest,
    distance: float | None = None,
) -> tuple[Feature, ...]:
    """按输入点、线或面图层的专用参数计算缓冲区并保留属性。"""
    resolved_distance: float = request.distance if distance is None else distance
    _validate_geometry_parameters(layer, request, resolved_distance)
    buffered_features: list[Feature] = []
    feature: Feature
    for feature in layer.features:
        if feature.geometry.is_empty:
            continue
        try:
            buffered_geometry: BaseGeometry = _buffer_geometry(
                feature.geometry,
                layer.geometry_family,
                request,
                resolved_distance,
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


def distance_to_crs_units(
    distance: float,
    distance_unit: BufferDistanceUnitName,
    crs: CRS,
) -> float:
    """将用户距离换算为投影 CRS 的坐标单位，避免输入值受 CRS 单位误读。"""
    if distance_unit not in _DISTANCE_UNIT_TO_METERS:
        raise InvalidBufferParameters("不支持的缓冲距离单位。")
    if not crs.is_projected or not crs.axis_info:
        raise InvalidBufferParameters("缓冲区内部计算必须使用米制投影坐标系。")
    unit_conversion_factor: float = crs.axis_info[0].unit_conversion_factor
    if not isfinite(unit_conversion_factor) or unit_conversion_factor <= 0:
        raise InvalidBufferParameters("无法识别分析坐标系的线性单位。")
    distance_in_meters: float = distance_to_meters(distance, distance_unit)
    return distance_in_meters / unit_conversion_factor


def distance_to_meters(distance: float, distance_unit: BufferDistanceUnitName) -> float:
    """将用户输入的距离换算为米，供分析历史和坐标转换共同使用。"""
    if distance_unit not in _DISTANCE_UNIT_TO_METERS:
        raise InvalidBufferParameters("不支持的缓冲距离单位。")
    return distance * _DISTANCE_UNIT_TO_METERS[distance_unit]


def resolve_buffer_analysis_crs(
    layer: VectorLayer,
    preferred_crs: CRS | None = None,
) -> CRS:
    """选择适合线性距离计算的投影 CRS；经纬度图层自动使用本地米制投影。"""
    candidates: tuple[CRS | None, ...] = (preferred_crs, layer.crs)
    candidate: CRS | None
    for candidate in candidates:
        if candidate is not None and candidate.is_projected:
            return candidate
    if layer.crs is None or not layer.crs.is_geographic:
        raise InvalidBufferParameters("缓冲区无法找到适合线性距离的投影坐标系。")

    minimum_x: float
    minimum_y: float
    maximum_x: float
    maximum_y: float
    minimum_x, minimum_y, maximum_x, maximum_y = layer.bounds
    center_longitude: float = (minimum_x + maximum_x) / 2.0
    center_latitude: float = (minimum_y + maximum_y) / 2.0
    if -80.0 <= center_latitude <= 84.0:
        zone: int = min(60, max(1, int((center_longitude + 180.0) // 6.0) + 1))
        epsg_code: int = (32600 if center_latitude >= 0 else 32700) + zone
        return CRS.from_epsg(epsg_code)
    return CRS.from_proj4(
        f"+proj=aeqd +lat_0={center_latitude} +lon_0={center_longitude} "
        "+datum=WGS84 +units=m +no_defs"
    )


def reproject_vector_layer(layer: VectorLayer, target_crs: CRS) -> VectorLayer:
    """把缓冲输入临时转换到计算 CRS，支持无源文件路径的内存图层。"""
    if layer.crs is None:
        raise InvalidBufferParameters(
            f"输入图层“{layer.name}”没有坐标参考系统，无法执行带单位的缓冲区分析。"
        )
    if layer.crs == target_crs:
        return layer
    try:
        projected_features: tuple[Feature, ...] = reproject_features(
            layer.features,
            layer.crs,
            target_crs,
        )
    except Exception as error:
        raise InvalidBufferParameters(
            f"输入图层“{layer.name}”无法转换到缓冲区内部计算坐标系。"
        ) from error
    return VectorLayer.create(
        layer_id=layer.layer_id,
        name=layer.name,
        features=projected_features,
        crs=target_crs,
        source_path=layer.source_path,
        source_layer_name=layer.source_layer_name,
    )


def _validate_geometry_parameters(
    layer: VectorLayer,
    request: BufferRequest,
    distance: float,
) -> None:
    """校验只有特定几何类别才支持的缓冲区参数。"""
    family: GeometryFamily = layer.geometry_family
    if family is GeometryFamily.MIXED:
        raise InvalidBufferParameters(
            f"输入图层“{layer.name}”包含点、线或面混合几何，无法自动确定缓冲区参数。"
        )
    if family is GeometryFamily.POINT:
        if distance <= 0:
            raise InvalidBufferParameters("点图层缓冲距离必须大于零。")
        if request.side_type != "full":
            raise InvalidBufferParameters("点图层不支持侧类型参数。")
        return
    if family is GeometryFamily.LINE:
        if distance <= 0:
            raise InvalidBufferParameters("线图层缓冲距离必须大于零。")
        if request.side_type == "outside":
            raise InvalidBufferParameters("线图层不支持面图层的仅外侧缓冲。")
        return
    if request.side_type in {"left", "right"}:
        raise InvalidBufferParameters("面图层不支持左侧或右侧缓冲。")
    if request.side_type == "outside" and distance <= 0:
        raise InvalidBufferParameters("面图层仅外侧缓冲的距离必须大于零。")


def _buffer_geometry(
    geometry: BaseGeometry,
    family: GeometryFamily,
    request: BufferRequest,
    distance: float,
) -> BaseGeometry:
    """按照几何类别调用 Shapely，并实现线侧向和面外侧缓冲。"""
    if family is GeometryFamily.POINT:
        # 点没有端点或折点，保持圆形缓冲的默认语义。
        return geometry.buffer(distance, quad_segs=request.segments)

    if family is GeometryFamily.LINE:
        single_sided: bool = request.side_type != "full"
        side_distance: float = distance
        if request.side_type == "right":
            # Shapely 用负距离表示有方向线的右侧，左侧使用正距离。
            side_distance = -side_distance
        return geometry.buffer(
            side_distance,
            quad_segs=request.segments,
            cap_style=_CAP_STYLE_VALUES[request.cap_style],
            join_style=_JOIN_STYLE_VALUES[request.join_style],
            mitre_limit=request.mitre_limit,
            single_sided=single_sided,
        )

    buffer_geometry: BaseGeometry = geometry.buffer(
        distance,
        quad_segs=request.segments,
        join_style=_JOIN_STYLE_VALUES[request.join_style],
        mitre_limit=request.mitre_limit,
    )
    if request.side_type == "outside":
        # ArcGIS 的 Outside Only 只保留原面外侧的环带，不把输入面带入结果。
        return buffer_geometry.difference(geometry)
    return buffer_geometry


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
