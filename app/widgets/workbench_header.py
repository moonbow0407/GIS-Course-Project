"""GIS 工作台顶部导航与 Ribbon。"""

from collections.abc import Mapping

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class WorkbenchHeader(QWidget):
    """固定结构的应用栏、导航页签和 Ribbon。"""

    def __init__(self, actions: Mapping[str, QAction], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("workbenchHeader")
        self._actions = actions

        self.quick_access_bar = self._create_quick_access_bar()
        self.nav_tabs = QTabBar()
        self.nav_tabs.setObjectName("workbenchNavTabs")
        self.nav_tabs.setDrawBase(False)
        self.nav_tabs.setExpanding(False)
        self.nav_tabs.setUsesScrollButtons(False)

        self.ribbon_stack = QStackedWidget()
        self.ribbon_stack.setObjectName("ribbonStack")
        self._create_ribbon_pages()
        self.nav_tabs.currentChanged.connect(self.ribbon_stack.setCurrentIndex)
        self.nav_tabs.setCurrentIndex(2)

        nav_bar = QWidget()
        nav_bar.setObjectName("workbenchNavBar")
        nav_layout = QHBoxLayout(nav_bar)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.addStretch()
        nav_layout.addWidget(self.nav_tabs)
        nav_layout.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.quick_access_bar)
        layout.addWidget(nav_bar)
        layout.addWidget(self.ribbon_stack)

    def _create_quick_access_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("quickAccessToolBar")

        left = QWidget()
        left.setObjectName("quickAccessLeft")
        left.setFixedWidth(300)
        left_layout = QHBoxLayout(left)
        left_layout.setContentsMargins(12, 0, 0, 0)
        left_layout.setSpacing(2)
        for key in ["open_vector", "save_layer", "undo", "redo"]:
            button = QToolButton()
            button.setDefaultAction(self._actions[key])
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.setIconSize(QSize(18, 18))
            left_layout.addWidget(button)
        left_layout.addStretch()

        title = QLabel("GIS桌面通用平台")
        title.setObjectName("applicationTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        right = QWidget()
        right.setObjectName("quickAccessRight")
        right.setFixedWidth(300)
        right_layout = QHBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 12, 0)
        right_layout.addStretch()
        status_dot = QLabel("●")
        status_dot.setObjectName("headerStatusDot")
        status_text = QLabel("本地工作空间")
        status_text.setObjectName("headerStatusText")
        right_layout.addWidget(status_dot)
        right_layout.addWidget(status_text)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(left)
        layout.addWidget(title, 1)
        layout.addWidget(right)
        return bar

    def _create_ribbon_pages(self) -> None:
        pages = [
            ("文件", [("项目", ["open_vector", "open_raster", "save_layer", "export_layer"])]),
            ("编辑", [("编辑", ["undo", "redo", "add_tool", "edit_tool", "delete_feature", "save_edit"])]),
            (
                "地图",
                [
                    ("导航", ["pan", "zoom_in_tool", "zoom_out_tool", "full_extent"]),
                    ("选择", ["point_select", "box_select", "clear_selection"]),
                    ("编辑", ["add_tool", "edit_tool", "delete_feature"]),
                    ("分析", ["buffer_analysis", "overlay_analysis"]),
                ],
            ),
            ("查询", [("查询方式", ["point_query", "box_query", "attribute_query", "clear_selection"])]),
            ("分析", [("空间分析", ["buffer_analysis", "overlay_analysis", "simplify_line", "smooth_line"])]),
            ("数据库", [("数据源", ["connect_db", "load_db_layer", "import_layer_db", "disconnect_db"])]),
            ("帮助", [("支持", ["help", "about"])]),
        ]
        for page_name, groups in pages:
            self.nav_tabs.addTab(page_name)
            self.ribbon_stack.addWidget(self._create_ribbon_page(groups))

    def _create_ribbon_page(self, groups: list[tuple[str, list[str]]]) -> QWidget:
        page = QWidget()
        page.setObjectName("ribbonPage")
        layout = QHBoxLayout(page)
        layout.setContentsMargins(18, 6, 18, 5)
        layout.setSpacing(0)
        for title, action_keys in groups:
            layout.addWidget(self._create_ribbon_group(title, action_keys))
        layout.addStretch()
        return page

    def _create_ribbon_group(self, title: str, action_keys: list[str]) -> QFrame:
        group = QFrame()
        group.setObjectName("ribbonGroup")
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(10, 0, 10, 0)
        group_layout.setSpacing(1)

        action_layout = QHBoxLayout()
        action_layout.setSpacing(3)
        for key in action_keys:
            button = QToolButton()
            button.setDefaultAction(self._actions[key])
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setIconSize(QSize(24, 24))
            button.setFixedSize(80, 62)
            action_layout.addWidget(button)

        title_label = QLabel(title)
        title_label.setObjectName("ribbonGroupTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        group_layout.addLayout(action_layout)
        group_layout.addWidget(title_label)
        return group
