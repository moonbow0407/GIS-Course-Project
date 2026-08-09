"""布局元素渲染 —— 将布局领域模型的制图元素转换为 QGraphicsItem / QPixmap。

地图框渲染复用现有的 QtVectorRenderer / QtRasterRenderer，
在离屏 QGraphicsScene 中重建图层图元，再通过 scene.render() 输出到像素图。
比例尺、图例、指北针直接在场景中绘制为矢量图元。
"""

import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
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
) -> QPixmap:
    """将工作区数据渲染到地图框对应的像素图。

    参数:
        frame: 地图框布局元素（位置、大小、比例尺）。
        snapshot: 当前工作区全部图层的快照。
        dpi: 输出分辨率（默认 300）。

    返回:
        已渲染的像素图；空快照时返回白色填充图。
    """
    pw: int = max(1, round(_mm_to_px(frame.width_mm, dpi)))
    ph: int = max(1, round(_mm_to_px(frame.height_mm, dpi)))
    mupp: float = frame.map_units_per_pixel

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
            layer = layer_snap.layer
            if isinstance(layer, RasterLayer):
                raster_renderer.render_layer(
                    scene, layer_snap, float(z_value),
                )
            elif isinstance(layer, VectorLayer):
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


def render_scale_bar(
    element: ScaleBarElement,
    scene: QGraphicsScene,
    dpi: float,
    map_frame: MapFrameElement | None = None,
) -> list[QGraphicsItem]:
    """在场景中绘制黑白交替比例尺。

    参数:
        element: 比例尺布局元素。
        scene: 目标场景。
        dpi: 输出分辨率。
        map_frame: 关联的地图框（用于获取比例），为 None 时使用默认值。

    返回:
        创建的全部图元。
    """
    items: list[QGraphicsItem] = []
    px: float = _mm_to_px(element.x_mm, dpi)
    py: float = _mm_to_px(element.y_mm, dpi)
    pw: float = _mm_to_px(element.width_mm, dpi) if element.width_mm > 0 else 120
    ph: float = _mm_to_px(element.height_mm, dpi) if element.height_mm > 0 else 4

    # 计算比例尺代表的实际距离
    mupp: float = map_frame.map_units_per_pixel if map_frame is not None else 1.0
    # 比例尺总宽度对应的地面距离（米）
    total_ground_m: float = pw * mupp
    # 取整到最接近的"好看"数字
    nice: float = _nice_number(total_ground_m)
    # 每个分段的地面距离
    seg_ground: float = nice / element.num_segments

    # 在场景中，总像素宽度应该反映 nice 地面距离
    actual_pw: float = nice / mupp if mupp > 0 else pw
    seg_px: float = actual_pw / element.num_segments

    bar_h: float = ph * 0.5
    bar_y: float = py + ph - bar_h

    color: QColor = QColor(element.color)
    alt_color: QColor = QColor("#ffffff")
    label_font: QFont = QFont("Arial")
    label_font.setPixelSize(max(1, round(_mm_to_px(element.label_font_size_mm, dpi))))

    for i in range(element.num_segments):
        seg_x: float = px + i * seg_px
        fill: QColor = color if i % 2 == 0 else alt_color
        rect: QGraphicsRectItem = scene.addRect(
            QRectF(seg_x, bar_y, seg_px, bar_h),
            QPen(Qt.PenStyle.NoPen),
            QBrush(fill),
        )
        rect.setZValue(20)
        items.append(rect)

        # 标签
        dist: float = seg_ground
        if element.unit == "km" and dist >= 1000:
            label: str = f"{dist / 1000:.1f} km" if i == element.num_segments - 1 else ""
        elif element.unit == "km":
            label = f"{dist:.0f} m" if i == 0 else ""
        else:
            label = f"{dist:.0f}" if i == 0 or i == element.num_segments - 1 else ""
        if label:
            text: QGraphicsSimpleTextItem = scene.addSimpleText(
                label, label_font,
            )
            text.setPos(seg_x, py)
            text.setBrush(QBrush(color))
            text.setZValue(20)
            items.append(text)

    # 顶层水平线
    line = scene.addLine(
        px, bar_y, px + actual_pw, bar_y,
        QPen(color, 1.0),
    )
    line.setZValue(20)
    items.append(line)

    return items


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


def render_legend(
    element: LegendElement,
    scene: QGraphicsScene,
    dpi: float,
    snapshot: WorkspaceSnapshot | None = None,
) -> list[QGraphicsItem]:
    """在场景中绘制图例：符号预览 + 图层名称。

    参数:
        element: 图例布局元素。
        scene: 目标场景。
        dpi: 输出分辨率。
        snapshot: 工作区快照（获取图层符号信息）。

    返回:
        创建的全部图元。
    """
    items: list[QGraphicsItem] = []
    px: float = _mm_to_px(element.x_mm, dpi)
    py: float = _mm_to_px(element.y_mm, dpi)

    title_font: QFont = QFont("Arial")
    title_font.setPixelSize(max(1, round(_mm_to_px(element.title_font_size_mm, dpi))))
    title_font.setBold(True)

    item_font: QFont = QFont("Arial")
    item_font.setPixelSize(max(1, round(_mm_to_px(element.item_font_size_mm, dpi))))

    row_h: float = _mm_to_px(element.item_font_size_mm + 1.5, dpi)
    patch_w: float = _mm_to_px(6.0, dpi)
    patch_h: float = _mm_to_px(3.0, dpi)
    gap: float = _mm_to_px(1.5, dpi)

    cur_y: float = py

    # 标题
    title: QGraphicsSimpleTextItem = scene.addSimpleText(element.title, title_font)
    title.setPos(px, cur_y)
    title.setBrush(QBrush(QColor("#1f2937")))
    title.setZValue(20)
    items.append(title)
    cur_y += title.boundingRect().height() + gap * 2

    # 遍历可见图层
    if snapshot is not None:
        for layer_snap in snapshot.layers:
            if not layer_snap.visible or not isinstance(layer_snap.layer, VectorLayer):
                continue
            vec_layer: VectorLayer = layer_snap.layer
            # 符号色块
            fill_color: QColor = QColor("#9ec5fe")
            stroke_color: QColor = QColor("#2f7de1")
            if vec_layer.symbology is not None:
                style = vec_layer.style
                if style is not None:
                    fill_color = QColor(style.fill_color) if style.fill_color != "transparent" else QColor(Qt.GlobalColor.transparent)
                    stroke_color = QColor(style.stroke_color)

            patch: QGraphicsRectItem = scene.addRect(
                QRectF(px, cur_y + (row_h - patch_h) / 2, patch_w, patch_h),
                QPen(stroke_color, 1.0),
                QBrush(fill_color),
            )
            patch.setZValue(20)
            items.append(patch)

            # 图层名
            layer_name: str = getattr(layer_snap.layer, "name", "图层")
            name_text: QGraphicsSimpleTextItem = scene.addSimpleText(
                layer_name, item_font,
            )
            name_text.setPos(px + patch_w + gap, cur_y)
            name_text.setBrush(QBrush(QColor("#374151")))
            name_text.setZValue(20)
            items.append(name_text)

            cur_y += row_h + gap

    # 更新元素高度
    element.height_mm = (cur_y - py) / dpi * 25.4

    return items


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
        circle: QGraphicsRectItem = scene.addEllipse(
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

    px: float = _mm_to_px(element.x_mm, dpi)
    py: float = _mm_to_px(element.y_mm, dpi)

    if element.alignment == "center":
        px -= text_item.boundingRect().width() / 2
    elif element.alignment == "right":
        px -= text_item.boundingRect().width()

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
) -> QPixmap:
    """将整个布局页面渲染到一个 QPixmap。

    参数:
        document: 布局文档（LayoutDocument），包含页面规格和元素列表。
        snapshot: 工作区快照（用于地图框渲染）。
        dpi: 输出分辨率；为 None 时使用页面自身的 dpi。

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

    for element in document.elements:
        if isinstance(element, MapFrameElement):
            if snapshot is not None:
                pixmap = render_map_frame(element, snapshot, out_dpi)
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
            render_scale_bar(element, scene, out_dpi)
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
