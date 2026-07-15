"""主窗口顶部功能区控件。"""

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
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


@dataclass(frozen=True, slots=True)
class RibbonActionSpec:
    """描述功能区中的单个操作入口。"""

    # 操作编号：作为界面层与后续业务实现之间的稳定接口标识。
    action_id: str

    # 显示名称：用于功能区按钮的中文标题。
    title: str

    # 图标字符：绘制到统一风格方形图标中的简短符号。
    glyph: str

    # 强调颜色：用于区分文件、编辑、分析等功能类别。
    color: str = "#2563eb"


@dataclass(frozen=True, slots=True)
class RibbonGroupSpec:
    """描述功能区中的一组相关操作。"""

    # 分组名称：显示在按钮组底部，说明组内操作的共同职责。
    title: str

    # 操作集合：按照界面从左到右的顺序保存组内按钮。
    actions: tuple[RibbonActionSpec, ...]


class RibbonBar(QWidget):
    """以标签页和分组按钮呈现 GIS 平台全部功能入口。"""

    # 操作触发信号：携带稳定操作编号，由主窗口决定当前接入方式。
    action_triggered = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建空功能区并装配项目文档定义的功能入口。

        参数:
            parent: 父控件；为空时由主窗口在装配时接管所有权。

        状态变化:
            创建标签栏和页面容器，并默认激活“地图”标签页。
        """
        super().__init__(parent)
        self.setObjectName("ribbonBar")
        # 标签栏：在文件、地图、编辑、分析、数据库和视图页面间切换。
        self._tabs: QTabBar = QTabBar()
        # 页面容器：保存当前标签页对应的操作分组。
        self._pages: QStackedWidget = QStackedWidget()
        self._create_ui()

    def _create_ui(self) -> None:
        """创建功能区标题行、标签栏和各功能页面。

        状态变化:
            将项目文档定义的全部功能分组装配到当前控件布局中。
        """
        brand: QLabel = QLabel("GIS 桌面通用平台")
        brand.setObjectName("applicationBrand")
        title_row: QHBoxLayout = QHBoxLayout()
        title_row.setContentsMargins(18, 5, 14, 0)
        title_row.addWidget(brand)
        title_row.addStretch()
        help_label: QLabel = QLabel("帮助  ·  关于")
        help_label.setObjectName("ribbonHelp")
        title_row.addWidget(help_label)

        self._tabs.setObjectName("ribbonTabs")
        self._tabs.setExpanding(False)
        self._tabs.setDrawBase(False)
        self._tabs.currentChanged.connect(self._pages.setCurrentIndex)

        tab_specs: tuple[tuple[str, tuple[RibbonGroupSpec, ...]], ...] = self._tab_specs()
        tab_title: str
        groups: tuple[RibbonGroupSpec, ...]
        for tab_title, groups in tab_specs:
            self._tabs.addTab(tab_title)
            self._pages.addWidget(self._create_page(groups))

        layout: QVBoxLayout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(title_row)
        layout.addWidget(self._tabs)
        layout.addWidget(self._pages)
        self._tabs.setCurrentIndex(1)

    def _create_page(self, groups: tuple[RibbonGroupSpec, ...]) -> QWidget:
        """根据分组描述创建单个功能区页面。

        参数:
            groups: 当前标签页需要展示的操作分组。

        返回:
            包含全部按钮组并在右侧保留弹性空间的页面控件。
        """
        page: QWidget = QWidget()
        page.setObjectName("ribbonPage")
        page_layout: QHBoxLayout = QHBoxLayout(page)
        page_layout.setContentsMargins(12, 6, 12, 5)
        page_layout.setSpacing(0)
        group_spec: RibbonGroupSpec
        for group_spec in groups:
            page_layout.addWidget(self._create_group(group_spec))
            separator: QFrame = QFrame()
            separator.setObjectName("ribbonSeparator")
            separator.setFrameShape(QFrame.Shape.VLine)
            page_layout.addWidget(separator)
        page_layout.addStretch()
        return page

    def _create_group(self, spec: RibbonGroupSpec) -> QWidget:
        """创建带底部标题的功能区按钮组。

        参数:
            spec: 分组标题及其操作入口定义。

        返回:
            已组装按钮和底部说明文字的分组控件。
        """
        group: QWidget = QWidget()
        group.setObjectName("ribbonGroup")
        button_row: QHBoxLayout = QHBoxLayout()
        button_row.setContentsMargins(4, 0, 4, 0)
        button_row.setSpacing(2)
        action_spec: RibbonActionSpec
        for action_spec in spec.actions:
            button_row.addWidget(self._create_button(action_spec))
        caption: QLabel = QLabel(spec.title)
        caption.setObjectName("ribbonGroupCaption")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout: QVBoxLayout = QVBoxLayout(group)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(1)
        layout.addLayout(button_row, 1)
        layout.addWidget(caption)
        return group

    def _create_button(self, spec: RibbonActionSpec) -> QToolButton:
        """创建具有统一大图标样式的功能入口按钮。

        参数:
            spec: 操作编号、显示标题、图标字符和强调颜色。

        返回:
            点击时发出稳定操作编号的功能区按钮。
        """
        button: QToolButton = QToolButton()
        button.setObjectName("ribbonButton")
        button.setText(spec.title)
        button.setIcon(self._glyph_icon(spec.glyph, spec.color))
        button.setIconSize(QPixmap(34, 34).size())
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.setAutoRaise(True)
        button.setMinimumWidth(68)
        button.setToolTip(spec.title)
        # 默认参数在创建按钮时固定编号，避免循环结束后所有按钮指向同一操作。
        button.clicked.connect(lambda checked=False, action_id=spec.action_id: self.action_triggered.emit(action_id))
        return button

    @staticmethod
    def _glyph_icon(glyph: str, color: str) -> QIcon:
        """把简短字符绘制为清晰、统一且无需外部资源的功能图标。

        参数:
            glyph: 用于表达操作含义的单字符或短符号。
            color: Qt 支持的十六进制图标颜色。

        返回:
            具有透明背景和统一画布尺寸的 Qt 图标。
        """
        pixmap: QPixmap = QPixmap(40, 40)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter: QPainter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(color))
        font: QFont = QFont("Segoe UI Symbol", 22)
        font.setWeight(QFont.Weight.Light)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, glyph)
        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _tab_specs() -> tuple[tuple[str, tuple[RibbonGroupSpec, ...]], ...]:
        """返回与设计文档功能模块对应的完整标签和操作定义。"""
        return (
            (
                "文件",
                (
                    RibbonGroupSpec("数据", (
                        RibbonActionSpec("open_data", "打开数据", "▱", "#d97706"),
                        RibbonActionSpec("save_layer", "保存图层", "▣", "#2563eb"),
                        RibbonActionSpec("export_layer", "导出数据", "⇱", "#0f766e"),
                    )),
                    RibbonGroupSpec("工程", (
                        RibbonActionSpec("new_project", "新建工程", "＋"),
                        RibbonActionSpec("open_project", "打开工程", "⌑"),
                        RibbonActionSpec("save_project", "保存工程", "◇"),
                    )),
                ),
            ),
            (
                "地图",
                (
                    RibbonGroupSpec("导航", (
                        RibbonActionSpec("zoom_in", "放大", "⊕"),
                        RibbonActionSpec("zoom_out", "缩小", "⊖"),
                        RibbonActionSpec("pan", "平移", "✋", "#475569"),
                        RibbonActionSpec("full_extent", "全图显示", "⌗", "#0f766e"),
                    )),
                    RibbonGroupSpec("查询", (
                        RibbonActionSpec("point_query", "点选查询", "⌖", "#0284c7"),
                        RibbonActionSpec("rectangle_query", "框选查询", "□", "#0284c7"),
                        RibbonActionSpec("attribute_query", "属性查询", "≣", "#7c3aed"),
                    )),
                    RibbonGroupSpec("地图", (
                        RibbonActionSpec("clear_selection", "清除选择", "×", "#dc2626"),
                        RibbonActionSpec("refresh_map", "刷新地图", "↻", "#0f766e"),
                    )),
                ),
            ),
            (
                "编辑",
                (
                    RibbonGroupSpec("要素编辑", (
                        RibbonActionSpec("add_feature", "新增要素", "＋"),
                        RibbonActionSpec("edit_feature", "修改要素", "✎", "#d97706"),
                        RibbonActionSpec("delete_feature", "删除要素", "×", "#dc2626"),
                    )),
                    RibbonGroupSpec("线处理", (
                        RibbonActionSpec("simplify_line", "线简化", "⌁", "#0f766e"),
                        RibbonActionSpec("smooth_line", "线平滑", "∿", "#7c3aed"),
                    )),
                    RibbonGroupSpec("编辑历史", (
                        RibbonActionSpec("undo", "撤销", "↶", "#475569"),
                        RibbonActionSpec("redo", "重做", "↷", "#475569"),
                    )),
                ),
            ),
            (
                "分析",
                (
                    RibbonGroupSpec("空间分析", (
                        RibbonActionSpec("buffer_analysis", "缓冲区分析", "◌", "#16a34a"),
                        RibbonActionSpec("overlay_analysis", "叠加分析", "▧", "#7c3aed"),
                    )),
                    RibbonGroupSpec("结果", (
                        RibbonActionSpec("analysis_history", "分析记录", "≣", "#475569"),
                        RibbonActionSpec("export_result", "导出结果", "⇱", "#0f766e"),
                    )),
                ),
            ),
            (
                "数据库",
                (
                    RibbonGroupSpec("连接", (
                        RibbonActionSpec("connect_database", "连接数据库", "◉", "#16a34a"),
                        RibbonActionSpec("disconnect_database", "断开连接", "○", "#dc2626"),
                    )),
                    RibbonGroupSpec("数据管理", (
                        RibbonActionSpec("import_database", "导入图层", "⇩", "#2563eb"),
                        RibbonActionSpec("load_database", "加载图层", "⇧", "#7c3aed"),
                        RibbonActionSpec("database_manager", "数据管理", "▤", "#475569"),
                    )),
                ),
            ),
            (
                "视图",
                (
                    RibbonGroupSpec("面板", (
                        RibbonActionSpec("toggle_layers", "图层面板", "▥", "#2563eb"),
                        RibbonActionSpec("show_attributes", "属性表", "▤", "#7c3aed"),
                    )),
                    RibbonGroupSpec("地图显示", (
                        RibbonActionSpec("set_crs", "坐标系统", "◎", "#0f766e"),
                        RibbonActionSpec("map_settings", "显示设置", "⚙", "#475569"),
                    )),
                    RibbonGroupSpec("帮助", (
                        RibbonActionSpec("help", "使用帮助", "?", "#2563eb"),
                        RibbonActionSpec("about", "关于平台", "i", "#475569"),
                    )),
                ),
            ),
        )

    @classmethod
    def action_title(cls, action_id: str) -> str | None:
        """根据稳定操作编号返回功能区中的唯一显示标题。

        参数:
            action_id: 功能区发出的稳定操作编号。

        返回:
            找到时返回对应中文标题，未知编号返回空值。
        """
        _tab_title: str
        groups: tuple[RibbonGroupSpec, ...]
        for _tab_title, groups in cls._tab_specs():
            group: RibbonGroupSpec
            for group in groups:
                action: RibbonActionSpec
                for action in group.actions:
                    if action.action_id == action_id:
                        return action.title
        return None
