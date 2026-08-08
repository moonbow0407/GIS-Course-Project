"""全局显示符号配置 —— 选择高亮、草图预览等跨图层的显示颜色。

存储后端为 QSettings，不经过领域模型，保持轻量。
"""

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor

_GROUP: str = "GlobalDisplay"

DEFAULT_SELECTION_COLOR: str = "#00E5FF"
DEFAULT_SKETCH_COLOR: str = "#FF1493"


def selection_color() -> QColor:
    """返回当前全局选择高亮颜色。"""
    raw: object = QSettings().value(
        f"{_GROUP}/selection_color", DEFAULT_SELECTION_COLOR
    )
    return QColor(str(raw))


def set_selection_color(color: QColor) -> None:
    """持久化全局选择高亮颜色。"""
    QSettings().setValue(f"{_GROUP}/selection_color", color.name())


def sketch_color() -> QColor:
    """返回当前全局草图/数字化预览颜色。"""
    raw: object = QSettings().value(
        f"{_GROUP}/sketch_color", DEFAULT_SKETCH_COLOR
    )
    return QColor(str(raw))


def set_sketch_color(color: QColor) -> None:
    """持久化全局草图/数字化预览颜色。"""
    QSettings().setValue(f"{_GROUP}/sketch_color", color.name())
