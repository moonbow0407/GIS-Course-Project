"""基于领域图层快照的地图画布。"""

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
    QRubberBand,
    QVBoxLayout,
)
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

from app.application.project_models import MapViewState
from app.application.results import LayerSnapshot, WorkspaceSnapshot
from app.domain.raster_layer import RasterLayer
from app.domain.vector_layer import Bounds, VectorLayer
from app.presentation.renderers.qt_raster_renderer import QtRasterRenderer
from app.presentation.renderers.qt_vector_renderer import QtVectorRenderer


class MapCanvas(QGraphicsView):
    """显示工作区快照并保留基础地图导航能力。"""

    # 坐标变化信号：携带可直接写入状态栏的格式化地图坐标文本。
    coordinate_changed = Signal(str)
    # 视图比例信号：携带相对于全图视图的近似缩放百分比文本。
    view_scale_changed = Signal(str)
    # 画布点击信号：单击地图任意位置时发出，供外部取消图层选中。
    canvas_clicked = Signal()
    # 点选查询信号：(地图坐标点, 是否追加到已有选择)。
    point_queried = Signal(Point, bool)
    # 框选查询信号：(地图坐标矩形, 是否追加到已有选择)。
    rectangle_queried = Signal(Polygon, bool)
    # 数字化完成信号：携带用户绘制的 Shapely 几何（Point/LineString/Polygon）。
    feature_digitized = Signal(BaseGeometry)
    # 顶点编辑提交信号：携带修改后的 Shapely 几何。
    geometry_edited = Signal(BaseGeometry)
    # 地图工具切换信号：供主窗口同步功能区按钮的持续高亮状态。
    tool_changed = Signal(str)

    def __init__(self, parent: QGraphicsView | None = None) -> None:
        """创建空地图场景和矢量、栅格渲染器。

        参数:
            parent: 父视图控件；为空时由主窗口工作区接管所有权。

        状态变化:
            初始化空场景和操作引导，但不创建任何地图数据图元。
        """
        super().__init__(parent)
        # 地图场景：保存当前工作区快照对应的全部 Qt 图元。
        self._scene: QGraphicsScene = QGraphicsScene(self)
        # 矢量渲染器：负责将 Shapely 几何转换为地图图元。
        self._vector_renderer: QtVectorRenderer = QtVectorRenderer()
        # 栅格渲染器：负责将 RGBA 像素按地理变换放入地图场景。
        self._raster_renderer: QtRasterRenderer = QtRasterRenderer()
        # 空状态面板：未加载真实数据时提供操作引导，不属于地图场景数据。
        self._empty_overlay: QFrame = self._create_empty_overlay()
        # 缩放百分比：以最近一次全图显示为 100% 的界面导航指标。
        self._zoom_percent: float = 100.0
        # 真实地图范围与可导航场景范围分开保存，避免小图层没有平移余量。
        self._map_scene_rect: QRectF | None = None
        # 中键平移状态：记录鼠标中键是否正在执行拖拽平移。
        self._pan_mode: str = "none"
        # 中键平移上一帧位置：用于计算帧间位移并驱动视图滚动。
        self._last_middle_pos: QPoint | None = None
        # 框选放大激活标记：为 True 时左键拖拽绘制橡皮筋矩形而非平移。
        self._zoom_rect_active: bool = False
        # 框选起点：橡皮筋矩形起始位置的视口像素坐标。
        self._zoom_origin: QPoint = QPoint()
        # 橡皮筋矩形：框选放大/框选查询时跟随鼠标绘制的临时可视化控件。
        self._rubber_band: QRubberBand | None = None
        # 点选查询激活标记：为 True 时左键单击执行空间点选并切换回平移工具。
        self._point_query_active: bool = False
        # 框选查询激活标记：为 True 时左键拖拽矩形执行空间查询并切换回平移工具。
        self._rectangle_query_active: bool = False
        # 每屏幕像素对应的地图单位：由 set_snapshot 刷新，供容差计算使用。
        self._map_units_per_pixel: float = 1.0
        # 数字化模式："none"/"point"/"line"/"polygon"。
        self._digitize_mode: str = "none"
        # 数字化顶点栈：屏幕像素坐标列表。
        self._digitize_vertices: list[QPoint] = []
        # 数字化预览图元：临时场景项，完成或取消后一并移除。
        self._sketch_items: list[QGraphicsItem] = []
        # 捕捉状态。
        self._snapping_enabled: bool = False
        self._snap_coords: list[tuple[float, float]] = []
        self._snap_layer_ids: tuple[str, ...] = ()
        self._snap_marker: QGraphicsPathItem | None = None
        # 缓存最近一次快照，供缩放后重新渲染使用。
        self._last_snapshot: WorkspaceSnapshot | None = None
        # 图层图元缓存：按图层编号保存该图层全部 Qt 图元，供显示比例过滤使用。
        self._layer_items: dict[str, list[QGraphicsItem]] = {}
        # 顶点编辑状态。
        self._vertex_edit_active: bool = False
        self._edit_geometry: BaseGeometry | None = None
        self._vertex_coords: list[tuple[float, float]] = []
        self._midpoint_coords: list[tuple[float, float]] = []
        self._vertex_drag_idx: int = -1
        self._hovered_vertex: int = -1
        self._edit_mode: str = "drag_vertex"
        self._vertex_items: list[QGraphicsItem] = []
        # 多选顶点：Ctrl+点击切换，Ctrl+A 全选，拖拽时全部一起移动。
        self._selected_vertex_indices: set[int] = set()
        # 选中要素集合：{(layer_id, fid), ...}。
        self._selected_fids: set[tuple[str, object]] = set()
        self._scene.setSceneRect(0, 0, 1000, 700)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(QBrush(QColor("#f3f6fa")))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setMouseTracking(True)
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    @property
    def has_map_data(self) -> bool:
        """返回画布当前是否已经建立真实地图范围。"""
        return self._map_scene_rect is not None

    def queryable_layer_ids(self) -> tuple[str, ...]:
        """返回当前视图中可见且未超出比例范围的图层编号。"""
        if self._last_snapshot is None:
            return ()
        return self._queryable_layer_ids(self._last_snapshot)

    @property
    def map_units_per_pixel(self) -> float:
        """返回当前视图每个屏幕像素对应的地图单位，用于容差计算。"""
        return self._map_units_per_pixel

    def set_snapshot(self, snapshot: WorkspaceSnapshot) -> None:
        """原子替换场景中的图层图元并适配真实数据范围。

        参数:
            snapshot: 包含真实矢量、栅格图层及显隐状态的工作区快照。

        状态变化:
            清空旧图元并重绘快照；空快照只显示操作引导。
            Qt 视图变换在 scene.clear / setSceneRect 期间保持不变，
            无需额外恢复，避免了反复 fitInView 导致的持续缩放漂移。
        """
        is_first_load: bool = self._map_scene_rect is None

        self._scene.clear()
        # scene.clear() 会销毁全部图元，先清空按图层保存的引用，避免空快照或
        # 失败回滚时缩放状态仍访问已经删除的 QGraphicsItem。
        self._layer_items.clear()
        # scene.clear() 会立即销毁全部 C++ 图元；同步清空 Python 侧临时图元引用，
        # 否则几何编辑提交后的工具切换会再次 removeItem 并中断查询工具激活。
        self._vertex_items.clear()
        self._sketch_items.clear()
        self._snap_marker = None
        self._empty_overlay.setVisible(not snapshot.layers)
        if not snapshot.layers:
            self._last_snapshot = snapshot
            self._selected_fids.clear()
            self._snap_coords.clear()
            self._snap_layer_ids = ()
            self._map_scene_rect = None
            self._scene.setSceneRect(0, 0, 1000, 700)
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
            self._reset_view_scale()
            return
        layer_snapshot: tuple[LayerSnapshot, ...] = snapshot.layers
        # 隐藏图层仍需创建图元以便恢复显示，但不能继续撑大当前全图范围。
        visible_layers: tuple[LayerSnapshot, ...] = tuple(
            layer for layer in layer_snapshot if layer.visible
        )
        extent_layers: tuple[LayerSnapshot, ...] = visible_layers or layer_snapshot
        minimum_x: float = min(layer.bounds[0] for layer in extent_layers)
        minimum_y: float = min(layer.bounds[1] for layer in extent_layers)
        maximum_x: float = max(layer.bounds[2] for layer in extent_layers)
        maximum_y: float = max(layer.bounds[3] for layer in extent_layers)
        map_scene_rect: QRectF = self._scene_rect_from_bounds(
            (minimum_x, minimum_y, maximum_x, maximum_y)
        )
        self._map_scene_rect = map_scene_rect
        self._scene.setSceneRect(map_scene_rect)
        if is_first_load:
            # 点符号使用屏幕像素定义尺寸；首次加载必须先适配真实数据范围，
            # 否则这里仍沿用空画布的 1000×700 场景变换，导致小范围经纬度点被巨幅放大。
            self.fitInView(map_scene_rect, Qt.AspectRatioMode.KeepAspectRatio)
            self._reset_view_scale()
        viewport_width: int = max(self.viewport().width(), 1)
        viewport_height: int = max(self.viewport().height(), 1)
        # 将屏幕像素尺寸换算为地图单位，使点符号保持稳定的视觉大小。
        visible_rect: QRectF = self._visible_scene_rect()
        map_units_per_pixel: float = max(
            visible_rect.width() / viewport_width,
            visible_rect.height() / viewport_height,
        )
        self._map_units_per_pixel = map_units_per_pixel
        # 快照按底到顶排列，枚举值可直接作为 Qt 图元的叠放顺序。
        for z_value, current_layer in enumerate(layer_snapshot):
            if isinstance(current_layer.layer, RasterLayer):
                raster_item = self._raster_renderer.render_layer(
                    self._scene, current_layer, float(z_value)
                )
                self._layer_items[current_layer.layer_id] = [raster_item]
            else:
                vector_items = self._vector_renderer.render_layer(
                    self._scene,
                    current_layer,
                    float(z_value),
                    map_units_per_pixel,
                )
                self._layer_items[current_layer.layer_id] = vector_items
        self._ensure_pan_area()
        self._build_snap_index(snapshot)
        self._last_snapshot = snapshot
        # 更新选中要素集合。
        self._selected_fids.clear()
        for layer in snapshot.layers:
            for fid in layer.selected_feature_ids:
                self._selected_fids.add((layer.layer_id, fid))
        # 按当前视图比例应用图层的显示比例范围。
        self._apply_scale_ranges()

    def capture_view_state(self) -> MapViewState:
        """捕获当前地图中心和相对于全图的缩放比例。"""
        center: QPointF = self.mapToScene(self.viewport().rect().center())
        # 场景 Y 轴为屏幕向下，工程中的地图坐标仍使用向上为正的约定。
        return MapViewState(
            center_x=center.x(),
            center_y=-center.y(),
            zoom_percent=self._zoom_percent,
        )

    def restore_view_state(self, view_state: MapViewState) -> None:
        """在当前图层场景上恢复工程保存的地图中心和缩放比例。"""
        if self._map_scene_rect is None:
            return
        self.fitInView(self._map_scene_rect, Qt.AspectRatioMode.KeepAspectRatio)
        scale_factor: float = view_state.zoom_percent / 100.0
        if scale_factor != 1.0:
            self.scale(scale_factor, scale_factor)
        self._zoom_percent = view_state.zoom_percent
        self._ensure_pan_area()
        self.centerOn(QPointF(view_state.center_x, -view_state.center_y))
        self._emit_view_scale()

    def set_pan_tool(self) -> None:
        """切换到地图平移工具，同时关闭所有特殊工具模式。"""
        self._deactivate_all_tools()
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self._update_cursor()
        self.tool_changed.emit("pan")

    def set_zoom_rect_tool(self) -> None:
        """切换到框选放大模式。

        状态变化:
            关闭 ScrollHandDrag，激活框选标记并将光标改为十字准星。
        """
        self._deactivate_all_tools()
        self._zoom_rect_active = True
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.tool_changed.emit("zoom_rect")

    def set_point_query_tool(self) -> None:
        """切换到点选查询模式。

        状态变化:
            关闭其他工具模式，激活点选标记，切换十字光标。
        """
        self._deactivate_all_tools()
        self._point_query_active = True
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.tool_changed.emit("point_query")

    def set_rectangle_query_tool(self) -> None:
        """切换到框选查询模式。

        状态变化:
            关闭其他工具模式，激活框选查询标记，切换十字光标。
        """
        self._deactivate_all_tools()
        self._rectangle_query_active = True
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.tool_changed.emit("rectangle_query")

    def set_digitize_point_tool(self) -> None:
        """切换到点要素数字化模式。"""
        self._deactivate_all_tools()
        self._digitize_mode = "point"
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.tool_changed.emit("digitize_point")

    def set_digitize_line_tool(self) -> None:
        """切换到线要素数字化模式。"""
        self._deactivate_all_tools()
        self._digitize_mode = "line"
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.tool_changed.emit("digitize_line")

    def set_digitize_polygon_tool(self) -> None:
        """切换到面要素数字化模式。"""
        self._deactivate_all_tools()
        self._digitize_mode = "polygon"
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.tool_changed.emit("digitize_polygon")

    def set_vertex_edit_tool(
        self, geometry: BaseGeometry, layer_id: str = "", fid: object = None
    ) -> None:
        """进入顶点编辑模式，为指定几何显示可交互顶点标记。

        参数:
            geometry: 待编辑的 Shapely 几何对象。
            layer_id: 要素所属图层 ID（用于实时更新渲染图元）。
            fid: 要素编号。
        """
        self._deactivate_all_tools()
        self._vertex_edit_active = True
        self._edit_geometry = geometry
        self._edit_layer_id: str = layer_id
        self._edit_fid: object = fid
        self._vertex_drag_idx = -1
        self._hovered_vertex = -1
        self._edit_mode = "drag_vertex"
        self._vertex_coords.clear()
        # 彻底清理上一次编辑留在场景中的所有标记。
        for item in self._vertex_items:
            try:
                self._scene.removeItem(item)
            except RuntimeError:
                pass
        self._vertex_items.clear()
        # 重新计算当前的地图单位，确保标记尺寸适配当前缩放级别。
        viewport_w: int = max(self.viewport().width(), 1)
        viewport_h: int = max(self.viewport().height(), 1)
        visible: QRectF = self._visible_scene_rect()
        if visible.width() > 0:
            self._map_units_per_pixel = max(
                visible.width() / viewport_w,
                visible.height() / viewport_h,
            )
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._rebuild_vertex_markers(geometry)
        self.tool_changed.emit("vertex_edit")

    def _rebuild_vertex_markers(self, geometry: BaseGeometry) -> None:
        """原地更新顶点标记位置，不删建以避免视觉闪烁。

        仅在首次调用时从几何提取坐标；后续调用使用已有 _vertex_coords
        （可能已被拖拽修改）。
        """
        if not self._vertex_coords:
            self._vertex_coords = self._extract_editable_coords(geometry)
        coords: list[tuple[float, float]] = self._vertex_coords
        marker_size: float = max(self._map_units_per_pixel * 7.0, 1e-6)

        # 移除旧的延伸标记和悬停高亮（顶点标记本身保留并更新）。
        items_to_keep: list[QGraphicsItem] = []
        for item in self._vertex_items:
            tag = item.data(0)
            if tag in ("vertex", "preview"):
                items_to_keep.append(item)
            else:
                self._scene.removeItem(item)
        self._vertex_items.clear()

        # 原地更新或创建顶点标记。选中顶点用金色(#FFD700)，未选中用品红。
        selected_set: set[int] = self._selected_vertex_indices
        for i, (mx, my) in enumerate(coords):
            color: str = "#FFD700" if i in selected_set else "#FF1493"
            if i < len(items_to_keep):
                item = items_to_keep[i]
                if isinstance(item, QGraphicsEllipseItem):
                    item.prepareGeometryChange()
                    item.setRect(
                        mx - marker_size, -my - marker_size,
                        marker_size * 2, marker_size * 2,
                    )
                    item.setPen(QPen(QColor(color), 2))
                    item.setBrush(QBrush(QColor(color)))
                    item.update()
            else:
                item = self._make_marker(mx, my, color, color, marker_size)
                item.setData(0, "vertex")
                item.setData(1, i)
                item.setZValue(3000)
                self._scene.addItem(item)
            item.setData(1, i)
            self._vertex_items.append(item)

        # 更新预览连线。
        for item in self._vertex_items:
            if item.data(0) == "preview" and isinstance(item, QGraphicsPathItem):
                item.prepareGeometryChange()
                item.setPath(self._build_preview_path())
                item.update()
                break

        # 移除不再需要的多余标记（顶点减少了）。
        for extra in items_to_keep[len(coords):]:
            self._scene.removeItem(extra)

        # 端点延伸标记（仅线要素）。
        if geometry.geom_type in ("LineString", "MultiLineString") and len(coords) >= 2:
            ext_size: float = marker_size * 1.3
            for tag, idx, target in [
                ("extend_start", 0, 1), ("extend_end", -1, -2)
            ]:
                dx = coords[idx][0] - coords[target][0]
                dy = coords[idx][1] - coords[target][1]
                d = (dx**2 + dy**2)**0.5 or 1.0
                ex = coords[idx][0] + dx / d * marker_size * 3
                ey = coords[idx][1] + dy / d * marker_size * 3
                ext_item = self._make_marker(
                    ex, ey, "#00FF88", "#00FF88", ext_size
                )
                ext_item.setData(0, tag)
                ext_item.setZValue(3000)
                self._scene.addItem(ext_item)
                self._vertex_items.append(ext_item)

        # 预览连线：品红虚线连接所有顶点。
        if len(coords) >= 2:
            preview_path: QPainterPath = self._build_preview_path()
            if not preview_path.isEmpty():
                preview_item: QGraphicsPathItem = QGraphicsPathItem(preview_path)
                preview_item.setData(0, "preview")
                prev_pen2: QPen = QPen(QColor("#FF1493"), 1.5, Qt.PenStyle.DashLine)
                prev_pen2.setCosmetic(True)
                preview_item.setPen(prev_pen2)
                preview_item.setBrush(Qt.BrushStyle.NoBrush)
                preview_item.setZValue(2998)
                self._scene.addItem(preview_item)
                self._vertex_items.append(preview_item)

        # 悬停高亮：绿色虚线框。
        if self._hovered_vertex >= 0 and self._hovered_vertex < len(coords):
            hx, hy = coords[self._hovered_vertex]
            hover_size: float = marker_size * 1.15
            hover_item: QGraphicsEllipseItem = QGraphicsEllipseItem(
                hx - hover_size, -hy - hover_size,
                hover_size * 2, hover_size * 2,
            )
            hover_pen: QPen = QPen(QColor("#00FF88"), 2, Qt.PenStyle.DashLine)
            hover_pen.setCosmetic(True)
            hover_item.setData(0, "hover")
            hover_item.setPen(hover_pen)
            hover_item.setBrush(Qt.BrushStyle.NoBrush)
            hover_item.setZValue(2999)
            self._scene.addItem(hover_item)
            self._vertex_items.append(hover_item)

        # 确保场景立即刷新。
        self._scene.update()

    def _extract_editable_coords(
        self, geometry: BaseGeometry
    ) -> list[tuple[float, float]]:
        """从几何中提取可编辑的顶点坐标列表。

        对 Multi 几何取其首个部件的坐标。
        """
        geom_type: str = geometry.geom_type
        # 单部件几何。
        if geom_type == "Point":
            return [(geometry.x, geometry.y)]
        if geom_type == "LineString":
            return [(c[0], c[1]) for c in geometry.coords]
        if geom_type == "Polygon":
            return [(c[0], c[1]) for c in geometry.exterior.coords[:-1]]
        # Multi 几何：递归提取首个部件。
        if geom_type in (
            "MultiPoint", "MultiLineString", "MultiPolygon",
            "GeometryCollection",
        ):
            if hasattr(geometry, "geoms") and geometry.geoms:
                return self._extract_editable_coords(geometry.geoms[0])
        return []

    @staticmethod
    def _is_polygon_type(geom_type: str) -> bool:
        """判断几何类型是否属于面类（需要闭合环）。"""
        return geom_type in ("Polygon", "MultiPolygon")

    def _compute_midpoints(
        self,
        coords: list[tuple[float, float]],
        geometry: BaseGeometry,
    ) -> list[tuple[float, float]]:
        """计算各边中点坐标，用于插入顶点。"""
        geom_type: str = geometry.geom_type
        if geom_type in ("Point", "MultiPoint"):
            return []
        midpoints: list[tuple[float, float]] = []
        n: int = len(coords)
        # 面要素需闭合回路的边中点；线要素无需闭合。
        pairs: int = n if self._is_polygon_type(geom_type) else n - 1
        for i in range(pairs):
            j: int = (i + 1) % n
            midpoints.append(
                ((coords[i][0] + coords[j][0]) / 2.0,
                 (coords[i][1] + coords[j][1]) / 2.0)
            )
        return midpoints

    @staticmethod
    def _make_marker(
        x: float, y: float, stroke: str, fill: str, size: float
    ) -> QGraphicsEllipseItem:
        """创建圆形顶点标记。"""
        item: QGraphicsEllipseItem = QGraphicsEllipseItem(
            x - size, -y - size, size * 2, size * 2
        )
        pen: QPen = QPen(QColor(stroke), 2)
        pen.setCosmetic(True)
        item.setPen(pen)
        item.setBrush(QBrush(QColor(fill)))
        return item

    def _hit_test_vertex(self, point: Point, tolerance: float) -> int:
        """返回容差内最近顶点的索引；无命中返回 -1。"""
        best: int = -1
        best_dist: float = tolerance
        for i, (vx, vy) in enumerate(self._vertex_coords):
            d: float = ((point.x - vx) ** 2 + (point.y - vy) ** 2) ** 0.5
            if d < best_dist:
                best_dist = d
                best = i
        return best

    def _hit_test_preview_path(self, point: Point, tolerance: float) -> int:
        """检测点击是否靠近预览连线，返回最近顶点的索引；无命中返回 -1。"""
        coords = self._vertex_coords
        n = len(coords)
        if n < 2:
            return -1
        min_dist: float = tolerance
        best_idx: int = -1
        for i in range(n - 1):
            d = self._point_to_segment_dist(
                point.x, point.y,
                coords[i][0], coords[i][1],
                coords[i + 1][0], coords[i + 1][1],
            )
            if d < min_dist:
                min_dist = d
                d1 = ((point.x - coords[i][0]) ** 2 + (point.y - coords[i][1]) ** 2) ** 0.5
                d2 = ((point.x - coords[i + 1][0]) ** 2 + (point.y - coords[i + 1][1]) ** 2) ** 0.5
                best_idx = i if d1 <= d2 else i + 1
        return best_idx

    @staticmethod
    def _point_to_segment_dist(px, py, ax, ay, bx, by) -> float:
        """计算点到线段 AB 的最短距离。"""
        dx, dy = bx - ax, by - ay
        seg_len_sq = dx * dx + dy * dy
        if seg_len_sq < 1e-20:
            return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))
        proj_x, proj_y = ax + t * dx, ay + t * dy
        return ((px - proj_x) ** 2 + (py - proj_y) ** 2) ** 0.5

    def _hit_test_extension(self, point: Point, tolerance: float) -> str | None:
        """检测是否点击了线端点延伸标记。返回 'extend_start'/'extend_end'/None。"""
        coords: list[tuple[float, float]] = self._vertex_coords
        if len(coords) < 2:
            return None
        marker_dist: float = self._map_units_per_pixel * 24.0
        # 首端延伸标记位置。
        dx0: float = coords[0][0] - coords[1][0]
        dy0: float = coords[0][1] - coords[1][1]
        d0: float = (dx0**2 + dy0**2)**0.5 or 1.0
        ex0: float = coords[0][0] + dx0 / d0 * marker_dist
        ey0: float = coords[0][1] + dy0 / d0 * marker_dist
        if ((point.x - ex0)**2 + (point.y - ey0)**2)**0.5 < tolerance:
            return "extend_start"
        # 尾端延伸标记位置。
        dx1: float = coords[-1][0] - coords[-2][0]
        dy1: float = coords[-1][1] - coords[-2][1]
        d1: float = (dx1**2 + dy1**2)**0.5 or 1.0
        ex1: float = coords[-1][0] + dx1 / d1 * marker_dist
        ey1: float = coords[-1][1] + dy1 / d1 * marker_dist
        if ((point.x - ex1)**2 + (point.y - ey1)**2)**0.5 < tolerance:
            return "extend_end"
        return None

    def _hit_test_midpoint(self, point: Point, tolerance: float) -> int:
        """返回容差内最近中点的索引；无命中返回 -1。"""
        best: int = -1
        best_dist: float = tolerance
        for i, (mx, my) in enumerate(self._midpoint_coords):
            d: float = ((point.x - mx) ** 2 + (point.y - my) ** 2) ** 0.5
            if d < best_dist:
                best_dist = d
                best = i
        return best

    @staticmethod
    def _can_delete_vertex(geom_type: str, vertex_count: int) -> bool:
        """判断是否允许删除一个顶点（保留最少顶点数）。"""
        if geom_type in ("Point", "MultiPoint"):
            return False
        if geom_type in ("LineString", "MultiLineString"):
            return vertex_count > 2
        return vertex_count > 3  # Polygon, MultiPolygon

    def _commit_vertex_edit(self) -> BaseGeometry | None:
        """从当前顶点坐标构建新几何（单部件）。"""
        geom_type: str = self._edit_geometry.geom_type if self._edit_geometry else ""
        coords: list[tuple[float, float]] = self._vertex_coords
        # 将 Multi 类型映射到单部件输出。
        if geom_type in ("MultiPoint",):
            geom_type = "Point"
        elif geom_type in ("MultiLineString",):
            geom_type = "LineString"
        elif geom_type in ("MultiPolygon",):
            geom_type = "Polygon"
        if geom_type == "Point":
            return Point(coords[0]) if coords else None
        if geom_type == "LineString":
            return LineString(coords) if len(coords) >= 2 else None
        if geom_type == "Polygon":
            if len(coords) < 3:
                return None
            closed: list[tuple[float, float]] = list(coords)
            if closed[0] != closed[-1]:
                closed.append(closed[0])
            return Polygon(closed)
        return None

    def _deactivate_all_tools(self) -> None:
        """关闭所有特殊工具模式，恢复默认交互状态。"""
        self._zoom_rect_active = False
        self._point_query_active = False
        self._rectangle_query_active = False
        self._pan_mode = "none"
        self._last_middle_pos = None
        self._digitize_mode = "none"
        self._clear_sketch()
        # 清理可能残留的橡皮筋。
        if self._rubber_band is not None:
            self._rubber_band.close()
            self._rubber_band = None
        self._vertex_edit_active = False
        self._edit_geometry = None
        self._edit_layer_id = ""
        self._edit_fid = None
        self._vertex_drag_idx = -1
        self._vertex_coords.clear()
        self._hovered_vertex = -1
        self._midpoint_coords.clear()
        self._selected_vertex_indices.clear()
        for item in self._vertex_items:
            self._scene.removeItem(item)
        self._vertex_items.clear()

    def set_snapping(self, enabled: bool) -> None:
        """启用或禁用顶点捕捉。

        参数:
            enabled: True 时数字化光标自动吸附到附近已有顶点。
        """
        self._snapping_enabled = enabled
        if not enabled:
            self._clear_snap_marker()

    def _build_snap_index(self, snapshot: WorkspaceSnapshot) -> None:
        """从工作区快照构建捕捉顶点索引。"""
        coords: list[tuple[float, float]] = []
        queryable_layer_ids: tuple[str, ...] = self._queryable_layer_ids(snapshot)
        queryable_ids: set[str] = set(queryable_layer_ids)
        for layer in snapshot.layers:
            if layer.layer_id not in queryable_ids:
                continue
            if not isinstance(layer.layer, VectorLayer):
                continue
            for feature in layer.layer.features:
                if feature.geometry.is_empty:
                    continue
                self._collect_coords(feature.geometry, coords)
        self._snap_coords = coords
        self._snap_layer_ids = queryable_layer_ids

    def _queryable_layer_ids(self, snapshot: WorkspaceSnapshot) -> tuple[str, ...]:
        """按当前视图比例返回可参与查询和捕捉的图层编号。"""
        return tuple(
            layer.layer_id
            for layer in snapshot.layers
            if layer.visible and self._is_layer_in_scale_range(layer)
        )

    def _is_layer_in_scale_range(self, layer: LayerSnapshot) -> bool:
        """判断图层在当前视图比例下是否应显示和参与交互。"""
        if (
            layer.min_scale_percent is not None
            and self._zoom_percent < float(layer.min_scale_percent)
        ):
            return False
        if (
            layer.max_scale_percent is not None
            and self._zoom_percent > float(layer.max_scale_percent)
        ):
            return False
        return True

    @staticmethod
    def _collect_coords(
        geometry: BaseGeometry, coords: list[tuple[float, float]]
    ) -> None:
        """递归收集几何中所有顶点。"""
        gtype: str = geometry.geom_type
        if gtype == "Point":
            coords.append((geometry.x, geometry.y))
        elif gtype in ("LineString", "LinearRing"):
            coords.extend((c[0], c[1]) for c in geometry.coords)
        elif gtype == "Polygon":
            coords.extend((c[0], c[1]) for c in geometry.exterior.coords)
            for ring in geometry.interiors:
                coords.extend((c[0], c[1]) for c in ring.coords)
        elif gtype in (
            "MultiPoint", "MultiLineString", "MultiPolygon",
            "GeometryCollection",
        ):
            for member in geometry.geoms:
                MapCanvas._collect_coords(member, coords)

    def _find_snap_target(self, cursor_point: Point) -> Point | None:
        """在容差内查找最近的捕捉顶点。

        参数:
            cursor_point: 当前光标在地图坐标系下的位置。

        返回:
            最近的捕捉点；无命中返回 None。
        """
        if not self._snapping_enabled or not self._snap_coords:
            return None
        tol: float = self._map_units_per_pixel * 10.0
        best_dist: float = tol
        best: tuple[float, float] | None = None
        px, py = cursor_point.x, cursor_point.y
        for sx, sy in self._snap_coords:
            d: float = ((px - sx) ** 2 + (py - sy) ** 2) ** 0.5
            if d < best_dist:
                best_dist = d
                best = (sx, sy)
        return Point(best[0], best[1]) if best else None

    def _show_snap_marker(self, point: Point) -> None:
        """在捕捉位置显示品红十字标记。"""
        self._clear_snap_marker()
        item: QGraphicsPathItem = QGraphicsPathItem()
        path: QPainterPath = QPainterPath()
        s: float = self._map_units_per_pixel * 5.0
        px, py = point.x, point.y
        path.moveTo(px - s, -py - s)
        path.lineTo(px + s, -py + s)
        path.moveTo(px + s, -py - s)
        path.lineTo(px - s, -py + s)
        pen: QPen = QPen(QColor("#FF1493"), 2)
        pen.setCosmetic(True)
        item.setPen(pen)
        item.setZValue(3000)
        self._scene.addItem(item)
        self._snap_marker = item

    def _snapped_position(self, screen_pos: QPoint) -> QPoint:
        """返回捕捉后的屏幕坐标；未启用或无命中时返回原坐标。"""
        if not self._snapping_enabled:
            return screen_pos
        snap_pt: Point | None = self._find_snap_target(
            self._screen_to_map_point(screen_pos)
        )
        if snap_pt is None:
            return screen_pos
        snap_scene: QPointF = QPointF(snap_pt.x, -snap_pt.y)
        return self.mapFromScene(snap_scene)

    def _update_feature_item_path(self) -> None:
        """拖拽时同步更新要素渲染图元，使面填充实时跟随。"""
        lid: str = getattr(self, "_edit_layer_id", "")
        fid: object = getattr(self, "_edit_fid", None)
        if not lid or fid is None:
            return
        new_path: QPainterPath = self._build_preview_path()
        if new_path.isEmpty():
            return
        for item in self._scene.items():
            if (
                isinstance(item, QGraphicsPathItem)
                and item.data(0) == lid
                and item.data(1) == fid
            ):
                item.prepareGeometryChange()
                item.setPath(new_path)
                item.update()

    def _build_preview_path(self) -> QPainterPath:
        """从当前顶点坐标构建预览折线/多边形路径。"""
        coords: list[tuple[float, float]] = self._vertex_coords
        if len(coords) < 2:
            return QPainterPath()
        path: QPainterPath = QPainterPath()
        path.moveTo(coords[0][0], -coords[0][1])
        for mx, my in coords[1:]:
            path.lineTo(mx, -my)
        if self._edit_geometry is not None and self._edit_geometry.geom_type in (
            "Polygon", "MultiPolygon"
        ):
            path.closeSubpath()
        return path

    def _clear_snap_marker(self) -> None:
        """清除捕捉标记。"""
        if self._snap_marker is not None:
            self._scene.removeItem(self._snap_marker)
            self._snap_marker = None

    def _clear_sketch(self) -> None:
        """清除数字化顶点和全部预览图元。"""
        self._digitize_vertices.clear()
        for item in self._sketch_items:
            self._scene.removeItem(item)
        self._sketch_items.clear()

    def _add_sketch_vertex(self, screen_pos: QPoint) -> None:
        """添加一个数字化顶点并重建预览。

        参数:
            screen_pos: 视口像素坐标。
        """
        self._digitize_vertices.append(screen_pos)
        self._rebuild_sketch_preview()

    def _finish_sketch(self) -> BaseGeometry | None:
        """根据已收集顶点构造 Shapely 几何并清理草图。

        返回:
            完成的 Point/LineString/Polygon；顶点不足时返回 None。
        """
        mode: str = self._digitize_mode
        vertices: list[QPoint] = self._digitize_vertices
        if mode == "point":
            if not vertices:
                return None
            return self._screen_to_map_point(vertices[0])
        map_pts: list[Point] = [self._screen_to_map_point(v) for v in vertices]
        coords: list[tuple[float, float]] = [(p.x, p.y) for p in map_pts]
        if mode == "line":
            if len(coords) < 2:
                return None
            return LineString(coords)
        if mode == "polygon":
            if len(coords) < 3:
                return None
            # 确保闭合。
            if coords[0] != coords[-1]:
                coords.append(coords[0])
            return Polygon(coords)
        return None

    def _rebuild_sketch_preview(
        self, cursor_pos: QPoint | None = None
    ) -> None:
        """重建数字化草图的场景预览图元。

        参数:
            cursor_pos: 当前鼠标视口位置；为空时不绘制到光标的虚线。
        """
        for item in self._sketch_items:
            self._scene.removeItem(item)
        self._sketch_items.clear()

        vertices: list[QPoint] = self._digitize_vertices
        if not vertices and cursor_pos is None:
            return

        # 顶点标记：亮黄小方块（地图坐标）。
        for v in vertices:
            mp: Point = self._screen_to_map_point(v)
            dot_item: QGraphicsPathItem = QGraphicsPathItem()
            dpath: QPainterPath = QPainterPath()
            # 用地图单位的小尺寸标记顶点。
            dot_size: float = self._map_units_per_pixel * 5.0
            dpath.addEllipse(
                QPointF(mp.x, -mp.y),
                dot_size,
                dot_size,
            )
            dot_item.setPath(dpath)
            dot_pen: QPen = QPen(QColor("#FF1493"), 1)
            dot_pen.setCosmetic(True)
            dot_item.setPen(dot_pen)
            dot_item.setBrush(QBrush(QColor("#FF1493")))
            dot_item.setZValue(1000)
            self._scene.addItem(dot_item)
            self._sketch_items.append(dot_item)

        if not vertices:
            return

        # 预览路径：已放置顶点 + 到光标的虚线。
        map_pts: list[tuple[float, float]] = []
        for v in vertices:
            mp = self._screen_to_map_point(v)
            map_pts.append((mp.x, mp.y))

        all_pts: list[tuple[float, float]] = list(map_pts)
        if cursor_pos is not None:
            cp: Point = self._screen_to_map_point(cursor_pos)
            all_pts.append((cp.x, cp.y))

        preview_path: QPainterPath = QPainterPath()
        is_polygon: bool = self._digitize_mode == "polygon" and len(all_pts) >= 3

        preview_path.moveTo(all_pts[0][0], -all_pts[0][1])
        for mx, my in all_pts[1:]:
            preview_path.lineTo(mx, -my)
        if is_polygon:
            preview_path.closeSubpath()

        preview_item: QGraphicsPathItem = QGraphicsPathItem(preview_path)
        prev_pen: QPen = QPen(QColor("#FF1493"), 1.5, Qt.PenStyle.DashLine)
        prev_pen.setCosmetic(True)
        preview_item.setPen(prev_pen)
        if is_polygon:
            preview_item.setBrush(QBrush(QColor(255, 20, 147, 40)))
        else:
            preview_item.setBrush(Qt.BrushStyle.NoBrush)
        preview_item.setZValue(1000)
        self._scene.addItem(preview_item)
        self._sketch_items.append(preview_item)

    def zoom_to_full_extent(self) -> None:
        """将当前地图范围完整缩放到视图内。"""
        fit_rect: QRectF = (
            self._map_scene_rect
            if self._map_scene_rect is not None
            else self._scene.sceneRect()
        )
        self.fitInView(fit_rect, Qt.AspectRatioMode.KeepAspectRatio)
        self._ensure_pan_area()
        self._reset_view_scale()

    def zoom_to_layer(self, bounds: Bounds) -> None:
        """将指定图层的完整空间范围缩放到视图内。

        参数:
            bounds: 图层在地图显示坐标系下的最小外包范围。
        """
        if self._map_scene_rect is None:
            return
        layer_scene_rect: QRectF = self._scene_rect_from_bounds(bounds)
        # 隐藏图层可能位于当前可见全图范围之外，导航场景也要包含其定位范围。
        self._scene.setSceneRect(self._scene.sceneRect().united(layer_scene_rect))
        self.fitInView(layer_scene_rect, Qt.AspectRatioMode.KeepAspectRatio)
        full_scale: float = self._fit_scale_for_rect(self._map_scene_rect)
        layer_scale: float = self._fit_scale_for_rect(layer_scene_rect)
        self._zoom_percent = (
            layer_scale / full_scale * 100.0 if full_scale > 0.0 else 100.0
        )
        self._ensure_pan_area()
        self._emit_view_scale()

    def zoom_to_feature(self, bounds: Bounds) -> None:
        """将单个要素放大到视图中，保证有明显的缩放效果。

        根据要素在当前视野中的占比计算放大倍率，确保至少放大 3 倍，
        最多放大 20 倍，无论当前缩放级别如何都能看到明显的拉近效果。

        参数:
            bounds: 要素在地图坐标系下的 (minx, miny, maxx, maxy)。
        """
        if self._map_scene_rect is None:
            return
        visible: QRectF = self._visible_scene_rect()
        min_x, min_y, max_x, max_y = bounds
        feature_w: float = max_x - min_x
        feature_h: float = max_y - min_y

        # 要素在当前视野中的占比。
        feature_fraction: float = max(
            feature_w / max(visible.width(), 1e-9),
            feature_h / max(visible.height(), 1e-9),
        )
        # 点要素占比接近 0，使用最小虚拟占比约 12%，对应约 3× 放大；
        # 避免点要素无限放大到 20× 上限。
        feature_fraction = max(feature_fraction, 0.12)
        # 目标：要素占视野约 35%，由此计算需要放大的倍率。
        target_fraction: float = 0.35
        zoom_factor: float = target_fraction / feature_fraction
        # 钳制：不缩小（≥1×），最多放大 20 倍。
        zoom_factor = max(1.0, min(20.0, zoom_factor))

        # 以要素中心为锚点，按倍率缩小视口范围。
        target_w: float = visible.width() / zoom_factor
        target_h: float = visible.height() / zoom_factor
        center_x: float = (min_x + max_x) / 2.0 if feature_w > 0.0 else min_x
        center_y: float = (min_y + max_y) / 2.0 if feature_h > 0.0 else min_y
        # Qt 场景 Y 轴向下，需将地图 Y（向上）取反后才能传入 fitInView。
        feature_rect: QRectF = QRectF(
            center_x - target_w / 2.0,
            -center_y - target_h / 2.0,
            target_w,
            target_h,
        )

        self._scene.setSceneRect(self._scene.sceneRect().united(feature_rect))
        self.fitInView(feature_rect, Qt.AspectRatioMode.KeepAspectRatio)
        full_scale: float = self._fit_scale_for_rect(self._map_scene_rect)
        feature_scale: float = self._fit_scale_for_rect(feature_rect)
        self._zoom_percent = (
            feature_scale / full_scale * 100.0 if full_scale > 0.0 else 100.0
        )
        self._ensure_pan_area()
        self._emit_view_scale()

    # ── 缩放 ────────────────────────────────────────────────

    def zoom_in(self) -> None:
        """以画布中心为基准将地图视图放大一级。"""
        center: QPoint = self.viewport().rect().center()
        self._zoom_at_screen_point(center, 1.25)

    def zoom_out(self) -> None:
        """以画布中心为基准将地图视图缩小一级。"""
        center: QPoint = self.viewport().rect().center()
        self._zoom_at_screen_point(center, 0.8)

    def _zoom_at_screen_point(self, anchor: QPoint | QPointF, factor: float) -> None:
        """以指定屏幕点为锚点进行缩放，使该点对应的地图位置保持不变。

        参数:
            anchor: 缩放锚点在视口中的像素位置（如鼠标光标或视图中心）。
            factor: 缩放倍率，大于 1 为放大，小于 1 为缩小。

        状态变化:
            更新视图变换矩阵、缩放百分比和状态栏比例文本。
        """
        # 缩放前光标对应的场景坐标。
        target_scene: QPointF = self.mapToScene(
            QPoint(int(anchor.x()), int(anchor.y()))
        )
        self.scale(factor, factor)
        self._zoom_percent *= factor
        # 缩放后同一场景坐标落在屏幕上的新位置。
        moved_to: QPoint = self.mapFromScene(target_scene)
        # 场景点漂移量 = moved_to - anchor：正值表示场景点跑到了光标右/下方，
        # 需要同向增大滚动条值才能把场景点拉回光标位置。
        drift_x: int = moved_to.x() - int(anchor.x())
        drift_y: int = moved_to.y() - int(anchor.y())
        if drift_x != 0:
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() + drift_x
            )
        if drift_y != 0:
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() + drift_y
            )
        self._ensure_pan_area()
        self._emit_view_scale()
        # 更新地图单位并重建顶点标记（如果正在顶点编辑模式）。
        vw: int = max(self.viewport().width(), 1)
        vh: int = max(self.viewport().height(), 1)
        vis: QRectF = self._visible_scene_rect()
        if vis.width() > 0:
            self._map_units_per_pixel = max(
                vis.width() / vw,
                vis.height() / vh,
            )
        if self._vertex_edit_active and self._edit_geometry is not None:
            self._rebuild_vertex_markers(self._edit_geometry)
        # 缩放后立即重建场景（关闭刷新避免闪烁），更新点大小。
        if self._last_snapshot is not None and not self._vertex_edit_active:
            self.setUpdatesEnabled(False)
            try:
                self.set_snapshot(self._last_snapshot)
            finally:
                self.setUpdatesEnabled(True)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """把鼠标滚轮动作转换为以光标为锚点的连续地图缩放。

        参数:
            event: 包含滚动方向和步长的 Qt 滚轮事件。

        状态变化:
            以鼠标所在位置为锚点缩放视图并消费该事件。
        """
        angle: int = event.angleDelta().y()
        if angle == 0:
            return
        factor: float = 1.25 if angle > 0 else 0.8
        self._zoom_at_screen_point(event.position().toPoint(), factor)
        event.accept()

    # ── 平移 ────────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """拦截中键平移、框选操作和查询工具的按下事件，其余交由父类处理。

        参数:
            event: 包含按钮类型和修饰键状态的 Qt 鼠标按下事件。

        状态变化:
            中键按下时进入手动平移模式；
            点选查询模式下左键单击执行查询并切回平移工具；
            框选查询/框选放大模式下左键拖拽创建橡皮筋矩形。
        """
        # 通知外部（如主窗口）可据此取消图层面板选中。
        self.canvas_clicked.emit()

        # 关闭残留的橡皮筋。
        if self._rubber_band is not None:
            self._rubber_band.close()
            self._rubber_band = None

        # 中键平移：在所有工具模式下常驻生效。
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_mode = "middle"
            self._last_middle_pos = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        # ── 顶点编辑（拖拽式）──
        if self._vertex_edit_active and self._edit_geometry is not None:
            screen_pos: QPoint = event.position().toPoint()
            scene_pos: QPointF = self.mapToScene(screen_pos)
            click_pt: Point = self._screen_to_map_point(screen_pos)
            tolerance: float = self._map_units_per_pixel * 15.0

            if event.button() == Qt.MouseButton.LeftButton:
                ctrl_held: bool = bool(
                    QApplication.keyboardModifiers()
                    & Qt.KeyboardModifier.ControlModifier
                )
                hit_idx: int = self._hit_test_vertex(click_pt, tolerance)
                if hit_idx < 0 and len(self._vertex_coords) >= 2:
                    hit_idx = self._hit_test_preview_path(click_pt, tolerance * 2.0)

                if hit_idx >= 0:
                    if ctrl_held:
                        # Ctrl+点击：切换顶点选中状态。
                        sel: set[int] = self._selected_vertex_indices
                        if hit_idx in sel:
                            sel.discard(hit_idx)
                        else:
                            sel.add(hit_idx)
                        self._rebuild_vertex_markers(self._edit_geometry)
                    elif self._edit_mode == "delete_vertex":
                        if self._can_delete_vertex(
                            self._edit_geometry.geom_type,
                            len(self._vertex_coords),
                        ):
                            del self._vertex_coords[hit_idx]
                            self._selected_vertex_indices.discard(hit_idx)
                            self._hovered_vertex = -1
                            self._rebuild_vertex_markers(self._edit_geometry)
                            self._update_feature_item_path()
                    else:
                        # 拖拽模式：命中顶点已在选中集中→保持多选拖拽；
                        # 否则单选该顶点。
                        if hit_idx not in self._selected_vertex_indices:
                            self._selected_vertex_indices.clear()
                            self._selected_vertex_indices.add(hit_idx)
                            self._rebuild_vertex_markers(self._edit_geometry)
                        self._vertex_drag_idx = hit_idx
                        self.setCursor(Qt.CursorShape.ClosedHandCursor)
                    event.accept()
                    return

                # 点击空白处清除选中。
                if not ctrl_held:
                    self._selected_vertex_indices.clear()
                    self._rebuild_vertex_markers(self._edit_geometry)
                # 检查延伸标记。
                for item in self._vertex_items:
                    tag: str | None = item.data(0)
                    if tag in ("extend_start", "extend_end") and item.contains(
                        scene_pos
                    ):
                        if tag == "extend_start":
                            self._vertex_coords.insert(0, (click_pt.x, click_pt.y))
                        else:
                            self._vertex_coords.append((click_pt.x, click_pt.y))
                        self._rebuild_vertex_markers(self._edit_geometry)
                        event.accept()
                        return
                event.accept()
                return

            if event.button() == Qt.MouseButton.RightButton:
                # 右键删除顶点。
                scene_pos2: QPointF = self.mapToScene(event.position().toPoint())
                for item in self._vertex_items:
                    if item.data(0) == "vertex" and item.contains(scene_pos2):
                        idx: int = item.data(1)
                        if self._can_delete_vertex(
                            self._edit_geometry.geom_type,
                            len(self._vertex_coords),
                        ):
                            del self._vertex_coords[idx]
                            self._selected_vertex_indices.discard(idx)
                            self._hovered_vertex = -1
                            self._rebuild_vertex_markers(self._edit_geometry)
                        break
                event.accept()
                return

            event.accept()
            return

        # ── 数字化工具 ──
        digitizing: bool = self._digitize_mode != "none"
        if digitizing:
            if event.button() == Qt.MouseButton.LeftButton:
                if self._digitize_mode == "point":
                    snapped: QPoint = self._snapped_position(
                        event.position().toPoint()
                    )
                    self._add_sketch_vertex(snapped)
                    self._clear_snap_marker()
                    geometry: BaseGeometry | None = self._finish_sketch()
                    if geometry is not None:
                        self.feature_digitized.emit(geometry)
                else:
                    self._add_sketch_vertex(
                        self._snapped_position(event.position().toPoint())
                    )
                event.accept()
                return
            if event.button() == Qt.MouseButton.RightButton:
                geometry = self._finish_sketch()
                if geometry is not None:
                    self.feature_digitized.emit(geometry)
                else:
                    self.set_pan_tool()
                event.accept()
                return
            # 其他按钮在数字化模式下不处理。
            event.accept()
            return

        # 点选查询：左键单击执行查询，Shift 按下时追加到已有选择。
        if self._point_query_active and event.button() == Qt.MouseButton.LeftButton:
            query_point: Point = self._screen_to_map_point(
                event.position().toPoint()
            )
            add_to_selection: bool = bool(
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            )
            self.point_queried.emit(query_point, add_to_selection)
            event.accept()
            return

        # 框选查询：左键拖拽绘制选择矩形。
        if self._rectangle_query_active and event.button() == Qt.MouseButton.LeftButton:
            self._zoom_origin = event.position().toPoint()
            self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self)
            self._rubber_band.setGeometry(QRect(self._zoom_origin, QSize()))
            self._rubber_band.show()
            event.accept()
            return

        # 框选放大：Shift+左键拖拽 或 框选放大工具模式下左键拖拽。
        if event.button() == Qt.MouseButton.LeftButton and (
            bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            or self._zoom_rect_active
        ):
            self._zoom_origin = event.position().toPoint()
            self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self)
            self._rubber_band.setGeometry(QRect(self._zoom_origin, QSize()))
            self._rubber_band.show()
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """线、面数字化时双击完成绘制，其余情况交给父类处理。

        双击序列中第一次左键按下已经把该位置添加为顶点，
        这里直接使用已收集的顶点完成草图，避免放置两个相同顶点。
        """
        if (
            self._digitize_mode in ("line", "polygon")
            and event.button() == Qt.MouseButton.LeftButton
        ):
            geometry: BaseGeometry | None = self._finish_sketch()
            if geometry is not None:
                self.feature_digitized.emit(geometry)
            else:
                # 顶点不足（如面少于 3 点）时退出数字化工具。
                self.set_pan_tool()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """驱动数字化预览、中键平移或更新框选橡皮筋。

        参数:
            event: 包含当前视口位置的 Qt 鼠标移动事件。
        """
        # ── 顶点编辑悬停高亮 ──
        if self._vertex_edit_active and self._vertex_drag_idx < 0:
            scene_pos: QPointF = self.mapToScene(
                event.position().toPoint()
            )
            hovered: int = -1
            for item in self._vertex_items:
                if item.data(0) == "vertex" and item.contains(scene_pos):
                    hovered = item.data(1)
                    break
            # 只在高亮目标变化时重建。
            if hovered != getattr(self, "_hovered_vertex", -1):
                self._hovered_vertex = hovered
                self._rebuild_vertex_markers(self._edit_geometry)

        # ── 顶点拖拽（多选全部一起移动）──
        if self._vertex_drag_idx >= 0:
            pt: Point = self._screen_to_map_point(event.position().toPoint())
            drag_idx: int = self._vertex_drag_idx
            old_x: float = self._vertex_coords[drag_idx][0]
            old_y: float = self._vertex_coords[drag_idx][1]
            dx: float = pt.x - old_x
            dy: float = pt.y - old_y
            # 移动所有选中顶点。
            move_indices: set[int] = (
                self._selected_vertex_indices
                if self._selected_vertex_indices
                else {drag_idx}
            )
            for i in move_indices:
                if i < len(self._vertex_coords):
                    cx, cy = self._vertex_coords[i]
                    self._vertex_coords[i] = (cx + dx, cy + dy)
            # 重建标记和预览。
            self._rebuild_vertex_markers(self._edit_geometry)
            self._update_feature_item_path()
            event.accept()
            return

        # ── 数字化预览（含捕捉）──
        if self._digitize_mode in ("line", "polygon"):
            cursor_pos: QPoint = event.position().toPoint()
            if self._snapping_enabled:
                snap_pt: Point | None = self._find_snap_target(
                    self._screen_to_map_point(cursor_pos)
                )
                if snap_pt is not None:
                    self._show_snap_marker(snap_pt)
                    # 把捕捉点反算回屏幕坐标用于预览。
                    snap_scene: QPointF = QPointF(snap_pt.x, -snap_pt.y)
                    cursor_pos = self.mapFromScene(snap_scene)
                else:
                    self._clear_snap_marker()
            else:
                self._clear_snap_marker()
            self._rebuild_sketch_preview(cursor_pos)
            # 仍更新状态栏坐标。
            digitize_scene_pos: QPointF = self.mapToScene(event.position().toPoint())
            self.coordinate_changed.emit(
                f"坐标  {digitize_scene_pos.x():.6f}, {-digitize_scene_pos.y():.6f}"
            )
            event.accept()
            return

        # 中键平移：计算位移并通过滚动条移动视图。
        if self._pan_mode == "middle" and self._last_middle_pos is not None:
            current_pos: QPoint = event.position().toPoint()
            delta: QPoint = self._last_middle_pos - current_pos
            self._last_middle_pos = current_pos
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() + delta.x()
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() + delta.y()
            )
            # 平移时同样更新状态栏坐标。
            pan_scene_pos: QPointF = self.mapToScene(current_pos)
            self.coordinate_changed.emit(
                f"坐标  {pan_scene_pos.x():.6f}, {-pan_scene_pos.y():.6f}"
            )
            event.accept()
            return

        # 框选橡皮筋（放大或查询）：跟随鼠标实时更新选择矩形。
        if self._rubber_band is not None:
            self._rubber_band.setGeometry(
                QRect(self._zoom_origin, event.position().toPoint()).normalized()
            )
            event.accept()
            return

        # 默认：更新状态栏坐标并交由父类处理平移/选择交互。
        scene_position: QPointF = self.mapToScene(event.position().toPoint())
        self.coordinate_changed.emit(
            f"坐标  {scene_position.x():.6f}, {-scene_position.y():.6f}"
        )
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        # ── 顶点拖拽释放 ──
        if self._vertex_drag_idx >= 0 and event.button() == Qt.MouseButton.LeftButton:
            self._vertex_drag_idx = -1
            self.setCursor(Qt.CursorShape.CrossCursor)
            self._rebuild_vertex_markers(self._edit_geometry)
            event.accept()
            return

        # 中键释放：恢复光标样式。
        if self._pan_mode == "middle" and event.button() == Qt.MouseButton.MiddleButton:
            self._pan_mode = "none"
            self._last_middle_pos = None
            self._update_cursor()
            event.accept()
            return

        # 框选释放（查询或缩放）：关闭橡皮筋。
        if self._rubber_band is not None and event.button() == Qt.MouseButton.LeftButton:
            self._rubber_band.close()
            self._rubber_band = None
            screen_rect: QRect = QRect(
                self._zoom_origin, event.position().toPoint()
            ).normalized()
            # 忽略过小的拖拽（可能是误触），阈值设为 8 像素。
            if screen_rect.width() <= 8 or screen_rect.height() <= 8:
                event.accept()
                return

            if self._rectangle_query_active:
                # 框选查询：屏幕矩形 → 地图坐标矩形 → Shapely Polygon。
                query_polygon: Polygon = self._screen_rect_to_map_polygon(
                    screen_rect
                )
                add_to_selection: bool = bool(
                    QApplication.keyboardModifiers()
                    & Qt.KeyboardModifier.ShiftModifier
                )
                self.rectangle_queried.emit(query_polygon, add_to_selection)
            else:
                # 框选缩放：计算场景范围并缩放到该区域。
                scene_rect: QRectF = QRectF(
                    self.mapToScene(screen_rect.topLeft()),
                    self.mapToScene(screen_rect.bottomRight()),
                ).normalized()
                self.fitInView(scene_rect, Qt.AspectRatioMode.KeepAspectRatio)
                if self._map_scene_rect is not None:
                    full_scale: float = self._fit_scale_for_rect(self._map_scene_rect)
                    new_scale: float = self._fit_scale_for_rect(scene_rect)
                    self._zoom_percent = (
                        new_scale / full_scale * 100.0 if full_scale > 0.0 else 100.0
                    )
                self._ensure_pan_area()
                self._emit_view_scale()
            event.accept()
            return

        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Esc 退出工具模式；Backspace 撤销数字化最后顶点。

        参数:
            event: Qt 键盘事件。
        """
        # Enter：提交顶点编辑。
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and self._vertex_edit_active
        ):
            new_geom: BaseGeometry | None = self._commit_vertex_edit()
            if new_geom is not None:
                self.geometry_edited.emit(new_geom)
            self.set_pan_tool()
            event.accept()
            return

        # Ctrl+A：全选所有顶点。
        if (
            event.key() == Qt.Key.Key_A
            and bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            and self._vertex_edit_active
        ):
            self._selected_vertex_indices = set(range(len(self._vertex_coords)))
            self._rebuild_vertex_markers(self._edit_geometry)
            event.accept()
            return

        # Esc：退出所有特殊模式。
        if event.key() == Qt.Key.Key_Escape:
            if (
                self._point_query_active
                or self._rectangle_query_active
                or self._zoom_rect_active
                or self._digitize_mode != "none"
                or self._vertex_edit_active
            ):
                self.set_pan_tool()
                event.accept()
                return

        # 数字化模式下的顶点撤销。
        if self._digitize_mode in ("line", "polygon"):
            is_ctrl_z: bool = (
                event.key() == Qt.Key.Key_Z
                and bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            )
            if event.key() == Qt.Key.Key_Backspace or is_ctrl_z:
                if self._digitize_vertices:
                    self._digitize_vertices.pop()
                    self._rebuild_sketch_preview()
                event.accept()
                return

        super().keyPressEvent(event)

    # ── 光标管理 ─────────────────────────────────────────────

    def _update_cursor(self) -> None:
        """根据当前工具模式恢复合适的光标样式。

        状态变化:
            框选工具激活时显示十字准星；手形拖拽模式显示张开手掌；
            其余模式恢复默认箭头。
        """
        if self._zoom_rect_active:
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif self.dragMode() == QGraphicsView.DragMode.ScrollHandDrag:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def resizeEvent(self, event: QResizeEvent) -> None:
        """在画布尺寸变化时保持空状态引导居中。

        参数:
            event: 包含画布新旧尺寸的 Qt 调整事件。

        状态变化:
            重新计算空状态面板的居中位置和自适应宽度。
        """
        super().resizeEvent(event)
        overlay_width: int = min(420, max(self.viewport().width() - 48, 220))
        overlay_height: int = 170
        left: int = max((self.viewport().width() - overlay_width) // 2, 0)
        top: int = max((self.viewport().height() - overlay_height) // 2, 0)
        self._empty_overlay.setGeometry(left, top, overlay_width, overlay_height)
        self._ensure_pan_area()

    def _create_empty_overlay(self) -> QFrame:
        """创建不含测试图形的空地图操作引导面板。"""
        overlay: QFrame = QFrame(self.viewport())
        overlay.setObjectName("emptyMapOverlay")
        symbol: QLabel = QLabel("◎")
        symbol.setObjectName("emptyMapSymbol")
        symbol.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title: QLabel = QLabel("开始创建您的地图")
        title.setObjectName("emptyMapTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description: QLabel = QLabel("从顶部“文件”功能区打开矢量与栅格数据")
        description.setObjectName("emptyMapDescription")
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout: QVBoxLayout = QVBoxLayout(overlay)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(6)
        layout.addWidget(symbol)
        layout.addWidget(title)
        layout.addWidget(description)
        return overlay

    def _reset_view_scale(self) -> None:
        """把当前全图显示记录为百分之百视图比例。"""
        self._zoom_percent = 100.0
        self._emit_view_scale()

    def _emit_view_scale(self) -> None:
        """发出格式化后的当前视图比例文本。"""
        self._apply_scale_ranges()
        self.view_scale_changed.emit(f"视图比例  {self._zoom_percent:.0f}%")

    def _apply_scale_ranges(self) -> None:
        """按当前视图比例过滤各图层的显示比例范围。

        视图比例低于图层最小比例或高于最大比例时，该图层所有图元隐藏；
        回到范围内后恢复显隐原状态。缩放操作会反复调用本方法，
        因此隐藏状态是临时的，始终以快照中的 visible 为最终依据。
        """
        if self._last_snapshot is None:
            return
        queryable_layer_ids: tuple[str, ...] = self._queryable_layer_ids(
            self._last_snapshot
        )
        queryable_ids: set[str] = set(queryable_layer_ids)
        for layer in self._last_snapshot.layers:
            layer_visible: bool = layer.visible and layer.layer_id in queryable_ids
            items: list[QGraphicsItem] | None = self._layer_items.get(layer.layer_id)
            if not items:
                continue
            for item in items:
                item.setVisible(layer_visible)
        if queryable_layer_ids != self._snap_layer_ids:
            self._build_snap_index(self._last_snapshot)

    def _ensure_pan_area(self) -> None:
        """确保当前视口周围存在可供手形拖动的场景范围。"""
        if (
            self._map_scene_rect is None
            or self.viewport().width() <= 0
            or self.viewport().height() <= 0
        ):
            return
        viewport_rect: QRectF = self._visible_scene_rect()
        # 扩展一整个当前视口，保证图层较小时仍能向任意方向拖动。
        required_rect: QRectF = self._map_scene_rect.united(
            viewport_rect.adjusted(
                -viewport_rect.width(),
                -viewport_rect.height(),
                viewport_rect.width(),
                viewport_rect.height(),
            )
        )
        if self._scene.sceneRect().contains(required_rect):
            return
        view_center: QPointF = self.mapToScene(self.viewport().rect().center())
        self._scene.setSceneRect(required_rect)
        self.centerOn(view_center)

    def _visible_scene_rect(self) -> QRectF:
        """返回当前视口在地图场景中的可见范围。"""
        top_left: QPointF = self.mapToScene(self.viewport().rect().topLeft())
        bottom_right: QPointF = self.mapToScene(self.viewport().rect().bottomRight())
        return QRectF(top_left, bottom_right).normalized()

    def _screen_to_map_point(self, screen_pos: QPoint) -> Point:
        """将视口像素坐标转换为地图坐标系下的 Shapely Point。

        Qt 场景 Y 轴向下，地图 Y 轴向上，需反转纵轴。
        """
        scene_pos: QPointF = self.mapToScene(screen_pos)
        return Point(scene_pos.x(), -scene_pos.y())

    def _screen_rect_to_map_polygon(self, screen_rect: QRect) -> Polygon:
        """将视口像素矩形转换为地图坐标系下的 Shapely Polygon。

        四个角按逆时针排列，闭合回起点。
        """
        top_left: QPointF = self.mapToScene(screen_rect.topLeft())
        bottom_right: QPointF = self.mapToScene(screen_rect.bottomRight())
        min_x, max_y = top_left.x(), -top_left.y()
        max_x, min_y = bottom_right.x(), -bottom_right.y()
        return Polygon([
            (min_x, min_y),
            (max_x, min_y),
            (max_x, max_y),
            (min_x, max_y),
            (min_x, min_y),
        ])

    @staticmethod
    def _scene_rect_from_bounds(bounds: Bounds) -> QRectF:
        """按 QGIS 式比例扩展，把地图范围转换为 Y 轴反向的 Qt 场景范围。"""
        minimum_x, minimum_y, maximum_x, maximum_y = MapCanvas._non_empty_bounds(bounds)
        width: float = maximum_x - minimum_x
        height: float = maximum_y - minimum_y
        # QGIS 将完整包络以中心为基准整体放大 1.05 倍，即每边留出 2.5%。
        extent_scale_factor: float = 1.05
        horizontal_margin: float = width * (extent_scale_factor - 1.0) / 2.0
        vertical_margin: float = height * (extent_scale_factor - 1.0) / 2.0
        return QRectF(
            minimum_x - horizontal_margin,
            -(maximum_y + vertical_margin),
            width + 2 * horizontal_margin,
            height + 2 * vertical_margin,
        )

    @staticmethod
    def _non_empty_bounds(bounds: Bounds) -> Bounds:
        """仅为零宽或零高包络增加极小范围，避免普通数据受固定地图单位影响。"""
        minimum_x, minimum_y, maximum_x, maximum_y = bounds
        width: float = maximum_x - minimum_x
        height: float = maximum_y - minimum_y
        if width > 0.0 and height > 0.0:
            return bounds
        # 与 QGIS 的全零范围策略一致，为原点处单点提供明确的默认视图。
        if minimum_x == maximum_x == minimum_y == maximum_y == 0.0:
            return (-1.0, -1.0, 1.0, 1.0)
        coordinate_padding_factor: float = 1e-8
        if width <= 0.0:
            horizontal_padding: float = max(abs(minimum_x), 1.0) * coordinate_padding_factor
            minimum_x -= horizontal_padding
            maximum_x += horizontal_padding
        if height <= 0.0:
            vertical_padding: float = max(abs(minimum_y), 1.0) * coordinate_padding_factor
            minimum_y -= vertical_padding
            maximum_y += vertical_padding
        return (minimum_x, minimum_y, maximum_x, maximum_y)

    def _fit_scale_for_rect(self, rect: QRectF) -> float:
        """估算指定场景范围适配当前视口时使用的缩放比例。"""
        if rect.width() <= 0.0 or rect.height() <= 0.0:
            return 0.0
        return min(
            max(self.viewport().width(), 1) / rect.width(),
            max(self.viewport().height(), 1) / rect.height(),
        )
