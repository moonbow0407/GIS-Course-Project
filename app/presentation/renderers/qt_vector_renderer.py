"""将 Shapely 矢量几何转换为 Qt 图元。"""

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPathItem, QGraphicsScene
from shapely import get_num_coordinates
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
from app.domain.vector_layer import VectorLayer


class QtVectorRenderer:
    """负责矢量领域模型的 Qt 路径构造、样式转换和选择高亮。"""

    def render_layer(
        self,
        scene: QGraphicsScene,
        snapshot: LayerSnapshot,
        z_value: float,
        map_units_per_pixel: float = 1.0,
    ) -> list[QGraphicsItem]:
        """将图层快照完整渲染到场景，并返回创建的图元。

        参数:
            scene: 接收图元的 Qt 地图场景。
            snapshot: 待绘制的矢量图层快照。
            z_value: 图层在场景中的堆叠顺序。
            map_units_per_pixel: 当前视图每个屏幕像素对应的地图单位。

        说明:
            图层级透明度和显示比例范围在渲染时统一应用到全部要素图元。
        """
        if not isinstance(snapshot.layer, VectorLayer):
            raise TypeError("矢量渲染器只能绘制矢量图层。")
        items: list[QGraphicsItem] = []
        feature: Feature
        for feature in snapshot.layer.features:
            if feature.geometry.is_empty:
                continue
            if snapshot.layer.symbology is None:
                continue
            style: LayerStyle | None = snapshot.layer.symbology.symbol_for(feature)
            if style is None:
                continue
            path: QPainterPath = QPainterPath()
            point_size: float = style.point_size * map_units_per_pixel
            selected: bool = feature.fid in snapshot.selected_feature_ids
            # 选中点要素时适度放大符号。
            if selected:
                point_size *= 1.6
            display_geometry: BaseGeometry = self._geometry_for_display(
                feature.geometry,
                map_units_per_pixel,
            )
            self._append_geometry(path, display_geometry, point_size)
            if path.isEmpty():
                continue
            # 选中要素先绘制光晕层（宽半透明描边），再绘制主体。
            if selected:
                halo: QGraphicsPathItem = QGraphicsPathItem(path)
                self._apply_halo(halo, feature.geometry.geom_type)
                halo.setData(0, snapshot.layer_id)
                halo.setData(1, feature.fid)
                halo.setZValue(z_value - 0.1)
                halo.setVisible(snapshot.visible)
                halo.setOpacity(snapshot.opacity)
                halo.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
                scene.addItem(halo)
                items.append(halo)
            item: QGraphicsPathItem = QGraphicsPathItem(path)
            self._apply_style(item, style, selected, feature.geometry.geom_type)
            # 图层级透明度乘以符号自身透明度，实现整体淡化而不破坏选择高亮。
            item.setOpacity(item.opacity() * snapshot.opacity)
            # 自定义数据把 Qt 图元关联回领域图层和要素。
            item.setData(0, snapshot.layer_id)
            item.setData(1, feature.fid)
            item.setZValue(z_value)
            item.setVisible(snapshot.visible)
            scene.addItem(item)
            items.append(item)
        return items

    @staticmethod
    def _geometry_for_display(
        geometry: BaseGeometry,
        map_units_per_pixel: float,
    ) -> BaseGeometry:
        """按当前屏幕分辨率简化显示路径，不改动领域层原始几何。

        参数:
            geometry: 查询、编辑仍需使用的完整几何。
            map_units_per_pixel: 当前视图一个屏幕像素对应的地图单位。

        返回:
            适合当前视图绘制的几何；小图层或低复杂度几何保持原样。
        """
        if map_units_per_pixel <= 0.0 or get_num_coordinates(geometry) < 64:
            return geometry
        tolerance: float = map_units_per_pixel * 0.5
        simplified: BaseGeometry = geometry.simplify(
            tolerance,
            preserve_topology=geometry.geom_type in {"Polygon", "MultiPolygon"},
        )
        return geometry if simplified.is_empty else simplified

    def _append_geometry(
        self,
        path: QPainterPath,
        geometry: BaseGeometry,
        point_size: float,
    ) -> None:
        """递归把受支持的 Shapely 几何追加到同一个绘制路径。"""
        if isinstance(geometry, Point):
            radius: float = max(point_size, 1e-9) / 2.0
            # Qt 场景纵轴向下，地图纵轴向上，因此显示时反转 Y 坐标。
            path.addEllipse(QPointF(geometry.x, -geometry.y), radius, radius)
            return
        if isinstance(geometry, LineString):
            self._append_line(path, geometry)
            return
        if isinstance(geometry, Polygon):
            # 奇偶填充规则让多边形内环显示为空洞。
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
    def _apply_halo(item: QGraphicsPathItem, geom_type: str) -> None:
        """为选中要素绘制半透明宽描边光晕，确保在任何底图上可见。

        参数:
            item: 光晕层 Qt 图元。
            geom_type: Shapely 几何类型名称。
        """
        # 光晕色：荧光青，自然界中几乎不出现的颜色。
        halo_color: QColor = QColor(0, 255, 255, 80)  # 半透明 cyan
        halo_width: float = 8.0
        halo_pen: QPen = QPen(halo_color, halo_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        halo_pen.setCosmetic(True)
        item.setPen(halo_pen)
        # 面要素光晕不填充，线/点同理。
        if geom_type in ("Polygon", "MultiPolygon"):
            item.setBrush(Qt.BrushStyle.NoBrush)
        else:
            item.setBrush(Qt.BrushStyle.NoBrush)

    @staticmethod
    def _apply_style(
        item: QGraphicsPathItem,
        style: LayerStyle,
        selected: bool,
        geom_type: str = "",
    ) -> None:
        """把领域样式转换为 Qt 画笔和画刷，选中要素按几何类型高亮。

        参数:
            item: 待应用样式的 Qt 图元。
            style: 领域图层样式配置。
            selected: 是否处于被选中状态。
            geom_type: Shapely 几何类型名称，用于区分面/线/点高亮策略。
        """
        if not selected:
            stroke_color: QColor = QColor(style.stroke_color)
            pen: QPen = QPen(stroke_color, style.line_width)
            pen.setCosmetic(True)
            fill_color: QColor = QColor(style.fill_color)
            brush: QBrush = QBrush(
                fill_color if fill_color.isValid() else Qt.BrushStyle.NoBrush
            )
            item.setPen(pen)
            item.setBrush(brush)
            item.setOpacity(style.opacity)
            return

        # ── 选中高亮策略 ──
        # 主体色：荧光青，与光晕同色系但完全不透明。
        highlight_color: QColor = QColor("#00E5FF")

        # 面要素：填充大幅加深，配醒目青绿色粗边界线。
        if geom_type in ("Polygon", "MultiPolygon"):
            original_fill: QColor = QColor(style.fill_color)
            if original_fill.isValid():
                # 降到原色的 25% 亮度，几乎黑，确保与周围未选面拉开差距。
                darkened: QColor = original_fill.darker(300)
                selected_brush: QBrush = QBrush(darkened)
            else:
                selected_brush = QBrush(Qt.BrushStyle.NoBrush)
            highlight_pen: QPen = QPen(highlight_color, style.line_width + 4.0)
            highlight_pen.setCosmetic(True)
            item.setPen(highlight_pen)
            item.setBrush(selected_brush)
            item.setOpacity(1.0)

        # 线要素：亮青粗线，配合光晕在密集线网中突出。
        elif geom_type in ("LineString", "MultiLineString", "LinearRing"):
            highlight_pen = QPen(highlight_color, style.line_width + 4.0)
            highlight_pen.setCosmetic(True)
            item.setPen(highlight_pen)
            item.setOpacity(1.0)

        # 点要素：亮青填充 + 深蓝描边，配合 2.5× 放大符号。
        else:
            highlight_pen = QPen(QColor("#0055AA"), style.line_width + 2.0)
            highlight_pen.setCosmetic(True)
            highlight_brush: QBrush = QBrush(highlight_color)
            item.setPen(highlight_pen)
            item.setBrush(highlight_brush)
            item.setOpacity(1.0)
