"""基于领域图层快照的地图画布。"""

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QMouseEvent,
    QPainter,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
    QRubberBand,
    QVBoxLayout,
)

from app.application.project_models import MapViewState
from app.application.results import LayerSnapshot, WorkspaceSnapshot
from app.domain.raster_layer import RasterLayer
from app.domain.vector_layer import Bounds
from app.presentation.renderers.qt_raster_renderer import QtRasterRenderer
from app.presentation.renderers.qt_vector_renderer import QtVectorRenderer


class MapCanvas(QGraphicsView):
    """显示工作区快照并保留基础地图导航能力。"""

    # 坐标变化信号：携带可直接写入状态栏的格式化地图坐标文本。
    coordinate_changed = Signal(str)
    # 视图比例信号：携带相对于全图视图的近似缩放百分比文本。
    view_scale_changed = Signal(str)

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
        # 橡皮筋矩形：框选放大时跟随鼠标绘制的临时可视化控件。
        self._rubber_band: QRubberBand | None = None
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

    def set_snapshot(self, snapshot: WorkspaceSnapshot) -> None:
        """原子替换场景中的图层图元并适配真实数据范围。

        参数:
            snapshot: 包含真实矢量、栅格图层及显隐状态的工作区快照。

        状态变化:
            清空旧图元并重绘快照；空快照只显示操作引导。
        """
        self._scene.clear()
        self._empty_overlay.setVisible(not snapshot.layers)
        if not snapshot.layers:
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
        viewport_width: int = max(self.viewport().width(), 1)
        viewport_height: int = max(self.viewport().height(), 1)
        # 将屏幕像素尺寸换算为地图单位，使点符号保持稳定的视觉大小。
        map_units_per_pixel: float = max(
            self._scene.sceneRect().width() / viewport_width,
            self._scene.sceneRect().height() / viewport_height,
        )
        # 快照按底到顶排列，枚举值可直接作为 Qt 图元的叠放顺序。
        for z_value, current_layer in enumerate(layer_snapshot):
            if isinstance(current_layer.layer, RasterLayer):
                self._raster_renderer.render_layer(self._scene, current_layer, float(z_value))
            else:
                self._vector_renderer.render_layer(
                    self._scene,
                    current_layer,
                    float(z_value),
                    map_units_per_pixel,
                )
        # 全图只按真实数据范围适配；随后扩展场景范围，给手形拖动留下余量。
        self.fitInView(map_scene_rect, Qt.AspectRatioMode.KeepAspectRatio)
        self._ensure_pan_area()
        self._reset_view_scale()

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
        """切换到地图平移工具。"""
        self._zoom_rect_active = False
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self._update_cursor()

    def set_zoom_rect_tool(self) -> None:
        """切换到框选放大模式。

        状态变化:
            关闭 ScrollHandDrag，激活框选标记并将光标改为十字准星。
        """
        self._zoom_rect_active = True
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.CrossCursor)

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

    # ── 缩放 ────────────────────────────────────────────────

    def zoom_in(self) -> None:
        """以画布中心为基准将地图视图放大一级。"""
        center: QPointF = self.viewport().rect().center()
        self._zoom_at_screen_point(center, 1.25)

    def zoom_out(self) -> None:
        """以画布中心为基准将地图视图缩小一级。"""
        center: QPointF = self.viewport().rect().center()
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
        """拦截中键平移和框选放大的按下事件，其余交由父类处理。

        参数:
            event: 包含按钮类型和修饰键状态的 Qt 鼠标按下事件。

        状态变化:
            中键按下时进入手动平移模式并切换抓取光标；
            Shift+左键或框选工具模式下创建橡皮筋矩形起点。
        """
        # 中键平移：手动跟踪位移，不依赖 ScrollHandDrag。
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_mode = "middle"
            self._last_middle_pos = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
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

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """驱动中键平移、更新框选橡皮筋或发出状态栏坐标。

        参数:
            event: 包含当前视口位置的 Qt 鼠标移动事件。

        状态变化:
            中键拖拽时通过滚动条平移视图并发出坐标信号；
            框选拖拽时实时更新橡皮筋矩形；
            其余情况发出地图坐标文本后交由父类继续处理。
        """
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
            scene_pos: QPointF = self.mapToScene(current_pos)
            self.coordinate_changed.emit(
                f"坐标  {scene_pos.x():.6f}, {-scene_pos.y():.6f}"
            )
            event.accept()
            return

        # 框选橡皮筋：跟随鼠标实时更新选择矩形。
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
        """结束中键平移或执行框选缩放，其余交由父类处理。

        参数:
            event: 包含释放按钮类型的 Qt 鼠标释放事件。

        状态变化:
            中键释放时退出平移模式并恢复光标样式；
            左键释放时关闭橡皮筋并按框选范围缩放地图。
        """
        # 中键释放：恢复光标样式。
        if self._pan_mode == "middle" and event.button() == Qt.MouseButton.MiddleButton:
            self._pan_mode = "none"
            self._last_middle_pos = None
            self._update_cursor()
            event.accept()
            return

        # 框选释放：关闭橡皮筋，计算场景范围并缩放到该区域。
        if self._rubber_band is not None and event.button() == Qt.MouseButton.LeftButton:
            self._rubber_band.close()
            self._rubber_band = None
            screen_rect: QRect = QRect(
                self._zoom_origin, event.position().toPoint()
            ).normalized()
            # 忽略过小的拖拽（可能是误触），阈值设为 8 像素。
            if screen_rect.width() > 8 and screen_rect.height() > 8:
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
        self.view_scale_changed.emit(f"视图比例  {self._zoom_percent:.0f}%")

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
