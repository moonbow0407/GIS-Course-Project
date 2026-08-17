"""矢量图层领域模型。"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeAlias
from uuid import uuid4

from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from app.domain.feature import Feature
from app.domain.labeling import LabelingConfig
from app.domain.layer_style import GeometryFamily, LayerStyle
from app.domain.lod import LodPyramid
from app.domain.symbology import VectorRendererType, VectorSymbology

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
        # 集合本身没有单一类别，需要递归合并每个非空成员的类别。
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

    # 数据源内的子图层名称：GeoPackage 等容器格式需要额外记录此字段。
    source_layer_name: str | None = None

    # 数据库图层编号：非空时表示可通过数据库服务重新读取原始几何。
    database_layer_id: int | None = None

    # 符号系统：为空时按几何类型生成单一符号。
    symbology: VectorSymbology | None = None

    # 动态标注配置：为空表示该图层尚未配置标注。
    labeling: LabelingConfig | None = None

    # 多级细节层次：为空表示尚未生成，渲染时按原始几何绘制。
    lod: LodPyramid | None = None

    # CRS 是否来自工程内定义/修正覆盖，而非源文件声明。
    crs_override: bool = False

    # 图层范围：根据全部有效几何计算得到的最小包围矩形。
    bounds: Bounds = field(init=False)

    # 几何类别：表示点、线、面或混合几何图层，显式传入时允许空要素。
    geometry_family: GeometryFamily | None = field(default=None)

    # 图层样式：根据几何类别生成的界面无关默认样式。
    style: LayerStyle = field(init=False)

    def __post_init__(self) -> None:
        """校验图层要素，并派生图层范围、几何类别和默认样式。

        当显式传入 geometry_family 时允许空要素，用于新建空白图层；
        否则通过自动检测派生几何类别，空要素时抛出 ValueError。
        """
        # Qt 下拉框会把 str Enum 存成裸字符串；先规范成枚举，后续 is / == 才可靠。
        if self.geometry_family is not None and not isinstance(
            self.geometry_family, GeometryFamily
        ):
            object.__setattr__(
                self, "geometry_family", GeometryFamily(self.geometry_family)
            )
        # 显式指定几何类型时允许空图层（新建空白图层场景）。
        if self.geometry_family is not None and not self.features:
            empty_bounds: Bounds = (0.0, 0.0, 0.0, 0.0)
            object.__setattr__(self, "bounds", empty_bounds)
            empty_default_style: LayerStyle = LayerStyle.for_geometry_family(
                self.geometry_family
            )
            empty_resolved_symbology: VectorSymbology = self.symbology or VectorSymbology(
                renderer_type=VectorRendererType.SIMPLE,
                base_symbol=empty_default_style,
            )
            object.__setattr__(self, "symbology", empty_resolved_symbology)
            object.__setattr__(self, "style", empty_resolved_symbology.base_symbol)
            return

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

        # auto-detect 优先于显式传入的值（兼容性）
        geometry_family: GeometryFamily = (
            next(iter(families)) if len(families) == 1 else GeometryFamily.MIXED
        )
        computed_bounds: Bounds = (
            min(minimum_x_values),
            min(minimum_y_values),
            max(maximum_x_values),
            max(maximum_y_values),
        )
        # frozen 数据类的派生字段只能在初始化阶段通过底层接口写入。
        object.__setattr__(self, "geometry_family", geometry_family)
        object.__setattr__(self, "bounds", computed_bounds)
        computed_default_style: LayerStyle = LayerStyle.for_geometry_family(geometry_family)
        computed_resolved_symbology: VectorSymbology = self.symbology or VectorSymbology(
            renderer_type=VectorRendererType.SIMPLE,
            base_symbol=computed_default_style,
        )
        object.__setattr__(self, "symbology", computed_resolved_symbology)
        object.__setattr__(self, "style", computed_resolved_symbology.base_symbol)

    @classmethod
    def create(
        cls,
        name: str,
        features: tuple[Feature, ...],
        crs: CRS | None,
        source_path: Path | None = None,
        source_layer_name: str | None = None,
        database_layer_id: int | None = None,
        layer_id: str | None = None,
        symbology: VectorSymbology | None = None,
        labeling: LabelingConfig | None = None,
        geometry_family: GeometryFamily | None = None,
        crs_override: bool = False,
    ) -> "VectorLayer":
        """创建矢量图层，并在未提供编号时生成稳定的随机编号。

        参数:
            geometry_family: 显式指定几何类别，允许传入空要素以创建空白图层。
        """
        resolved_layer_id: str = layer_id or uuid4().hex
        return cls(
            layer_id=resolved_layer_id,
            name=name,
            features=features,
            crs=crs,
            source_path=source_path,
            source_layer_name=source_layer_name,
            database_layer_id=database_layer_id,
            symbology=symbology,
            labeling=labeling,
            geometry_family=geometry_family,
            crs_override=crs_override,
        )
