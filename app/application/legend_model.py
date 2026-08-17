"""从图层面板与布局图例共用的符号条目模型。

把可见图层的符号系统收成不可变补丁列表，界面只负责画，不再各自解释
唯一值、分级和栅格分类规则。
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum

from app.application.results import WorkspaceSnapshot
from app.application.symbology_service import raster_stretch_legend_text
from app.domain.layer_style import GeometryFamily, LayerStyle
from app.domain.raster_layer import RasterLayer
from app.domain.spatial_layer import SpatialLayer
from app.domain.symbology import (
    COLOR_RAMPS,
    RasterClass,
    RasterRendererType,
    RasterSymbology,
    VectorRendererType,
    attribute_value_key,
    attribute_value_label,
)
from app.domain.vector_layer import VectorLayer


class LegendPatchKind(str, Enum):
    """图例补丁的绘制形态。"""

    POINT = "point"
    LINE = "line"
    POLYGON = "polygon"
    SWATCH = "swatch"
    RAMP = "ramp"


@dataclass(frozen=True, slots=True)
class LegendPatch:
    """一条可绘制的图例补丁。

    属性:
        label: 显示文本。
        kind: 补丁形态。
        style: 矢量点/线/面符号；色块和色带为空。
        colors: 色块或色带使用的颜色序列。
        visible: 类别是否可见。
        checkable: 图层树是否显示复选框。
        category_index: 图层树切换显隐时使用的类别下标。
        patch_id: 布局图例标签覆盖使用的稳定编号。
    """

    label: str
    kind: LegendPatchKind
    style: LayerStyle | None = None
    colors: tuple[str, ...] = ()
    visible: bool = True
    checkable: bool = False
    category_index: int | None = None
    patch_id: str = ""


@dataclass(frozen=True, slots=True)
class LegendLayerBlock:
    """单个图层在图例中的一块内容。

    属性:
        layer_id: 图层稳定编号。
        layer_name: 图层显示名。
        heading: 图层树摘要行文本，布局图例可忽略。
        heading_colors: 摘要行色带。
        patches: 按绘制顺序排列的补丁。
    """

    layer_id: str
    layer_name: str
    heading: str | None = None
    heading_colors: tuple[str, ...] = ()
    patches: tuple[LegendPatch, ...] = ()


def apply_legend_overrides(
    blocks: tuple[LegendLayerBlock, ...],
    overrides: Mapping[str, str],
) -> tuple[LegendLayerBlock, ...]:
    """用布局图例里的自定义标签覆盖补丁文字；空覆盖保持原标签。"""
    if not overrides:
        return blocks
    updated: list[LegendLayerBlock] = []
    for block in blocks:
        patches = tuple(
            replace(patch, label=overrides[patch.patch_id])
            if patch.patch_id and overrides.get(patch.patch_id, "").strip()
            else patch
            for patch in block.patches
        )
        updated.append(replace(block, patches=patches))
    return tuple(updated)


def build_layout_legend_model(
    snapshot: WorkspaceSnapshot,
    overrides: Mapping[str, str] | None = None,
) -> tuple[LegendLayerBlock, ...]:
    """构建供布局图例绘制的模型，并套用标签覆盖。"""
    return apply_legend_overrides(build_legend_model(snapshot), overrides or {})


def vector_attribute_names(layer: VectorLayer) -> tuple[str, ...]:
    """收集矢量图层出现过的属性字段名。"""
    names: set[str] = set()
    for feature in layer.features:
        names.update(str(key) for key in feature.attributes)
    return tuple(sorted(names))


def suggested_legend_label_field(layer: VectorLayer) -> str | None:
    """猜测适合作为图例文字的名称字段。"""
    names = vector_attribute_names(layer)
    preferred = (
        "省名", "名称", "地名", "NAME", "Name", "name",
        "省", "NAME_1", "NL_NAME_1", "NAME_CH",
    )
    for candidate in preferred:
        if candidate in names:
            return candidate
    class_field = (
        layer.symbology.field_name
        if layer.symbology is not None
        else None
    )
    for name in names:
        if name != class_field and _field_looks_textual(layer, name):
            return name
    return None


def unique_patch_labels_from_field(
    layer: VectorLayer,
    label_field: str,
) -> dict[str, str]:
    """按另一属性字段为唯一值类别生成图例标签。

    分类字段仍决定颜色（例如面积），标签字段决定图例文字（例如省名）。
    """
    symbology = layer.symbology
    if (
        symbology is None
        or symbology.renderer_type is not VectorRendererType.UNIQUE
        or not symbology.field_name
    ):
        return {}
    class_field = symbology.field_name
    labels: dict[str, str] = {}
    for feature in layer.features:
        value_key = attribute_value_key(feature.attributes.get(class_field))
        patch_id = f"{layer.layer_id}|unique|{value_key}"
        if patch_id in labels:
            continue
        labels[patch_id] = attribute_value_label(
            feature.attributes.get(label_field)
        )
    return labels


def _field_looks_textual(layer: VectorLayer, field_name: str) -> bool:
    """字段样本以文本为主时，更适合作为图例名称。"""
    samples = 0
    textual = 0
    for feature in layer.features:
        value = feature.attributes.get(field_name)
        if value is None:
            continue
        samples += 1
        if isinstance(value, str) and value.strip():
            textual += 1
        if samples >= 8:
            break
    return samples > 0 and textual >= samples / 2


def build_legend_model(snapshot: WorkspaceSnapshot) -> tuple[LegendLayerBlock, ...]:
    """为快照中全部可见图层构建图例模型。"""
    blocks: list[LegendLayerBlock] = []
    for layer_snap in snapshot.layers:
        if not layer_snap.visible:
            continue
        block = build_legend_block(layer_snap.layer)
        if block is not None:
            blocks.append(block)
    return tuple(blocks)


def build_legend_block(layer: SpatialLayer) -> LegendLayerBlock | None:
    """为单个图层构建图例块；无符号信息时返回空。"""
    if isinstance(layer, RasterLayer):
        return _build_raster_block(layer)
    if isinstance(layer, VectorLayer):
        return _build_vector_block(layer)
    return None


def raster_class_legend_label(category: RasterClass) -> str:
    """生成栅格分类在图例中的显示标签。"""
    value_text: str = f"{category.value:.6g}"
    label: str = category.label.strip()
    if category.upper is not None:
        return label or f"{value_text}–{category.upper:.6g}"
    if not label or label == value_text:
        return value_text
    return f"{value_text} · {label}"


def _geometry_patch_kind(family: GeometryFamily | None) -> LegendPatchKind:
    """把几何类别映射为图例补丁形态。"""
    if family == GeometryFamily.POINT:
        return LegendPatchKind.POINT
    if family == GeometryFamily.LINE:
        return LegendPatchKind.LINE
    return LegendPatchKind.POLYGON


def _build_vector_block(layer: VectorLayer) -> LegendLayerBlock | None:
    """构建矢量图层图例块。"""
    symbology = layer.symbology
    if symbology is None:
        return None
    kind = _geometry_patch_kind(layer.geometry_family)
    if symbology.renderer_type is VectorRendererType.SIMPLE:
        return LegendLayerBlock(
            layer_id=layer.layer_id,
            layer_name=layer.name,
            patches=(
                LegendPatch(
                    label="单一符号",
                    kind=kind,
                    style=symbology.base_symbol,
                    visible=True,
                    checkable=False,
                    category_index=0,
                    patch_id=f"{layer.layer_id}|simple|",
                ),
            ),
        )
    if symbology.unique_classes:
        patches = [
            LegendPatch(
                label=category.label,
                kind=kind,
                style=category.symbol,
                visible=category.visible,
                checkable=True,
                category_index=index,
                patch_id=f"{layer.layer_id}|unique|{category.value_key}",
            )
            for index, category in enumerate(symbology.unique_classes)
        ]
    else:
        patches = [
            LegendPatch(
                label=category.label,
                kind=kind,
                style=category.symbol,
                visible=category.visible,
                checkable=True,
                category_index=index,
                patch_id=f"{layer.layer_id}|grad|{index}",
            )
            for index, category in enumerate(symbology.graduated_classes)
        ]
    if symbology.renderer_type is VectorRendererType.UNIQUE and symbology.other_symbol:
        patches.append(
            LegendPatch(
                label="其他值",
                kind=kind,
                style=symbology.other_symbol,
                visible=symbology.other_visible,
                checkable=True,
                category_index=len(patches),
                patch_id=f"{layer.layer_id}|unique|__other__",
            )
        )
    return LegendLayerBlock(
        layer_id=layer.layer_id,
        layer_name=layer.name,
        patches=tuple(patches),
    )


def _build_raster_block(layer: RasterLayer) -> LegendLayerBlock | None:
    """构建栅格图层图例块。"""
    symbology = layer.symbology
    if symbology is None:
        return None
    if symbology.renderer_type is RasterRendererType.RGB:
        return LegendLayerBlock(
            layer_id=layer.layer_id,
            layer_name=layer.name,
            patches=(
                LegendPatch(
                    label="RGB 合成",
                    kind=LegendPatchKind.RAMP,
                    colors=("#EF4444", "#22C55E", "#3B82F6"),
                    patch_id=f"{layer.layer_id}|rgb|",
                ),
                *_nodata_patches(layer, symbology),
            ),
        )
    if symbology.renderer_type is RasterRendererType.CLASSIFIED:
        heading_colors = tuple(item.color for item in symbology.classes)
        patches: list[LegendPatch] = [
            LegendPatch(
                label=raster_class_legend_label(item),
                kind=LegendPatchKind.SWATCH,
                colors=(item.color,),
                visible=item.visible,
                patch_id=f"{layer.layer_id}|raster|{item.value}",
            )
            for item in symbology.classes
        ]
        if symbology.other_visible:
            patches.append(
                LegendPatch(
                    label="其他值",
                    kind=LegendPatchKind.SWATCH,
                    colors=(symbology.other_color,),
                    patch_id=f"{layer.layer_id}|raster|__other__",
                )
            )
        patches.extend(_nodata_patches(layer, symbology))
        return LegendLayerBlock(
            layer_id=layer.layer_id,
            layer_name=layer.name,
            heading=f"分类值 · {len(symbology.classes)} 类",
            heading_colors=heading_colors,
            patches=tuple(patches),
        )
    ramp_colors = COLOR_RAMPS.get(symbology.color_scheme, ("#000000", "#FFFFFF"))
    if symbology.inverted:
        ramp_colors = tuple(reversed(ramp_colors))
    patches = [
        LegendPatch(
            label=raster_stretch_legend_text(layer),
            kind=LegendPatchKind.RAMP,
            colors=ramp_colors,
            patch_id=f"{layer.layer_id}|stretch|",
        ),
        *_nodata_patches(layer, symbology),
    ]
    return LegendLayerBlock(
        layer_id=layer.layer_id,
        layer_name=layer.name,
        patches=tuple(patches),
    )


def _nodata_patches(
    layer: RasterLayer,
    symbology: RasterSymbology,
) -> tuple[LegendPatch, ...]:
    """在开启无数据显示时追加一条无数据补丁。"""
    if not symbology.nodata_visible:
        return ()
    if layer.nodata is None:
        label = "无数据"
    else:
        label = f"无数据 · {float(layer.nodata):.6g}"
    return (
        LegendPatch(
            label=label,
            kind=LegendPatchKind.SWATCH,
            colors=(symbology.nodata_color,),
            patch_id=f"{layer.layer_id}|nodata|",
        ),
    )
