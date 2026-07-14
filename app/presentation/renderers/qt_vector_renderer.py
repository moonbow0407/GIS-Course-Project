"""将 Shapely 矢量几何转换为 Qt 图元。"""

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPathItem, QGraphicsScene
from shapely.geometry import (
    GeometryCollection,
    LinearRing,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

from app.application.results import LayerSnapshot
from app.domain.feature import Feature
from app.domain.layer_style import LayerStyle


class QtVectorRenderer:
    """负责矢量领域模型的 Qt 路径构造、样式转换和选择高亮。"""

    def render_layer(
        self,
        scene: QGraphicsScene,
        snapshot: LayerSnapshot,
        z_value: float,
    ) -> list[QGraphicsItem]:
        """将图层快照完整渲染到场景，并返回创建的图元。"""
        items: list[QGraphicsItem] = []
        feature: Feature
        for feature in snapshot.layer.features:
            if feature.geometry.is_empty:
                continue
            path: QPainterPath = QPainterPath()
            self._append_geometry(path, feature.geometry, snapshot.layer.style.point_size)
            if path.isEmpty():
                continue
            item: QGraphicsPathItem = QGraphicsPathItem(path)
            selected: bool = feature.fid in snapshot.selected_feature_ids
            self._apply_style(item, snapshot.layer.style, selected)
            item.setData(0, snapshot.layer_id)
            item.setData(1, feature.fid)
            item.setZValue(z_value)
            item.setVisible(snapshot.visible)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            scene.addItem(item)
            items.append(item)
        return items

    def _append_geometry(
        self,
        path: QPainterPath,
        geometry: BaseGeometry,
        point_size: float,
    ) -> None:
        """递归把受支持的 Shapely 几何追加到同一个绘制路径。"""
        if isinstance(geometry, Point):
            radius: float = max(point_size, 6.0) / 2.0
            path.addEllipse(QPointF(geometry.x, -geometry.y), radius, radius)
            return
        if isinstance(geometry, LineString):
            self._append_line(path, geometry)
            return
        if isinstance(geometry, Polygon):
            path.setFillRule(Qt.FillRule.OddEvenFill)
            self._append_line(path, geometry.exterior, close=True)
            interior_ring: LinearRing
            for interior_ring in geometry.interiors:
                self._append_line(path, interior_ring, close=True)
            return
        if isinstance(
            geometry,
            (MultiPoint, MultiLineString, MultiPolygon, GeometryCollection),
        ):
            member: BaseGeometry
            for member in geometry.geoms:
                self._append_geometry(path, member, point_size)

    @staticmethod
    def _append_line(
        path: QPainterPath,
        geometry: LineString | LinearRing,
        close: bool = False,
    ) -> None:
        """把线或环坐标追加到路径，并统一反转地图纵轴。"""
        coordinates: list[tuple[float, float]] = [
            (float(coordinate[0]), float(coordinate[1])) for coordinate in geometry.coords
        ]
        if not coordinates:
            return
        path.moveTo(coordinates[0][0], -coordinates[0][1])
        coordinate: tuple[float, float]
        for coordinate in coordinates[1:]:
            path.lineTo(coordinate[0], -coordinate[1])
        if close:
            path.closeSubpath()

    @staticmethod
    def _apply_style(item: QGraphicsPathItem, style: LayerStyle, selected: bool) -> None:
        """把领域样式转换为 Qt 画笔和画刷，并应用选择高亮。"""
        stroke_color: QColor = QColor("#e63946" if selected else style.stroke_color)
        line_width: float = style.line_width + (1.5 if selected else 0.0)
        pen: QPen = QPen(stroke_color, line_width)
        fill_color: QColor = QColor(style.fill_color)
        brush: QBrush = QBrush(fill_color if fill_color.isValid() else Qt.BrushStyle.NoBrush)
        item.setPen(pen)
        item.setBrush(brush)
        item.setOpacity(1.0 if selected else style.opacity)
