"""基于领域图层快照的地图画布。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QPainter
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView

from app.application.results import WorkspaceSnapshot
from app.presentation.renderers.qt_vector_renderer import QtVectorRenderer


class MapCanvas(QGraphicsView):
    """显示工作区快照并保留基础地图导航能力。"""

    def __init__(self, parent: QGraphicsView | None = None) -> None:
        """创建空地图场景和矢量渲染器。"""
        super().__init__(parent)
        self._scene: QGraphicsScene = QGraphicsScene(self)
        self._renderer: QtVectorRenderer = QtVectorRenderer()
        self._scene.setSceneRect(0, 0, 1000, 700)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(QBrush(QColor("#f4f6f8")))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def set_snapshot(self, snapshot: WorkspaceSnapshot) -> None:
        """原子替换场景中的图层图元并适配真实数据范围。"""
        self._scene.clear()
        if not snapshot.layers:
            self._scene.setSceneRect(0, 0, 1000, 700)
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
            return
        layer_snapshot = snapshot.layers
        minimum_x: float = min(layer.bounds[0] for layer in layer_snapshot)
        minimum_y: float = min(layer.bounds[1] for layer in layer_snapshot)
        maximum_x: float = max(layer.bounds[2] for layer in layer_snapshot)
        maximum_y: float = max(layer.bounds[3] for layer in layer_snapshot)
        margin: float = max(maximum_x - minimum_x, maximum_y - minimum_y, 1.0) * 0.05
        self._scene.setSceneRect(
            minimum_x - margin,
            -(maximum_y + margin),
            maximum_x - minimum_x + 2 * margin,
            maximum_y - minimum_y + 2 * margin,
        )
        for z_value, current_layer in enumerate(layer_snapshot):
            self._renderer.render_layer(self._scene, current_layer, float(z_value))
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def set_pan_tool(self) -> None:
        """切换到地图平移工具。"""
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

    def zoom_to_full_extent(self) -> None:
        """将当前地图范围完整缩放到视图内。"""
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
