"""矢量图层样式领域值对象。"""

from dataclasses import dataclass
from enum import Enum


class GeometryFamily(str, Enum):
    """描述图层用于显示的主要几何类别。"""

    POINT = "point"
    LINE = "line"
    POLYGON = "polygon"
    MIXED = "mixed"


@dataclass(frozen=True, slots=True)
class LayerStyle:
    """保存与具体界面框架无关的矢量图层视觉参数。"""

    # 描边颜色：使用十六进制颜色或透明色名称表示边界颜色。
    stroke_color: str

    # 填充颜色：使用十六进制颜色或透明色名称表示内部颜色。
    fill_color: str

    # 线条宽度：以界面逻辑像素为单位，必须大于零。
    line_width: float

    # 点符号大小：以界面逻辑像素为单位，非点图层使用零。
    point_size: float

    # 图层透明度：取值范围为零到一。
    opacity: float

    @classmethod
    def for_geometry_family(cls, family: GeometryFamily) -> "LayerStyle":
        """根据几何类别创建稳定且高对比度的默认样式。"""
        if family is GeometryFamily.POINT:
            return cls(
                stroke_color="#1769d2",
                fill_color="#2f7de1",
                line_width=1.2,
                point_size=8.0,
                opacity=0.9,
            )
        if family is GeometryFamily.LINE:
            return cls(
                stroke_color="#f28c28",
                fill_color="transparent",
                line_width=2.0,
                point_size=0.0,
                opacity=0.95,
            )
        return cls(
            stroke_color="#2f7de1",
            fill_color="#9ec5fe",
            line_width=1.2,
            point_size=0.0,
            opacity=0.65,
        )
