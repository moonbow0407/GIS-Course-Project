"""叠加分析用例的参数校验和几何计算。"""

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, cast

import geopandas as gpd
import pandas as pd
from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from app.application.errors import (
    EmptyOverlayResult,
    InvalidOverlayParameters,
    OverlayAnalysisFailed,
)
from app.domain.feature import AttributeValue, Feature, FeatureId
from app.domain.layer_style import GeometryFamily
from app.domain.vector_layer import VectorLayer

OverlayOperationName = Literal[
    "intersection",
    "union",
    "identity",
    "difference",
    "symmetric_difference",
    "update",
    "point_in_polygon",
    "line_in_polygon",
]
SJoinPredicateName = Literal[
    "intersects",
    "contains",
    "within",
    "touches",
    "crosses",
    "overlaps",
]
SJoinHowName = Literal["inner", "left", "right"]

_GEOMETRIC_OVERLAY_OPS: frozenset[str] = frozenset({
    "intersection",
    "union",
    "identity",
    "difference",
    "symmetric_difference",
    "update",
})
_SPATIAL_JOIN_OPS: frozenset[str] = frozenset({
    "point_in_polygon",
    "line_in_polygon",
})

_OPERATION_LABELS: dict[str, str] = {
    "intersection": "相交",
    "union": "联合",
    "identity": "识别",
    "difference": "擦除",
    "symmetric_difference": "对称差异",
    "update": "更新",
    "point_in_polygon": "点面叠置",
    "line_in_polygon": "线面叠置",
}


def operation_label(operation: OverlayOperationName) -> str:
    """返回叠加操作的中文标签。"""
    return _OPERATION_LABELS.get(operation, operation)


@dataclass(frozen=True, slots=True)
class OverlayRequest:
    """描述一次叠加分析及其输出位置。"""

    # 输入图层编号：主输入图层，必须引用当前地图文档中的矢量图层。
    input_layer_id: str

    # 叠加图层编号：用于叠加或空间连接的第二个矢量图层。
    overlay_layer_id: str

    # 叠加操作：几何叠加（intersection/union/identity/difference/symmetric_difference/update）
    # 或空间连接（point_in_polygon/line_in_polygon）。
    operation: OverlayOperationName

    # 输出文件位置：由用户在界面中指定，支持当前写入器支持的矢量格式。
    output_path: Path

    # 输出图层名称：用于工作区显示，并作为 GeoPackage 的内部图层名。
    output_layer_name: str

    # 保持几何类型：仅对几何叠加操作有效，过滤掉与输入几何类型不同的结果。
    keep_geom_type: bool = True

    # 自动修复几何：仅对几何叠加操作有效，自动修复无效几何后再计算。
    make_valid: bool = True

    # 空间连接谓词：仅对空间连接操作有效，决定匹配的空间关系。
    sjoin_predicate: SJoinPredicateName = "intersects"

    # 空间连接方式：仅对空间连接操作有效。
    # inner=仅保留匹配到的要素，left=保留所有输入要素，right=保留所有叠加要素。
    sjoin_how: SJoinHowName = "inner"

    def __post_init__(self) -> None:
        """验证叠加分析参数满足业务约束。

        异常:
            InvalidOverlayParameters: 输入为空、两图层相同、操作类型无效或路径为空时抛出。
        """
        if not self.input_layer_id:
            raise InvalidOverlayParameters("叠加分析的主输入图层不能为空。")
        if not self.overlay_layer_id:
            raise InvalidOverlayParameters("叠加分析的叠加图层不能为空。")
        if self.input_layer_id == self.overlay_layer_id:
            raise InvalidOverlayParameters("叠加分析的两个输入图层不能相同。")
        if self.operation not in _GEOMETRIC_OVERLAY_OPS and self.operation not in _SPATIAL_JOIN_OPS:
            raise InvalidOverlayParameters(f"不支持的叠加操作类型：{self.operation}")
        if not self.output_layer_name.strip():
            raise InvalidOverlayParameters("叠加分析输出图层名不能为空。")


# ---------------------------------------------------------------------------
# 核心计算入口
# ---------------------------------------------------------------------------


def overlay_features(
    input_layer: VectorLayer,
    overlay_layer: VectorLayer,
    request: OverlayRequest,
) -> tuple[Feature, ...]:
    """按操作类型执行几何叠加或空间连接，返回结果要素。

    参数:
        input_layer: 主输入矢量图层。
        overlay_layer: 叠加或空间连接的第二个矢量图层。
        request: 包含操作类型、几何参数和空间连接配置的叠加分析请求。

    返回:
        不可变的结果要素元组。

    异常:
        InvalidOverlayParameters: 几何类型与操作不兼容时抛出。
        OverlayAnalysisFailed: 几何计算过程中发生异常时抛出。
        EmptyOverlayResult: 计算结果不包含任何有效几何时抛出。
    """
    _validate_geometry_compatibility(input_layer, overlay_layer, request.operation)

    input_gdf: gpd.GeoDataFrame = _vector_layer_to_geodataframe(input_layer)
    overlay_gdf: gpd.GeoDataFrame = _vector_layer_to_geodataframe(overlay_layer)

    if input_gdf.crs is not None and overlay_gdf.crs is not None and input_gdf.crs != overlay_gdf.crs:
        overlay_gdf = overlay_gdf.to_crs(input_gdf.crs)

    try:
        if request.operation in _GEOMETRIC_OVERLAY_OPS:
            result_gdf: gpd.GeoDataFrame = _perform_geometric_overlay(
                input_gdf,
                overlay_gdf,
                how=request.operation,
                keep_geom_type=request.keep_geom_type,
                make_valid=request.make_valid,
            )
        else:
            result_gdf = _perform_spatial_join(
                input_gdf,
                overlay_gdf,
                how=request.sjoin_how,
                predicate=request.sjoin_predicate,
            )
    except Exception as error:
        raise OverlayAnalysisFailed(f"叠加分析计算失败：{error}") from error

    if result_gdf.empty or len(result_gdf) == 0:
        raise EmptyOverlayResult(
            f"叠加分析（{operation_label(request.operation)}）未产生任何结果几何，"
            "请检查两个图层的空间重叠情况。"
        )

    return _geodataframe_to_features(result_gdf)


# ---------------------------------------------------------------------------
# 几何兼容性验证
# ---------------------------------------------------------------------------


def _validate_geometry_compatibility(
    input_layer: VectorLayer,
    overlay_layer: VectorLayer,
    operation: OverlayOperationName,
) -> None:
    """验证两个图层的几何类型与所选叠加操作兼容。

    参数:
        input_layer: 主输入图层。
        overlay_layer: 叠加图层。
        operation: 叠加操作类型。

    异常:
        InvalidOverlayParameters: 几何类型不兼容时抛出。
    """
    if operation in _GEOMETRIC_OVERLAY_OPS:
        if input_layer.geometry_family != GeometryFamily.POLYGON:
            raise InvalidOverlayParameters(
                f"几何叠加要求主输入图层为面图层，当前为“{input_layer.geometry_family.value}”。"
            )
        if overlay_layer.geometry_family != GeometryFamily.POLYGON:
            raise InvalidOverlayParameters(
                f"几何叠加要求叠加图层为面图层，当前为“{overlay_layer.geometry_family.value}”。"
            )
    elif operation == "point_in_polygon":
        if input_layer.geometry_family != GeometryFamily.POINT:
            raise InvalidOverlayParameters(
                f"点面叠置要求输入图层为点图层，当前为“{input_layer.geometry_family.value}”。"
            )
        if overlay_layer.geometry_family != GeometryFamily.POLYGON:
            raise InvalidOverlayParameters(
                f"点面叠置要求叠加图层为面图层，当前为“{overlay_layer.geometry_family.value}”。"
            )
    elif operation == "line_in_polygon":
        if input_layer.geometry_family != GeometryFamily.LINE:
            raise InvalidOverlayParameters(
                f"线面叠置要求输入图层为线图层，当前为“{input_layer.geometry_family.value}”。"
            )
        if overlay_layer.geometry_family != GeometryFamily.POLYGON:
            raise InvalidOverlayParameters(
                f"线面叠置要求叠加图层为面图层，当前为“{overlay_layer.geometry_family.value}”。"
            )


# ---------------------------------------------------------------------------
# 几何叠加
# ---------------------------------------------------------------------------


def _perform_geometric_overlay(
    input_gdf: gpd.GeoDataFrame,
    overlay_gdf: gpd.GeoDataFrame,
    how: str,
    keep_geom_type: bool,
    make_valid: bool,
) -> gpd.GeoDataFrame:
    """执行几何叠加或合成更新操作。

    参数:
        input_gdf: 主输入 GeoDataFrame。
        overlay_gdf: 叠加 GeoDataFrame。
        how: 叠加操作类型，包括 update。
        keep_geom_type: 是否仅保留与输入几何类型相同的要素。
        make_valid: 是否在计算前自动修复无效几何。

    返回:
        叠加结果 GeoDataFrame。
    """
    if how == "update":
        return _perform_update(input_gdf, overlay_gdf, keep_geom_type, make_valid)
    return gpd.overlay(
        input_gdf,
        overlay_gdf,
        how=how,
        keep_geom_type=keep_geom_type,
        make_valid=make_valid,
    )


def _perform_update(
    input_gdf: gpd.GeoDataFrame,
    overlay_gdf: gpd.GeoDataFrame,
    keep_geom_type: bool,
    make_valid: bool,
) -> gpd.GeoDataFrame:
    """合成 ArcGIS 更新操作：(输入 - 叠加) ∪ 叠加。

    计算输入图层与叠加图层的差异区域，然后将完整的叠加图层合并回去。
    在覆盖区域内，叠加图层的属性和几何完全替代输入图层；
    在非覆盖区域内，保留输入图层的原始属性。

    参数:
        input_gdf: 主输入 GeoDataFrame。
        overlay_gdf: 叠加 GeoDataFrame。
        keep_geom_type: 是否仅保留与输入几何类型相同的要素。
        make_valid: 是否在计算前自动修复无效几何。

    返回:
        更新结果 GeoDataFrame。
    """
    diff_gdf: gpd.GeoDataFrame = gpd.overlay(
        input_gdf,
        overlay_gdf,
        how="difference",
        keep_geom_type=keep_geom_type,
        make_valid=make_valid,
    )
    result_gdf: gpd.GeoDataFrame = cast(
        gpd.GeoDataFrame,
        pd.concat([diff_gdf, overlay_gdf], ignore_index=True),
    )
    if not isinstance(result_gdf, gpd.GeoDataFrame):
        result_gdf = gpd.GeoDataFrame(result_gdf, crs=input_gdf.crs, geometry="geometry")
    return result_gdf


# ---------------------------------------------------------------------------
# 空间连接
# ---------------------------------------------------------------------------


def _perform_spatial_join(
    input_gdf: gpd.GeoDataFrame,
    overlay_gdf: gpd.GeoDataFrame,
    how: SJoinHowName,
    predicate: SJoinPredicateName,
) -> gpd.GeoDataFrame:
    """执行空间属性连接：将叠加图层的属性附加到输入图层的要素上。

    参数:
        input_gdf: 主输入 GeoDataFrame（点或线）。
        overlay_gdf: 叠加 GeoDataFrame（面）。
        how: 连接方式，inner/left/right。
        predicate: 空间关系谓词。

    返回:
        带有叠加图层属性的结果 GeoDataFrame。
    """
    result: gpd.GeoDataFrame = gpd.sjoin(
        input_gdf,
        overlay_gdf,
        how=how,
        predicate=predicate,
        lsuffix="_input",
        rsuffix="_overlay",
    )
    if "index_right" in result.columns:
        result = result.drop(columns=["index_right"])
    return result


# ---------------------------------------------------------------------------
# 领域模型 ↔ GeoDataFrame 转换
# ---------------------------------------------------------------------------


def _vector_layer_to_geodataframe(layer: VectorLayer) -> gpd.GeoDataFrame:
    """将领域矢量图层转换为 GeoDataFrame，保留几何、属性和 CRS。

    参数:
        layer: 领域矢量图层。

    返回:
        等效的 GeoDataFrame。
    """
    geometries: list[BaseGeometry] = []
    records: list[dict[str, Any]] = []
    for feature in layer.features:
        geometries.append(feature.geometry)
        records.append(dict(feature.attributes))
    gdf: gpd.GeoDataFrame = gpd.GeoDataFrame(
        records,
        geometry=geometries,
        crs=layer.crs,
    )
    return gdf


def _geodataframe_to_features(gdf: gpd.GeoDataFrame) -> tuple[Feature, ...]:
    """将 GeoDataFrame 转换回不可变 Feature 元组。

    使用 reset_index 后的连续整数作为 FID。

    参数:
        gdf: 待转换的 GeoDataFrame。

    返回:
        不可变的 Feature 元组。
    """
    cleaned: gpd.GeoDataFrame = gdf.reset_index(drop=True)
    features: list[Feature] = []
    for idx, row in cleaned.iterrows():
        if row.geometry is None or row.geometry.is_empty:
            continue
        attributes: dict[str, AttributeValue] = {}
        for col_name in cleaned.columns:
            if col_name == "geometry":
                continue
            value = row[col_name]
            attributes[col_name] = _normalize_attribute_value(value)
        features.append(
            Feature(
                fid=int(idx),
                geometry=row.geometry,
                attributes=MappingProxyType(attributes),
            )
        )
    return tuple(features)


def _normalize_attribute_value(value: Any) -> AttributeValue:
    """将 pandas/numpy 值规范化为领域层可接受的属性类型。

    参数:
        value: 来自 GeoDataFrame 单元格的原始值。

    返回:
        规范化的 AttributeValue。
    """
    import math

    import numpy as np

    if value is None:
        return None
    if isinstance(value, (str, int, float, bool, date, datetime)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        v: float = float(value)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.ndarray):
        return str(value.tolist())
    if isinstance(value, (pd.Timestamp,)):
        return value.to_pydatetime()
    if hasattr(value, "item"):
        try:
            item = value.item()
            if item is None:
                return None
            if isinstance(item, (str, int, float, bool)):
                return item
        except (ValueError, TypeError):
            pass
    return str(value)
