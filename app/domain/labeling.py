"""矢量图层动态标注的领域配置。"""

from dataclasses import asdict, dataclass
from enum import Enum
from math import isfinite
from typing import cast

from app.domain.feature import Feature


class LabelPlacement(str, Enum):
    """标注相对于要素锚点的九宫格位置。"""

    ABOVE_LEFT = "above_left"
    ABOVE = "above"
    ABOVE_RIGHT = "above_right"
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    BELOW_LEFT = "below_left"
    BELOW = "below"
    BELOW_RIGHT = "below_right"


@dataclass(frozen=True, slots=True)
class LabelClass:
    """描述一组要素标注的字段、过滤条件、位置和文本符号。"""

    name: str
    field_name: str
    placement: LabelPlacement = LabelPlacement.ABOVE_RIGHT
    font_size: float = 12.0
    text_color: str = "#20354A"
    halo_color: str = "#FFFFFF"
    halo_width: float = 3.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    filter_field: str | None = None
    filter_value: str | None = None
    visible: bool = True
    # 是否绘制文字后面的不透明光晕/底框；默认关闭，避免缩小时底框挤成一团。
    halo_enabled: bool = False

    def __post_init__(self) -> None:
        """校验标注类的关键参数，避免渲染时产生不可读或无效文本。"""
        if not self.name.strip():
            raise ValueError("标注分类名称不能为空。")
        if not self.field_name.strip():
            raise ValueError("标注字段不能为空。")
        if self.font_size < 6.0 or self.font_size > 72.0:
            raise ValueError("标注字号必须介于 6 和 72 像素之间。")
        if self.halo_width < 0.0 or self.halo_width > 12.0:
            raise ValueError("标注光晕宽度必须介于 0 和 12 像素之间。")
        if not all(
            isfinite(value)
            for value in (self.font_size, self.halo_width, self.offset_x, self.offset_y)
        ):
            raise ValueError("标注字号、光晕和偏移量必须是有限数值。")
        if self.filter_field is None and self.filter_value is not None:
            raise ValueError("设置分类值时必须同时设置分类字段。")

    def text_for(self, feature: Feature) -> str | None:
        """返回要素的标注文本；不满足分类过滤或为空时返回空值。"""
        if self.filter_field is not None:
            actual_value: object = feature.attributes.get(self.filter_field)
            if str(actual_value) != self.filter_value:
                return None
        value: object = feature.attributes.get(self.field_name)
        if value is None:
            return None
        text: str = str(value).strip()
        return text or None


@dataclass(frozen=True, slots=True)
class LabelingConfig:
    """保存一个矢量图层的标注开关和标注分类集合。"""

    enabled: bool = False
    classes: tuple[LabelClass, ...] = ()


def attribute_fields(features: tuple[Feature, ...]) -> tuple[str, ...]:
    """按数据首次出现顺序返回图层可用于标注的属性字段。"""
    fields: dict[str, None] = {}
    for feature in features:
        for field_name in feature.attributes:
            fields.setdefault(field_name, None)
    return tuple(fields)


def default_labeling_for_features(features: tuple[Feature, ...]) -> LabelingConfig:
    """为图层生成一个启用状态的默认标注类，优先选择常见名称字段。"""
    fields: tuple[str, ...] = attribute_fields(features)
    if not fields:
        return LabelingConfig()
    preferred_names: tuple[str, ...] = (
        "name",
        "名称",
        "label",
        "title",
        "city",
        "province",
    )
    field_name: str = next(
        (
            field
            for preferred in preferred_names
            for field in fields
            if field.casefold() == preferred.casefold()
        ),
        fields[0],
    )
    return LabelingConfig(
        enabled=True,
        classes=(LabelClass(name="默认标注", field_name=field_name),),
    )


def labeling_to_dict(config: LabelingConfig | None) -> dict[str, object] | None:
    """把标注配置转换为可写入工程 JSON 的字典。"""
    if config is None:
        return None
    return {
        "enabled": config.enabled,
        "classes": [
            {
                **cast(dict[str, object], asdict(label_class)),
                "placement": label_class.placement.value,
            }
            for label_class in config.classes
        ],
    }


def labeling_from_dict(payload: dict[str, object] | None) -> LabelingConfig | None:
    """从工程 JSON 字典恢复标注配置，兼容缺少标注字段的旧工程。"""
    if payload is None:
        return None
    raw_classes: object = payload.get("classes", [])
    classes: list[LabelClass] = []
    if isinstance(raw_classes, list):
        for raw_class in raw_classes:
            if not isinstance(raw_class, dict):
                continue
            try:
                classes.append(
                    LabelClass(
                        name=str(raw_class.get("name", "默认标注")),
                        field_name=str(raw_class.get("field_name", "")),
                        placement=LabelPlacement(
                            str(raw_class.get("placement", LabelPlacement.ABOVE_RIGHT.value))
                        ),
                        font_size=float(raw_class.get("font_size", 12.0)),
                        text_color=str(raw_class.get("text_color", "#20354A")),
                        halo_color=str(raw_class.get("halo_color", "#FFFFFF")),
                        halo_width=float(raw_class.get("halo_width", 3.0)),
                        offset_x=float(raw_class.get("offset_x", 0.0)),
                        offset_y=float(raw_class.get("offset_y", 0.0)),
                        filter_field=(
                            str(raw_class["filter_field"])
                            if raw_class.get("filter_field") is not None
                            else None
                        ),
                        filter_value=(
                            str(raw_class["filter_value"])
                            if raw_class.get("filter_value") is not None
                            else None
                        ),
                        visible=bool(raw_class.get("visible", True)),
                        halo_enabled=bool(raw_class.get("halo_enabled", False)),
                    )
                )
            except (KeyError, TypeError, ValueError):
                # 单个旧配置损坏时不阻止整个工程打开，交给用户重新配置标注。
                continue
    return LabelingConfig(enabled=bool(payload.get("enabled", False)), classes=tuple(classes))
