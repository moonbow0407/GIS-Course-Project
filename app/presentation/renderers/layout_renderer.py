"""布局元素渲染 —— 将布局领域模型的制图元素转换为 QGraphicsItem / QPixmap。

地图框渲染复用现有的 QtVectorRenderer / QtRasterRenderer，
在离屏 QGraphicsScene 中重建图层图元，再通过 scene.render() 输出到像素图。
"""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QGraphicsScene

from app.application.results import WorkspaceSnapshot
from app.domain.layout import MapFrameElement
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
