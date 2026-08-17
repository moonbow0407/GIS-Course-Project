"""布局元素渲染 —— 将布局领域模型的制图元素转换为 QGraphicsItem / QPixmap。

地图框渲染复用现有的 QtVectorRenderer / QtRasterRenderer，
在离屏 QGraphicsScene 中重建图层图元，再通过 scene.render() 输出到像素图。
比例尺、图例、指北针直接在场景中绘制为矢量图元。
"""

import math
from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
)

from app.application.display_models import RasterDisplayPayload, VectorDisplayPayload
from app.application.legend_model import (
    LegendLayerBlock,
    LegendPatch,
    LegendPatchKind,
    build_layout_legend_model,
)
from app.application.results import WorkspaceSnapshot
from app.domain.layout import (
    LegendElement,
    MapFrameElement,
    NorthArrowElement,
    ScaleBarElement,
    TextElement,
)
from app.domain.raster_layer import RasterLayer
from app.domain.vector_layer import VectorLayer
from app.presentation.renderers.qt_raster_renderer import QtRasterRenderer
from app.presentation.renderers.qt_vector_renderer import QtVectorRenderer


def _mm_to_px(mm: float, dpi: float) -> float:
    """毫米转像素。"""
    return mm / 25.4 * dpi


def render_map_frame(
    frame: MapFrameElement,
    snapshot: WorkspaceSnapshot,
    dpi: float = 300.0,
    reference_dpi: float | None = None,
) -> QPixmap:
    """将工作区数据渲染到地图框对应的像素图。

    参数:
        frame: 地图框布局元素（位置、大小、比例尺）。
        snapshot: 当前工作区全部图层的快照。
        dpi: 输出分辨率（默认 300）。
        reference_dpi: map_units_per_pixel 当初计算时使用的 DPI。
                       为 None 时假定与 dpi 相同（无需调整）。
                       若与 dpi 不同，则等比缩放 mupp 以保证地理范围不变。

    返回:
        已渲染的像素图；空快照时返回白色填充图。
    """
    pw: int = max(1, round(_mm_to_px(frame.width_mm, dpi)))
    ph: int = max(1, round(_mm_to_px(frame.height_mm, dpi)))
    mupp: float = frame.map_units_per_pixel
    if reference_dpi is not None and reference_dpi > 0:
        mupp = mupp * (reference_dpi / dpi)

    # --- 计算地图范围 ---
    ground_half_w: float = pw * mupp / 2.0
    ground_half_h: float = ph * mupp / 2.0
    cx: float = frame.map_center_x
    cy: float = frame.map_center_y
    min_x: float = cx - ground_half_w
    max_x: float = cx + ground_half_w
    min_y: float = cy - ground_half_h
    max_y: float = cy + ground_half_h

    # Qt 场景 Y 轴向下翻转（与 MapCanvas 约定一致）
    scene_rect: QRectF = QRectF(min_x, -max_y, max_x - min_x, max_y - min_y)

    # --- 离屏渲染 ---
    scene: QGraphicsScene = QGraphicsScene()
    scene.setSceneRect(scene_rect)
    scene.setBackgroundBrush(QBrush(QColor(frame.background_color)))

    vector_renderer: QtVectorRenderer = QtVectorRenderer()
    raster_renderer: QtRasterRenderer = QtRasterRenderer()

    if snapshot.layers:
        for z_value, layer_snap in enumerate(snapshot.layers):
            if not layer_snap.visible:
                continue
            if isinstance(layer_snap.display_payload, RasterDisplayPayload):
                raster_renderer.render_layer(
                    scene, layer_snap, float(z_value),
                )
            elif isinstance(layer_snap.display_payload, VectorDisplayPayload):
                vector_renderer.render_layer(
                    scene, layer_snap, float(z_value), mupp,
                )
            elif isinstance(layer_snap.layer, RasterLayer):
                # 兼容旧调用方直接构造未附带显示载荷的快照。
                raster_renderer.render_layer(
                    scene, layer_snap, float(z_value),
                )
            elif isinstance(layer_snap.layer, VectorLayer):
                vector_renderer.render_layer(
                    scene, layer_snap, float(z_value), mupp,
                )

    # --- 输出到像素图 ---
    pixmap: QPixmap = QPixmap(pw, ph)
    pixmap.fill(QColor(frame.background_color))
    painter: QPainter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    scene.render(
        painter,
        target=QRectF(0, 0, pw, ph),
        source=scene_rect,
        aspectRatioMode=Qt.AspectRatioMode.IgnoreAspectRatio,
    )
    painter.end()

    return pixmap


# ---------------------------------------------------------------------------
# 比例尺渲染
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ScaleBarMetrics:
    """比例尺绘制用的像素几何。"""

    px: float
    py: float
    actual_pw: float
    seg_px: float
    seg_ground_m: float
    num_segments: int
    unit: str
    color: QColor
    alt_color: QColor
    label_font: QFont
    bar_y: float
    bar_h: float
    stroke: float


def render_scale_bar(
    element: ScaleBarElement,
    scene: QGraphicsScene,
    dpi: float,
    map_frame: MapFrameElement | None = None,
) -> list[QGraphicsItem]:
    """在场景中绘制比例尺，形态对齐 ArcGIS Pro 的条状 / 线状两类。

    参数:
        element: 比例尺布局元素。
        scene: 目标场景。
        dpi: 输出分辨率。
        map_frame: 关联的地图框（用于获取比例），为 None 时使用默认值。

    返回:
        创建的全部图元。
    """
    metrics = _scale_bar_metrics(element, dpi, map_frame)
    items: list[QGraphicsItem] = []
    if element.style == "double_alternating":
        items.extend(_draw_double_alternating_bar(scene, metrics))
    elif element.style == "line":
        items.extend(_draw_line_scale_bar(scene, metrics))
    else:
        items.extend(_draw_alternating_bar(scene, metrics))
    items.extend(_draw_scale_bar_labels(scene, metrics))
    return items


def _scale_bar_metrics(
    element: ScaleBarElement,
    dpi: float,
    map_frame: MapFrameElement | None,
) -> _ScaleBarMetrics:
    """按框宽和地图比例计算分段几何。"""
    px: float = _mm_to_px(element.x_mm, dpi)
    py: float = _mm_to_px(element.y_mm, dpi)
    pw: float = _mm_to_px(element.width_mm, dpi) if element.width_mm > 0 else 120
    ph: float = _mm_to_px(element.height_mm, dpi) if element.height_mm > 0 else 4
    segments: int = max(1, element.num_segments)

    mupp: float = map_frame.map_units_per_pixel if map_frame is not None else 1.0
    total_ground_m: float = pw * mupp
    nice: float = _nice_number(total_ground_m)
    actual_pw: float = nice / mupp if mupp > 0 else pw

    label_font: QFont = QFont("Arial")
    label_font.setPixelSize(max(1, round(_mm_to_px(element.label_font_size_mm, dpi))))
    bar_h: float = ph * 0.5
    return _ScaleBarMetrics(
        px=px,
        py=py,
        actual_pw=actual_pw,
        seg_px=actual_pw / segments,
        seg_ground_m=nice / segments,
        num_segments=segments,
        unit=element.unit,
        color=QColor(element.color),
        alt_color=QColor("#ffffff"),
        label_font=label_font,
        bar_y=py + ph - bar_h,
        bar_h=bar_h,
        stroke=max(1.0, _mm_to_px(0.2, dpi)),
    )


def _draw_alternating_bar(
    scene: QGraphicsScene,
    metrics: _ScaleBarMetrics,
) -> list[QGraphicsItem]:
    """单层黑白交替条（Pro Alternating Scale Bar）。"""
    items: list[QGraphicsItem] = []
    for i in range(metrics.num_segments):
        fill = metrics.color if i % 2 == 0 else metrics.alt_color
        items.append(
            _add_scale_rect(
                scene,
                metrics.px + i * metrics.seg_px,
                metrics.bar_y,
                metrics.seg_px,
                metrics.bar_h,
                fill,
            )
        )
    items.extend(_add_scale_dividers(scene, metrics))
    items.append(_add_scale_outline(scene, metrics))
    return items


def _draw_double_alternating_bar(
    scene: QGraphicsScene,
    metrics: _ScaleBarMetrics,
) -> list[QGraphicsItem]:
    """双层黑白交错条（Pro Double Alternating Scale Bar）。"""
    items: list[QGraphicsItem] = []
    row_h: float = metrics.bar_h / 2.0
    for i in range(metrics.num_segments):
        top_fill = metrics.color if i % 2 == 0 else metrics.alt_color
        bot_fill = metrics.alt_color if i % 2 == 0 else metrics.color
        seg_x: float = metrics.px + i * metrics.seg_px
        items.append(
            _add_scale_rect(scene, seg_x, metrics.bar_y, metrics.seg_px, row_h, top_fill)
        )
        items.append(
            _add_scale_rect(
                scene, seg_x, metrics.bar_y + row_h, metrics.seg_px, row_h, bot_fill,
            )
        )
    items.extend(_add_scale_dividers(scene, metrics))
    items.append(_add_scale_outline(scene, metrics))
    mid_y: float = metrics.bar_y + row_h
    items.append(
        _add_scale_line(
            scene,
            metrics.px,
            mid_y,
            metrics.px + metrics.actual_pw,
            mid_y,
            metrics.color,
            metrics.stroke,
        )
    )
    return items


def _draw_line_scale_bar(
    scene: QGraphicsScene,
    metrics: _ScaleBarMetrics,
) -> list[QGraphicsItem]:
    """线状刻度比例尺（Pro Scale Line）。"""
    items: list[QGraphicsItem] = []
    base_y: float = metrics.bar_y + metrics.bar_h
    items.append(
        _add_scale_line(
            scene,
            metrics.px,
            base_y,
            metrics.px + metrics.actual_pw,
            base_y,
            metrics.color,
            metrics.stroke,
        )
    )
    ticks: int = metrics.num_segments + 1
    for i in range(ticks):
        tick_x: float = metrics.px + i * metrics.seg_px
        # 起止刻度拉满，中间刻度略短，对应 Pro 的 division marks。
        tick_h: float = metrics.bar_h if i in {0, ticks - 1} else metrics.bar_h * 0.7
        items.append(
            _add_scale_line(
                scene,
                tick_x,
                base_y - tick_h,
                tick_x,
                base_y,
                metrics.color,
                metrics.stroke,
            )
        )
    return items


def _draw_scale_bar_labels(
    scene: QGraphicsScene,
    metrics: _ScaleBarMetrics,
) -> list[QGraphicsItem]:
    """在各分段点标注累计距离，单位写在末端。"""
    items: list[QGraphicsItem] = []
    font_metrics = QFontMetrics(metrics.label_font)
    label_h: float = float(font_metrics.height())
    label_y: float = max(metrics.py, metrics.bar_y - label_h - 1.0)
    last_index: int = metrics.num_segments
    for i in range(last_index + 1):
        meters: float = i * metrics.seg_ground_m
        text = _format_scale_label(
            meters, metrics.unit, with_unit=(i == last_index),
        )
        item: QGraphicsSimpleTextItem = scene.addSimpleText(text, metrics.label_font)
        width: float = item.boundingRect().width()
        tick_x: float = metrics.px + i * metrics.seg_px
        if i == 0:
            pos_x = tick_x
        elif i == last_index:
            pos_x = tick_x
        else:
            pos_x = tick_x - width / 2.0
        item.setPos(pos_x, label_y)
        item.setBrush(QBrush(metrics.color))
        item.setZValue(20)
        items.append(item)
    return items


def _format_scale_label(meters: float, unit: str, *, with_unit: bool) -> str:
    """把地面距离格式化为比例尺数字。"""
    if unit == "km" and meters >= 1000.0:
        value = meters / 1000.0
        suffix = " km"
    elif unit == "km" and meters == 0.0:
        value = 0.0
        suffix = " km"
    else:
        value = meters
        suffix = " m"
    if abs(value - round(value)) < 1e-6:
        number = str(int(round(value)))
    else:
        number = f"{value:.1f}"
    return f"{number}{suffix}" if with_unit else number


def _add_scale_rect(
    scene: QGraphicsScene,
    x: float,
    y: float,
    width: float,
    height: float,
    fill: QColor,
) -> QGraphicsRectItem:
    """添加无描边填充块。"""
    rect = scene.addRect(
        QRectF(x, y, width, height),
        QPen(Qt.PenStyle.NoPen),
        QBrush(fill),
    )
    rect.setZValue(20)
    return rect


def _add_scale_dividers(
    scene: QGraphicsScene,
    metrics: _ScaleBarMetrics,
) -> list[QGraphicsItem]:
    """在分段处画竖线。"""
    items: list[QGraphicsItem] = []
    for i in range(1, metrics.num_segments):
        tick_x = metrics.px + i * metrics.seg_px
        items.append(
            _add_scale_line(
                scene,
                tick_x,
                metrics.bar_y,
                tick_x,
                metrics.bar_y + metrics.bar_h,
                metrics.color,
                metrics.stroke,
            )
        )
    return items


def _add_scale_outline(
    scene: QGraphicsScene,
    metrics: _ScaleBarMetrics,
) -> QGraphicsRectItem:
    """勾出整条比例尺外框，保证浅色分段在白纸上可见。"""
    outline = scene.addRect(
        QRectF(metrics.px, metrics.bar_y, metrics.actual_pw, metrics.bar_h),
        QPen(metrics.color, metrics.stroke),
        QBrush(Qt.BrushStyle.NoBrush),
    )
    outline.setZValue(21)
    return outline


def _add_scale_line(
    scene: QGraphicsScene,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    color: QColor,
    width: float,
) -> QGraphicsItem:
    """添加比例尺线段。"""
    line = scene.addLine(x1, y1, x2, y2, QPen(color, width))
    line.setZValue(21)
    return line


def _nice_number(value: float) -> float:
    """将数值取整到最接近的"好看"数字（1, 2, 5, 10, 20, 50...）。"""
    if value <= 0:
        return 1.0
    exp: float = math.floor(math.log10(value))
    mantissa: float = value / (10 ** exp)
    nice_mantissa: float
    if mantissa < 1.5:
        nice_mantissa = 1.0
    elif mantissa < 3.0:
        nice_mantissa = 2.0
    elif mantissa < 7.5:
        nice_mantissa = 5.0
    else:
        nice_mantissa = 10.0
    return nice_mantissa * (10 ** exp)


# ---------------------------------------------------------------------------
# 图例渲染
# ---------------------------------------------------------------------------

_LEGEND_PAD_MM = 2.5
_LEGEND_PATCH_W_MM = 8.0
_LEGEND_PATCH_H_MM = 4.5
_LEGEND_PATCH_GAP_MM = 2.0
_LEGEND_CLASS_GAP_MM = 1.4
_LEGEND_TITLE_GAP_MM = 2.2
_LEGEND_COL_GAP_MM = 4.0
_LEGEND_RULE_MM = 0.18
_LEGEND_TITLE_COLOR = "#111827"
_LEGEND_LABEL_COLOR = "#1f2937"
_LEGEND_FONTS = (
    "Microsoft YaHei",
    "Microsoft YaHei UI",
    "Source Han Sans SC",
    "SimHei",
    "Arial",
)


def estimate_legend_height_mm(
    element: LegendElement,
    snapshot: WorkspaceSnapshot | None,
) -> float:
    """按当前图例模型估算所需高度（毫米），供首次添加时定框。"""
    rows, headings = _legend_body_counts(element, snapshot)
    height = _LEGEND_PAD_MM * 2
    if _legend_shows_title(element):
        height += element.title_font_size_mm + _LEGEND_TITLE_GAP_MM
    row_h = max(_LEGEND_PATCH_H_MM, element.item_font_size_mm) + _LEGEND_CLASS_GAP_MM
    cols = max(1, element.column_count)
    body = rows + headings
    per_col = math.ceil(body / cols) if body else 0
    height += per_col * row_h
    return max(12.0, height)


def estimate_legend_width_mm(
    element: LegendElement,
    snapshot: WorkspaceSnapshot | None,
) -> float:
    """按最长标题/标签估算图例宽度（毫米）。"""
    labels = _legend_width_labels(element, snapshot)
    text_w = 12.0
    for text, size_mm, bold in labels:
        text_w = max(text_w, _estimate_text_width_mm(text, size_mm, bold=bold))
    patch_row_w = _LEGEND_PATCH_W_MM + _LEGEND_PATCH_GAP_MM + text_w
    if _legend_shows_title(element):
        title_w = _estimate_text_width_mm(
            element.title.strip(), element.title_font_size_mm, bold=True,
        )
        patch_row_w = max(patch_row_w, title_w)
    cols = max(1, element.column_count)
    width = (
        _LEGEND_PAD_MM * 2
        + cols * patch_row_w
        + (cols - 1) * _LEGEND_COL_GAP_MM
    )
    return max(22.0, width)


def _legend_shows_title(element: LegendElement) -> bool:
    """标题开关打开且文字非空时才绘制标题。"""
    return element.show_title and bool(element.title.strip())


def _legend_body_counts(
    element: LegendElement,
    snapshot: WorkspaceSnapshot | None,
) -> tuple[int, int]:
    """返回 (补丁行数, 图层名行数)。"""
    rows = 0
    headings = 0
    for _block, visible in _iter_legend_blocks(element, snapshot):
        if _should_draw_layer_heading(element, visible):
            headings += 1
        rows += len(visible)
    return rows, headings


def _legend_width_labels(
    element: LegendElement,
    snapshot: WorkspaceSnapshot | None,
) -> list[tuple[str, float, bool]]:
    """收集用于估宽的 (文字, 字号毫米, 是否粗体)。"""
    labels: list[tuple[str, float, bool]] = []
    if _legend_shows_title(element):
        labels.append((element.title.strip(), element.title_font_size_mm, True))
    for block, visible in _iter_legend_blocks(element, snapshot):
        if _should_draw_layer_heading(element, visible):
            labels.append((block.layer_name, element.item_font_size_mm, True))
        for patch in visible:
            label = _legend_patch_label(block, visible, patch)
            labels.append((label, element.item_font_size_mm, False))
    return labels


def _iter_legend_blocks(
    element: LegendElement,
    snapshot: WorkspaceSnapshot | None,
) -> list[tuple[LegendLayerBlock, tuple[LegendPatch, ...]]]:
    """按布局覆盖后的模型列出有可见补丁的图层块。"""
    if snapshot is None:
        return []
    result: list[tuple[LegendLayerBlock, tuple[LegendPatch, ...]]] = []
    for block in build_layout_legend_model(snapshot, element.label_overrides):
        visible = _visible_layout_patches(block)
        if visible:
            result.append((block, visible))
    return result


def _should_draw_layer_heading(
    element: LegendElement,
    patches: tuple[LegendPatch, ...],
) -> bool:
    """图层名默认关闭；打开后仍只在多类别/栅格专题下显示。"""
    return element.show_layer_headings and _needs_layer_header(patches)


def _legend_patch_label(
    block: LegendLayerBlock,
    visible: tuple[LegendPatch, ...],
    patch: LegendPatch,
) -> str:
    """单符号图层用图层名作标签，分类图层用类别名。"""
    is_simple = (
        len(visible) == 1
        and patch.kind not in {LegendPatchKind.SWATCH, LegendPatchKind.RAMP}
    )
    return block.layer_name if is_simple else patch.label


def _estimate_text_width_mm(
    text: str, font_size_mm: float, *, bold: bool = False,
) -> float:
    """按中西文字宽粗估一行宽度，避免估宽依赖 Qt 字体度量。"""
    width = 0.0
    for char in text:
        code = ord(char)
        if 0x4E00 <= code <= 0x9FFF or 0x3000 <= code <= 0x303F:
            width += font_size_mm * 1.05
        elif char == " ":
            width += font_size_mm * 0.35
        else:
            width += font_size_mm * 0.56
    if bold:
        width *= 1.06
    return width


def _visible_layout_patches(block: LegendLayerBlock) -> tuple[LegendPatch, ...]:
    """返回布局图例应绘制的可见补丁。"""
    return tuple(patch for patch in block.patches if patch.visible)


def _needs_layer_header(patches: tuple[LegendPatch, ...]) -> bool:
    """多个类别或栅格专题时，在补丁前加一层图层名。"""
    if len(patches) > 1:
        return True
    if not patches:
        return False
    return patches[0].kind in {LegendPatchKind.SWATCH, LegendPatchKind.RAMP}


@dataclass(frozen=True, slots=True)
class _LegendDrawRow:
    """图例框内的一行待绘内容。"""

    kind: str
    label: str
    font: QFont
    patch: LegendPatch | None


def _legend_font(size_mm: float, dpi: float, *, bold: bool = False) -> QFont:
    """图例用中文字体，避免 Arial 把省名画得又稀又淡。"""
    font = QFont()
    font.setFamilies(list(_LEGEND_FONTS))
    font.setPixelSize(max(1, round(_mm_to_px(size_mm, dpi))))
    font.setBold(bold)
    return font


def render_legend(
    element: LegendElement,
    scene: QGraphicsScene,
    dpi: float,
    snapshot: WorkspaceSnapshot | None = None,
) -> list[QGraphicsItem]:
    """在元素框内绘制左对齐、贴内容的专题图例。

    参数:
        element: 图例布局元素。
        scene: 目标场景。
        dpi: 输出分辨率。
        snapshot: 工作区快照。

    返回:
        创建的全部图元。
    """
    items: list[QGraphicsItem] = []
    px: float = _mm_to_px(element.x_mm, dpi)
    py: float = _mm_to_px(element.y_mm, dpi)
    frame_h: float = _mm_to_px(element.height_mm, dpi)
    frame_w: float = _mm_to_px(element.width_mm, dpi)
    frame_bottom: float = py + frame_h
    pad: float = _mm_to_px(_LEGEND_PAD_MM, dpi)
    inner_x: float = px + pad
    inner_w: float = max(1.0, frame_w - pad * 2)

    title_font = _legend_font(element.title_font_size_mm, dpi, bold=True)
    item_font = _legend_font(element.item_font_size_mm, dpi)
    heading_font = _legend_font(element.item_font_size_mm, dpi, bold=True)

    row_h: float = _mm_to_px(
        max(_LEGEND_PATCH_H_MM, element.item_font_size_mm) + _LEGEND_CLASS_GAP_MM,
        dpi,
    )
    patch_w: float = _mm_to_px(_LEGEND_PATCH_W_MM, dpi)
    patch_h: float = _mm_to_px(_LEGEND_PATCH_H_MM, dpi)
    patch_gap: float = _mm_to_px(_LEGEND_PATCH_GAP_MM, dpi)

    body_entries: list[_LegendDrawRow] = []
    for block, visible in _iter_legend_blocks(element, snapshot):
        if _should_draw_layer_heading(element, visible):
            body_entries.append(
                _LegendDrawRow(
                    kind="layer_header",
                    label=block.layer_name,
                    font=heading_font,
                    patch=None,
                )
            )
        for patch in visible:
            body_entries.append(
                _LegendDrawRow(
                    kind="patch",
                    label=_legend_patch_label(block, visible, patch),
                    font=item_font,
                    patch=patch,
                )
            )

    cursor_y: float = py + pad
    content_w: float = 0.0
    title_rule_y: float | None = None
    if _legend_shows_title(element):
        title_item = scene.addSimpleText(element.title.strip(), title_font)
        title_item.setPos(inner_x, cursor_y)
        title_item.setBrush(QBrush(QColor(_LEGEND_TITLE_COLOR)))
        title_item.setZValue(20)
        items.append(title_item)
        title_box = title_item.boundingRect()
        content_w = max(content_w, title_box.width())
        cursor_y += title_box.height() + _mm_to_px(0.7, dpi)
        title_rule_y = cursor_y
        cursor_y += _mm_to_px(_LEGEND_TITLE_GAP_MM - 0.7, dpi)

    num_cols: int = max(1, element.column_count)
    col_gap: float = _mm_to_px(_LEGEND_COL_GAP_MM, dpi)
    col_width: float = (
        (inner_w - (num_cols - 1) * col_gap) / num_cols if num_cols > 1 else inner_w
    )
    per_col: int = math.ceil(len(body_entries) / num_cols) if body_entries else 1

    for idx, entry in enumerate(body_entries):
        col: int = min(idx // per_col, num_cols - 1)
        row_in_col: int = idx % per_col
        entry_x: float = inner_x + col * (col_width + col_gap)
        entry_y: float = cursor_y + row_in_col * row_h
        if entry_y + row_h > frame_bottom - pad + 1.0:
            continue

        if entry.kind == "layer_header":
            text = scene.addSimpleText(entry.label, entry.font)
            text_h = text.boundingRect().height()
            text.setPos(entry_x, entry_y + (row_h - text_h) / 2.0)
            text.setBrush(QBrush(QColor(_LEGEND_TITLE_COLOR)))
            text.setZValue(20)
            items.append(text)
            content_w = max(content_w, text.boundingRect().width())
            continue

        row_patch = entry.patch
        if row_patch is None:
            continue
        _draw_legend_patch(
            scene, items, row_patch, entry_x, entry_y, patch_w, patch_h, row_h,
        )
        text = scene.addSimpleText(entry.label, entry.font)
        text_box = text.boundingRect()
        text.setPos(
            entry_x + patch_w + patch_gap,
            entry_y + (row_h - text_box.height()) / 2.0,
        )
        text.setBrush(QBrush(QColor(_LEGEND_LABEL_COLOR)))
        text.setZValue(20)
        items.append(text)
        content_w = max(content_w, patch_w + patch_gap + text_box.width())

    if title_rule_y is not None and content_w > 0:
        rule = scene.addLine(
            inner_x,
            title_rule_y,
            inner_x + min(content_w, inner_w),
            title_rule_y,
            QPen(QColor("#9ca3af"), max(1.0, _mm_to_px(_LEGEND_RULE_MM, dpi))),
        )
        rule.setZValue(20)
        items.append(rule)

    return items


def _draw_legend_patch(
    scene: QGraphicsScene,
    items: list[QGraphicsItem],
    patch: LegendPatch,
    px: float,
    py: float,
    patch_w: float,
    patch_h: float,
    row_h: float,
) -> None:
    """按补丁类型绘制色块、色带或几何符号。"""
    if patch.kind is LegendPatchKind.RAMP:
        _draw_color_ramp(scene, items, px, py, patch_w, patch_h, row_h, patch.colors)
        return
    if patch.kind is LegendPatchKind.SWATCH:
        color = patch.colors[0] if patch.colors else "#6b7280"
        _draw_swatch(scene, items, px, py, patch_w, patch_h, row_h, color)
        return
    if patch.style is None:
        _draw_raster_patch(scene, items, px, py, patch_w, patch_h, row_h)
        return
    if patch.kind is LegendPatchKind.POINT:
        _draw_point_patch(scene, items, px, py, patch_w, patch_h, row_h, patch.style)
    elif patch.kind is LegendPatchKind.LINE:
        _draw_line_patch(scene, items, px, py, patch_w, patch_h, row_h, patch.style)
    else:
        _draw_polygon_patch(scene, items, px, py, patch_w, patch_h, row_h, patch.style)


def _legend_patch_rect(
    px: float, py: float, patch_w: float, patch_h: float, row_h: float,
) -> QRectF:
    """色块在行内垂直居中。"""
    return QRectF(px, py + (row_h - patch_h) / 2.0, patch_w, patch_h)


def _add_rounded_patch(
    scene: QGraphicsScene,
    items: list[QGraphicsItem],
    rect: QRectF,
    fill: QColor,
    stroke: QColor,
    stroke_w: float,
) -> None:
    """略圆角的图例色块，接近 Pro 默认 patch。"""
    path = QPainterPath()
    radius = min(rect.height(), rect.width()) * 0.12
    path.addRoundedRect(rect, radius, radius)
    item = scene.addPath(path, QPen(stroke, stroke_w), QBrush(fill))
    item.setZValue(20)
    items.append(item)


def _draw_swatch(
    scene: QGraphicsScene,
    items: list[QGraphicsItem],
    px: float,
    py: float,
    patch_w: float,
    patch_h: float,
    row_h: float,
    color: str,
) -> None:
    """绘制单色矩形色块。"""
    _add_rounded_patch(
        scene,
        items,
        _legend_patch_rect(px, py, patch_w, patch_h, row_h),
        QColor(color),
        QColor("#374151"),
        0.7,
    )


def _draw_color_ramp(
    scene: QGraphicsScene,
    items: list[QGraphicsItem],
    px: float,
    py: float,
    patch_w: float,
    patch_h: float,
    row_h: float,
    colors: tuple[str, ...],
) -> None:
    """绘制横向色带。"""
    resolved: tuple[str, ...] = colors or ("#000000", "#FFFFFF")
    rect = _legend_patch_rect(px, py, patch_w, patch_h, row_h)
    seg_w: float = rect.width() / len(resolved)
    for index, color in enumerate(resolved):
        cell = scene.addRect(
            QRectF(rect.x() + index * seg_w, rect.y(), max(seg_w, 1.0), rect.height()),
            QPen(Qt.PenStyle.NoPen),
            QBrush(QColor(color)),
        )
        cell.setZValue(20)
        items.append(cell)
    outline = scene.addRect(
        rect, QPen(QColor("#374151"), 0.7), QBrush(Qt.BrushStyle.NoBrush),
    )
    outline.setZValue(21)
    items.append(outline)


def _draw_point_patch(
    scene: QGraphicsScene,
    items: list[QGraphicsItem],
    px: float,
    py: float,
    patch_w: float,
    patch_h: float,
    row_h: float,
    style,
) -> None:
    """绘制点符号：实心圆。"""
    fill = QColor(style.fill_color) if style.fill_color != "transparent" else QColor(Qt.GlobalColor.transparent)
    stroke = QColor(style.stroke_color)
    size = min(patch_w, patch_h)
    r: float = size * 0.38
    cx: float = px + patch_w / 2
    cy: float = py + row_h / 2
    circle: QGraphicsEllipseItem = scene.addEllipse(
        QRectF(cx - r, cy - r, r * 2, r * 2),
        QPen(stroke, max(0.8, style.line_width * 0.6)),
        QBrush(fill),
    )
    circle.setZValue(20)
    items.append(circle)


def _draw_line_patch(
    scene: QGraphicsScene,
    items: list[QGraphicsItem],
    px: float,
    py: float,
    patch_w: float,
    patch_h: float,
    row_h: float,
    style,
) -> None:
    """绘制线符号：水平线段。"""
    stroke = QColor(style.stroke_color)
    lw: float = max(1.0, style.line_width * 1.2)
    y: float = py + row_h / 2
    line = scene.addLine(
        px, y, px + patch_w, y,
        QPen(stroke, lw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap),
    )
    line.setZValue(20)
    items.append(line)


def _draw_polygon_patch(
    scene: QGraphicsScene,
    items: list[QGraphicsItem],
    px: float,
    py: float,
    patch_w: float,
    patch_h: float,
    row_h: float,
    style,
) -> None:
    """绘制面符号：填充矩形 + 描边。"""
    fill = QColor(style.fill_color) if style.fill_color != "transparent" else QColor(Qt.GlobalColor.transparent)
    stroke = QColor(style.stroke_color)
    _add_rounded_patch(
        scene,
        items,
        _legend_patch_rect(px, py, patch_w, patch_h, row_h),
        fill,
        stroke,
        max(0.7, style.line_width * 0.5),
    )


def _draw_raster_patch(
    scene: QGraphicsScene,
    items: list[QGraphicsItem],
    px: float,
    py: float,
    patch_w: float,
    patch_h: float,
    row_h: float,
) -> None:
    """绘制栅格符号：3×3 棋盘格。"""
    rect = _legend_patch_rect(px, py, patch_w, patch_h, row_h)
    cell_w: float = rect.width() / 3
    cell_h: float = rect.height() / 3
    for row in range(3):
        for col in range(3):
            is_dark: bool = (row + col) % 2 == 0
            color: QColor = QColor("#6b7280") if is_dark else QColor("#d1d5db")
            cell_rect = scene.addRect(
                QRectF(
                    rect.x() + col * cell_w,
                    rect.y() + row * cell_h,
                    cell_w,
                    cell_h,
                ),
                QPen(Qt.PenStyle.NoPen),
                QBrush(color),
            )
            cell_rect.setZValue(20)
            items.append(cell_rect)


# ---------------------------------------------------------------------------
# 指北针渲染
# ---------------------------------------------------------------------------


def render_north_arrow(
    element: NorthArrowElement,
    scene: QGraphicsScene,
    dpi: float,
) -> list[QGraphicsItem]:
    """在场景中绘制指北针。

    参数:
        element: 指北针布局元素。
        scene: 目标场景。
        dpi: 输出分辨率。

    返回:
        创建的全部图元。
    """
    items: list[QGraphicsItem] = []
    cx: float = _mm_to_px(element.x_mm + element.width_mm / 2, dpi)
    cy: float = _mm_to_px(element.y_mm + element.height_mm / 2, dpi)
    size: float = _mm_to_px(min(element.width_mm, element.height_mm), dpi) * 0.45

    color: QColor = QColor(element.color)

    if element.style == "simple":
        # 简单三角形
        path: QPainterPath = QPainterPath()
        path.moveTo(cx, cy - size)
        path.lineTo(cx + size * 0.6, cy + size * 0.5)
        path.lineTo(cx, cy + size * 0.2)
        path.lineTo(cx - size * 0.6, cy + size * 0.5)
        path.closeSubpath()
        item: QGraphicsPathItem = scene.addPath(
            path,
            QPen(Qt.PenStyle.NoPen),
            QBrush(color),
        )
        item.setZValue(20)
        items.append(item)

    elif element.style == "arrow":
        # 箭头形状
        path = QPainterPath()
        path.moveTo(cx, cy - size)
        path.lineTo(cx + size * 0.35, cy - size * 0.15)
        path.lineTo(cx + size * 0.1, cy - size * 0.1)
        path.lineTo(cx + size * 0.1, cy + size)
        path.lineTo(cx - size * 0.1, cy + size)
        path.lineTo(cx - size * 0.1, cy - size * 0.1)
        path.lineTo(cx - size * 0.35, cy - size * 0.15)
        path.closeSubpath()
        item = scene.addPath(path, QPen(Qt.PenStyle.NoPen), QBrush(color))
        item.setZValue(20)
        items.append(item)

    else:  # "compass" — 经典罗盘样式
        compass_color: QColor = QColor(element.color)
        light_color: QColor = QColor("#9ca3af")

        # N 半部分
        n_path: QPainterPath = QPainterPath()
        n_path.moveTo(cx, cy - size)
        n_path.lineTo(cx + size * 0.5, cy)
        n_path.lineTo(cx, cy - size * 0.3)
        n_path.lineTo(cx - size * 0.5, cy)
        n_path.closeSubpath()
        n_item: QGraphicsPathItem = scene.addPath(
            n_path, QPen(Qt.PenStyle.NoPen), QBrush(compass_color),
        )
        n_item.setZValue(20)
        items.append(n_item)

        # S 半部分
        s_path: QPainterPath = QPainterPath()
        s_path.moveTo(cx, cy + size)
        s_path.lineTo(cx + size * 0.5, cy)
        s_path.lineTo(cx, cy + size * 0.3)
        s_path.lineTo(cx - size * 0.5, cy)
        s_path.closeSubpath()
        s_item: QGraphicsPathItem = scene.addPath(
            s_path, QPen(Qt.PenStyle.NoPen), QBrush(light_color),
        )
        s_item.setZValue(20)
        items.append(s_item)

        # 中心圆
        circle_r: float = size * 0.08
        circle: QGraphicsEllipseItem = scene.addEllipse(
            QRectF(cx - circle_r, cy - circle_r, circle_r * 2, circle_r * 2),
            QPen(Qt.PenStyle.NoPen),
            QBrush(compass_color),
        )
        circle.setZValue(21)
        items.append(circle)

    # N 文字标签
    label_font: QFont = QFont("Arial")
    label_font.setPixelSize(max(1, round(size * 0.25)))
    label_font.setBold(True)
    label: QGraphicsSimpleTextItem = scene.addSimpleText("N", label_font)
    label.setPos(cx - label.boundingRect().width() / 2, cy - size - label.boundingRect().height())
    label.setBrush(QBrush(color))
    label.setZValue(20)
    items.append(label)

    return items


# ---------------------------------------------------------------------------
# 文本渲染
# ---------------------------------------------------------------------------


def render_text(
    element: TextElement,
    scene: QGraphicsScene,
    dpi: float,
) -> list[QGraphicsItem]:
    """在场景中绘制自由文本。

    参数:
        element: 文本布局元素。
        scene: 目标场景。
        dpi: 输出分辨率。

    返回:
        创建的全部图元。
    """
    items: list[QGraphicsItem] = []

    font: QFont = QFont("Arial")
    font.setPixelSize(max(1, round(_mm_to_px(element.font_size_mm, dpi))))
    font.setBold(element.bold)
    font.setItalic(element.italic)

    color: QColor = QColor(element.color)

    text_item: QGraphicsSimpleTextItem = scene.addSimpleText(element.text, font)
    text_item.setBrush(QBrush(color))
    text_item.setZValue(20)

    pad: float = _mm_to_px(1.0, dpi)
    frame_x: float = _mm_to_px(element.x_mm, dpi)
    frame_y: float = _mm_to_px(element.y_mm, dpi)
    frame_w: float = _mm_to_px(max(element.width_mm, 1.0), dpi)
    frame_h: float = _mm_to_px(max(element.height_mm, 1.0), dpi)
    text_w: float = text_item.boundingRect().width()
    text_h: float = text_item.boundingRect().height()

    if element.alignment == "center":
        px = frame_x + (frame_w - text_w) / 2.0
    elif element.alignment == "right":
        px = frame_x + frame_w - text_w - pad
    else:
        px = frame_x + pad
    py = frame_y + (frame_h - text_h) / 2.0

    text_item.setPos(px, py)
    items.append(text_item)

    return items


# ---------------------------------------------------------------------------
# 整页导出渲染
# ---------------------------------------------------------------------------


def render_full_page(
    document,
    snapshot: WorkspaceSnapshot | None = None,
    dpi: float | None = None,
    view_dpi: float = 96.0,
) -> QPixmap:
    """将整个布局页面渲染到一个 QPixmap。

    参数:
        document: 布局文档（LayoutDocument），包含页面规格和元素列表。
        snapshot: 工作区快照（用于地图框渲染）。
        dpi: 输出分辨率；为 None 时使用页面自身的 dpi。
        view_dpi: 交互式视图使用的屏幕 DPI。
                  map_units_per_pixel 基于此值计算，导出 DPI 不同时需等比缩放。

    返回:
        渲染完成的像素图。
    """
    from app.domain.layout import (
        LegendElement,
        MapFrameElement,
        NorthArrowElement,
        ScaleBarElement,
        TextElement,
    )

    page = document.page
    out_dpi: float = dpi if dpi is not None else page.dpi
    pw: int = max(1, round(_mm_to_px(page.width_mm, out_dpi)))
    ph: int = max(1, round(_mm_to_px(page.height_mm, out_dpi)))

    scene: QGraphicsScene = QGraphicsScene()
    scene.setSceneRect(0, 0, pw, ph)
    scene.setBackgroundBrush(QBrush(QColor("#ffffff")))

    frames_by_id: dict[str, MapFrameElement] = {
        element.element_id: element
        for element in document.elements
        if isinstance(element, MapFrameElement)
    }
    first_frame: MapFrameElement | None = next(iter(frames_by_id.values()), None)

    for element in document.elements:
        if isinstance(element, MapFrameElement):
            if snapshot is not None:
                pixmap = render_map_frame(element, snapshot, out_dpi, reference_dpi=view_dpi)
                pix_item = scene.addPixmap(pixmap)
                px = _mm_to_px(element.x_mm, out_dpi)
                py = _mm_to_px(element.y_mm, out_dpi)
                pix_item.setPos(px, py)
            # 边框
            border_pen = QPen(
                QColor(element.border_color),
                max(1.0, _mm_to_px(element.border_width_mm, out_dpi)),
            )
            border_pen.setCosmetic(True)
            bx = _mm_to_px(element.x_mm, out_dpi)
            by = _mm_to_px(element.y_mm, out_dpi)
            bw = _mm_to_px(element.width_mm, out_dpi)
            bh = _mm_to_px(element.height_mm, out_dpi)
            border_rect = scene.addRect(
                QRectF(bx, by, bw, bh), border_pen, QBrush(Qt.BrushStyle.NoBrush),
            )
            border_rect.setZValue(11)
        elif isinstance(element, ScaleBarElement):
            linked = frames_by_id.get(element.linked_frame_id, first_frame)
            render_scale_bar(element, scene, out_dpi, linked)
        elif isinstance(element, LegendElement):
            render_legend(element, scene, out_dpi, snapshot)
        elif isinstance(element, NorthArrowElement):
            items = render_north_arrow(element, scene, out_dpi)
            if element.rotation != 0:
                cx = _mm_to_px(element.x_mm + element.width_mm / 2, out_dpi)
                cy = _mm_to_px(element.y_mm + element.height_mm / 2, out_dpi)
                for item in items:
                    item.setTransformOriginPoint(cx, cy)
                    item.setRotation(element.rotation)
        elif isinstance(element, TextElement):
            items = render_text(element, scene, out_dpi)
            if element.rotation != 0:
                cx = _mm_to_px(element.x_mm + element.width_mm / 2, out_dpi)
                cy = _mm_to_px(element.y_mm + element.height_mm / 2, out_dpi)
                for item in items:
                    item.setTransformOriginPoint(cx, cy)
                    item.setRotation(element.rotation)

    output: QPixmap = QPixmap(pw, ph)
    output.fill(QColor("#ffffff"))
    painter: QPainter = QPainter(output)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    scene.render(
        painter,
        target=QRectF(0, 0, pw, ph),
        source=QRectF(0, 0, pw, ph),
    )
    painter.end()

    return output
