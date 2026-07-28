"""矢量与栅格图层的可持久化符号系统模型。"""

from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import Enum
from typing import cast

from app.domain.feature import AttributeValue, Feature
from app.domain.layer_style import LayerStyle


class VectorRendererType(str, Enum):
    """矢量图层支持的主符号类型。"""

    SIMPLE = "simple"
    UNIQUE = "unique"
    GRADUATED = "graduated"


class RasterRendererType(str, Enum):
    """栅格图层支持的显示模式。"""

    RGB = "rgb"
    STRETCH = "stretch"


class StretchType(str, Enum):
    """首版支持的栅格拉伸算法。"""

    MIN_MAX = "min_max"
    PERCENT_CLIP = "percent_clip"


@dataclass(frozen=True, slots=True)
class UniqueValueClass:
    """描述一个唯一值类别及其符号。"""

    value_key: str
    label: str
    symbol: LayerStyle
    visible: bool = True


@dataclass(frozen=True, slots=True)
class GraduatedClass:
    """描述一个数值分级区间及其符号。"""

    lower: float
    upper: float
    label: str
    symbol: LayerStyle
    visible: bool = True


@dataclass(frozen=True, slots=True)
class VectorSymbology:
    """保存矢量单一、唯一值或分级颜色配置。"""

    renderer_type: VectorRendererType
    base_symbol: LayerStyle
    field_name: str | None = None
    color_scheme: str = "standard"
    unique_classes: tuple[UniqueValueClass, ...] = ()
    graduated_classes: tuple[GraduatedClass, ...] = ()
    classification_method: str = "equal_interval"
    other_symbol: LayerStyle | None = None
    other_visible: bool = True

    def symbol_for(self, feature: Feature) -> LayerStyle | None:
        """返回要素应使用的符号；类别隐藏时返回空值。"""
        if self.renderer_type is VectorRendererType.SIMPLE or self.field_name is None:
            return self.base_symbol
        value: AttributeValue = feature.attributes.get(self.field_name)
        if self.renderer_type is VectorRendererType.UNIQUE:
            value_key: str = attribute_value_key(value)
            for category in self.unique_classes:
                if category.value_key == value_key:
                    return category.symbol if category.visible else None
            if not self.other_visible:
                return None
            return self.other_symbol or self.base_symbol
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        numeric_value: float = float(value)
        for graduated_category in self.graduated_classes:
            if graduated_category.lower <= numeric_value <= graduated_category.upper:
                return (
                    graduated_category.symbol if graduated_category.visible else None
                )
        return None


@dataclass(frozen=True, slots=True)
class RasterSymbology:
    """保存 RGB 合成或单波段拉伸配置。"""

    renderer_type: RasterRendererType
    rgb_bands: tuple[int, int, int] = (0, 1, 2)
    stretch_band: int = 0
    stretch_type: StretchType = StretchType.PERCENT_CLIP
    lower_percent: float = 2.0
    upper_percent: float = 98.0
    color_scheme: str = "gray"
    inverted: bool = False


CATEGORICAL_SCHEMES: dict[str, tuple[str, ...]] = {
    "standard": (
        "#4E79A7",
        "#F28E2B",
        "#59A14F",
        "#E15759",
        "#B07AA1",
        "#76B7B2",
        "#EDC948",
        "#FF9DA7",
        "#9C755F",
        "#7F8C8D",
    ),
    "soft": (
        "#8FB3D9",
        "#F4B77A",
        "#8BC58B",
        "#E58B8B",
        "#C6A0D5",
        "#9FD2CF",
        "#E9D889",
        "#F3B8C2",
        "#C2A18C",
        "#B5BBC1",
    ),
    "contrast": (
        "#0057B8",
        "#F57C00",
        "#00843D",
        "#C62828",
        "#6A1B9A",
        "#00838F",
        "#D4A000",
        "#AD1457",
        "#5D4037",
        "#37474F",
    ),
}

COLOR_RAMPS: dict[str, tuple[str, ...]] = {
    "gray": ("#000000", "#FFFFFF"),
    "blue": ("#F7FBFF", "#6BAED6", "#08306B"),
    "viridis": ("#440154", "#21918C", "#FDE725"),
    "terrain": ("#2E8B57", "#E5D96F", "#9A6A3A", "#FFFFFF"),
    "blue_white_red": ("#2166AC", "#F7F7F7", "#B2182B"),
}


def attribute_value_key(value: AttributeValue) -> str:
    """生成保留值类型的稳定唯一值键。"""
    if value is None:
        return "null:"
    if isinstance(value, bool):
        return f"bool:{str(value).lower()}"
    if isinstance(value, datetime):
        return f"datetime:{value.isoformat()}"
    if isinstance(value, date):
        return f"date:{value.isoformat()}"
    return f"{type(value).__name__}:{value}"


def attribute_value_label(value: AttributeValue) -> str:
    """把属性值格式化为类别图例标签。"""
    return "<空值>" if value is None else str(value)


def symbology_to_dict(value: VectorSymbology | RasterSymbology) -> dict[str, object]:
    """把符号配置转换为 JSON 可编码字典。"""
    payload: dict[str, object] = cast(dict[str, object], asdict(value))
    payload["kind"] = "vector" if isinstance(value, VectorSymbology) else "raster"
    payload["renderer_type"] = value.renderer_type.value
    if isinstance(value, RasterSymbology):
        payload["stretch_type"] = value.stretch_type.value
    return payload


def vector_symbology_from_dict(payload: dict[str, object]) -> VectorSymbology:
    """从工程字典恢复矢量符号配置。"""
    base_symbol = _style_from_object(payload["base_symbol"])
    unique_classes = tuple(
        UniqueValueClass(
            value_key=str(item["value_key"]),
            label=str(item["label"]),
            symbol=_style_from_object(item["symbol"]),
            visible=bool(item.get("visible", True)),
        )
        for item in _dict_items(payload.get("unique_classes", []))
    )
    graduated_classes = tuple(
        GraduatedClass(
            lower=float(cast(str | int | float, item["lower"])),
            upper=float(cast(str | int | float, item["upper"])),
            label=str(item["label"]),
            symbol=_style_from_object(item["symbol"]),
            visible=bool(item.get("visible", True)),
        )
        for item in _dict_items(payload.get("graduated_classes", []))
    )
    other_value: object = payload.get("other_symbol")
    return VectorSymbology(
        renderer_type=VectorRendererType(str(payload["renderer_type"])),
        base_symbol=base_symbol,
        field_name=str(payload["field_name"]) if payload.get("field_name") is not None else None,
        color_scheme=str(payload.get("color_scheme", "standard")),
        unique_classes=unique_classes,
        graduated_classes=graduated_classes,
        classification_method=str(payload.get("classification_method", "equal_interval")),
        other_symbol=_style_from_object(other_value) if isinstance(other_value, dict) else None,
        other_visible=bool(payload.get("other_visible", True)),
    )


def raster_symbology_from_dict(payload: dict[str, object]) -> RasterSymbology:
    """从工程字典恢复栅格符号配置。"""
    raw_bands: object = payload.get("rgb_bands", [0, 1, 2])
    bands = tuple(int(cast(str | int | float, value)) for value in cast(list[object], raw_bands))
    if len(bands) != 3:
        bands = (0, 1, 2)
    return RasterSymbology(
        renderer_type=RasterRendererType(str(payload["renderer_type"])),
        rgb_bands=bands,
        stretch_band=int(cast(str | int | float, payload.get("stretch_band", 0))),
        stretch_type=StretchType(str(payload.get("stretch_type", "percent_clip"))),
        lower_percent=float(cast(str | int | float, payload.get("lower_percent", 2.0))),
        upper_percent=float(cast(str | int | float, payload.get("upper_percent", 98.0))),
        color_scheme=str(payload.get("color_scheme", "gray")),
        inverted=bool(payload.get("inverted", False)),
    )


def _style_from_object(value: object) -> LayerStyle:
    """校验并恢复基础符号。"""
    if not isinstance(value, dict):
        raise ValueError("工程符号样式格式无效。")
    return LayerStyle(
        stroke_color=str(value["stroke_color"]),
        fill_color=str(value["fill_color"]),
        line_width=float(value["line_width"]),
        point_size=float(value["point_size"]),
        opacity=float(value["opacity"]),
    )


def _dict_items(value: object) -> tuple[dict[str, object], ...]:
    """把工程数组校验为字典元组。"""
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))
