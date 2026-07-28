"""符号分类与栅格显示缓存生成服务。"""

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
    """按等间隔或分位数创建三到七级数值颜色。"""
    if not 3 <= class_count <= 7:
        raise ValueError("分级数量必须在 3 到 7 之间。")
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


def apply_raster_symbology(layer: RasterLayer, symbology: RasterSymbology) -> RasterLayer:
    """根据原始像元重新生成 RGBA 显示缓存并保留空间身份。"""
    if symbology.renderer_type is RasterRendererType.RGB:
        bands = tuple(min(max(index, 0), layer.band_count - 1) for index in symbology.rgb_bands)
        stretched = [
            _stretch_band(layer.raster_data[index], layer.valid_mask, symbology)
            for index in bands
        ]
        rgb = np.stack(stretched, axis=2)
    else:
        band_index: int = min(max(symbology.stretch_band, 0), layer.band_count - 1)
        normalized = _stretch_normalized(
            layer.raster_data[band_index],
            layer.valid_mask,
            symbology,
        )
        if symbology.inverted:
            normalized = 1.0 - normalized
        rgb = _apply_color_ramp(normalized, COLOR_RAMPS[symbology.color_scheme])
    alpha: NDArray[np.uint8] = np.where(layer.valid_mask, 255, 0).astype(np.uint8)
    image_data = np.ascontiguousarray(np.dstack((rgb, alpha)).astype(np.uint8))
    return replace(layer, image_data=image_data, symbology=symbology)


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
    return np.clip((numeric_band - lower) / (upper - lower), 0.0, 1.0)


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
