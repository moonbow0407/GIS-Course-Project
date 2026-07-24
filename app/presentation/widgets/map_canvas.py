"""基于领域图层快照的地图画布。"""

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QMouseEvent, QPainter, QResizeEvent, QWheelEvent
from PySide6.QtWidgets import QFrame, QGraphicsScene, QGraphicsView, QLabel, QVBoxLayout

from app.application.results import LayerSnapshot, WorkspaceSnapshot
from app.domain.raster_layer import RasterLayer
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
        self._scene.setSceneRect(0, 0, 1000, 700)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(QBrush(QColor("#f3f6fa")))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setMouseTracking(True)
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

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
        margin: float = max(maximum_x - minimum_x, maximum_y - minimum_y, 1.0) * 0.05
        # Qt 纵轴向下，场景范围中的地图 Y 坐标需要取反。
        map_scene_rect: QRectF = QRectF(
            minimum_x - margin,
            -(maximum_y + margin),
            maximum_x - minimum_x + 2 * margin,
            maximum_y - minimum_y + 2 * margin,
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

    def set_pan_tool(self) -> None:
        """切换到地图平移工具。"""
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

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

    def zoom_in(self) -> None:
        """以画布中心为基准将地图视图放大一级。"""
        self.scale(1.25, 1.25)
        self._zoom_percent *= 1.25
        self._emit_view_scale()

    def zoom_out(self) -> None:
        """以画布中心为基准将地图视图缩小一级。"""
        self.scale(0.8, 0.8)
        self._zoom_percent *= 0.8
        self._emit_view_scale()

    def wheelEvent(self, event: QWheelEvent) -> None:
        """把鼠标滚轮动作转换为连续地图缩放。

        参数:
            event: 包含滚动方向和步长的 Qt 滚轮事件。

        状态变化:
            更新画布变换和状态栏视图比例，并消费该事件。
        """
        if event.angleDelta().y() > 0:
            self.zoom_in()
        else:
            self.zoom_out()
        self._ensure_pan_area()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """更新状态栏地图坐标并保留当前导航交互。

        参数:
            event: 包含当前视口位置的 Qt 鼠标移动事件。

        状态变化:
            发出地图坐标文本，再交由父类继续处理平移交互。
        """
        scene_position: QPointF = self.mapToScene(event.position().toPoint())
        # 渲染时反转过 Y 轴，状态栏输出地图坐标时需要恢复方向。
        self.coordinate_changed.emit(
            f"坐标  {scene_position.x():.6f}, {-scene_position.y():.6f}"
        )
        super().mouseMoveEvent(event)

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
