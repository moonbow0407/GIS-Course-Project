"""矢量图层领域模型。"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeAlias
from uuid import uuid4

from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from app.domain.feature import Feature
from app.domain.layer_style import GeometryFamily, LayerStyle

Bounds: TypeAlias = tuple[float, float, float, float]


def _geometry_families(geometry: BaseGeometry) -> set[GeometryFamily]:
    """递归提取单个 Shapely 几何对象包含的显示类别。"""
    geometry_type: str = geometry.geom_type

    if geometry_type in {"Point", "MultiPoint"}:
        return {GeometryFamily.POINT}

    if geometry_type in {"LineString", "LinearRing", "MultiLineString"}:
        return {GeometryFamily.LINE}

    if geometry_type in {"Polygon", "MultiPolygon"}:
        return {GeometryFamily.POLYGON}

    if geometry_type == "GeometryCollection":
        families: set[GeometryFamily] = set()
        member: BaseGeometry

        for member in geometry.geoms:
            if not member.is_empty:
                families.update(_geometry_families(member))
        return families

    return set()


@dataclass(frozen=True, slots=True)
class VectorLayer:
    """表示已经转换为统一领域模型的内存矢量图层。"""

    # 图层编号：应用内部生成或调用方提供的稳定唯一标识。
    layer_id: str

    # 图层名称：用于图层面板、状态栏和导出名称显示。
    name: str

    # 要素集合：按源数据顺序保存图层中的全部空间要素。
    features: tuple[Feature, ...]

    # 坐标参考系统：为空表示源数据没有声明坐标系。
    crs: CRS | None

    # 数据源路径：记录图层来源；内存构造图层时可以为空。
    source_path: Path | None = None

    # 图层范围：根据全部有效几何计算得到的最小包围矩形。
    bounds: Bounds = field(init=False)

    # 几何类别：表示点、线、面或混合几何图层。
    geometry_family: GeometryFamily = field(init=False)

    # 图层样式：根据几何类别生成的界面无关默认样式。
    style: LayerStyle = field(init=False)

    def __post_init__(self) -> None:
        """校验图层要素，并派生图层范围、几何类别和默认样式。"""
        if not self.features:
            raise ValueError("矢量图层必须至少包含一个要素。")

        usable_features: tuple[Feature, ...] = tuple(
            feature for feature in self.features if not feature.geometry.is_empty
        )
        if not usable_features:
            raise ValueError("矢量图层不包含可用几何。")

        families: set[GeometryFamily] = set()

        minimum_x_values: list[float] = []
        minimum_y_values: list[float] = []
        maximum_x_values: list[float] = []
        maximum_y_values: list[float] = []

        feature: Feature

        for feature in usable_features:
            families.update(_geometry_families(feature.geometry))
            feature_bounds: tuple[float, float, float, float] = feature.geometry.bounds
            minimum_x_values.append(feature_bounds[0])
            minimum_y_values.append(feature_bounds[1])
            maximum_x_values.append(feature_bounds[2])
            maximum_y_values.append(feature_bounds[3])

        if not families:
            raise ValueError("矢量图层不包含受支持的可用几何。")

        geometry_family: GeometryFamily = (
            next(iter(families)) if len(families) == 1 else GeometryFamily.MIXED
        )
        bounds: Bounds = (
            min(minimum_x_values),
            min(minimum_y_values),
            max(maximum_x_values),
            max(maximum_y_values),
        )
        object.__setattr__(self, "geometry_family", geometry_family)
        object.__setattr__(self, "bounds", bounds)
        object.__setattr__(self, "style", LayerStyle.for_geometry_family(geometry_family))

    @classmethod
    def create(
        cls,
        name: str,
        features: tuple[Feature, ...],
        crs: CRS | None,
        source_path: Path | None = None,
        layer_id: str | None = None,
    ) -> "VectorLayer":
        """创建矢量图层，并在未提供编号时生成稳定的随机编号。"""
        resolved_layer_id: str = layer_id or uuid4().hex
        return cls(
            layer_id=resolved_layer_id,
            name=name,
            features=features,
            crs=crs,
            source_path=source_path,
        )
