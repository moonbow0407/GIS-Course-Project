"""空间要素领域模型。"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import TypeAlias

from shapely.geometry.base import BaseGeometry

FeatureId: TypeAlias = str | int
AttributeValue: TypeAlias = str | int | float | bool | date | datetime | None


@dataclass(frozen=True, slots=True)
class Feature:
    """表示具有稳定编号、空间几何和属性集合的单个空间要素。"""

    # 要素编号：标识要素在所属图层中的唯一身份。
    fid: FeatureId

    # 空间几何：保存点、线、面或复合 Shapely 几何对象。
    geometry: BaseGeometry

    # 要素属性：保存字段名称到受支持属性值的只读映射。
    attributes: Mapping[str, AttributeValue]

    def __post_init__(self) -> None:
        """复制属性映射，避免外部字典修改已经创建的要素。"""
        normalized_attributes: dict[str, AttributeValue] = dict(self.attributes)
        readonly_attributes: Mapping[str, AttributeValue] = MappingProxyType(normalized_attributes)
        object.__setattr__(self, "attributes", readonly_attributes)
