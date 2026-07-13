"""GIS 工作台单层命令面板。"""

from collections.abc import Mapping

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class WorkbenchHeader(QWidget):
    """直接展示全部系统功能的分组命令面板。"""

    _LABELS = {
        "open_vector": "矢量",
        "open_raster": "栅格",
        "save_layer": "保存",
        "export_layer": "导出",
        "zoom_in_tool": "放大",
        "zoom_out_tool": "缩小",
        "full_extent": "全图",
        "point_select": "点选",
        "box_select": "框选",
        "attribute_query": "属性",
        "clear_selection": "清除",
        "add_tool": "新增",
        "edit_tool": "修改",
        "delete_feature": "删除",
        "save_edit": "保存编辑",
        "buffer_analysis": "缓冲区",
        "overlay_analysis": "叠加",
        "simplify_line": "线简化",
        "smooth_line": "线平滑",
        "connect_db": "连接",
        "load_db_layer": "加载",
        "import_layer_db": "导入",
        "disconnect_db": "断开",
        "help": "说明",
        "about": "关于",
    }

    _GLYPHS = {
        "open_vector": "V",
        "open_raster": "R",
        "save_layer": "S",
        "export_layer": "↗",
        "pan": "✥",
        "zoom_in_tool": "+",
        "zoom_out_tool": "−",
        "full_extent": "⌂",
        "point_select": "●",
        "box_select": "□",
        "attribute_query": "表",
        "clear_selection": "×",
        "undo": "↶",
        "redo": "↷",
        "add_tool": "+",
        "edit_tool": "✎",
        "delete_feature": "−",
        "save_edit": "✓",
        "buffer_analysis": "◎",
        "overlay_analysis": "∩",
        "simplify_line": "≋",
        "smooth_line": "∿",
        "connect_db": "DB",
        "load_db_layer": "↓",
        "import_layer_db": "⇩",
        "disconnect_db": "×",
        "help": "?",
        "about": "i",
    }

    _COLORS = {
        "file": "#2f7de1",
        "navigation": "#168f89",
        "query": "#745bc7",
        "edit": "#d97922",
        "analysis": "#3d9142",
        "database": "#5268b8",
        "help": "#687482",
    }

    def __init__(self, actions: Mapping[str, QAction], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("commandSurface")
        self._actions = actions

        groups = [
            ("项目", "file", ["open_vector", "open_raster", "save_layer", "export_layer"]),
            ("导航", "navigation", ["pan", "zoom_in_tool", "zoom_out_tool", "full_extent"]),
            ("查询", "query", ["point_select", "box_select", "attribute_query", "clear_selection"]),
            ("编辑", "edit", ["undo", "redo", "add_tool", "edit_tool", "delete_feature", "save_edit"]),
            ("分析", "analysis", ["buffer_analysis", "overlay_analysis", "simplify_line", "smooth_line"]),
            ("数据库", "database", ["connect_db", "load_db_layer", "import_layer_db", "disconnect_db"]),
            ("帮助", "help", ["help", "about"]),
        ]

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(7)
        for title, category, action_keys in groups:
            stretch = 3 if len(action_keys) > 4 else 2
            layout.addWidget(self._create_group(title, category, action_keys), stretch)

    def _create_group(self, title: str, category: str, action_keys: list[str]) -> QFrame:
        group = QFrame()
        group.setObjectName("commandGroup")
        group.setProperty("category", category)

        title_label = QLabel(title)
        title_label.setObjectName("commandGroupTitle")
        title_label.setProperty("category", category)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)
        columns = 3 if len(action_keys) > 4 else 2
        for index, key in enumerate(action_keys):
            button = QToolButton()
            button.setDefaultAction(self._actions[key])
            button.setText(self._LABELS.get(key, self._actions[key].text()))
            button.setIcon(self._create_command_icon(self._GLYPHS.get(key, "•"), category))
            button.setProperty("category", category)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setIconSize(QSize(22, 22))
            button.setMinimumSize(82, 48)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            grid.addWidget(button, index // columns, index % columns)

        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(5)
        layout.addWidget(title_label)
        layout.addLayout(grid)
        layout.addStretch()
        return group

    def _create_command_icon(self, glyph: str, category: str) -> QIcon:
        pixmap = QPixmap(30, 30)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._COLORS[category]))
        painter.drawRoundedRect(1, 1, 28, 28, 6, 6)
        painter.setPen(Qt.GlobalColor.white)
        font = QFont("Segoe UI Symbol", 10, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, glyph)
        painter.end()
        return QIcon(pixmap)
