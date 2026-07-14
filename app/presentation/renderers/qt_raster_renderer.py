"""将栅格领域模型转换为 Qt 图元。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap, QTransform
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene

from app.application.results import LayerSnapshot
from app.domain.raster_layer import RasterLayer


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
        height: int = int(layer.image_data.shape[0])
        width: int = int(layer.image_data.shape[1])
        bytes_per_line: int = int(layer.image_data.strides[0])
        image: QImage = QImage(
            layer.image_data.data,
            width,
            height,
            bytes_per_line,
            QImage.Format.Format_RGBA8888,
        ).copy()
        item: QGraphicsPixmapItem = QGraphicsPixmapItem(QPixmap.fromImage(image))
        transform = layer.transform
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
        item.setData(0, snapshot.layer_id)
        scene.addItem(item)
        return item
