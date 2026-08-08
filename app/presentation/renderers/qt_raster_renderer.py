"""将栅格领域模型转换为 Qt 图元。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter, QPixmap, QTransform
from PySide6.QtWidgets import QGraphicsItem, QGraphicsPixmapItem, QGraphicsScene, QWidget

from app.application.results import LayerSnapshot
from app.domain.raster_layer import RasterLayer

_BLEND_MODE_MAP: dict[str, QPainter.CompositionMode] = {
    "normal": QPainter.CompositionMode.CompositionMode_SourceOver,
    "multiply": QPainter.CompositionMode.CompositionMode_Multiply,
    "darken": QPainter.CompositionMode.CompositionMode_Darken,
}


class _BlendPixmapItem(QGraphicsPixmapItem):
    """在绘制时应用指定合成模式的像素图元。"""

    def __init__(
        self,
        pixmap: QPixmap,
        composition_mode: QPainter.CompositionMode,
    ) -> None:
        super().__init__(pixmap)
        self._composition_mode = composition_mode

    def paint(
        self,
        painter: QPainter,
        option: QGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        painter.save()
        painter.setCompositionMode(self._composition_mode)
        super().paint(painter, option, widget)
        painter.restore()


class QtRasterRenderer:
    """负责栅格像素、地理仿射变换和显隐状态的 Qt 转换。"""

    def render_layer(
        self,
        scene: QGraphicsScene,
        snapshot: LayerSnapshot,
        z_value: float,
    ) -> QGraphicsPixmapItem:
        """把单个栅格快照渲染为具有地图地理定位的像素图元。

        参数:
            scene: 接收栅格图元的地图场景。
            snapshot: 必须包含栅格领域图层的界面快照。
            z_value: 栅格图元在场景中的堆叠顺序。

        返回:
            已加入场景并应用地理仿射变换的像素图元。

        异常:
            TypeError: 快照实际包含矢量图层时抛出。
        """
        if not isinstance(snapshot.layer, RasterLayer):
            raise TypeError("栅格渲染器只能绘制栅格图层。")
        layer: RasterLayer = snapshot.layer
        # RGBA 数组的三个维度依次为高度、宽度和颜色通道。
        height: int = int(layer.image_data.shape[0])
        width: int = int(layer.image_data.shape[1])
        bytes_per_line: int = int(layer.image_data.strides[0])
        # copy() 使 QImage 独立保存像素，避免继续依赖 NumPy 数组的内存。
        image: QImage = QImage(
            layer.image_data.data,
            width,
            height,
            bytes_per_line,
            QImage.Format.Format_RGBA8888,
        ).copy()
        composition_mode = _BLEND_MODE_MAP.get(snapshot.blend_mode)
        _needs_blend = (
            composition_mode is not None
            and composition_mode != QPainter.CompositionMode.CompositionMode_SourceOver
        )
        pixmap = QPixmap.fromImage(image)
        item: QGraphicsPixmapItem = (
            _BlendPixmapItem(pixmap, composition_mode)
            if _needs_blend
            else QGraphicsPixmapItem(pixmap)
        )
        # 预览像元可能是降采样结果，使用其独立变换仍覆盖完整栅格范围。
        transform = layer.display_transform or layer.transform
        # Qt 的 Y 轴向下，地图坐标的 Y 轴通常向上，因此对 Y 方向取反。
        item.setTransform(
            QTransform(
                transform.a,
                -transform.d,
                transform.b,
                -transform.e,
                transform.c,
                -transform.f,
            )
        )
        item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        item.setZValue(z_value)
        item.setVisible(snapshot.visible)
        item.setOpacity(snapshot.opacity)
        # 保存图层编号，方便后续由 Qt 图元反查领域图层。
        item.setData(0, snapshot.layer_id)
        scene.addItem(item)
        return item
