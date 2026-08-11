"""全局显示符号配置 —— 选择高亮、草图预览等跨图层的显示颜色。

存储后端为 QSettings，不经过领域模型，保持轻量。
"""

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor

_GROUP: str = "GlobalDisplay"
_settings: QSettings | None = None


def _get_settings() -> QSettings:
    """延迟初始化 QSettings，确保在 QApplication 创建之后才构造。

    QSettings 的无参构造函数依赖 QApplication.organizationName()
    和 QApplication.applicationName()，因此在模块导入时（早于
    QApplication 实例化）创建会导致存储路径为空，setValue/sync
    完全无效。延迟到首次读写时创建可避免此问题。
    """
    global _settings
    if _settings is None:
        _settings = QSettings()
    return _settings

DEFAULT_SELECTION_COLOR: str = "#00E5FF"
DEFAULT_SKETCH_COLOR: str = "#FF1493"
DEFAULT_SNAP_COLOR: str = "#FF1493"
DEFAULT_SNAP_EDGE_COLOR: str = "#00BFFF"


def selection_color() -> QColor:
    """返回当前全局选择高亮颜色。"""
    raw: object = _get_settings().value(
        f"{_GROUP}/selection_color", DEFAULT_SELECTION_COLOR
    )
    return QColor(str(raw))


def set_selection_color(color: QColor) -> None:
    """持久化全局选择高亮颜色。"""
    s = _get_settings()
    s.setValue(f"{_GROUP}/selection_color", color.name())
    s.sync()


def sketch_color() -> QColor:
    """返回当前全局草图/数字化预览颜色。"""
    raw: object = _get_settings().value(
        f"{_GROUP}/sketch_color", DEFAULT_SKETCH_COLOR
    )
    return QColor(str(raw))


def set_sketch_color(color: QColor) -> None:
    """持久化全局草图/数字化预览颜色。"""
    s = _get_settings()
    s.setValue(f"{_GROUP}/sketch_color", color.name())
    s.sync()


def snap_color() -> QColor:
    """返回当前顶点捕捉标记颜色。"""
    raw: object = _get_settings().value(
        f"{_GROUP}/snap_color", DEFAULT_SNAP_COLOR
    )
    return QColor(str(raw))


def set_snap_color(color: QColor) -> None:
    """持久化顶点捕捉标记颜色。"""
    s = _get_settings()
    s.setValue(f"{_GROUP}/snap_color", color.name())
    s.sync()


def snap_edge_color() -> QColor:
    """返回当前边捕捉标记颜色。"""
    raw: object = _get_settings().value(
        f"{_GROUP}/snap_edge_color", DEFAULT_SNAP_EDGE_COLOR
    )
    return QColor(str(raw))


def set_snap_edge_color(color: QColor) -> None:
    """持久化边捕捉标记颜色。"""
    s = _get_settings()
    s.setValue(f"{_GROUP}/snap_edge_color", color.name())
    s.sync()
