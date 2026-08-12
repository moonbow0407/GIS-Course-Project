"""符号分类与栅格显示缓存生成服务。"""

from collections.abc import Mapping
from dataclasses import replace

import numpy as np
from numpy.typing import NDArray

from app.domain.feature import AttributeValue
from app.domain.layer_style import LayerStyle
from app.domain.raster_layer import RasterLayer
from app.domain.symbology import (
    CATEGORICAL_SCHEMES,
    COLOR_RAMPS,
    GraduatedClass,
    RasterClass,
    RasterRendererType,
    RasterSymbology,
    StretchType,
    UniqueValueClass,
    VectorRendererType,
    VectorSymbology,
    attribute_value_key,
    attribute_value_label,
)
from app.domain.vector_layer import VectorLayer


def create_unique_value_symbology(
    layer: VectorLayer,
    field_name: str,
    color_scheme: str,
) -> VectorSymbology:
    """按字段前一百个稳定唯一值创建分类符号。"""
    colors: tuple[str, ...] = CATEGORICAL_SCHEMES[color_scheme]
    values_by_key: dict[str, AttributeValue] = {}
    for feature in layer.features:
        value = feature.attributes.get(field_name)
        values_by_key.setdefault(attribute_value_key(value), value)
    ordered_values = sorted(values_by_key.items(), key=lambda item: attribute_value_label(item[1]))
    classes: list[UniqueValueClass] = []
    for index, (value_key, value) in enumerate(ordered_values[:100]):
        color: str = colors[index % len(colors)]
        classes.append(
            UniqueValueClass(
                value_key=value_key,
                label=attribute_value_label(value),
                symbol=_symbol_with_color(layer.style, color),
            )
        )
    return VectorSymbology(
        renderer_type=VectorRendererType.UNIQUE,
        base_symbol=layer.style,
        field_name=field_name,
        color_scheme=color_scheme,
        unique_classes=tuple(classes),
        other_symbol=_symbol_with_color(layer.style, "#9CA3AF"),
        other_visible=True,
    )


def create_graduated_symbology(
    layer: VectorLayer,
    field_name: str,
    color_scheme: str,
    classification_method: str,
    class_count: int,
) -> VectorSymbology:
    """按等间隔或分位数创建数值颜色分级。"""
    if class_count < 3:
        raise ValueError("分级数量至少为 3 级。")
    values = np.asarray(
        [
            float(value)
            for feature in layer.features
            if isinstance((value := feature.attributes.get(field_name)), (int, float))
            and not isinstance(value, bool)
            and np.isfinite(float(value))
        ],
        dtype=np.float64,
    )
    if values.size == 0:
        raise ValueError("所选字段没有可用于分级的数值。")
    sample_count: int = int(values.size)
    if class_count > sample_count:
        raise ValueError(
            f"分级数量不能超过可用于分级的数值样本数（当前为 {sample_count}）。"
        )
    if classification_method == "quantile":
        breaks = np.quantile(values, np.linspace(0.0, 1.0, class_count + 1))
    elif classification_method == "equal_interval":
        breaks = np.linspace(float(values.min()), float(values.max()), class_count + 1)
    else:
        raise ValueError("不支持的分级方法。")
    colors: tuple[str, ...] = sample_color_ramp(color_scheme, class_count)
    classes = tuple(
        GraduatedClass(
            lower=float(breaks[index]),
            upper=float(breaks[index + 1]),
            label=f"{breaks[index]:.6g} – {breaks[index + 1]:.6g}",
            symbol=_symbol_with_color(layer.style, colors[index]),
        )
        for index in range(class_count)
    )
    return VectorSymbology(
        renderer_type=VectorRendererType.GRADUATED,
        base_symbol=layer.style,
        field_name=field_name,
        color_scheme=color_scheme,
        graduated_classes=classes,
        classification_method=classification_method,
    )


def create_raster_classified_symbology(
    values: tuple[float, ...],
    color_scheme: str = "standard",
    labels: Mapping[float, str] | None = None,
) -> RasterSymbology:
    """为重分类输出的离散值创建分类颜色配置，可选保留源区间标签。"""
    colors: tuple[str, ...] = CATEGORICAL_SCHEMES[color_scheme]
    ordered_values = tuple(
        sorted({float(value) for value in values if np.isfinite(float(value))})
    )
    classes = tuple(
        RasterClass(
            value=value,
            label=(labels or {}).get(value, _format_raster_class_value(value)),
            color=colors[index % len(colors)],
        )
        for index, value in enumerate(ordered_values[:100])
    )
    return RasterSymbology(
        renderer_type=RasterRendererType.CLASSIFIED,
        color_scheme=color_scheme,
        classes=classes,
        other_color="#BDBDBD",
        other_visible=True,
    )


def apply_raster_symbology(layer: RasterLayer, symbology: RasterSymbology) -> RasterLayer:
    """根据原始像元重新生成 RGBA 显示缓存并保留空间身份。

    延迟栅格优先使用读取时保留的低分辨率原始预览，避免工程重开或修改
    符号系统时为了刷新地图而加载整幅分析数据。
    """
    requested_band_candidates = (
        symbology.rgb_bands
        if symbology.renderer_type is RasterRendererType.RGB
        else (symbology.stretch_band,)
    )
    requested_bands: tuple[int, ...] = tuple(
        min(max(index, 0), layer.band_count - 1)
        for index in requested_band_candidates
    )
    if layer.analysis_data_loaded:
        source_data = layer.raster_data
        source_valid_mask = layer.valid_mask
        render_band_indexes = requested_bands
        display_transform = layer.transform
    elif (
        layer.display_values is not None
        and layer.display_valid_mask is not None
        and all(index in layer.display_band_indexes for index in requested_bands)
    ):
        source_data = layer.display_values
        source_valid_mask = layer.display_valid_mask
        positions = {index: position for position, index in enumerate(layer.display_band_indexes)}
        render_band_indexes = tuple(positions[index] for index in requested_bands)
        display_transform = layer.display_transform or layer.transform
    else:
        # 外部构造的延迟图层可能只有 RGBA 预览而没有对应原始值，不能凭颜色
        # 反推分类结果；保留预览并恢复参数，避免意外触发不可控的全图读取。
        return replace(layer, symbology=symbology)

    if symbology.renderer_type is RasterRendererType.RGB:
        stretched = [
            _stretch_band(source_data[index], source_valid_mask, symbology)
            for index in render_band_indexes
        ]
        rgb = np.stack(stretched, axis=2)
        stretch_alpha: NDArray[np.uint8] = np.where(
            source_valid_mask, 255, 0
        ).astype(np.uint8)
        image_data = np.ascontiguousarray(
            np.dstack((rgb, stretch_alpha)).astype(np.uint8)
        )
    elif symbology.renderer_type is RasterRendererType.STRETCH:
        normalized = _stretch_normalized(
            source_data[render_band_indexes[0]],
            source_valid_mask,
            symbology,
        )
        if symbology.inverted:
            normalized = 1.0 - normalized
        rgb = _apply_color_ramp(normalized, COLOR_RAMPS[symbology.color_scheme])
        alpha: NDArray[np.uint8] = np.where(source_valid_mask, 255, 0).astype(np.uint8)
        image_data = np.ascontiguousarray(np.dstack((rgb, alpha)).astype(np.uint8))
    else:
        image_data = render_raster_classified(
            source_data[render_band_indexes[0]],
            source_valid_mask,
            symbology,
        )
    # 使用低分辨率原始值时必须保留预览变换，否则颜色虽正确但地图位置会偏移。
    return replace(
        layer,
        image_data=image_data,
        display_transform=display_transform,
        symbology=symbology,
    )


def render_raster_classified(
    values: NDArray[np.generic],
    valid_mask: NDArray[np.bool_],
    symbology: RasterSymbology,
) -> NDArray[np.uint8]:
    """将栅格离散值渲染为分类颜色，并保持 NoData 透明。"""
    numeric_values = np.asarray(values, dtype=np.float64)
    shape = numeric_values.shape
    other_rgb = np.asarray(_hex_to_rgb(symbology.other_color), dtype=np.uint8)
    rgb = np.broadcast_to(other_rgb, (*shape, 3)).copy()
    visible = np.asarray(valid_mask, dtype=bool).copy()
    matched = np.zeros(shape, dtype=bool)
    for category in symbology.classes:
        category_mask = valid_mask & np.isfinite(numeric_values) & (
            numeric_values == category.value
        )
        matched |= category_mask
        rgb[category_mask] = _hex_to_rgb(category.color)
        if not category.visible:
            visible[category_mask] = False
    if not symbology.other_visible:
        visible[valid_mask & ~matched] = False
    alpha = np.where(visible, 255, 0).astype(np.uint8)
    return np.ascontiguousarray(np.dstack((rgb, alpha)).astype(np.uint8))


def sample_color_ramp(name: str, count: int) -> tuple[str, ...]:
    """从内置连续色带等距采样指定数量颜色。"""
    stops: tuple[str, ...] = COLOR_RAMPS[name]
    positions = np.linspace(0.0, 1.0, count)
    return tuple(_interpolate_hex(stops, float(position)) for position in positions)


def _symbol_with_color(base: LayerStyle, color: str) -> LayerStyle:
    """保持几何尺寸和透明度，仅替换主要颜色。"""
    if base.fill_color == "transparent":
        return replace(base, stroke_color=color)
    return replace(base, fill_color=color, stroke_color="#4B5563")


def _format_raster_class_value(value: float) -> str:
    """把分类值格式化为不带无意义小数的图例标签。"""
    return f"{value:.6g}"


def _stretch_band(
    band: NDArray[np.generic],
    valid_mask: NDArray[np.bool_],
    symbology: RasterSymbology,
) -> NDArray[np.uint8]:
    """把单波段拉伸为八位灰度。"""
    normalized = _stretch_normalized(band, valid_mask, symbology)
    return np.asarray(normalized * 255.0, dtype=np.uint8)


def _stretch_normalized(
    band: NDArray[np.generic],
    valid_mask: NDArray[np.bool_],
    symbology: RasterSymbology,
) -> NDArray[np.float64]:
    """把有效像元线性拉伸到零至一。"""
    numeric_band = np.asarray(band, dtype=np.float64)
    samples = numeric_band[valid_mask & np.isfinite(numeric_band)]
    if samples.size == 0:
        return np.zeros(numeric_band.shape, dtype=np.float64)
    if symbology.stretch_type is StretchType.PERCENT_CLIP:
        lower = float(np.percentile(samples, symbology.lower_percent))
        upper = float(np.percentile(samples, symbology.upper_percent))
    else:
        lower = float(samples.min())
        upper = float(samples.max())
    if upper <= lower:
        upper = lower + 1.0
    normalized = np.clip((numeric_band - lower) / (upper - lower), 0.0, 1.0)
    # 无效像元（NoData/NaN）拉伸后仍是非有限值，直接参与取整会触发告警并
    # 产生越界索引；统一置零，其可见性由 alpha 通道按 valid_mask 决定。
    return np.where(np.isfinite(normalized), normalized, 0.0)


def _apply_color_ramp(
    normalized: NDArray[np.float64],
    stops: tuple[str, ...],
) -> NDArray[np.uint8]:
    """按连续色带插值生成三通道颜色。"""
    stop_colors = np.asarray([_hex_to_rgb(color) for color in stops], dtype=np.float64)
    scaled = np.clip(normalized, 0.0, 1.0) * (len(stops) - 1)
    lower_indexes = np.floor(scaled).astype(np.int64)
    upper_indexes = np.minimum(lower_indexes + 1, len(stops) - 1)
    fractions = (scaled - lower_indexes)[..., np.newaxis]
    rgb = stop_colors[lower_indexes] * (1.0 - fractions) + stop_colors[upper_indexes] * fractions
    return np.asarray(rgb, dtype=np.uint8)


def _interpolate_hex(stops: tuple[str, ...], position: float) -> str:
    """在多段色带中插值一个十六进制颜色。"""
    scaled: float = min(max(position, 0.0), 1.0) * (len(stops) - 1)
    lower_index: int = int(np.floor(scaled))
    upper_index: int = min(lower_index + 1, len(stops) - 1)
    fraction: float = scaled - lower_index
    lower = _hex_to_rgb(stops[lower_index])
    upper = _hex_to_rgb(stops[upper_index])
    channels = tuple(
        round(low + (high - low) * fraction)
        for low, high in zip(lower, upper, strict=True)
    )
    return "#{:02X}{:02X}{:02X}".format(*channels)


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    """解析六位十六进制颜色。"""
    normalized = color.lstrip("#")
    return tuple(int(normalized[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]
