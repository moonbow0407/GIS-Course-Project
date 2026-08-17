"""基于领域图层快照的地图画布。"""

import math
from dataclasses import dataclass, replace

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
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
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

from app.application.display_models import RasterDisplayPayload, VectorDisplayPayload
from app.application.project_models import MapViewState
from app.application.results import LayerSnapshot, WorkspaceSnapshot
from app.domain.raster_layer import RasterLayer
from app.domain.vector_layer import Bounds
from app.presentation.feature_editing import (
    VertexAddress,
    iter_vertices,
    rebuild_geometry,
)
from app.presentation.global_display_settings import sketch_color, snap_color, snap_edge_color
from app.presentation.renderers.qt_raster_renderer import QtRasterRenderer
from app.presentation.renderers.qt_vector_renderer import QtVectorRenderer
from app.presentation.snapping_engine import SnappingEngine, SnapResult

# 矢量视域裁剪：视口每边向外预留的整幅视口倍数。余量内的平移和未超过
# 重建阈值的缩放复用现有图元；越出余量后由防抖视口刷新按新视野重渲染。
_CULL_VIEWPORT_MARGIN: float = 1.0


@dataclass
class _LayerRenderState:
    """图层级图元缓存：签名一致时整层图元原样复用。

    属性:
        signature: 渲染签名，由图层身份、载荷、状态、堆叠顺序和视域组成。
        items: 该图层当前在场景中的全部图元。
    """

    signature: tuple
    items: list[QGraphicsItem]


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
    # 测量完成信号：(length/area, 地图显示 CRS 下的临时几何)。
    measurement_completed = Signal(str, BaseGeometry)
    # 顶点编辑提交信号：携带修改后的 Shapely 几何。
    geometry_edited = Signal(BaseGeometry)
    # 拆分请求信号：携带用户绘制的切割线（LineString）。
    feature_split_requested = Signal(BaseGeometry)
    # 拓扑编辑提交信号：携带 {fid: new_geometry} 映射。
    topology_edited = Signal(dict)
    # 编辑预览变化：(工作几何, 操作参数)，仅更新会话，不写盘。
    edit_preview_changed = Signal(object, dict)
    # 画布键盘入口请求应用或取消当前编辑会话。
    edit_apply_requested = Signal()
    edit_cancel_requested = Signal()
    # 地图工具切换信号：供主窗口同步功能区按钮的持续高亮状态。
    tool_changed = Signal(str)
    # 导航停止后的地图范围和视口像素尺寸，供后台金字塔窗口读取。
    viewport_changed = Signal(object, object)

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
        # 上次重建图元时的 _map_units_per_pixel：缩放时若变化小于 50%
        # 则跳过全量重建，仅通过 _apply_scale_ranges 更新图层显隐。
        self._last_render_mupp: float = 0.0
        # 数字化模式："none"/"point"/"line"/"polygon"。
        self._digitize_mode: str = "none"
        # 数字化顶点栈：屏幕像素坐标列表。
        self._digitize_vertices: list[QPoint] = []
        # 数字化预览图元：临时场景项，完成或取消后一并移除。
        self._sketch_items: list[QGraphicsItem] = []
        # 捕捉引擎：STRtree 空间索引 + 顶点/边捕捉。
        self._snap_engine: SnappingEngine = SnappingEngine()
        self._snap_marker: QGraphicsPathItem | None = None
        self._snap_edge_marker: QGraphicsPathItem | None = None
        # 缓存最近一次快照，供缩放后重新渲染使用。
        self._last_snapshot: WorkspaceSnapshot | None = None
        self._viewport_timer = QTimer(self)
        self._viewport_timer.setSingleShot(True)
        self._viewport_timer.setInterval(180)
        self._viewport_timer.timeout.connect(self._emit_viewport_changed)
        self._last_viewport_key: tuple[float | int, ...] | None = None
        # 最近一次矢量渲染的裁剪场景范围：视口越出其余量时触发重渲染。
        self._last_cull_scene_rect: QRectF | None = None
        # 图层图元缓存：按图层编号保存该图层全部 Qt 图元，供显示比例过滤使用。
        self._layer_items: dict[str, list[QGraphicsItem]] = {}
        # 图层级增量重建状态：签名一致的图层直接复用缓存图元。
        self._layer_render_state: dict[str, _LayerRenderState] = {}
        # 顶点编辑状态。
        self._vertex_edit_active: bool = False
        self._edit_geometry: BaseGeometry | None = None
        self._vertex_coords: list[tuple[float, float]] = []
        self._vertex_addresses: list[VertexAddress] = []
        self._midpoint_coords: list[tuple[float, float]] = []
        self._vertex_drag_idx: int = -1
        self._hovered_vertex: int = -1
        self._edit_mode: str = "drag_vertex"
        self._vertex_items: list[QGraphicsItem] = []
        # 多选顶点：Ctrl+点击切换，Ctrl+A 全选，拖拽时全部一起移动。
        self._selected_vertex_indices: set[int] = set()
        # 整要素移动状态。
        self._move_active: bool = False
        self._move_geometry: BaseGeometry | None = None
        self._move_original_geometry: BaseGeometry | None = None
        self._move_gesture_geometry: BaseGeometry | None = None
        self._move_layer_id: str = ""
        self._move_fid: object = None
        self._move_start_map: Point | None = None
        self._move_total_dx: float = 0.0
        self._move_total_dy: float = 0.0
        self._move_preview_item: QGraphicsPathItem | None = None
        # 变换（旋转/缩放）状态。
        self._transform_active: bool = False
        self._transform_mode: str = "rotate"
        self._transform_geometry: BaseGeometry | None = None
        self._transform_original_geometry: BaseGeometry | None = None
        self._transform_gesture_geometry: BaseGeometry | None = None
        self._transform_layer_id: str = ""
        self._transform_fid: object = None
        self._transform_centroid: tuple[float, float] = (0.0, 0.0)
        self._transform_start_pos: QPoint | None = None
        self._transform_preview_item: QGraphicsPathItem | None = None
        self._transform_guide_item: QGraphicsPathItem | None = None
        # 参考点拖动时的临时捕捉标记；只在命中顶点时显示。
        self._transform_pivot_snap_item: QGraphicsPathItem | None = None
        self._transform_pivot_snap_kind: str = ""
        self._transform_pivot_dragging: bool = False
        self._transform_angle: float = 0.0  # 累积旋转角度（度）
        self._transform_scale: float = 1.0  # 累积缩放比例
        self._transform_gesture_angle: float = 0.0
        self._transform_gesture_scale: float = 1.0
        # 拆分要素状态。
        self._split_active: bool = False
        self._split_layer_id: str = ""
        self._split_fid: object = None
        self._split_target_geometry: BaseGeometry | None = None
        self._static_edit_active: bool = False
        self._static_edit_geometry: BaseGeometry | None = None
        # 共享边界拓扑。
        self._shared_topology: dict[int, list[tuple[object, int]]] = {}
        self._linked_features: dict[
            object,
            tuple[BaseGeometry, list[VertexAddress], list[tuple[float, float]]],
        ] = {}
        self._topology_layer_id: str = ""
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

    @property
    def current_snapshot(self) -> WorkspaceSnapshot | None:
        """返回画布最近一次应用的工作区快照。"""
        return self._last_snapshot

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
        """按图层增量更新场景图元并适配真实数据范围。

        参数:
            snapshot: 包含真实矢量、栅格图层及显隐状态的工作区快照。

        状态变化:
            只重建签名变化的图层，未变化图层的图元原样复用；空快照
            移除全部图元并只显示操作引导。Qt 视图变换在更新期间保持
            不变，无需额外恢复，避免反复 fitInView 导致的持续缩放漂移。
        """
        is_first_load: bool = self._map_scene_rect is None

        # 与旧 scene.clear() 行为保持一致：几何编辑、数字化草图和捕捉
        # 标记都是临时图元，任何快照刷新都应移除，由工具自行重建。
        self._clear_transient_items()
        self._empty_overlay.setVisible(not snapshot.layers)
        if not snapshot.layers:
            self._remove_all_layer_items()
            self._last_snapshot = snapshot
            self._selected_fids.clear()
            self._snap_engine.clear()
            self._map_scene_rect = None
            self._last_cull_scene_rect = None
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
        # setSceneRect 会按新范围钳制滚动条，缩放/平移后的视图中心可能被
        # 拉回场景中部，导致数字化的新要素落到视口外。场景范围已能容纳
        # 地图范围时不重复设置，保持视图和图层图元签名稳定；需要扩展时
        # 使用与 _ensure_pan_area 相同的"地图 ∪ 三倍视口"范围，保证后续
        # 刷新幂等，centerOn 的目标始终可达。
        if not self._scene.sceneRect().contains(map_scene_rect):
            viewport_rect: QRectF = self._visible_scene_rect()
            expanded_rect: QRectF = map_scene_rect.united(
                viewport_rect.adjusted(
                    -viewport_rect.width(),
                    -viewport_rect.height(),
                    viewport_rect.width(),
                    viewport_rect.height(),
                )
            )
            view_center: QPointF = self.mapToScene(self.viewport().rect().center())
            self._scene.setSceneRect(expanded_rect)
            self.centerOn(view_center)
        if is_first_load:
            # 点符号使用屏幕像素定义尺寸；首次加载必须先适配真实数据范围，
            # 否则这里仍沿用空画布的 1000×700 场景变换，导致小范围经纬度点被巨幅放大。
            self.fitInView(map_scene_rect, Qt.AspectRatioMode.KeepAspectRatio)
            self._reset_view_scale()
        # 快照按底到顶排列，枚举值可直接作为 Qt 图元的叠放顺序。
        # 先扩展可平移场景，再测量当前视野，保证点符号尺寸和最终视口一致。
        self._ensure_pan_area()
        self._update_map_units_per_pixel()
        # 矢量按当前视野加余量裁剪；栅格由视口 worker 单独提供高清窗口。
        cull_bounds: Bounds | None = self._cull_bounds_for_current_view()
        new_render_state: dict[str, _LayerRenderState] = {}
        for z_value, current_layer in enumerate(layer_snapshot):
            signature: tuple = self._layer_signature(current_layer, z_value, cull_bounds)
            previous_state: _LayerRenderState | None = self._layer_render_state.get(
                current_layer.layer_id
            )
            if previous_state is not None and previous_state.signature == signature:
                # 图层、显示载荷、选择集、视域和堆叠顺序都未变化：整层复用。
                new_render_state[current_layer.layer_id] = previous_state
                self._layer_items[current_layer.layer_id] = previous_state.items
                continue
            if previous_state is not None:
                for item in previous_state.items:
                    self._scene.removeItem(item)
            rendered_items: list[QGraphicsItem] = self._render_layer_items(
                current_layer, float(z_value), cull_bounds
            )
            new_render_state[current_layer.layer_id] = _LayerRenderState(
                signature=signature, items=rendered_items
            )
            self._layer_items[current_layer.layer_id] = rendered_items
        # 移除快照中已不存在的图层图元。
        for removed_layer_id in set(self._layer_render_state) - set(new_render_state):
            for item in self._layer_render_state[removed_layer_id].items:
                self._scene.removeItem(item)
            self._layer_items.pop(removed_layer_id, None)
        self._layer_render_state = new_render_state
        queryable_ids: set[str] = set(self._queryable_layer_ids(snapshot))
        self._snap_engine.build_index(
            snapshot, queryable_ids, snapshot.active_layer_id
        )
        self._last_snapshot = snapshot
        # 更新选中要素集合。
        self._selected_fids.clear()
        for layer in snapshot.layers:
            for fid in layer.selected_feature_ids:
                self._selected_fids.add((layer.layer_id, fid))
        # 按当前视图比例应用图层的显示比例范围。
        self._apply_scale_ranges()
        # 记录本次重建时的地图单位，供缩放时判断是否需要再次重建。
        self._last_render_mupp = self._map_units_per_pixel
        self.schedule_viewport_refresh(force=True)

    def _render_layer_items(
        self,
        current_layer: LayerSnapshot,
        z_value: float,
        cull_bounds: Bounds | None,
    ) -> list[QGraphicsItem]:
        """按图层类型调用渲染器生成图元列表。"""
        if isinstance(current_layer.display_payload, RasterDisplayPayload):
            return [
                self._raster_renderer.render_layer(self._scene, current_layer, z_value)
            ]
        if isinstance(current_layer.display_payload, VectorDisplayPayload):
            return self._vector_renderer.render_layer(
                self._scene,
                current_layer,
                z_value,
                self._map_units_per_pixel,
                cull_bounds,
            )
        if isinstance(current_layer.layer, RasterLayer):
            # 兼容旧调用方直接构造未附带显示载荷的快照。
            return [
                self._raster_renderer.render_layer(self._scene, current_layer, z_value)
            ]
        return self._vector_renderer.render_layer(
            self._scene,
            current_layer,
            z_value,
            self._map_units_per_pixel,
            cull_bounds,
        )

    def _layer_signature(
        self,
        layer: LayerSnapshot,
        z_value: int,
        cull_bounds: Bounds | None,
    ) -> tuple:
        """生成图层渲染签名：签名一致时图元可直接复用。

        领域图层与显示载荷均不可变，对象身份相等即内容相等。矢量签名
        额外包含视域裁剪范围与地图单位比例，因为点符号尺寸和几何简化
        都按当时的视野计算；栅格载荷按地理变换放置，与视域无关。
        """
        signature: tuple = (
            id(layer.layer),
            id(layer.display_payload),
            layer.visible,
            layer.opacity,
            layer.blend_mode,
            layer.selected_feature_ids,
            z_value,
        )
        if isinstance(layer.layer, RasterLayer):
            return signature
        return signature + (cull_bounds, round(self._map_units_per_pixel, 12))

    def _clear_transient_items(self) -> None:
        """移除编辑顶点、数字化草图和捕捉标记等临时图元。"""
        for item in self._vertex_items:
            self._scene.removeItem(item)
        self._vertex_items.clear()
        for item in self._sketch_items:
            self._scene.removeItem(item)
        self._sketch_items.clear()
        if self._snap_marker is not None:
            self._scene.removeItem(self._snap_marker)
            self._snap_marker = None
        if self._snap_edge_marker is not None:
            self._scene.removeItem(self._snap_edge_marker)
            self._snap_edge_marker = None

    def _remove_all_layer_items(self) -> None:
        """移除全部图层图元并清空缓存状态。"""
        for render_state in self._layer_render_state.values():
            for item in render_state.items:
                self._scene.removeItem(item)
        self._layer_render_state.clear()
        self._layer_items.clear()

    def update_raster_viewport(self, payload: RasterDisplayPayload) -> None:
        """只替换一个栅格图层的视口图元，不重建矢量场景或改变视图。

        后台窗口可能只落在栅格的 NoData 区域。此时不能以全透明图元替换
        首屏预览，否则用户会看到图层短暂出现后消失。
        """
        if self._last_snapshot is None:
            return
        if not bool(payload.image_data[..., 3].any()):
            return
        layer_index = next(
            (
                index
                for index, item in enumerate(self._last_snapshot.layers)
                if item.layer_id == payload.layer_id
                and isinstance(item.layer, RasterLayer)
            ),
            None,
        )
        if layer_index is None:
            return
        old_items = self._layer_items.get(payload.layer_id, [])
        for item in old_items:
            self._scene.removeItem(item)
        snapshot_layer = replace(
            self._last_snapshot.layers[layer_index],
            display_payload=payload,
        )
        raster_item = self._raster_renderer.render_layer(
            self._scene,
            snapshot_layer,
            float(layer_index),
        )
        self._layer_items[payload.layer_id] = [raster_item]
        # 视口高清图元替换了缓存中的预览图元，同步增量重建状态，
        # 让后续 set_snapshot 在签名一致时继续复用高清图元。
        render_state = self._layer_render_state.get(payload.layer_id)
        if render_state is not None:
            render_state.items = [raster_item]
        layers = list(self._last_snapshot.layers)
        layers[layer_index] = snapshot_layer
        self._last_snapshot = replace(self._last_snapshot, layers=tuple(layers))
        self._apply_scale_ranges()

    def schedule_viewport_refresh(self, *, force: bool = False) -> None:
        """防抖请求当前视口，连续缩放和平移期间不执行文件 I/O。"""
        if force:
            self._last_viewport_key = None
        if self._map_scene_rect is not None:
            self._viewport_timer.start()

    def _emit_viewport_changed(self) -> None:
        """将可见场景矩形转换为正常 Y 轴方向的地图范围。"""
        visible = self._visible_scene_rect()
        if visible.width() <= 0 or visible.height() <= 0:
            return
        bounds: Bounds = (
            visible.left(),
            -visible.bottom(),
            visible.right(),
            -visible.top(),
        )
        viewport_size = (
            max(self.viewport().width(), 1),
            max(self.viewport().height(), 1),
        )
        key = tuple(round(value, 9) for value in bounds) + viewport_size
        if key == self._last_viewport_key:
            return
        self._last_viewport_key = key
        self.viewport_changed.emit(bounds, viewport_size)
        # 平移不重建矢量；越出裁剪余量后按新视野重渲染，保证新暴露区域有图元。
        if (
            self._last_snapshot is not None
            and self._last_snapshot.layers
            and self._needs_cull_refresh(visible)
        ):
            self.set_snapshot(self._last_snapshot)

    def _cull_bounds_for_current_view(self) -> Bounds | None:
        """计算当前视口外扩后的矢量渲染裁剪范围（地图坐标，Y 向上）。

        返回:
            (min_x, min_y, max_x, max_y)；尚未建立地图范围或视口退化时返回
            None，此时渲染全部要素。

        状态变化:
            同时把裁剪范围记录到 _last_cull_scene_rect，供导航越界判断。
        """
        if self._map_scene_rect is None:
            self._last_cull_scene_rect = None
            return None
        viewport_rect: QRectF = self._visible_scene_rect()
        if viewport_rect.width() <= 0.0 or viewport_rect.height() <= 0.0:
            return None
        margin_x: float = viewport_rect.width() * _CULL_VIEWPORT_MARGIN
        margin_y: float = viewport_rect.height() * _CULL_VIEWPORT_MARGIN
        cull_rect: QRectF = viewport_rect.adjusted(-margin_x, -margin_y, margin_x, margin_y)
        self._last_cull_scene_rect = cull_rect
        return (
            cull_rect.left(),
            -cull_rect.bottom(),
            cull_rect.right(),
            -cull_rect.top(),
        )

    def _needs_cull_refresh(self, viewport_rect: QRectF) -> bool:
        """判断视口是否已越出矢量裁剪余量，需要按新视野重渲染。

        余量按半个裁剪边距收缩为"核心区"：视口完全位于核心区内时平移
        后暴露的区域仍被现有图元覆盖，无需重建。
        """
        if self._last_snapshot is None or not self._last_snapshot.layers:
            return False
        cull_rect = self._last_cull_scene_rect
        if cull_rect is None:
            return False
        margin_x: float = viewport_rect.width() * _CULL_VIEWPORT_MARGIN / 2.0
        margin_y: float = viewport_rect.height() * _CULL_VIEWPORT_MARGIN / 2.0
        core_rect: QRectF = cull_rect.adjusted(margin_x, margin_y, -margin_x, -margin_y)
        if core_rect.width() <= 0.0 or core_rect.height() <= 0.0:
            return True
        return not core_rect.contains(viewport_rect)

    def capture_view_state(self) -> MapViewState:
        """捕获当前地图中心和相对于全图的缩放比例。"""
        center: QPointF = self.mapToScene(self.viewport().rect().center())
        # 场景 Y 轴为屏幕向下，工程中的地图坐标仍使用向上为正的约定。
        return MapViewState(
            center_x=center.x(),
            center_y=-center.y(),
            zoom_percent=self._zoom_percent,
        )

    def capture_view_extent(self) -> tuple[float, float, float, float] | None:
        """返回当前可见范围（地图坐标，Y 向上），用于布局视图同步。

        返回值:
            (center_x, center_y, extent_w, extent_h) 元组；
            无数据时返回 None。
        """
        vp = self.viewport()
        top_left = self.mapToScene(0, 0)
        bottom_right = self.mapToScene(vp.width(), vp.height())
        if top_left == bottom_right:
            return None
        # 场景 Y 向下，翻转得到地图坐标中心
        extent_w: float = abs(bottom_right.x() - top_left.x())
        extent_h: float = abs(bottom_right.y() - top_left.y())
        center_x: float = (top_left.x() + bottom_right.x()) / 2.0
        center_y: float = -(top_left.y() + bottom_right.y()) / 2.0
        return (center_x, center_y, extent_w, extent_h)

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
        # 工程恢复会改变视图变换；必须按恢复后的视野重建点符号，
        # 否则点符号仍沿用全图适配时的地图单位尺寸，可能铺满整个画布。
        self._refresh_rendering_for_current_view()
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

    def set_measure_length_tool(self) -> None:
        """切换到临时线测量工具，不修改任何图层。"""
        self._deactivate_all_tools()
        self._digitize_mode = "measure_line"
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.tool_changed.emit("measure_length")

    def set_measure_area_tool(self) -> None:
        """切换到临时面测量工具，不修改任何图层。"""
        self._deactivate_all_tools()
        self._digitize_mode = "measure_polygon"
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.tool_changed.emit("measure_area")

    def set_vertex_edit_tool(
        self, geometry: BaseGeometry, layer_id: str = "", fid: object = None,
        shared_topology: dict | None = None,
        linked_features: dict | None = None,
    ) -> None:
        """进入顶点编辑模式，为指定几何显示可交互顶点标记。

        参数:
            geometry: 待编辑的 Shapely 几何对象。
            layer_id: 要素所属图层 ID（用于实时更新渲染图元）。
            fid: 要素编号。
            shared_topology: 共享顶点映射 {idx: [(fid, other_idx), ...]}。
            linked_features: 关联要素坐标快照 {fid: [(x,y), ...]}。
        """
        if geometry.geom_type == "GeometryCollection":
            raise ValueError("GeometryCollection 暂不支持顶点编辑。")
        self._deactivate_all_tools()
        self._vertex_edit_active = True
        self._edit_geometry = geometry
        self._edit_layer_id: str = layer_id
        self._edit_fid: object = fid
        if shared_topology is not None:
            self._shared_topology = shared_topology
            self._linked_features = linked_features or {}
            self._topology_layer_id = layer_id
        else:
            self._shared_topology.clear()
            self._linked_features.clear()
            self._topology_layer_id = ""
        self._vertex_drag_idx = -1
        self._hovered_vertex = -1
        self._edit_mode = "drag_vertex"
        editable_vertices = iter_vertices(geometry)
        self._vertex_addresses = [address for address, _coordinate in editable_vertices]
        self._vertex_coords = [coordinate for _address, coordinate in editable_vertices]
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

    def set_move_feature_tool(
        self, geometry: BaseGeometry, layer_id: str = "", fid: object = None
    ) -> None:
        """进入整要素移动模式。

        参数:
            geometry: 待移动的 Shapely 几何对象。
            layer_id: 要素所属图层 ID。
            fid: 要素编号。
        """
        self._deactivate_all_tools()
        self._move_active = True
        self._move_geometry = geometry
        self._move_original_geometry = geometry
        self._move_gesture_geometry = geometry
        self._move_layer_id = layer_id
        self._move_fid = fid
        self._move_start_map = None
        self._move_total_dx = 0.0
        self._move_total_dy = 0.0
        # 创建半透明预览图元。
        path: QPainterPath = self._geometry_to_preview_path(geometry)
        self._move_preview_item = QGraphicsPathItem(path)
        self._style_preview_item(self._move_preview_item, geometry, QColor("#d97706"))
        self._move_preview_item.setZValue(100)
        self._scene.addItem(self._move_preview_item)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.tool_changed.emit("move_feature")

    def configure_topology(
        self,
        layer_id: str,
        shared_topology: dict[int, list[tuple[object, int]]],
        linked_features: dict[
            object,
            tuple[BaseGeometry, list[VertexAddress], list[tuple[float, float]]],
        ],
    ) -> None:
        """显式配置当前顶点会话的同图层拓扑联动。"""
        if not self._vertex_edit_active:
            return
        self._shared_topology = shared_topology
        self._linked_features = linked_features
        self._topology_layer_id = layer_id if shared_topology else ""
        if self._edit_geometry is not None:
            self._rebuild_vertex_markers(self._edit_geometry)

    def _commit_move(self) -> None:
        """请求应用整要素移动；真正写回由编辑控制器执行。"""
        if self._move_geometry is None:
            return
        self.edit_apply_requested.emit()

    def set_transform_tool(
        self, geometry: BaseGeometry, mode: str,
        layer_id: str = "", fid: object = None,
    ) -> None:
        """进入要素变换模式（旋转或缩放）。

        参数:
            geometry: 待变换的 Shapely 几何对象。
            mode: "rotate" 或 "scale"。
            layer_id: 要素所属图层 ID。
            fid: 要素编号。
        """
        if geometry.geom_type in ("Point", "MultiPoint"):
            raise ValueError("点要素不支持旋转或缩放，请使用移动工具。")
        self._deactivate_all_tools()
        self._transform_active = True
        self._transform_mode = mode
        self._transform_geometry = geometry
        self._transform_original_geometry = geometry
        self._transform_gesture_geometry = geometry
        self._transform_layer_id = layer_id
        self._transform_fid = fid
        self._transform_angle = 0.0
        self._transform_scale = 1.0
        self._transform_gesture_angle = 0.0
        self._transform_gesture_scale = 1.0
        centroid = geometry.centroid
        self._transform_centroid = (centroid.x, centroid.y)
        self._transform_pivot_snap_kind = ""
        # 创建预览。
        path: QPainterPath = self._geometry_to_preview_path(geometry)
        self._transform_preview_item = QGraphicsPathItem(path)
        self._style_preview_item(
            self._transform_preview_item, geometry, QColor("#7c3aed")
        )
        self._transform_preview_item.setZValue(100)
        self._scene.addItem(self._transform_preview_item)
        # 绘制质心十字标记。
        cx, cy = self._transform_centroid
        marker: QPainterPath = self._transform_guide_path()
        r: float = max(self._map_units_per_pixel * 6.0, 1e-6)
        marker.moveTo(cx - r, -cy)
        marker.lineTo(cx + r, -cy)
        marker.moveTo(cx, -(cy - r))
        marker.lineTo(cx, -(cy + r))
        marker_item: QGraphicsPathItem = QGraphicsPathItem(marker)
        marker_item.setPen(QPen(QColor("#7c3aed"), 0))
        marker_item.setZValue(101)
        marker_item.setData(0, "transform_marker")
        self._scene.addItem(marker_item)
        self._vertex_items.append(marker_item)
        self._transform_guide_item = marker_item
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.CrossCursor)
        tool_id: str = f"transform_{mode}"
        self.tool_changed.emit(tool_id)

    def _nearest_transform_vertex(
        self, screen_pos: QPoint, tolerance_pixels: float = 10.0
    ) -> tuple[float, float] | None:
        """返回屏幕容差内离光标最近的变换几何顶点。"""
        geometry = self._transform_geometry
        if geometry is None:
            return None
        try:
            vertices = iter_vertices(geometry)
        except ValueError:
            return None
        nearest: tuple[float, float] | None = None
        nearest_distance = tolerance_pixels
        for _address, (x, y) in vertices:
            vertex_screen = self.mapFromScene(QPointF(float(x), -float(y)))
            distance = math.hypot(
                float(vertex_screen.x() - screen_pos.x()),
                float(vertex_screen.y() - screen_pos.y()),
            )
            if distance <= nearest_distance:
                nearest_distance = distance
                nearest = (float(x), float(y))
        return nearest

    def _update_transform_pivot_from_cursor(self, screen_pos: QPoint) -> None:
        """拖动参考点时执行顶点自动捕捉或自由定位。"""
        pivot = self._screen_to_map_point(screen_pos)
        disable_snap = bool(
            QApplication.keyboardModifiers() & Qt.KeyboardModifier.AltModifier
        )
        snapped = None if disable_snap else self._nearest_transform_vertex(screen_pos)
        if snapped is None:
            self._transform_centroid = (pivot.x, pivot.y)
            self._transform_pivot_snap_kind = ""
            self._clear_transform_pivot_snap_marker()
        else:
            self._transform_centroid = snapped
            self._transform_pivot_snap_kind = "vertex"
            self._show_transform_pivot_snap_marker(snapped)
        self._refresh_transform_guide()

    def _show_transform_pivot_snap_marker(self, coordinate: tuple[float, float]) -> None:
        """显示参考点已吸附到顶点的视觉提示。"""
        x, y = coordinate
        radius = max(self._map_units_per_pixel * 8.0, 1e-9)
        path = QPainterPath()
        path.addEllipse(QPointF(x, -y), radius, radius)
        if self._transform_pivot_snap_item is None:
            item = QGraphicsPathItem(path)
            pen = QPen(QColor("#0ea5e9"), 0)
            pen.setCosmetic(True)
            item.setPen(pen)
            item.setBrush(Qt.BrushStyle.NoBrush)
            item.setZValue(103)
            item.setData(0, "transform_pivot_snap")
            self._scene.addItem(item)
            self._vertex_items.append(item)
            self._transform_pivot_snap_item = item
        else:
            self._transform_pivot_snap_item.prepareGeometryChange()
            self._transform_pivot_snap_item.setPath(path)

    def _clear_transform_pivot_snap_marker(self) -> None:
        """移除参考点捕捉提示。"""
        if self._transform_pivot_snap_item is None:
            return
        self._scene.removeItem(self._transform_pivot_snap_item)
        try:
            self._vertex_items.remove(self._transform_pivot_snap_item)
        except ValueError:
            pass
        self._transform_pivot_snap_item = None

    def set_split_tool(
        self, layer_id: str, fid: object, geometry: BaseGeometry,
    ) -> None:
        """进入要素拆分模式——绘制切割线。

        复用数字化线的 sketch 系统，以 _split_active 标志区分。
        """
        self._deactivate_all_tools()
        self._split_active = True
        self._split_layer_id = layer_id
        self._split_fid = fid
        self._split_target_geometry = geometry
        self._digitize_mode = "line"
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.tool_changed.emit("split_feature")

    def set_static_edit_preview(
        self,
        geometry: BaseGeometry,
        *,
        tool_id: str,
        working_geometry: BaseGeometry | None = None,
    ) -> None:
        """显示拆分、合并、简化或平滑结果的只读几何预览。"""
        self._deactivate_all_tools()
        self._static_edit_active = True
        self._static_edit_geometry = (
            working_geometry if working_geometry is not None else geometry
        )
        item = QGraphicsPathItem(self._geometry_to_preview_path(geometry))
        self._style_preview_item(item, geometry, QColor("#7c3aed"))
        item.setZValue(100)
        self._scene.addItem(item)
        self._move_preview_item = item
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.tool_changed.emit(tool_id)

    def _commit_transform(self) -> None:
        """请求应用变换；真正写回由编辑控制器执行。"""
        if self._transform_geometry is None:
            return
        self.edit_apply_requested.emit()

    def _geometry_to_path(self, geometry: BaseGeometry) -> QPainterPath:
        """将 Shapely 几何转换为场景坐标的 QPainterPath（Y 轴反转）。"""

        def _append_coords(
            path: QPainterPath, coords, close: bool = False
        ) -> None:
            if not coords:
                return
            it = iter(coords)
            first = next(it)
            path.moveTo(float(first[0]), -float(first[1]))
            for c in it:
                path.lineTo(float(c[0]), -float(c[1]))
            if close:
                path.closeSubpath()

        path: QPainterPath = QPainterPath()
        geom_type: str = geometry.geom_type

        if geom_type == "Point":
            path.addEllipse(
                QPointF(geometry.x, -geometry.y), 4.0, 4.0
            )
        elif geom_type == "LineString":
            _append_coords(path, list(geometry.coords))
        elif geom_type == "Polygon":
            path.setFillRule(Qt.FillRule.OddEvenFill)
            _append_coords(path, list(geometry.exterior.coords), close=True)
            for interior in geometry.interiors:
                _append_coords(path, list(interior.coords), close=True)
        elif geom_type in (
            "MultiPoint", "MultiLineString", "MultiPolygon", "GeometryCollection",
        ):
            for member in geometry.geoms:
                sub_path: QPainterPath = self._geometry_to_path(member)
                path.addPath(sub_path)
        return path

    def _geometry_to_preview_path(self, geometry: BaseGeometry) -> QPainterPath:
        """按几何类型创建预览路径，点标记保持当前屏幕可见尺寸。"""
        if geometry.geom_type == "Point":
            radius = max(self._map_units_per_pixel * 6.0, 1e-9)
            path = QPainterPath()
            path.addEllipse(QPointF(geometry.x, -geometry.y), radius, radius)
            return path
        if geometry.geom_type == "MultiPoint":
            path = QPainterPath()
            for point in geometry.geoms:
                path.addPath(self._geometry_to_preview_path(point))
            return path
        return self._geometry_to_path(geometry)

    @staticmethod
    def _style_preview_item(
        item: QGraphicsPathItem, geometry: BaseGeometry, color: QColor
    ) -> None:
        """分别设置点、线、面的预览样式，避免线要素被错误填充。"""
        outline = QColor(color)
        outline.setAlpha(230)
        pen = QPen(outline, 2.0, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        item.setPen(pen)
        if geometry.geom_type in ("Polygon", "MultiPolygon"):
            fill = QColor(color)
            fill.setAlpha(90)
            item.setBrush(QBrush(fill))
        elif geometry.geom_type in ("Point", "MultiPoint"):
            fill = QColor(color)
            fill.setAlpha(210)
            item.setBrush(QBrush(fill))
        else:
            item.setBrush(Qt.BrushStyle.NoBrush)

    def _transform_guide_path(self) -> QPainterPath:
        """构造包围框、四角控制柄和可移动中心点的辅助路径。"""
        geometry = self._transform_geometry
        path = QPainterPath()
        if geometry is None or geometry.is_empty:
            return path
        min_x, min_y, max_x, max_y = geometry.bounds
        path.addRect(QRectF(min_x, -max_y, max_x - min_x, max_y - min_y))
        radius = max(self._map_units_per_pixel * 5.0, 1e-9)
        for x, y in ((min_x, min_y), (min_x, max_y), (max_x, min_y), (max_x, max_y)):
            path.addRect(QRectF(x - radius, -y - radius, radius * 2, radius * 2))
        cx, cy = self._transform_centroid
        path.addEllipse(QPointF(cx, -cy), radius, radius)
        return path

    def _refresh_transform_guide(self) -> None:
        """刷新变换包围框和中心点。"""
        if self._transform_guide_item is not None:
            self._transform_guide_item.prepareGeometryChange()
            self._transform_guide_item.setPath(self._transform_guide_path())

    def current_edit_geometry(self) -> BaseGeometry | None:
        """返回当前画布编辑工具生成的工作几何，不触发提交。"""
        if self._vertex_edit_active:
            return self._commit_vertex_edit()
        if self._move_active:
            return self._move_geometry
        if self._transform_active:
            return self._transform_geometry
        if self._static_edit_active:
            return self._static_edit_geometry
        return None

    def apply_edit_parameters(self, parameters: dict[str, float]) -> None:
        """按上下文栏数值参数更新移动、旋转或缩放预览。"""
        if self._move_active and self._move_original_geometry is not None:
            dx = float(parameters.get("dx", 0.0))
            dy = float(parameters.get("dy", 0.0))
            geometry = affinity.translate(self._move_original_geometry, dx, dy)
            self._move_geometry = geometry
            self._move_total_dx = dx
            self._move_total_dy = dy
            if self._move_preview_item is not None:
                self._move_preview_item.setPath(self._geometry_to_preview_path(geometry))
            self.edit_preview_changed.emit(geometry, {"dx": dx, "dy": dy})
            return
        if self._transform_active and self._transform_original_geometry is not None:
            if self._transform_mode == "rotate":
                angle = float(parameters.get("angle", 0.0))
                geometry = affinity.rotate(
                    self._transform_original_geometry,
                    angle,
                    origin=self._transform_centroid,
                )
                self._transform_angle = angle
                values = {"angle": angle}
            else:
                scale = max(0.01, min(float(parameters.get("scale", 1.0)), 100.0))
                geometry = affinity.scale(
                    self._transform_original_geometry,
                    xfact=scale,
                    yfact=scale,
                    origin=self._transform_centroid,
                )
                self._transform_scale = scale
                values = {"scale": scale}
            self._transform_geometry = geometry
            if self._transform_preview_item is not None:
                self._transform_preview_item.setPath(self._geometry_to_preview_path(geometry))
            self._refresh_transform_guide()
            return
        if self._static_edit_active:
            self._static_edit_geometry = geometry
            if self._move_preview_item is not None:
                self._move_preview_item.setPath(self._geometry_to_preview_path(geometry))
            self.edit_preview_changed.emit(geometry, values)

    def set_edit_preview_geometry(self, geometry: BaseGeometry) -> None:
        """将会话撤销/重做结果同步回当前画布工具。"""
        if self._vertex_edit_active:
            editable_vertices = iter_vertices(geometry)
            self._vertex_addresses = [address for address, _coordinate in editable_vertices]
            self._vertex_coords = [coordinate for _address, coordinate in editable_vertices]
            base_geometry = self._edit_geometry if self._edit_geometry is not None else geometry
            self._rebuild_vertex_markers(base_geometry)
            self._update_feature_item_path()
            return
        if self._move_active:
            self._move_geometry = geometry
            if self._move_preview_item is not None:
                self._move_preview_item.setPath(self._geometry_to_preview_path(geometry))
            return
        if self._transform_active:
            self._transform_geometry = geometry
            if self._transform_preview_item is not None:
                self._transform_preview_item.setPath(self._geometry_to_preview_path(geometry))
            self._refresh_transform_guide()

    def _geometry_hit(self, geometry: BaseGeometry, point: Point, pixels: float = 10.0) -> bool:
        """按固定像素容差判断鼠标是否命中目标几何。"""
        tolerance = max(self._map_units_per_pixel * pixels, 1e-12)
        return bool(geometry.distance(point) <= tolerance)

    def _rebuild_vertex_markers(self, geometry: BaseGeometry) -> None:
        """原地更新顶点标记位置，不删建以避免视觉闪烁。

        仅在首次调用时从几何提取坐标；后续调用使用已有 _vertex_coords
        （可能已被拖拽修改）。
        """
        if not self._vertex_addresses:
            editable_vertices = iter_vertices(geometry)
            self._vertex_addresses = [address for address, _coordinate in editable_vertices]
            self._vertex_coords = [coordinate for _address, coordinate in editable_vertices]
        coords: list[tuple[float, float]] = self._vertex_coords
        marker_size: float = max(self._map_units_per_pixel * 7.0, 1e-6)

        # 移除旧的延伸标记和悬停高亮（顶点标记本身保留并更新）。
        existing_vertices: list[QGraphicsEllipseItem] = []
        existing_preview: QGraphicsPathItem | None = None
        for item in self._vertex_items:
            tag = item.data(0)
            if tag == "vertex" and isinstance(item, QGraphicsEllipseItem):
                existing_vertices.append(item)
            elif tag == "preview" and isinstance(item, QGraphicsPathItem):
                if existing_preview is None:
                    existing_preview = item
                else:
                    self._scene.removeItem(item)
            else:
                self._scene.removeItem(item)
        self._vertex_items.clear()

        # 原地更新或创建顶点标记。
        # 共享顶点用红色(#EF4444)，选中用金色(#FFD700)，普通用品红。
        selected_set: set[int] = self._selected_vertex_indices
        shared_set: set[int] = set(self._shared_topology.keys())
        for i, (mx, my) in enumerate(coords):
            if i in shared_set:
                color: str = "#EF4444"
            elif i in selected_set:
                color = "#FFD700"
            else:
                color = sketch_color().name()
            if i < len(existing_vertices):
                item = existing_vertices[i]
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

        # 移除不再需要的多余标记（顶点减少了）。
        for extra in existing_vertices[len(coords):]:
            self._scene.removeItem(extra)

        # 端点延伸标记（仅线要素）。
        if geometry.geom_type == "LineString" and len(coords) >= 2:
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
                preview_item = existing_preview or QGraphicsPathItem(preview_path)
                preview_item.prepareGeometryChange()
                preview_item.setPath(preview_path)
                preview_item.setData(0, "preview")
                prev_pen2: QPen = QPen(sketch_color(), 1.5, Qt.PenStyle.DashLine)
                prev_pen2.setCosmetic(True)
                preview_item.setPen(prev_pen2)
                if geometry.geom_type in ("Polygon", "MultiPolygon"):
                    preview_fill = QColor(sketch_color())
                    preview_fill.setAlpha(70)
                    preview_item.setBrush(QBrush(preview_fill))
                else:
                    preview_item.setBrush(Qt.BrushStyle.NoBrush)
                preview_item.setZValue(2998)
                if existing_preview is None:
                    self._scene.addItem(preview_item)
                self._vertex_items.append(preview_item)
        elif existing_preview is not None:
            self._scene.removeItem(existing_preview)

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
        """从几何中提取全部部件和面内环的可编辑顶点。"""
        return [coordinate for _address, coordinate in iter_vertices(geometry)]

    def _delete_vertex_at(self, index: int) -> bool:
        """删除一个顶点并重排同部件同环内的稳定地址。"""
        if not 0 <= index < len(self._vertex_addresses):
            return False
        target = self._vertex_addresses[index]
        group_indices = [
            item_index
            for item_index, address in enumerate(self._vertex_addresses)
            if address.part_index == target.part_index and address.ring_index == target.ring_index
        ]
        minimum = 3 if self._edit_geometry and self._is_polygon_type(self._edit_geometry.geom_type) else 2
        if len(group_indices) <= minimum:
            return False
        del self._vertex_addresses[index]
        del self._vertex_coords[index]
        for item_index, address in enumerate(self._vertex_addresses):
            if (
                address.part_index == target.part_index
                and address.ring_index == target.ring_index
                and address.vertex_index > target.vertex_index
            ):
                self._vertex_addresses[item_index] = VertexAddress(
                    address.part_index, address.ring_index, address.vertex_index - 1
                )
        return True

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
            current_address = self._vertex_addresses[i]
            next_address = self._vertex_addresses[i + 1]
            if (
                current_address.part_index != next_address.part_index
                or current_address.ring_index != next_address.ring_index
            ):
                continue
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
    def detect_shared_topology(
        edit_geometry: BaseGeometry,
        features: tuple,
        edit_fid: object,
        tolerance: float = 1e-8,
    ) -> tuple[
        dict[int, list[tuple[object, int]]],
        dict[object, tuple[BaseGeometry, list[VertexAddress], list[tuple[float, float]]]],
    ]:
        """检测同一图层中与编辑要素共享顶点的相邻要素。

        参数:
            edit_geometry: 正在编辑的要素几何。
            features: 同图层所有要素。
            edit_fid: 编辑要素的 FID（排除自身）。
            tolerance: 坐标匹配容差。

        返回:
            (shared_topology, linked_features):
            - shared_topology: {vertex_idx: [(fid, vertex_idx_in_other), ...]}
            - linked_features: {fid: coordinate_list}  关联要素的初始坐标快照
        """
        if edit_geometry.geom_type not in (
            "LineString",
            "MultiLineString",
            "Polygon",
            "MultiPolygon",
        ):
            return {}, {}
        my_vertices = iter_vertices(edit_geometry)
        my_coords = [coordinate for _address, coordinate in my_vertices]

        # 为其他要素的顶点建立空间索引（分桶）。
        bucket_size: float = max(tolerance * 10, 1e-6)
        bucket: dict[tuple[int, int], list[tuple[object, int, float, float]]] = {}
        linked: dict[
            object,
            tuple[BaseGeometry, list[VertexAddress], list[tuple[float, float]]],
        ] = {}
        for f in features:
            if f.fid == edit_fid or f.geometry.geom_type not in (
                "LineString",
                "MultiLineString",
                "Polygon",
                "MultiPolygon",
            ):
                continue
            other_vertices = iter_vertices(f.geometry)
            other_addresses = [address for address, _coordinate in other_vertices]
            other_coords = [coordinate for _address, coordinate in other_vertices]
            linked[f.fid] = (f.geometry, other_addresses, other_coords)
            for oi, (ox, oy) in enumerate(other_coords):
                bk = (math.floor(ox / bucket_size), math.floor(oy / bucket_size))
                bucket.setdefault(bk, []).append((f.fid, oi, ox, oy))

        # 为每个编辑顶点查找共享。
        shared: dict[int, list[tuple[object, int]]] = {}
        affected_fids: set[object] = set()
        for mi, (mx, my) in enumerate(my_coords):
            bk = (math.floor(mx / bucket_size), math.floor(my / bucket_size))
            candidates: list[tuple[object, int, float, float]] = []
            for dbx in (-1, 0, 1):
                for dby in (-1, 0, 1):
                    candidates.extend(bucket.get((bk[0] + dbx, bk[1] + dby), []))
            matches: list[tuple[object, int]] = []
            for fid_other, oi, ox, oy in candidates:
                dist: float = ((mx - ox) ** 2 + (my - oy) ** 2) ** 0.5
                if dist <= tolerance:
                    matches.append((fid_other, oi))
                    affected_fids.add(fid_other)
            if matches:
                shared[mi] = matches
        return shared, {fid: linked[fid] for fid in affected_fids}

    @staticmethod
    def _can_delete_vertex(geom_type: str, vertex_count: int) -> bool:
        """判断是否允许删除一个顶点（保留最少顶点数）。"""
        if geom_type in ("Point", "MultiPoint"):
            return False
        if geom_type in ("LineString", "MultiLineString"):
            return vertex_count > 2
        return vertex_count > 3  # Polygon, MultiPolygon

    def _commit_vertex_edit(self) -> BaseGeometry | None:
        """按稳定顶点地址重建同类型几何，保留所有部件和面内环。"""
        if self._edit_geometry is None:
            return None
        try:
            return rebuild_geometry(
                self._edit_geometry,
                tuple(zip(self._vertex_addresses, self._vertex_coords, strict=True)),
            )
        except (IndexError, ValueError):
            return None

    def _commit_topology_edit(self) -> dict:
        """构建所有受影响要素的新几何并返回 {fid: new_geometry}。

        包含编辑要素本身和所有共享边界的关联要素。
        """
        result: dict = {}
        # 编辑要素自身。
        new_self: BaseGeometry | None = self._commit_vertex_edit()
        if new_self is not None and self._edit_fid is not None:
            result[self._edit_fid] = new_self
        # 关联要素。
        for other_fid, (original, addresses, coords) in self._linked_features.items():
            try:
                result[other_fid] = rebuild_geometry(
                    original, tuple(zip(addresses, coords, strict=True))
                )
            except (IndexError, ValueError):
                continue
        return result

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
        self._vertex_addresses.clear()
        self._hovered_vertex = -1
        self._midpoint_coords.clear()
        self._selected_vertex_indices.clear()
        for item in self._vertex_items:
            self._scene.removeItem(item)
        self._vertex_items.clear()
        # 整要素移动。
        self._move_active = False
        if self._move_preview_item is not None:
            self._scene.removeItem(self._move_preview_item)
            self._move_preview_item = None
        self._move_geometry = None
        self._move_original_geometry = None
        self._move_gesture_geometry = None
        self._move_layer_id = ""
        self._move_fid = None
        self._move_start_map = None
        self._move_total_dx = 0.0
        self._move_total_dy = 0.0
        # 变换。
        self._transform_active = False
        if self._transform_preview_item is not None:
            self._scene.removeItem(self._transform_preview_item)
            self._transform_preview_item = None
        self._transform_geometry = None
        self._transform_original_geometry = None
        self._transform_gesture_geometry = None
        self._transform_layer_id = ""
        self._transform_fid = None
        self._transform_start_pos = None
        self._transform_angle = 0.0
        self._transform_scale = 1.0
        self._transform_gesture_angle = 0.0
        self._transform_gesture_scale = 1.0
        self._transform_guide_item = None
        self._transform_pivot_snap_item = None
        self._transform_pivot_snap_kind = ""
        self._transform_pivot_dragging = False
        # 拆分。
        self._split_active = False
        self._split_layer_id = ""
        self._split_fid = None
        self._split_target_geometry = None
        self._static_edit_active = False
        self._static_edit_geometry = None
        # 拓扑。
        self._shared_topology.clear()
        self._linked_features.clear()
        self._topology_layer_id = ""

    def set_snapping(self, enabled: bool) -> None:
        """启用或禁用顶点/边捕捉。

        参数:
            enabled: True 时数字化光标自动吸附到附近已有顶点或边。
        """
        self._snap_engine.enabled = enabled
        if not enabled:
            self._clear_snap_marker()

    @property
    def snap_engine(self) -> SnappingEngine:
        """暴露捕捉引擎供主窗口配置容差、捕捉类型等。"""
        return self._snap_engine

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
        """递归收集几何中所有顶点。（保留供共享拓扑编辑等内部使用。）"""
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

    def _show_snap_marker(self, result: SnapResult) -> None:
        """按捕捉类型绘制不同标记。

        顶点捕捉：实心方块；边捕捉：空心菱形 + 边高亮虚线。

        参数:
            result: 捕捉命中结果。
        """
        self._clear_snap_marker()
        px, py = result.map_point.x, result.map_point.y
        s: float = self._map_units_per_pixel * 6.0
        path: QPainterPath = QPainterPath()

        if result.snap_type == "vertex":
            # 实心方块。
            path.addRect(QRectF(px - s, -py - s, s * 2, s * 2))
            item: QGraphicsPathItem = QGraphicsPathItem(path)
            pen: QPen = QPen(snap_color(), 1.5)
            pen.setCosmetic(True)
            item.setPen(pen)
            item.setBrush(QBrush(snap_color()))
        else:
            # 边捕捉：空心菱形。
            diamond: QPainterPath = QPainterPath()
            diamond.moveTo(px, -py - s)
            diamond.lineTo(px + s, -py)
            diamond.lineTo(px, -py + s)
            diamond.lineTo(px - s, -py)
            diamond.closeSubpath()
            item = QGraphicsPathItem(diamond)
            pen = QPen(snap_edge_color(), 1.5)
            pen.setCosmetic(True)
            item.setPen(pen)
            item.setBrush(Qt.BrushStyle.NoBrush)

            # 边高亮虚线。
            if len(result.source_coords) >= 2:
                (ax, ay), (bx, by) = result.source_coords[:2]
                edge_path: QPainterPath = QPainterPath()
                edge_path.moveTo(ax, -ay)
                edge_path.lineTo(bx, -by)
                edge_pen: QPen = QPen(snap_edge_color(), 1.5, Qt.PenStyle.DashLine)
                edge_pen.setCosmetic(True)
                edge_item: QGraphicsPathItem = QGraphicsPathItem(edge_path)
                edge_item.setPen(edge_pen)
                edge_item.setZValue(2999)
                self._scene.addItem(edge_item)
                self._snap_edge_marker = edge_item

        item.setZValue(3000)
        self._scene.addItem(item)
        self._snap_marker = item

    def _snapped_position(self, screen_pos: QPoint) -> QPoint:
        """返回捕捉后的屏幕坐标；未启用或无命中时返回原坐标。"""
        snap_result: SnapResult | None = self._snap_engine.find_snap(
            self._screen_to_map_point(screen_pos),
            self._map_units_per_pixel,
            self._last_snapshot.active_layer_id if self._last_snapshot else None,
        )
        if snap_result is None:
            return screen_pos
        snap_scene: QPointF = QPointF(snap_result.map_point.x, -snap_result.map_point.y)
        return self.mapFromScene(snap_scene)

    def _update_feature_item_path(self) -> None:
        """刷新顶点编辑预览，不改动原始图层渲染项。"""
        for item in self._vertex_items:
            if item.data(0) == "preview" and isinstance(item, QGraphicsPathItem):
                item.prepareGeometryChange()
                item.setPath(self._build_preview_path())
                item.update()
                return

    def _build_preview_path(self) -> QPainterPath:
        """从当前顶点坐标构建预览折线/多边形路径。"""
        path: QPainterPath = QPainterPath()
        if self._edit_geometry is None:
            return path
        path.setFillRule(Qt.FillRule.OddEvenFill)
        grouped: dict[tuple[int, int], list[tuple[int, tuple[float, float]]]] = {}
        for address, coordinate in zip(
            self._vertex_addresses, self._vertex_coords, strict=False
        ):
            grouped.setdefault((address.part_index, address.ring_index), []).append(
                (address.vertex_index, coordinate)
            )
        polygon = self._edit_geometry.geom_type in ("Polygon", "MultiPolygon")
        for values in grouped.values():
            coords = [coordinate for _index, coordinate in sorted(values)]
            if not coords:
                continue
            if len(coords) == 1:
                radius = max(self._map_units_per_pixel * 5.0, 1e-9)
                path.addEllipse(QPointF(coords[0][0], -coords[0][1]), radius, radius)
                continue
            path.moveTo(coords[0][0], -coords[0][1])
            for mx, my in coords[1:]:
                path.lineTo(mx, -my)
            if polygon:
                path.closeSubpath()
        return path

    def _clear_snap_marker(self) -> None:
        """清除捕捉标记和边高亮。"""
        if self._snap_marker is not None:
            self._scene.removeItem(self._snap_marker)
            self._snap_marker = None
        if self._snap_edge_marker is not None:
            self._scene.removeItem(self._snap_edge_marker)
            self._snap_edge_marker = None

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
        if mode in ("line", "measure_line"):
            if len(coords) < 2:
                return None
            return LineString(coords)
        if mode in ("polygon", "measure_polygon"):
            if len(coords) < 3:
                return None
            # 确保闭合。
            if coords[0] != coords[-1]:
                coords.append(coords[0])
            return Polygon(coords)
        return None

    def _emit_completed_sketch(self, geometry: BaseGeometry) -> None:
        """按当前草图工具分发数字化或测量结果。"""
        if self._digitize_mode == "measure_line":
            self.measurement_completed.emit("length", geometry)
            self.set_pan_tool()
            return
        if self._digitize_mode == "measure_polygon":
            self.measurement_completed.emit("area", geometry)
            self.set_pan_tool()
            return
        if self._split_active:
            self.feature_split_requested.emit(geometry)
            return
        self.feature_digitized.emit(geometry)

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
            dot_pen: QPen = QPen(sketch_color(), 1)
            dot_pen.setCosmetic(True)
            dot_item.setPen(dot_pen)
            dot_item.setBrush(QBrush(sketch_color()))
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
        is_polygon: bool = self._digitize_mode in ("polygon", "measure_polygon") and len(all_pts) >= 3

        preview_path.moveTo(all_pts[0][0], -all_pts[0][1])
        for mx, my in all_pts[1:]:
            preview_path.lineTo(mx, -my)
        if is_polygon:
            preview_path.closeSubpath()

        preview_item: QGraphicsPathItem = QGraphicsPathItem(preview_path)
        prev_pen: QPen = QPen(sketch_color(), 1.5, Qt.PenStyle.DashLine)
        prev_pen.setCosmetic(True)
        preview_item.setPen(prev_pen)
        if is_polygon:
            preview_item.setBrush(
                QBrush(QColor(sketch_color().red(), sketch_color().green(), sketch_color().blue(), 40))
            )
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
        self._refresh_rendering_for_current_view()
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
        self._refresh_rendering_for_current_view()
        self._emit_view_scale()
        # 缩放至图层后即使缩放比例恰好与全图一致，也要读取当前图层范围的
        # 最新窗口；否则首次后台请求若落在 NoData 区域，画面会一直停留在旧预览。
        self.schedule_viewport_refresh(force=True)

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
        self._refresh_rendering_for_current_view()
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
        # 缩放后按需重建场景：若每像素地图单位变化不足 50%，
        # 已渲染的简化几何在视觉上不可分辨，跳过全量重建以避免卡顿。
        # _emit_view_scale() 已调用 _apply_scale_ranges() 更新图层显隐。
        if self._last_snapshot is not None and not self._vertex_edit_active:
            _MUPP_CHANGE_THRESHOLD: float = 0.5
            mupp_changed: bool = (
                self._last_render_mupp == 0.0
                or abs(self._map_units_per_pixel - self._last_render_mupp)
                / max(self._last_render_mupp, 1e-9)
                > _MUPP_CHANGE_THRESHOLD
            )
            if mupp_changed:
                self.setUpdatesEnabled(False)
                try:
                    self.set_snapshot(self._last_snapshot)
                finally:
                    self.setUpdatesEnabled(True)
        self.schedule_viewport_refresh()

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
                        if self._delete_vertex_at(hit_idx):
                            self._selected_vertex_indices.discard(hit_idx)
                            self._hovered_vertex = -1
                            self._rebuild_vertex_markers(self._edit_geometry)
                            self._update_feature_item_path()
                            preview = self._commit_vertex_edit()
                            if preview is not None:
                                self.edit_preview_changed.emit(preview, {})
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
                            self._vertex_addresses.insert(0, VertexAddress(0, 0, 0))
                            for address_index in range(1, len(self._vertex_addresses)):
                                address = self._vertex_addresses[address_index]
                                self._vertex_addresses[address_index] = VertexAddress(
                                    address.part_index,
                                    address.ring_index,
                                    address.vertex_index + 1,
                                )
                        else:
                            self._vertex_coords.append((click_pt.x, click_pt.y))
                            self._vertex_addresses.append(
                                VertexAddress(0, 0, len(self._vertex_coords) - 1)
                            )
                        self._rebuild_vertex_markers(self._edit_geometry)
                        preview = self._commit_vertex_edit()
                        if preview is not None:
                            self.edit_preview_changed.emit(preview, {})
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
                        if self._delete_vertex_at(idx):
                            self._selected_vertex_indices.discard(idx)
                            self._hovered_vertex = -1
                            self._rebuild_vertex_markers(self._edit_geometry)
                            preview = self._commit_vertex_edit()
                            if preview is not None:
                                self.edit_preview_changed.emit(preview, {})
                        break
                event.accept()
                return

            event.accept()
            return

        # ── 整要素移动 ──
        if self._move_active and self._move_geometry is not None:
            if event.button() == Qt.MouseButton.LeftButton:
                start = self._screen_to_map_point(event.position().toPoint())
                if not self._geometry_hit(self._move_geometry, start):
                    event.accept()
                    return
                self._move_start_map = start
                self._move_gesture_geometry = self._move_geometry
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return
            event.accept()
            return

        # ── 变换要素（旋转/缩放）──
        if self._transform_active and self._transform_geometry is not None:
            if event.button() == Qt.MouseButton.LeftButton:
                start_map = self._screen_to_map_point(event.position().toPoint())
                cx, cy = self._transform_centroid
                if math.hypot(start_map.x - cx, start_map.y - cy) <= (
                    self._map_units_per_pixel * 10.0
                ):
                    self._transform_pivot_dragging = True
                    self.setCursor(Qt.CursorShape.ClosedHandCursor)
                    event.accept()
                    return
                self._transform_start_pos = event.position().toPoint()
                self._transform_gesture_geometry = self._transform_geometry
                self._transform_gesture_angle = self._transform_angle
                self._transform_gesture_scale = self._transform_scale
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
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
                        self._emit_completed_sketch(geometry)
                else:
                    self._add_sketch_vertex(
                        self._snapped_position(event.position().toPoint())
                    )
                event.accept()
                return
            if event.button() == Qt.MouseButton.RightButton:
                geometry = self._finish_sketch()
                if geometry is not None:
                    self._emit_completed_sketch(geometry)
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
            self._digitize_mode in ("line", "polygon", "measure_line", "measure_polygon")
            and event.button() == Qt.MouseButton.LeftButton
        ):
            geometry: BaseGeometry | None = self._finish_sketch()
            if geometry is not None:
                self._emit_completed_sketch(geometry)
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
            # 拓扑联动：移动共享顶点时同步更新关联要素坐标。
            if self._shared_topology and self._linked_features:
                for i in move_indices:
                    if i in self._shared_topology:
                        for other_fid, other_idx in self._shared_topology[i]:
                            if other_fid in self._linked_features:
                                _original, _addresses, lc = self._linked_features[other_fid]
                                if other_idx < len(lc):
                                    ox, oy = lc[other_idx]
                                    lc[other_idx] = (ox + dx, oy + dy)
            # 重建标记和预览。
            self._rebuild_vertex_markers(self._edit_geometry)
            self._update_feature_item_path()
            preview = self._commit_vertex_edit()
            if preview is not None:
                self.edit_preview_changed.emit(preview, {})
            event.accept()
            return

        # ── 整要素移动拖拽 ──
        if self._move_active and self._move_start_map is not None:
            current_pt: Point = self._screen_to_map_point(
                event.position().toPoint()
            )
            move_dx: float = current_pt.x - self._move_start_map.x
            move_dy: float = current_pt.y - self._move_start_map.y
            # 忽略极小拖拽。
            if abs(move_dx) < 1e-9 and abs(move_dy) < 1e-9:
                event.accept()
                return
            base_geometry = (
                self._move_gesture_geometry
                if self._move_gesture_geometry is not None
                else self._move_original_geometry
            )
            translated: BaseGeometry = affinity.translate(base_geometry, move_dx, move_dy)
            self._move_geometry = translated
            total_dx = self._move_total_dx + move_dx
            total_dy = self._move_total_dy + move_dy
            if self._move_preview_item is not None:
                self._move_preview_item.prepareGeometryChange()
                self._move_preview_item.setPath(
                    self._geometry_to_preview_path(translated)
                )
            self.edit_preview_changed.emit(
                translated, {"dx": total_dx, "dy": total_dy}
            )
            event.accept()
            return

        # ── 变换要素拖拽 ──
        if self._transform_active and self._transform_pivot_dragging:
            self._update_transform_pivot_from_cursor(event.position().toPoint())
            event.accept()
            return

        if self._transform_active and self._transform_start_pos is not None:
            current_pos: QPoint = event.position().toPoint()
            gesture_geometry = (
                self._transform_gesture_geometry
                if self._transform_gesture_geometry is not None
                else self._transform_original_geometry
            )
            if gesture_geometry is None:
                event.accept()
                return
            cx, cy = self._transform_centroid
            # 质心在场景中的像素位置。
            centroid_scene: QPointF = QPointF(cx, -cy)
            centroid_screen: QPoint = self.mapFromScene(centroid_scene)

            if self._transform_mode == "rotate":
                # 计算当前角度与起始角度之差。
                start_dx: float = float(
                    self._transform_start_pos.x() - centroid_screen.x()
                )
                start_dy: float = float(
                    self._transform_start_pos.y() - centroid_screen.y()
                )
                curr_dx: float = float(
                    current_pos.x() - centroid_screen.x()
                )
                curr_dy: float = float(
                    current_pos.y() - centroid_screen.y()
                )
                start_angle: float = math.degrees(
                    math.atan2(-start_dy, start_dx)
                )
                curr_angle: float = math.degrees(
                    math.atan2(-curr_dy, curr_dx)
                )
                delta_angle: float = curr_angle - start_angle
                # Shift 吸附到 15° 倍数。
                if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier:
                    delta_angle = round(delta_angle / 15.0) * 15.0
                total_angle: float = self._transform_gesture_angle + delta_angle
                if abs(delta_angle) < 0.1:
                    event.accept()
                    return
                transformed: BaseGeometry = affinity.rotate(
                    gesture_geometry,
                    delta_angle,
                    origin=self._transform_centroid,
                )
                self._transform_geometry = transformed
                self._transform_angle = total_angle
            else:  # scale
                start_dist: float = math.hypot(
                    self._transform_start_pos.x() - centroid_screen.x(),
                    self._transform_start_pos.y() - centroid_screen.y(),
                )
                curr_dist: float = math.hypot(
                    current_pos.x() - centroid_screen.x(),
                    current_pos.y() - centroid_screen.y(),
                )
                if start_dist < 5.0:
                    event.accept()
                    return
                scale_factor: float = curr_dist / start_dist
                scale_factor = max(0.01, min(scale_factor, 100.0))
                transformed = affinity.scale(
                    gesture_geometry,
                    xfact=scale_factor,
                    yfact=scale_factor,
                    origin=self._transform_centroid,
                )
                self._transform_geometry = transformed
                self._transform_scale = self._transform_gesture_scale * scale_factor

            if self._transform_preview_item is not None:
                self._transform_preview_item.prepareGeometryChange()
                self._transform_preview_item.setPath(
                    self._geometry_to_preview_path(transformed)
                )
            self._refresh_transform_guide()
            parameters = (
                {"angle": self._transform_angle}
                if self._transform_mode == "rotate"
                else {"scale": self._transform_scale}
            )
            self.edit_preview_changed.emit(transformed, parameters)
            event.accept()
            return

        # ── 数字化预览（含捕捉）──
        if self._digitize_mode in (
            "line",
            "polygon",
            "measure_line",
            "measure_polygon",
        ):
            cursor_pos: QPoint = event.position().toPoint()
            active_id: str | None = (
                self._last_snapshot.active_layer_id if self._last_snapshot else None
            )
            snap_result: SnapResult | None = self._snap_engine.find_snap(
                self._screen_to_map_point(cursor_pos),
                self._map_units_per_pixel,
                active_id,
            )
            if snap_result is not None:
                self._show_snap_marker(snap_result)
                # 把捕捉点反算回屏幕坐标用于预览。
                snap_scene: QPointF = QPointF(
                    snap_result.map_point.x, -snap_result.map_point.y
                )
                cursor_pos = self.mapFromScene(snap_scene)
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
            pan_pos: QPoint = event.position().toPoint()
            delta: QPoint = self._last_middle_pos - pan_pos
            self._last_middle_pos = pan_pos
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

        # ── 整要素移动释放 ──
        if self._move_active and self._move_start_map is not None:
            if event.button() == Qt.MouseButton.LeftButton:
                self._move_start_map = None
                if self._move_original_geometry is not None and self._move_geometry is not None:
                    original_center = self._move_original_geometry.centroid
                    current_center = self._move_geometry.centroid
                    self._move_total_dx = current_center.x - original_center.x
                    self._move_total_dy = current_center.y - original_center.y
                self.setCursor(Qt.CursorShape.SizeAllCursor)
                event.accept()
                return

        # ── 变换要素释放 ──
        if self._transform_active and self._transform_start_pos is not None:
            if event.button() == Qt.MouseButton.LeftButton:
                self._transform_start_pos = None
                self.setCursor(Qt.CursorShape.CrossCursor)
                event.accept()
                return
        if self._transform_active and self._transform_pivot_dragging:
            if event.button() == Qt.MouseButton.LeftButton:
                self._transform_pivot_dragging = False
                self.setCursor(Qt.CursorShape.CrossCursor)
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
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self.schedule_viewport_refresh()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Esc 退出工具模式；Backspace 撤销数字化最后顶点。

        参数:
            event: Qt 键盘事件。
        """
        # Enter：请求应用当前预览，写回由统一编辑控制器执行。
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if (
                self._vertex_edit_active
                or self._move_active
                or self._transform_active
                or self._static_edit_active
            ):
                self.edit_apply_requested.emit()
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
                or self._move_active
                or self._transform_active
                or self._static_edit_active
            ):
                if (
                    self._vertex_edit_active
                    or self._move_active
                    or self._transform_active
                    or self._static_edit_active
                ):
                    self.edit_cancel_requested.emit()
                else:
                    self.set_pan_tool()
                event.accept()
                return

        # 数字化模式下的顶点撤销。
        if self._digitize_mode in (
            "line",
            "polygon",
            "measure_line",
            "measure_polygon",
        ):
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
        """画布尺寸变化时保持视图中心不变，避免偏移。

        说明:
            Qt 的 centerOn 只是设置标记而非立即生效，在 resizeEvent
            中调用会产生时间差导致画面跳动。这里改为直接操作滚动条，
            像素级别的补偿即刻生效，不会产生中间帧。
        """
        old_size: QSize = event.oldSize()
        new_size: QSize = event.size()
        center_before: QPointF | None = None
        if (
            self._map_scene_rect is not None
            and old_size.width() > 0
            and old_size.height() > 0
            and old_size != new_size
        ):
            center_before = self.mapToScene(
                QPoint(old_size.width() // 2, old_size.height() // 2)
            )
        super().resizeEvent(event)
        if center_before is not None:
            current_pos: QPoint = self.mapFromScene(center_before)
            target_center: QPoint = QPoint(
                new_size.width() // 2, new_size.height() // 2
            )
            dx: int = current_pos.x() - target_center.x()
            dy: int = current_pos.y() - target_center.y()
            if dx != 0:
                self.horizontalScrollBar().setValue(
                    self.horizontalScrollBar().value() + dx
                )
            if dy != 0:
                self.verticalScrollBar().setValue(
                    self.verticalScrollBar().value() + dy
                )
        overlay_width: int = min(420, max(self.viewport().width() - 48, 220))
        overlay_height: int = 170
        left: int = max((self.viewport().width() - overlay_width) // 2, 0)
        top: int = max((self.viewport().height() - overlay_height) // 2, 0)
        self._empty_overlay.setGeometry(left, top, overlay_width, overlay_height)
        self._ensure_pan_area()
        self.schedule_viewport_refresh()

    def _update_map_units_per_pixel(self) -> None:
        """按当前视野更新屏幕像素对应的地图单位。"""
        viewport_width: int = max(self.viewport().width(), 1)
        viewport_height: int = max(self.viewport().height(), 1)
        visible_rect: QRectF = self._visible_scene_rect()
        if visible_rect.width() <= 0.0 or visible_rect.height() <= 0.0:
            return
        self._map_units_per_pixel = max(
            visible_rect.width() / viewport_width,
            visible_rect.height() / viewport_height,
        )
        # 编辑控制点采用屏幕像素语义；缩放地图后按新的地图单位比例重建，
        # 避免控制柄随地图一起变得过大或过小。
        if self._vertex_edit_active and self._edit_geometry is not None:
            self._rebuild_vertex_markers(self._edit_geometry)
        if self._move_active and self._move_geometry is not None:
            if self._move_preview_item is not None:
                self._move_preview_item.setPath(
                    self._geometry_to_preview_path(self._move_geometry)
                )
        if self._transform_active and self._transform_geometry is not None:
            if self._transform_preview_item is not None:
                self._transform_preview_item.setPath(
                    self._geometry_to_preview_path(self._transform_geometry)
                )
            self._refresh_transform_guide()

    def _refresh_rendering_for_current_view(self) -> None:
        """在视图变换改变后按当前分辨率重建图元。"""
        if self._last_snapshot is None:
            self._update_map_units_per_pixel()
            return
        self.set_snapshot(self._last_snapshot)

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
        if queryable_ids != self._snap_engine.indexed_layer_ids:
            self._snap_engine.build_index(
                self._last_snapshot, queryable_ids,
                self._last_snapshot.active_layer_id,
            )

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
        # centerOn 通过整数像素滚动条定位，与 fitInView 的精确变换存在
        # 亚像素偏差；比较时保留 2 像素容差，避免该偏差反复触发扩展，
        # 导致同一视图下每次快照刷新都重建全部图元。
        tolerance_x: float = self._map_units_per_pixel * 2.0
        tolerance_y: float = self._map_units_per_pixel * 2.0
        if self._scene.sceneRect().contains(
            required_rect.adjusted(tolerance_x, tolerance_y, -tolerance_x, -tolerance_y)
        ):
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
