"""中间地图画布组件。"""

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
)


class MapCanvas(QGraphicsView):
    """基于 QGraphicsView 的模拟 GIS 地图画布。"""

    coordinate_changed = Signal(float, float)
    feature_selected = Signal(dict)
    features_selected = Signal(list)
    selection_count_changed = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self._tool = "pan"
        self._zoom_level = 0
        self._selectable_items: list[QGraphicsItem] = []
        self._selected_items: list[QGraphicsItem] = []
        self._rubber_band_item: QGraphicsRectItem | None = None
        self._drag_start: QPointF | None = None

        self._create_view()
        self._draw_mock_map()

    def set_map_tool(self, tool: str) -> None:
        """设置当前地图交互工具。"""
        self._tool = tool
        if tool == "pan":
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        elif tool == "box_select":
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def zoom_in(self) -> None:
        self._zoom_by(1.18)

    def zoom_out(self) -> None:
        self._zoom_by(1 / 1.18)

    def zoom_to_full_extent(self) -> None:
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom_level = 0

    def zoom_to_layer(self, layer_name: str) -> None:
        """当前阶段用全图显示模拟缩放到图层。"""
        self.zoom_to_full_extent()
        self.coordinate_changed.emit(118.78, 32.04)

    def _create_view(self) -> None:
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setMouseTracking(True)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QBrush(QColor("#eef3ea")))
        self._scene.setSceneRect(0, 0, 1000, 700)
        self.set_map_tool("pan")

    def _draw_mock_map(self) -> None:
        self._scene.clear()
        self._selectable_items.clear()
        self._draw_background()
        self._draw_regions()
        self._draw_rivers()
        self._draw_roads()
        self._draw_pois()
        self._draw_decorations()
        self.zoom_to_full_extent()

    def _draw_background(self) -> None:
        self._scene.addRect(self._scene.sceneRect(), QPen(QColor("#ccd7c8")), QBrush(QColor("#eef4e8")))

        grid_pen = QPen(QColor("#b7c2b2"))
        grid_pen.setStyle(Qt.PenStyle.DashLine)
        for x in range(100, 1000, 200):
            self._scene.addLine(x, 0, x, 700, grid_pen)
        for y in range(80, 700, 160):
            self._scene.addLine(0, y, 1000, y, grid_pen)

        label_font = QFont("Microsoft YaHei", 9)
        for x, label in [(100, "118.70"), (300, "118.75"), (500, "118.80"), (700, "118.85"), (900, "118.90")]:
            text = self._scene.addSimpleText(label, label_font)
            text.setPos(x - 28, -24)
        for y, label in [(70, "32.10"), (550, "31.90")]:
            text = self._scene.addSimpleText(label, label_font)
            text.setPos(-50, y - 8)

    def _draw_regions(self) -> None:
        regions = [
            ("江北区", [(70, 50), (310, 20), (420, 150), (330, 310), (90, 280)]),
            ("城区区", [(420, 40), (720, 30), (770, 210), (610, 320), (440, 240)]),
            ("城中区", [(210, 275), (470, 235), (530, 385), (350, 500), (150, 430)]),
            ("城南区", [(490, 345), (720, 320), (790, 520), (560, 620), (430, 480)]),
            ("西湖区", [(10, 300), (190, 260), (210, 460), (80, 600), (0, 520)]),
            ("江南区", [(745, 240), (1000, 210), (1000, 610), (790, 560), (710, 390)]),
            ("滨江区", [(230, 500), (470, 505), (560, 700), (190, 700)]),
        ]
        fill_colors = ["#e4efd8", "#edf4df", "#e8f2da", "#edf5e3", "#e3efda", "#eef5e7", "#eaf3dc"]
        pen = QPen(QColor("#99b77d"), 1.2)
        font = QFont("Microsoft YaHei", 13, QFont.Weight.Bold)
        for index, (name, points) in enumerate(regions):
            polygon = QPolygonF([QPointF(x, y) for x, y in points])
            item = QGraphicsPolygonItem(polygon)
            item.setPen(pen)
            item.setBrush(QBrush(QColor(fill_colors[index % len(fill_colors)])))
            item.setZValue(1)
            self._scene.addItem(item)

            center = polygon.boundingRect().center()
            label = self._scene.addSimpleText(name, font)
            label.setBrush(QBrush(QColor("#1f2a24")))
            label.setPos(center.x() - 34, center.y() - 12)
            label.setZValue(6)

    def _draw_rivers(self) -> None:
        river_pen = QPen(QColor("#86bce8"), 24, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        river_edge_pen = QPen(QColor("#5f9ccd"), 2)
        path = QPainterPath(QPointF(-20, 560))
        path.cubicTo(130, 525, 180, 480, 310, 450)
        path.cubicTo(450, 415, 520, 260, 650, 240)
        path.cubicTo(770, 220, 840, 160, 1020, 155)

        river = QGraphicsPathItem(path)
        river.setPen(river_pen)
        river.setZValue(2)
        self._scene.addItem(river)

        river_edge = QGraphicsPathItem(path)
        river_edge.setPen(river_edge_pen)
        river_edge.setZValue(2.1)
        self._scene.addItem(river_edge)

        for text, pos in [("长江", (705, 190)), ("秦淮河", (330, 485)), ("玄武湖", (350, 420))]:
            label = self._scene.addSimpleText(text, QFont("Microsoft YaHei", 9))
            label.setBrush(QBrush(QColor("#2f75a8")))
            label.setRotation(-15)
            label.setPos(*pos)
            label.setZValue(7)

    def _draw_roads(self) -> None:
        road_pen = QPen(QColor("#f59a2a"), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        roads = [
            [(40, 255), (210, 230), (360, 220), (520, 160), (620, -20)],
            [(650, 0), (575, 140), (545, 250), (510, 380), (505, 560), (520, 720)],
            [(180, 690), (260, 565), (360, 470), (520, 390), (720, 340), (1000, 330)],
            [(0, 660), (240, 560), (410, 610), (600, 575), (760, 520), (1000, 455)],
            [(720, 90), (835, 160), (940, 175), (1020, 210)],
        ]
        for coords in roads:
            self._scene.addItem(self._path_item(coords, road_pen, 4))

        selected_pen = QPen(QColor("#ffd736"), 18, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        selected_edge_pen = QPen(QColor("#ff7d20"), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        selected_coords = [(470, 170), (490, 250), (475, 325), (465, 420)]
        self._path_item(selected_coords, selected_pen, 8)
        selected_fg = self._path_item(selected_coords, selected_edge_pen, 9)
        feature = {
            "名称": "中山路",
            "类型": "主干道",
            "面积": "0 m²",
            "长度": "5,432.18 m",
            "FID": 1256,
            "起点": "118.7783, 31.9687",
            "终点": "118.7826, 32.0365",
            "备注": "无",
        }
        selected_fg.setData(0, feature)
        selected_fg.setData(1, "road")
        self._selectable_items.append(selected_fg)
        self._selected_items = [selected_fg]

        callout = self._scene.addSimpleText("道路（主干道）", QFont("Microsoft YaHei", 10))
        callout.setBrush(QBrush(QColor("#202020")))
        callout.setPos(510, 300)
        callout.setZValue(20)

    def _draw_pois(self) -> None:
        pois = [
            ("市政府", 445, 95),
            ("市博物馆", 270, 335),
            ("体育中心", 690, 335),
            ("火车站", 475, 480),
            ("大学城", 130, 535),
        ]
        for name, x, y in pois:
            item = QGraphicsEllipseItem(x - 6, y - 6, 12, 12)
            item.setPen(QPen(QColor("#4f339d"), 1.2))
            item.setBrush(QBrush(QColor("#7253c4")))
            item.setZValue(10)
            item.setData(0, {
                "名称": name,
                "类型": "POI点",
                "面积": "0 m²",
                "长度": "0 m",
                "FID": int(x + y),
                "起点": f"{118.70 + x / 5000:.4f}, {31.90 + (700 - y) / 3500:.4f}",
                "终点": "同起点",
                "备注": "模拟兴趣点",
            })
            item.setData(1, "poi")
            self._scene.addItem(item)
            self._selectable_items.append(item)

            label = self._scene.addSimpleText(name, QFont("Microsoft YaHei", 9))
            label.setPos(x + 10, y - 12)
            label.setZValue(10)

    def _draw_decorations(self) -> None:
        self._draw_scale_bar()
        self._draw_north_arrow()

    def _draw_scale_bar(self) -> None:
        x, y = 30, 650
        segment_width = 55
        for i in range(4):
            brush = QBrush(QColor("#111111") if i % 2 == 0 else QColor("#ffffff"))
            self._scene.addRect(x + i * segment_width, y, segment_width, 10, QPen(QColor("#111111")), brush).setZValue(30)
        for i, text in enumerate(["0", "250", "500", "750", "1000 m"]):
            label = self._scene.addSimpleText(text, QFont("Microsoft YaHei", 8))
            label.setPos(x + i * segment_width - 5, y - 22)
            label.setZValue(31)

    def _draw_north_arrow(self) -> None:
        arrow = QPolygonF([QPointF(950, 38), QPointF(930, 120), QPointF(950, 98), QPointF(970, 120)])
        item = QGraphicsPolygonItem(arrow)
        item.setPen(QPen(QColor("#111111"), 2))
        item.setBrush(QBrush(QColor("#f7f7f7")))
        item.setZValue(30)
        self._scene.addItem(item)
        label = self._scene.addSimpleText("N", QFont("Arial", 15, QFont.Weight.Bold))
        label.setPos(943, 10)
        label.setZValue(31)

    def _path_item(self, coords: list[tuple[float, float]], pen: QPen, z_value: float) -> QGraphicsPathItem:
        path = QPainterPath(QPointF(*coords[0]))
        for x, y in coords[1:]:
            path.lineTo(x, y)
        item = QGraphicsPathItem(path)
        item.setPen(pen)
        item.setZValue(z_value)
        self._scene.addItem(item)
        return item

    def wheelEvent(self, event) -> None:
        factor = 1.18 if event.angleDelta().y() > 0 else 1 / 1.18
        self._zoom_by(factor)

    def mouseMoveEvent(self, event) -> None:
        scene_pos = self.mapToScene(event.position().toPoint())
        longitude, latitude = self._to_mock_coordinate(scene_pos)
        self.coordinate_changed.emit(longitude, latitude)

        if self._tool == "box_select" and self._rubber_band_item is not None and self._drag_start is not None:
            rect = QRectF(self._drag_start, scene_pos).normalized()
            self._rubber_band_item.setRect(rect)
            return
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._tool == "box_select":
            self._drag_start = self.mapToScene(event.position().toPoint())
            self._rubber_band_item = QGraphicsRectItem(QRectF(self._drag_start, self._drag_start))
            self._rubber_band_item.setPen(QPen(QColor("#2f7de1"), 1.5, Qt.PenStyle.DashLine))
            self._rubber_band_item.setBrush(QBrush(QColor(47, 125, 225, 45)))
            self._rubber_band_item.setZValue(100)
            self._scene.addItem(self._rubber_band_item)
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._tool == "point_select":
            self._select_at(event.position().toPoint())
            return
        if event.button() == Qt.MouseButton.LeftButton and self._tool == "box_select":
            self._finish_box_select()
            return
        super().mouseReleaseEvent(event)

    def _zoom_by(self, factor: float) -> None:
        next_level = self._zoom_level + (1 if factor > 1 else -1)
        if next_level < -8 or next_level > 14:
            return
        self.scale(factor, factor)
        self._zoom_level = next_level

    def _to_mock_coordinate(self, pos: QPointF) -> tuple[float, float]:
        longitude = 118.68 + pos.x() / 4000
        latitude = 31.86 + (700 - pos.y()) / 3000
        return longitude, latitude

    def _select_at(self, viewport_pos) -> None:
        scene_pos = self.mapToScene(viewport_pos)
        hit_items = self._scene.items(scene_pos)
        for item in hit_items:
            if item in self._selectable_items:
                self._set_selected_items([item])
                feature = item.data(0)
                if isinstance(feature, dict):
                    self.feature_selected.emit(feature)
                return
        self._set_selected_items([])

    def _finish_box_select(self) -> None:
        if self._rubber_band_item is None:
            return
        rect = self._rubber_band_item.rect()
        self._scene.removeItem(self._rubber_band_item)
        self._rubber_band_item = None
        self._drag_start = None

        selected = [item for item in self._selectable_items if rect.intersects(item.sceneBoundingRect())]
        self._set_selected_items(selected)
        features = [item.data(0) for item in selected if isinstance(item.data(0), dict)]
        self.features_selected.emit(features)

    def _set_selected_items(self, items: list[QGraphicsItem]) -> None:
        for item in self._selected_items:
            self._restore_item_style(item)
        self._selected_items = items
        for item in self._selected_items:
            self._apply_highlight_style(item)
        self.selection_count_changed.emit(len(items))
        if not items:
            self.feature_selected.emit({})

    def _apply_highlight_style(self, item: QGraphicsItem) -> None:
        item_type = item.data(1)
        if item_type == "poi" and isinstance(item, QGraphicsEllipseItem):
            item.setPen(QPen(QColor("#ff7d20"), 3))
            item.setBrush(QBrush(QColor("#ffd736")))
        elif item_type == "road" and isinstance(item, QGraphicsPathItem):
            item.setPen(QPen(QColor("#ff3b30"), 7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))

    def _restore_item_style(self, item: QGraphicsItem) -> None:
        item_type = item.data(1)
        if item_type == "poi" and isinstance(item, QGraphicsEllipseItem):
            item.setPen(QPen(QColor("#4f339d"), 1.2))
            item.setBrush(QBrush(QColor("#7253c4")))
        elif item_type == "road" and isinstance(item, QGraphicsPathItem):
            item.setPen(QPen(QColor("#ff7d20"), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
