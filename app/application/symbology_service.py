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
    SCHEME_LABELS,
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


_SLOPE_DISPLAY_CLASSES: tuple[tuple[float, float, str, str], ...] = (
    (0.0, 2.0, "平缓（0°–2°）", "#2E7D32"),
    (2.0, 5.0, "较缓（2°–5°）", "#7CB342"),
    (5.0, 15.0, "缓坡（5°–15°）", "#C0CA33"),
    (15.0, 25.0, "斜坡（15°–25°）", "#FDD835"),
    (25.0, 35.0, "陡坡（25°–35°）", "#FFB300"),
    (35.0, 45.0, "急陡（35°–45°）", "#F57C00"),
    (45.0, 90.0, "峭壁（45°–90°）", "#C62828"),
)

_ASPECT_DISPLAY_CLASSES: tuple[tuple[float, float, str, str], ...] = (
    (337.5, 22.5, "北（337.5°–22.5°）", "#E53935"),
    (22.5, 67.5, "东北（22.5°–67.5°）", "#FB8C00"),
    (67.5, 112.5, "东（67.5°–112.5°）", "#FDD835"),
    (112.5, 157.5, "东南（112.5°–157.5°）", "#43A047"),
    (157.5, 202.5, "南（157.5°–202.5°）", "#00ACC1"),
    (202.5, 247.5, "西南（202.5°–247.5°）", "#1E88E5"),
    (247.5, 292.5, "西（247.5°–292.5°）", "#5E35B1"),
    (292.5, 337.5, "西北（292.5°–337.5°）", "#8E24AA"),
)


def create_dem_result_symbology(mode: str) -> RasterSymbology:
    """为 DEM 坡度、坡向或山体阴影结果生成可直接图例化的显示符号。

    坡度、坡向使用区间分类，图层树能看出等级含义；山体阴影保留
    最小—最大灰度拉伸，以明暗表达起伏。不改变分析结果的像元值。
    """
    if mode == "slope":
        return RasterSymbology(
            renderer_type=RasterRendererType.CLASSIFIED,
            color_scheme="standard",
            classes=tuple(
                RasterClass(lower, label, color, upper=upper)
                for lower, upper, label, color in _SLOPE_DISPLAY_CLASSES
            ),
            other_visible=False,
        )
    if mode == "aspect":
        return RasterSymbology(
            renderer_type=RasterRendererType.CLASSIFIED,
            color_scheme="standard",
            classes=tuple(
                RasterClass(lower, label, color, upper=upper)
                for lower, upper, label, color in _ASPECT_DISPLAY_CLASSES
            ),
            other_visible=False,
        )
    if mode == "hillshade":
        return RasterSymbology(
            renderer_type=RasterRendererType.STRETCH,
            stretch_type=StretchType.MIN_MAX,
            color_scheme="gray",
        )
    raise ValueError(f"不支持的 DEM 分析类型：{mode}")


_MAX_DEFAULT_UNIQUE_VALUES: int = 20


def raster_display_samples(
    layer: RasterLayer,
    band_index: int = 0,
) -> NDArray[np.float64]:
    """从显示预览或已加载分析数组采集有效像元，避免为符号推断加载整幅栅格。"""
    values: NDArray[np.float64] | None = None
    valid: NDArray[np.bool_] | None = None
    if layer.display_values is not None and layer.display_valid_mask is not None:
        indexes = layer.display_band_indexes or tuple(
            range(int(layer.display_values.shape[0]))
        )
        position = indexes.index(band_index) if band_index in indexes else 0
        values = np.asarray(layer.display_values[position], dtype=np.float64)
        valid = np.asarray(layer.display_valid_mask, dtype=bool)
    elif layer.analysis_data_loaded:
        safe_band = min(max(band_index, 0), layer.band_count - 1)
        values = np.asarray(layer.raster_data[safe_band], dtype=np.float64)
        valid = np.asarray(layer.valid_mask, dtype=bool)
    if values is None or valid is None:
        return np.empty(0, dtype=np.float64)
    sample_mask = valid & np.isfinite(values)
    # 降采样后的 GDAL 掩膜在 NoData 边界处可能仍报告有效，统计时按声明值再次兜底。
    if layer.nodata is not None:
        sample_mask &= values != layer.nodata
    return values[sample_mask]


def is_placeholder_raster_symbology(symbology: RasterSymbology) -> bool:
    """判断是否为图层构造时生成的占位符号（默认灰度拉伸或默认 RGB）。"""
    if symbology.renderer_type is RasterRendererType.RGB:
        return symbology.rgb_bands == (0, 1, 2) and not symbology.classes
    if symbology.renderer_type is RasterRendererType.STRETCH:
        return (
            symbology.color_scheme == "gray"
            and symbology.stretch_type is StretchType.PERCENT_CLIP
            and symbology.stretch_band == 0
            and not symbology.inverted
            and symbology.nodata_color == "#000000"
            and not symbology.nodata_visible
            and not symbology.classes
        )
    return False


def infer_default_raster_symbology(layer: RasterLayer) -> RasterSymbology:
    """按波段数和值分布选择打开栅格时的默认显示符号。"""
    if layer.band_count >= 3:
        return RasterSymbology(renderer_type=RasterRendererType.RGB)
    samples = raster_display_samples(layer)
    if samples.size == 0:
        return RasterSymbology(
            renderer_type=RasterRendererType.STRETCH,
            color_scheme="terrain",
        )
    unique_values = np.unique(samples)
    value_span = float(unique_values.max() - unique_values.min()) if unique_values.size else 0.0
    if (
        unique_values.size <= _MAX_DEFAULT_UNIQUE_VALUES
        and np.allclose(unique_values, np.round(unique_values))
        and (
            value_span <= 50.0
            or unique_values.size >= value_span + 1.0
        )
    ):
        return create_raster_classified_symbology(
            tuple(float(value) for value in unique_values)
        )
    minimum = float(samples.min())
    maximum = float(samples.max())
    if 0.0 <= minimum and maximum <= 90.0 and maximum > 15.0:
        color_scheme = "slope"
    elif 0.0 <= minimum and maximum <= 255.0 and maximum - minimum >= 200.0:
        return RasterSymbology(
            renderer_type=RasterRendererType.STRETCH,
            stretch_type=StretchType.MIN_MAX,
            color_scheme="gray",
        )
    elif 0.0 <= minimum and maximum <= 360.0 and maximum > 180.0:
        color_scheme = "aspect"
    else:
        color_scheme = "terrain"
    return RasterSymbology(
        renderer_type=RasterRendererType.STRETCH,
        stretch_type=StretchType.PERCENT_CLIP,
        color_scheme=color_scheme,
    )


def create_raster_graduated_symbology(
    layer: RasterLayer,
    color_scheme: str,
    classification_method: str,
    class_count: int,
) -> RasterSymbology:
    """按等间隔或分位数为连续栅格生成区间分类符号。"""
    if class_count < 3:
        raise ValueError("分级数量至少为 3 级。")
    if color_scheme not in COLOR_RAMPS:
        raise ValueError(f"不支持的栅格色带：{color_scheme}")
    current = layer.symbology
    samples = raster_display_samples(layer, current.stretch_band if current else 0)
    if samples.size == 0:
        raise ValueError("没有可用于分级的有效像元。")
    if class_count > int(samples.size):
        raise ValueError(
            f"分级数量不能超过可用于分级的有效像元数（当前为 {int(samples.size)}）。"
        )
    if classification_method == "quantile":
        breaks = np.quantile(samples, np.linspace(0.0, 1.0, class_count + 1))
    elif classification_method == "equal_interval":
        breaks = np.linspace(float(samples.min()), float(samples.max()), class_count + 1)
    else:
        raise ValueError("不支持的分级方法。")
    for index in range(1, len(breaks)):
        if breaks[index] <= breaks[index - 1]:
            breaks[index] = breaks[index - 1] + 1e-9
    colors = sample_color_ramp(color_scheme, class_count)
    classes = tuple(
        RasterClass(
            value=float(breaks[index]),
            label=f"{breaks[index]:.6g} – {breaks[index + 1]:.6g}",
            color=colors[index],
            upper=float(breaks[index + 1]),
        )
        for index in range(class_count)
    )
    return RasterSymbology(
        renderer_type=RasterRendererType.CLASSIFIED,
        color_scheme=color_scheme,
        classes=classes,
        other_visible=False,
        classification_method=classification_method,
        nodata_color=current.nodata_color if current else "#000000",
        nodata_visible=current.nodata_visible if current else False,
    )


def raster_stretch_legend_text(layer: RasterLayer) -> str:
    """生成拉伸栅格在图层树中的图例摘要。"""
    symbology = layer.symbology
    if symbology is None:
        return "拉伸"
    scheme_label = SCHEME_LABELS.get(symbology.color_scheme, symbology.color_scheme)
    samples = raster_display_samples(layer, symbology.stretch_band)
    if samples.size == 0:
        return f"拉伸 · {scheme_label}"
    return (
        f"拉伸 · {scheme_label} · "
        f"{float(samples.min()):.6g}–{float(samples.max()):.6g}"
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
        classification_method="unique",
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
        rgb[~source_valid_mask] = _hex_to_rgb(symbology.nodata_color)
        stretch_alpha: NDArray[np.uint8] = np.where(
            source_valid_mask | symbology.nodata_visible, 255, 0
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
        rgb[~source_valid_mask] = _hex_to_rgb(symbology.nodata_color)
        alpha: NDArray[np.uint8] = np.where(
            source_valid_mask | symbology.nodata_visible, 255, 0
        ).astype(np.uint8)
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
    rgb[~valid_mask] = _hex_to_rgb(symbology.nodata_color)
    if symbology.nodata_visible:
        visible[~valid_mask] = True
    matched = np.zeros(shape, dtype=bool)
    for category in symbology.classes:
        category_mask = (
            valid_mask
            & ~matched
            & _raster_class_mask(numeric_values, category)
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


def _raster_class_mask(
    values: NDArray[np.float64],
    category: RasterClass,
) -> NDArray[np.bool_]:
    """按精确值或数值区间生成分类掩膜。"""
    finite = np.isfinite(values)
    if category.upper is None:
        return finite & (values == category.value)
    lower = category.value
    upper = category.upper
    if lower <= upper:
        return finite & (values >= lower) & (values <= upper)
    return finite & ((values >= lower) | (values <= upper))


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
