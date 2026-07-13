"""中间地图画布组件。"""

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
)


class MapCanvas(QGraphicsView):
    """基于 QGraphicsView 的空白 GIS 工作画布。"""

    coordinate_changed = Signal(float, float)
    feature_selected = Signal(dict)
    features_selected = Signal(list)
    selection_count_changed = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("mapCanvas")
        self._scene = QGraphicsScene(self)
        self._tool = "pan"
        self._zoom_level = 0
        self._selectable_items: list[QGraphicsItem] = []
        self._selected_items: list[QGraphicsItem] = []
        self._rubber_band_item: QGraphicsRectItem | None = None
        self._drag_start: QPointF | None = None

        self._create_view()
        self._initialize_empty_workspace()

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
        """放大当前视图。"""
        self._zoom_by(1.18)

    def zoom_out(self) -> None:
        """缩小当前视图。"""
        self._zoom_by(1 / 1.18)

    def zoom_to_full_extent(self) -> None:
        """显示当前工作区全范围。"""
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom_level = 0

    def zoom_to_layer(self, layer_name: str) -> None:
        """当前阶段以工作区全图显示模拟缩放到图层。"""
        del layer_name
        self.zoom_to_full_extent()
        self.coordinate_changed.emit(118.78, 32.04)

    def _create_view(self) -> None:
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setMouseTracking(True)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self._scene.setSceneRect(0, 0, 1000, 700)
        self.set_map_tool("pan")

    def _initialize_empty_workspace(self) -> None:
        """清空场景，以无底图的工作区作为初始状态。"""
        self._scene.clear()
        self._selectable_items.clear()
        self._selected_items.clear()
        self.setBackgroundBrush(QBrush(QColor("#ffffff")))
        self.zoom_to_full_extent()

    def wheelEvent(self, event) -> None:
        factor = 1.18 if event.angleDelta().y() > 0 else 1 / 1.18
        self._zoom_by(factor)

    def mouseMoveEvent(self, event) -> None:
        scene_pos = self.mapToScene(event.position().toPoint())
        longitude, latitude = self._to_display_coordinate(scene_pos)
        self.coordinate_changed.emit(longitude, latitude)

        if self._tool == "box_select" and self._rubber_band_item is not None and self._drag_start is not None:
            self._rubber_band_item.setRect(QRectF(self._drag_start, scene_pos).normalized())
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
        if -8 <= next_level <= 14:
            self.scale(factor, factor)
            self._zoom_level = next_level

    def _to_display_coordinate(self, pos: QPointF) -> tuple[float, float]:
        """为状态栏暂存的坐标显示换算，后续可由真实坐标系替换。"""
        longitude = 118.68 + pos.x() / 4000
        latitude = 31.86 + (700 - pos.y()) / 3000
        return longitude, latitude

    def _select_at(self, viewport_pos) -> None:
        scene_pos = self.mapToScene(viewport_pos)
        for item in self._scene.items(scene_pos):
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
        self.features_selected.emit([item.data(0) for item in selected if isinstance(item.data(0), dict)])

    def _set_selected_items(self, items: list[QGraphicsItem]) -> None:
        self._selected_items = items
        self.selection_count_changed.emit(len(items))
        if not items:
            self.feature_selected.emit({})
