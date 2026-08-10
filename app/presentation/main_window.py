"""GIS 桌面通用平台主窗口。"""

from collections.abc import Callable, Mapping
from dataclasses import replace
from functools import partial
from pathlib import Path

from pyproj import CRS
from pyproj.exceptions import CRSError
from PySide6.QtCore import QPointF, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QCloseEvent,
    QDesktopServices,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry

from app.application.database_service import DatabaseService
from app.application.errors import ApplicationError
from app.application.gis_application import GisApplication, _chaikin_smooth
from app.application.project_models import MapViewState
from app.application.results import (
    LayerSnapshot,
    OpenDataResult,
    SelectedFeature,
    WorkspaceSnapshot,
)
from app.domain.feature import AttributeValue, Feature, FeatureId
from app.domain.labeling import LabelingConfig, default_labeling_for_features
from app.domain.layer_style import GeometryFamily
from app.domain.layout import LayoutDocument, layout_from_dict
from app.domain.symbology import RasterSymbology, VectorSymbology
from app.domain.vector_layer import VectorLayer
from app.infrastructure.database.postgis_database_gateway import PostgisDatabaseGateway
from app.infrastructure.file_io.auto_reader import AutoDataReader
from app.infrastructure.file_io.auto_writer import AutoDataWriter
from app.infrastructure.project.json_project_store import JsonProjectStore
from app.presentation.widgets.analysis_history_panel import AnalysisHistoryPanel
from app.presentation.widgets.attribute_query_dialog import (
    AttributeQueryDialog,
    AttributeQueryRequest,
)
from app.presentation.widgets.attribute_table import AttributeTablePanel
from app.presentation.widgets.buffer_analysis_dialog import BufferAnalysisDialog
from app.presentation.widgets.crs_select_widget import CrsSelectWidget
from app.presentation.widgets.database_dialogs import (
    DatabaseConnectionDialog,
    DatabaseLayerDialog,
)
from app.presentation.widgets.display_settings_dialog import DisplaySettingsDialog
from app.presentation.widgets.edit_feature_dialog import EditFeatureDialog
from app.presentation.widgets.geometry_edit_toolbar import GeometryEditToolbar
from app.presentation.widgets.labeling_dialog import LabelingDialog
from app.presentation.widgets.layer_panel import LayerPanel
from app.presentation.widgets.layout_toolbar import LayoutToolbar
from app.presentation.widgets.layout_view import LayoutView
from app.presentation.widgets.map_canvas import MapCanvas
from app.presentation.widgets.new_layer_dialog import NewLayerDialog
from app.presentation.widgets.overlay_analysis_dialog import OverlayAnalysisDialog
from app.presentation.widgets.raster_calculator_dialog import RasterCalculatorDialog
from app.presentation.widgets.ribbon_bar import RibbonBar
from app.presentation.widgets.startup_dialog import (
    save_recent_project,
)
from app.presentation.widgets.target_layer_dialog import (
    TargetLayerDialog,
    TargetLayerOption,
)


class MainWindow(QMainWindow):
    """组装功能区、图层面板、地图画布和状态栏的 GIS 工作台。"""

    _ANALYSIS_TAB_INDEX: int = 0

    def __init__(self, project_path: Path | None = None) -> None:
        """创建 GIS 工作区，可选自动加载指定工程。

        参数:
            project_path: 启动时自动打开的工程路径；为空则新建空白工程。
        """
        super().__init__()
        # 应用服务：统一编排空间数据读取和地图文档操作。
        self._data_reader: AutoDataReader = AutoDataReader()
        self._application: GisApplication = GisApplication(
            self._data_reader,
            AutoDataWriter(),
            project_store=JsonProjectStore(),
            database_service=DatabaseService(PostgisDatabaseGateway),
        )
        # 顶部功能区：集中呈现文档规划的全部现有及预留功能入口。
        self._ribbon: RibbonBar = RibbonBar()
        # 图层面板：展示并操作当前地图文档中的真实图层。
        self._layer_panel: LayerPanel = LayerPanel()
        # 地图画布：显示矢量与栅格图层并提供基础导航能力。
        self._map_canvas: MapCanvas = MapCanvas()
        # 布局视图：制图排版与打印预览。
        self._layout_view: LayoutView = LayoutView()
        self._layout_view.set_map_canvas(self._map_canvas)
        # 布局工具栏：浮动在布局视图上方。
        self._layout_toolbar: LayoutToolbar = LayoutToolbar(self)
        self._layout_toolbar.add_map_frame.connect(self._layout_view.add_map_frame)
        self._layout_toolbar.add_scale_bar.connect(self._layout_view.add_scale_bar)
        self._layout_toolbar.add_legend.connect(self._layout_view.add_legend)
        self._layout_toolbar.add_north_arrow.connect(self._layout_view.add_north_arrow)
        self._layout_toolbar.add_text.connect(self._layout_view.add_text_element)
        self._layout_toolbar.page_setup.connect(self._on_page_setup)
        self._layout_toolbar.zoom_in.connect(self._layout_view.zoom_in)
        self._layout_toolbar.zoom_out.connect(self._layout_view.zoom_out)
        self._layout_toolbar.zoom_fit.connect(self._layout_view.fit_page)
        self._layout_toolbar.edit_properties.connect(self._on_edit_properties)
        self._layout_toolbar.export_layout.connect(self._export_layout)
        self._layout_toolbar.delete_selected.connect(self._layout_view._delete_selected)
        self._layout_toolbar.undo.connect(self._layout_view._undo)
        self._layout_toolbar.redo.connect(self._layout_view._redo)
        self._layout_toolbar.close_requested.connect(self._exit_layout_mode)
        self._layout_view.element_selected.connect(
            lambda eid: self._layout_toolbar.set_delete_enabled(eid is not None)
        )
        self._layout_view.undo_state_changed.connect(
            lambda can_undo, can_redo: (
                self._layout_toolbar.set_undo_enabled(can_undo),
                self._layout_toolbar.set_redo_enabled(can_redo),
            )
        )
        self._layout_toolbar.hide()  # 初始隐藏，进入布局视图时才显示
        # 视图栈：在数据视图和布局视图之间切换。
        self._view_stack: QStackedWidget = QStackedWidget()
        self._layout_mode: bool = False
        # 右侧停靠面板：分析历史记录。
        self._analysis_history_panel: AnalysisHistoryPanel = AnalysisHistoryPanel()
        self._attribute_table_panel: AttributeTablePanel = AttributeTablePanel()
        self._attribute_table_dock: QDockWidget = QDockWidget("属性表", self)
        self._panel_tabs: QTabWidget = QTabWidget()
        self._panel_dock: QDockWidget = QDockWidget("工作面板", self)
        # 状态提示标签：显示就绪状态和最近一次操作反馈。
        self._ready_label: QLabel = QLabel("就绪")
        # 坐标标签：实时显示鼠标对应的地图坐标。
        self._coordinate_label: QLabel = QLabel("坐标  --, --")
        # 比例标签：显示相对于当前全图视图的缩放比例。
        self._scale_label: QLabel = QLabel("视图比例  100%")
        # 活动图层标签：显示当前活动图层名称。
        self._layer_label: QLabel = QLabel("当前图层  无")
        # 选择数量标签：显示当前选中的矢量要素总数。
        self._selection_label: QLabel = QLabel("选中要素  0")
        # 坐标系标签：显示地图文档采用的显示坐标参考系统。
        self._crs_label: QLabel = QLabel("坐标系  未设置")
        # 延迟刷新标记：避免在图层树信号回调中删除仍在处理事件的 Qt 节点。
        self._workspace_refresh_scheduled: bool = False
        # 撤销栈：每项为 (操作描述, 逆向操作, 重做操作)，最多保留 50 步。
        self._undo_stack: list[tuple[str, Callable[[], object], Callable[[], object]]] = []
        # 重做栈：撤销后暂存被撤销的操作，新操作执行时清空。
        self._redo_stack: list[tuple[str, Callable[[], object], Callable[[], object]]] = []
        # 正在编辑几何要素的要素标识，供顶点编辑回调使用。
        self._editing_layer_id: str | None = None
        self._editing_fid: FeatureId | None = None
        # 当前数字化模式：供连续创建后重新激活工具。
        self._digitize_mode: str = "point"
        # 数字化目标图层：启动时锁定，绘制期间画布点击会清除活动图层，
        # 追加必须回到启动数字化时选定的图层而不是当时的活动图层。
        self._digitize_target_layer_id: str | None = None
        # 捕捉开关：默认关闭。
        self._snapping_enabled: bool = False
        # 当前持续激活的查询入口，用于同步三种查询按钮的互斥高亮状态。
        self._active_query_action: str | None = None
        self._active_digitize_action: str | None = None
        # 编辑几何要素悬浮工具栏。
        self._geom_edit_toolbar: GeometryEditToolbar = GeometryEditToolbar()
        self._geom_edit_toolbar.mode_changed.connect(self._on_geom_edit_mode)
        self._geom_edit_toolbar.commit_requested.connect(
            self._on_geom_edit_commit
        )
        self._geom_edit_toolbar.cancel_requested.connect(
            self._on_geom_edit_cancel
        )
        self._geom_edit_toolbar.select_all_requested.connect(
            self._select_all_vertices
        )
        # 原始几何缓存：key=(layer_id, fid)，用于线简化/平滑"从原始重算"。
        self._original_geoms: dict[tuple[str, FeatureId], BaseGeometry] = {}
        self._create_ui()
        self._connect_signals()
        # 初始空栈时功能区撤销/重做按钮禁用。
        self._update_undo_buttons()
        # Ctrl+Z 撤销最近一次地图修改。
        QShortcut(QKeySequence.StandardKey.Undo, self, self._undo)
        # Ctrl+Shift+Z 重做最近一次撤销。
        QShortcut(QKeySequence.StandardKey.Redo, self, self._redo)
        # Delete 删除选中要素（ApplicationShortcut 确保即使在属性表焦点下也生效）。
        delete_shortcut: QShortcut = QShortcut(
            QKeySequence(Qt.Key.Key_Delete), self, self._delete_selected_features
        )
        delete_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)

        if project_path is not None and project_path.exists():
            self._open_project_path(project_path)
        else:
            self._refresh_workspace(preserve_view=False)

    def _create_ui(self) -> None:
        """创建功能区、双栏工作区、统一工具面板和多信息状态栏。"""
        self.setObjectName("mainWindow")
        self.setWindowTitle("GIS桌面通用平台")
        self.resize(1680, 940)
        self.setMinimumSize(1120, 720)
        self.setMenuWidget(self._ribbon)

        map_workspace: QSplitter = QSplitter(Qt.Orientation.Horizontal)
        map_workspace.setObjectName("mapWorkspaceSplitter")
        map_workspace.setChildrenCollapsible(False)

        # 左侧图层面板独立占据整列；右侧地图区域使用可停靠属性表窗口，
        # 不再固定嵌入到地图下方。
        self._attribute_table_dock.setObjectName("attributeTableDock")
        self._attribute_table_dock.setWidget(self._attribute_table_panel)
        self._attribute_table_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        # 只允许拖动和浮动，使用自绘标题栏统一控制浮动与关闭，避免系统主题干扰。
        self._attribute_table_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self._attribute_table_dock.setTitleBarWidget(
            self._create_dock_title_bar(
                title="属性表",
                dock=self._attribute_table_dock,
                title_bar_object_name="attributeTableTitleBar",
                title_object_name="attributeTableDockTitle",
                float_button_object_name="attributeTableFloatButton",
                close_button_object_name="attributeTableCloseButton",
                close_callback=self._hide_attribute_table,
            )
        )
        self._attribute_table_dock.setMinimumWidth(400)
        self._attribute_table_dock.hide()
        self._attribute_table_dock.topLevelChanged.connect(
            self._on_attribute_table_top_level_changed
        )
        self.addDockWidget(
            Qt.DockWidgetArea.BottomDockWidgetArea, self._attribute_table_dock
        )

        map_workspace.addWidget(self._layer_panel)
        # 使用 QStackedWidget 在数据视图（MapCanvas）和布局视图（LayoutView）之间切换。
        self._view_stack.addWidget(self._map_canvas)
        self._view_stack.addWidget(self._layout_view)
        self._view_stack.setCurrentWidget(self._map_canvas)
        map_workspace.addWidget(self._view_stack)
        map_workspace.setSizes([300, 1380])
        map_workspace.setStretchFactor(0, 0)
        map_workspace.setStretchFactor(1, 1)
        self.setCentralWidget(map_workspace)
        self._panel_tabs.setObjectName("workspacePanelTabs")
        self._panel_tabs.setDocumentMode(True)
        self._panel_tabs.tabBar().setObjectName("workspacePanelTabBar")
        self._panel_tabs.tabBar().setMovable(False)
        self._panel_tabs.addTab(self._analysis_history_panel, "分析记录")
        self._panel_dock.setObjectName("workspacePanelDock")
        self._panel_dock.setWidget(self._panel_tabs)
        self._panel_dock.setTitleBarWidget(self._create_panel_title_bar())
        self._panel_dock.setMinimumWidth(360)
        self._panel_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._panel_dock)
        self._panel_dock.hide()

        status_bar: QStatusBar = QStatusBar(self)
        status_bar.setObjectName("mainStatusBar")
        status_bar.setSizeGripEnabled(False)
        status_bar.addWidget(self._ready_label)
        status_bar.addWidget(self._status_separator())
        status_bar.addWidget(self._coordinate_label)
        status_bar.addWidget(self._status_separator())
        status_bar.addWidget(self._scale_label)
        status_bar.addWidget(self._status_separator())
        status_bar.addWidget(self._layer_label)
        status_bar.addWidget(self._status_separator())
        status_bar.addWidget(self._selection_label)
        status_bar.addPermanentWidget(self._crs_label)
        self.setStatusBar(status_bar)

    def _connect_signals(self) -> None:
        """连接功能区、图层面板和地图画布的界面请求。"""
        # Qt 信号把控件操作转发给主窗口，控件本身不直接调用业务层。
        self._ribbon.action_triggered.connect(self._handle_action)
        self._layer_panel.layer_activated.connect(self._activate_layer)
        self._layer_panel.layer_visibility_changed.connect(self._change_visibility)
        self._layer_panel.layer_removed.connect(self._remove_layer)
        self._layer_panel.layer_folder_requested.connect(self._open_layer_folder)
        self._layer_panel.layer_attribute_requested.connect(self._show_attribute_table)
        self._layer_panel.layer_zoom_requested.connect(self._zoom_to_layer)
        self._layer_panel.layer_symbology_requested.connect(self._show_symbology)
        self._layer_panel.layer_labeling_changed.connect(self._change_labeling_visibility)
        self._layer_panel.layer_labeling_requested.connect(self._show_labeling)
        self._layer_panel.category_visibility_changed.connect(
            self._change_category_visibility
        )
        self._layer_panel.layer_move_requested.connect(self._move_layer)
        self._layer_panel.selection_cleared.connect(self._clear_active_layer)
        self._analysis_history_panel.clear_requested.connect(self._clear_analysis_history)
        self._map_canvas.coordinate_changed.connect(self._coordinate_label.setText)
        self._map_canvas.view_scale_changed.connect(self._scale_label.setText)
        self._map_canvas.canvas_clicked.connect(self._on_canvas_clicked)
        self._map_canvas.point_queried.connect(self._on_point_queried)
        self._map_canvas.rectangle_queried.connect(self._on_rectangle_queried)
        self._map_canvas.feature_digitized.connect(
            self._on_feature_digitized
        )
        self._map_canvas.geometry_edited.connect(
            self._on_geometry_edited
        )
        self._map_canvas.tool_changed.connect(self._on_map_tool_changed)
        self._attribute_table_panel.selection_changed.connect(
            self._on_table_selection_changed
        )
        self._attribute_table_panel.feature_zoom_requested.connect(
            self._on_table_zoom_requested
        )
        self._attribute_table_panel.query_requested.connect(
            self._on_attribute_table_query_requested
        )
        self._attribute_table_panel.add_feature_requested.connect(
            self._on_attribute_table_add_requested
        )
        self._attribute_table_panel.edit_feature_requested.connect(
            self._on_attribute_table_edit_requested
        )
        self._attribute_table_panel.delete_features_requested.connect(
            self._on_attribute_table_delete_requested
        )
        self._attribute_table_panel.close_requested.connect(
            self._hide_attribute_table
        )

    def _create_panel_title_bar(self) -> QWidget:
        """创建工作面板的统一浅色标题栏。"""
        return self._create_dock_title_bar(
            title="工作面板",
            dock=self._panel_dock,
            title_bar_object_name="workspacePanelTitleBar",
            title_object_name="workspacePanelTitle",
            float_button_object_name="workspacePanelFloatButton",
            close_button_object_name="workspacePanelCloseButton",
            close_callback=self._panel_dock.hide,
        )

    def _create_dock_title_bar(
        self,
        title: str,
        dock: QDockWidget,
        title_bar_object_name: str,
        title_object_name: str,
        float_button_object_name: str,
        close_button_object_name: str,
        close_callback: Callable[[], None],
    ) -> QWidget:
        """创建不依赖系统主题的停靠栏标题和操作按钮。"""
        title_bar: QWidget = QWidget()
        title_bar.setObjectName(title_bar_object_name)
        layout: QHBoxLayout = QHBoxLayout(title_bar)
        layout.setContentsMargins(10, 0, 4, 0)
        layout.setSpacing(3)

        title_label: QLabel = QLabel(title)
        title_label.setObjectName(title_object_name)
        layout.addWidget(title_label, 1)

        float_button: QToolButton = QToolButton()
        float_button.setObjectName(float_button_object_name)
        float_button.setIcon(self._panel_title_icon("float"))
        float_button.setIconSize(QSize(16, 16))
        float_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        float_button.setAccessibleName("浮动/停靠")
        float_button.setToolTip("浮动/停靠")
        float_button.clicked.connect(
            lambda: dock.setFloating(not dock.isFloating())
        )
        layout.addWidget(float_button)

        close_button: QToolButton = QToolButton()
        close_button.setObjectName(close_button_object_name)
        close_button.setIcon(self._panel_title_icon("close"))
        close_button.setIconSize(QSize(16, 16))
        close_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        close_label: str = f"关闭{title}"
        close_button.setAccessibleName(close_label)
        close_button.setToolTip(close_label)
        close_button.clicked.connect(close_callback)
        layout.addWidget(close_button)
        return title_bar

    @staticmethod
    def _panel_title_icon(kind: str) -> QIcon:
        """绘制不依赖系统字体的停靠栏按钮图标。"""
        pixmap: QPixmap = QPixmap(18, 18)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter: QPainter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen: QPen = QPen(Qt.GlobalColor.darkGray, 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        if kind == "close":
            painter.drawLine(4, 4, 14, 14)
            painter.drawLine(14, 4, 4, 14)
        else:
            painter.drawRect(3, 6, 8, 8)
            painter.drawLine(8, 3, 15, 3)
            painter.drawLine(15, 3, 15, 10)
            painter.drawLine(15, 3, 9, 9)
        painter.end()
        return QIcon(pixmap)

    def _show_workspace_panel(self, tab_index: int) -> None:
        """切换到指定工具面板并确保统一停靠容器获得焦点。"""
        self._panel_tabs.setCurrentIndex(tab_index)
        self._panel_dock.show()
        self._panel_dock.raise_()
        self._panel_dock.activateWindow()

    def _show_attribute_table_panel(self) -> None:
        """显示可停靠属性表窗口。"""
        self._attribute_table_dock.show()

    def _hide_attribute_table(self) -> None:
        """隐藏可停靠属性表窗口，保留当前图层和选择状态。"""
        self._attribute_table_dock.hide()

    def _on_attribute_table_top_level_changed(self, floating: bool) -> None:
        """属性表浮动/停靠时调整窗口尺寸和位置。"""
        if floating:
            self._attribute_table_dock.resize(680, 480)
            # 浮动窗口居中于主窗口。
            geo = self._attribute_table_dock.frameGeometry()
            geo.moveCenter(self.mapToGlobal(self.rect().center()))
            self._attribute_table_dock.move(geo.topLeft())
            self._attribute_table_dock.raise_()
            self._attribute_table_dock.activateWindow()

    def _workspace_panel_is_visible(self) -> bool:
        """返回统一工具面板是否处于可见状态。"""
        return self._panel_dock.isVisible()

    def _handle_action(self, action_id: str) -> None:
        """把稳定功能编号路由到已实现能力或预留接口。

        参数:
            action_id: 功能区按钮发出的稳定操作编号。

        说明:
            数据库连接、导入和加载通过应用层数据库服务执行；其余未实现入口
            明确保留为界面接口，不伪造业务结果或测试数据。
        """
        # 使用操作编号映射处理函数，避免大量重复的条件分支。
        implemented_actions: dict[str, Callable[[], None]] = {
            "open_data": self._open_data,
            "export_layer": self._export_data,
            "connect_database": self._connect_database,
            "disconnect_database": self._disconnect_database,
            "import_database": self._import_database,
            "load_database": self._load_database,
            "database_manager": self._database_manager,
            "new_project": self._new_project,
            "new_layer": self._new_layer,
            "open_project": self._open_project,
            "save_project": self._save_project_action,
            "zoom_in": self._map_canvas.zoom_in,
            "zoom_out": self._map_canvas.zoom_out,
            "pan": self._map_canvas.set_pan_tool,
            "zoom_rect": self._map_canvas.set_zoom_rect_tool,
            "full_extent": self._map_canvas.zoom_to_full_extent,
            "refresh_map": self._refresh_workspace,
            "clear_selection": self._clear_selection,
            "buffer_analysis": self._buffer_analysis,
            "overlay_analysis": self._overlay_analysis,
            "raster_calculator": self._raster_calculator,
            "analysis_history": self._toggle_analysis_history,
            "toggle_layers": self._toggle_layer_panel,
            "toggle_layout_view": self._toggle_layout_view,
            "add_feature": self._add_point_feature,
            "add_point_feature": self._add_point_feature,
            "add_line_feature": self._add_line_feature,
            "add_polygon_feature": self._add_polygon_feature,
            "delete_feature": self._delete_selected_features,
            "edit_feature": self._edit_selected_feature,
            "edit_geometry": self._edit_selected_geometry,
            "simplify_line": self._simplify_selected,
            "smooth_line": self._smooth_selected,
            "toggle_snapping": self._toggle_snapping,
            "point_query": self._point_query,
            "point_query_fast": lambda: self._point_query(fast=True, toggle=False),
            "point_query_precise": lambda: self._point_query(fast=False, toggle=False),
            "rectangle_query": self._rectangle_query,
            "attribute_query": self._attribute_query,
            "show_attributes": self._show_active_attribute_table,
            "set_crs": self._set_display_crs,
            "map_settings": self._show_display_settings,
            "about": self._show_about,
            "undo": self._undo,
            "redo": self._redo,
        }
        handler: Callable[[], None] | None = implemented_actions.get(action_id)
        if handler is not None:
            handler()
            return
        self._show_placeholder(RibbonBar.action_title(action_id) or "该功能")

    def _connect_database(self) -> None:
        """打开连接参数窗口，并测试 PostgreSQL/PostGIS 服务。"""
        dialog: DatabaseConnectionDialog = DatabaseConnectionDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            server_info = self._application.connect_database(dialog.config())
        except (ApplicationError, ValueError) as error:
            QMessageBox.warning(self, "连接数据库失败", str(error))
            return
        self._ready_label.setText(
            f"已连接  {server_info.database} · PostGIS {server_info.postgis_version}"
        )
        QMessageBox.information(
            self,
            "数据库连接成功",
            f"数据库：{server_info.database}\n"
            f"用户：{server_info.username}\n"
            f"PostGIS：{server_info.postgis_version}",
        )

    def _disconnect_database(self) -> None:
        """断开当前数据库连接并清理连接池资源。"""
        if not self._application.database_is_connected:
            self._ready_label.setText("数据库未连接")
            return
        try:
            self._application.disconnect_database()
        except ApplicationError as error:
            QMessageBox.warning(self, "断开数据库失败", str(error))
            return
        self._ready_label.setText("数据库已断开")

    def _import_database(self) -> None:
        """将当前活动矢量图层导入数据库。"""
        if not self._application.database_is_connected:
            QMessageBox.information(self, "导入图层", "请先连接 PostgreSQL/PostGIS 数据库。")
            return
        try:
            layer_info = self._application.import_active_layer_to_database()
        except (ApplicationError, ValueError) as error:
            QMessageBox.warning(self, "导入数据库失败", str(error))
            return
        self._ready_label.setText(
            f"已导入数据库  {layer_info.name} · {layer_info.feature_count} 个要素"
        )
        QMessageBox.information(
            self,
            "导入数据库成功",
            f"图层：{layer_info.name}\n"
            f"数据库图层 ID：{layer_info.layer_id}\n"
            f"要素数：{layer_info.feature_count}",
        )

    def _load_database(self) -> None:
        """从数据库图层目录选择一个图层并加入地图。"""
        if not self._application.database_is_connected:
            QMessageBox.information(self, "加载图层", "请先连接 PostgreSQL/PostGIS 数据库。")
            return
        try:
            layers = self._application.list_database_layers()
        except (ApplicationError, ValueError) as error:
            QMessageBox.warning(self, "读取数据库图层失败", str(error))
            return
        if not layers:
            QMessageBox.information(self, "加载图层", "当前数据库中没有可加载的图层。")
            return
        dialog: DatabaseLayerDialog = DatabaseLayerDialog(layers, "加载数据库图层", parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        db_layer_id: int = dialog.selected_layer_id()
        try:
            result = self._application.load_database_layer(db_layer_id)
        except (ApplicationError, ValueError) as error:
            QMessageBox.warning(self, "加载数据库图层失败", str(error))
            return
        # 可变单元：重做重新加载会产生新图层编号，撤销始终移除当前编号。
        current_layer_id: list[str] = [result.layer_id]
        self._push_undo(
            f"加载数据库图层  {result.layer_id}",
            undo_action=partial(self._remove_layer_record, current_layer_id),
            redo_action=partial(
                self._reload_database_layer,
                db_layer_id,
                current_layer_id,
            ),
        )
        self._refresh_workspace()
        self._ready_label.setText(f"已加载数据库图层  {result.layer_id}")

    def _database_manager(self) -> None:
        """展示当前数据库中的图层目录和要素数量。"""
        if not self._application.database_is_connected:
            QMessageBox.information(self, "数据管理", "请先连接 PostgreSQL/PostGIS 数据库。")
            return
        try:
            layers = self._application.list_database_layers()
        except (ApplicationError, ValueError) as error:
            QMessageBox.warning(self, "读取数据库图层失败", str(error))
            return
        DatabaseLayerDialog(
            layers,
            title="数据库图层管理",
            selection_required=False,
            parent=self,
        ).exec()

    def _open_data(self) -> None:
        """选择一个或多个空间数据文件，并逐个交给应用层读取。"""
        # 原生多选对话框同时支持单击、Ctrl 追加选择和 Shift 连续选择。
        path_strings: list[str] = QFileDialog.getOpenFileNames(
            self,
            "打开空间数据",
            "",
            "空间数据 (*.shp *.geojson *.json *.gpkg *.kml *.tif *.tiff *.img *.dem);;所有文件 (*.*)",
        )[0]
        if not path_strings:
            return

        loaded_paths: list[Path] = []
        failures: list[str] = []
        warnings: list[str] = []
        for path_string in path_strings:
            data_path: Path = Path(path_string)
            try:
                layer_name, accepted = self._select_geopackage_layer(data_path)
                if not accepted:
                    continue
                result = self._application.open_data(data_path, layer_name)
            except (ApplicationError, ValueError) as error:
                failures.append(f"{data_path.name}：{error}")
                continue
            loaded_paths.append(data_path)
            if result.warning:
                warnings.append(f"{data_path.name}：{result.warning}")
            # 可变单元：重做重新打开文件会生成新图层编号，撤销始终移除当前编号。
            current_layer_id: list[str] = [result.layer_id]
            self._push_undo(
                f"打开数据  {data_path.name}",
                undo_action=partial(self._remove_layer_record, current_layer_id),
                redo_action=partial(
                    self._reopen_data,
                    data_path,
                    layer_name,
                    current_layer_id,
                ),
            )

        if loaded_paths:
            # 全部文件处理完后只刷新一次，避免大批量导入时反复重绘地图。
            self._refresh_workspace()
            if len(loaded_paths) == 1:
                self._ready_label.setText(f"已加载  {loaded_paths[0].name}")
            else:
                self._ready_label.setText(f"已加载  {len(loaded_paths)} 个数据")
        if warnings:
            self.statusBar().showMessage("；".join(warnings), 5000)
        if failures:
            title: str = "部分数据打开失败" if loaded_paths else "打开数据失败"
            QMessageBox.warning(self, title, "\n".join(failures))

    def _select_geopackage_layer(self, data_path: Path) -> tuple[str | None, bool]:
        """为 GeoPackage 选择内部图层，其他格式直接允许读取。

        参数:
            data_path: 用户选择的空间数据文件。

        返回:
            内部图层名称和是否继续读取；用户取消选择时仅跳过当前文件。
        """
        if data_path.suffix.lower() != ".gpkg":
            return None, True
        layer_names: tuple[str, ...] = self._data_reader.list_layers(data_path)
        if len(layer_names) > 1:
            layer_name, accepted = QInputDialog.getItem(
                self,
                "选择 GeoPackage 图层",
                "图层：",
                list(layer_names),
                0,
                False,
            )
            return (layer_name or None), bool(accepted and layer_name)
        if layer_names:
            return layer_names[0], True
        return None, True

    def _export_data(self) -> None:
        """选择活动图层的输出位置并执行真实空间数据导出。"""
        snapshot: WorkspaceSnapshot = self._application.snapshot()
        active_layer: LayerSnapshot | None = next(
            (
                layer
                for layer in snapshot.layers
                if layer.layer_id == snapshot.active_layer_id
            ),
            None,
        )
        if active_layer is None:
            self.statusBar().showMessage("请先打开并选择一个图层。", 3500)
            return

        if active_layer.is_raster:
            suggested_path: str = f"{active_layer.name}.tif"
            filters: str = "GeoTIFF (*.tif *.tiff)"
        else:
            suggested_path = f"{active_layer.name}.geojson"
            filters = "GeoJSON (*.geojson);;GeoPackage (*.gpkg);;Shapefile (*.shp)"
        path_string, selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出空间数据",
            suggested_path,
            filters,
        )
        if not path_string:
            return
        output_path: Path = self._with_export_suffix(
            Path(path_string),
            selected_filter,
            active_layer.is_raster,
        )
        try:
            layer_name: str | None = (
                active_layer.name if output_path.suffix.lower() == ".gpkg" else None
            )
            result = self._application.export_active_layer(output_path, layer_name)
        except (ApplicationError, ValueError) as error:
            QMessageBox.warning(self, "导出数据失败", str(error))
            return
        self._ready_label.setText(f"已导出  {result.path.name}")
        QMessageBox.information(
            self,
            "导出数据成功",
            f"空间数据已导出到：\n{result.path}",
        )

    def _cleanup_query_and_selection(self) -> None:
        """退出查询/数字化模式并清空所有要素选择。

        在关闭、新建或打开工程之前调用，确保无论用户保存与否，
        查询工具状态和选择集都不会残留到下一个工作区。
        """
        # 退出查询和数字化模式，恢复默认平移工具。
        # set_pan_tool 内部调用 _deactivate_all_tools() 会同时关闭
        # 点选/框选/数字化/顶点编辑等全部特殊工具。
        self._map_canvas.set_pan_tool()
        self._set_active_query_action(None)
        self._set_active_digitize_action(None)
        # 清空所有图层的要素选择（不推入撤销栈，避免在切换工程时
        # 残留的撤销记录引用已销毁的领域对象）。
        self._application.clear_selection()
        # 隐藏属性表，关闭几何编辑工具栏，退出布局视图。
        self._hide_attribute_table()
        self._geom_edit_toolbar.hide()
        self._exit_layout_mode()
        self._ready_label.setText("就绪")

    def _new_project(self) -> None:
        """新建空白工程，并在需要时处理当前未保存修改。"""
        self._cleanup_query_and_selection()
        if not self._confirm_project_switch():
            return
        self._application.new_project()
        self._clear_undo_history()
        self._refresh_workspace(preserve_view=False)
        self._ready_label.setText("已新建空白工程")

    def _new_layer(self) -> None:
        """弹出新建空白图层对话框并创建空图层加入地图。"""
        snapshot: WorkspaceSnapshot = self._application.snapshot()
        dialog: NewLayerDialog = NewLayerDialog(
            display_crs=snapshot.display_crs,
            parent=self,
        )
        if dialog.exec() != NewLayerDialog.DialogCode.Accepted:
            return

        name: str = dialog.layer_name()
        geometry_family: GeometryFamily = dialog.geometry_family()
        crs_text: str = dialog.crs_text()

        # 解析坐标系：用户输入优先，否则使用地图 CRS。
        try:
            if crs_text:
                crs: CRS | None = CRS.from_user_input(crs_text)
            elif snapshot.display_crs is not None:
                crs = snapshot.display_crs
            else:
                QMessageBox.warning(
                    self,
                    "缺少坐标系",
                    "当前地图没有坐标系，请在对话框中输入坐标系（如 EPSG:4326）。",
                )
                return
        except CRSError:
            QMessageBox.warning(
                self,
                "坐标系无效",
                f"无法识别坐标系输入：{crs_text}",
            )
            return

        try:
            result: OpenDataResult = self._application.create_empty_layer(
                name=name,
                geometry_family=geometry_family,
                crs=crs,
            )
        except ValueError as error:
            QMessageBox.warning(self, "新建图层失败", str(error))
            return

        self._push_undo(
            description=f"新建空白图层“{name}”",
            undo_action=partial(self._application.remove_layer, result.layer_id),
            redo_action=partial(
                self._application.create_empty_layer,
                name=name,
                geometry_family=geometry_family,
                crs=crs,
            ),
        )
        self._refresh_workspace()
        self._ready_label.setText(f"已新建空白图层  {name}")
        if result.warning:
            self.statusBar().showMessage(result.warning, 8000)

    def _open_project_path(self, path: Path) -> None:
        """直接打开指定工程文件（用于启动对话框）。

        参数:
            path: 工程文件路径。
        """
        self._cleanup_query_and_selection()
        try:
            result = self._application.open_project(path)
        except ApplicationError as error:
            QMessageBox.warning(self, "打开工程失败", str(error))
            self._refresh_workspace(preserve_view=False)
            return
        self._clear_undo_history()
        save_recent_project(path)
        self._refresh_workspace(
            view_state=result.view_state, preserve_view=False
        )
        self._restore_layout(result.layout_state)
        for warning in result.warnings:
            self.statusBar().showMessage(warning, 5000)
        self._ready_label.setText(f"已打开工程  {path.name}")

    def _open_project(self) -> None:
        """选择工程文件并恢复其中的图层、结果和分析历史。"""
        path_string: str = QFileDialog.getOpenFileName(
            self,
            "打开 GIS 工程",
            "",
            "GIS 工程 (*.gisproj);;所有文件 (*.*)",
        )[0]
        if not path_string:
            return
        self._cleanup_query_and_selection()
        if not self._confirm_project_switch():
            return
        try:
            result = self._application.open_project(Path(path_string))
        except (ApplicationError, ValueError) as error:
            QMessageBox.warning(self, "打开工程失败", str(error))
            return
        self._clear_undo_history()
        self._refresh_workspace(result.view_state)
        self._restore_layout(result.layout_state)
        self._ready_label.setText(f"已打开工程  {result.path.name}")
        save_recent_project(Path(path_string))
        if result.warnings:
            self.statusBar().showMessage("；".join(result.warnings), 8000)

    def _save_project(self) -> bool:
        """保存当前工程快照；未命名工程先选择工程路径。"""
        project_path: Path | None = self._application.project_path
        if project_path is None:
            path_string: str = QFileDialog.getSaveFileName(
                self,
                "保存 GIS 工程",
                "未命名工程.gisproj",
                "GIS 工程 (*.gisproj)",
            )[0]
            if not path_string:
                return False
            project_path = Path(path_string)
        if not project_path.suffix:
            project_path = project_path.with_suffix(".gisproj")
        try:
            result = self._application.save_project(
                project_path,
                self._map_canvas.capture_view_state(),
                self._layout_view.document(),
            )
        except (ApplicationError, ValueError) as error:
            QMessageBox.warning(self, "保存工程失败", str(error))
            return False
        self._ready_label.setText(f"工程已保存  {result.path.name}")
        save_recent_project(result.path)
        return True

    def _save_project_action(self) -> None:
        """适配功能区无返回值的保存工程操作。"""
        self._save_project()

    def _activate_layer(self, layer_id: str) -> None:
        """设置活动图层并仅刷新状态栏和符号面板，不重建任何控件。

        参数:
            layer_id: 图层面板选中的真实图层编号。

        说明:
            图层激活只影响状态栏标签和符号系统面板，不涉及地图显示
            或图层树重建。若在此处调用 apply_snapshot 重建图层树，会
            在拖拽排序过程中因 QTreeWidget 内部 currentItem 变化而
            触发同步树清空，导致 drop 目标位置被重置为相邻行。
        """
        self._application.set_active_layer(layer_id)
        snapshot: WorkspaceSnapshot = self._application.snapshot()
        active_name: str = "无"
        for layer in snapshot.layers:
            if layer.layer_id == snapshot.active_layer_id:
                active_name = layer.name
        self._layer_label.setText(f"当前图层  {active_name}")

    def _clear_active_layer(self) -> None:
        """点击图层面板空白处时清除活动图层。"""
        try:
            self._application.clear_active_layer()
        except ApplicationError:
            pass
        self._layer_label.setText("当前图层  无")

    def _on_canvas_clicked(self) -> None:
        """点击地图画布时取消图层面板选中。"""
        self._layer_panel.clear_layer_selection()

    def _change_visibility(self, layer_id: str, visible: bool) -> None:
        """更新图层显隐状态并刷新工作区。

        参数:
            layer_id: 需要更新的真实图层编号。
            visible: 图层是否参与地图绘制和空间查询。
        """
        self._push_undo(
            "图层显隐",
            undo_action=partial(
                self._application.set_layer_visibility, layer_id, not visible
            ),
            redo_action=partial(
                self._application.set_layer_visibility, layer_id, visible
            ),
        )
        self._application.set_layer_visibility(layer_id, visible)
        self._schedule_workspace_refresh()

    def _remove_layer(self, layer_id: str) -> None:
        """删除指定图层并刷新工作区；删除可撤销恢复原图层。

        参数:
            layer_id: 需要从地图文档移除的真实图层编号。
        """
        snapshot: WorkspaceSnapshot = self._application.snapshot()
        old_index: int | None = None
        layer_snapshot: LayerSnapshot | None = None
        for i, layer in enumerate(snapshot.layers):
            if layer.layer_id == layer_id:
                old_index = i
                layer_snapshot = layer
                break
        if layer_snapshot is None or old_index is None:
            return
        was_active: bool = snapshot.active_layer_id == layer_id
        self._application.remove_layer(layer_id)
        self._push_undo(
            f"删除图层  {layer_snapshot.name}",
            undo_action=partial(
                self._restore_deleted_layer,
                layer_snapshot,
                old_index,
                was_active,
            ),
            redo_action=partial(self._application.remove_layer, layer_id),
        )
        self._schedule_workspace_refresh()

    def _open_layer_folder(self, layer_id: str) -> None:
        """在操作系统中打开图层数据文件所在文件夹。

        参数:
            layer_id: 待定位数据文件的图层编号。

        说明:
            数据库图层和未导出的数字化临时图层没有本地数据文件，
            此时在状态栏提示而不是打开任何文件夹。
        """
        for layer in self._application.snapshot().layers:
            if layer.layer_id != layer_id:
                continue
            source_path: Path | None = layer.layer.source_path
            if source_path is None:
                self.statusBar().showMessage(f"图层“{layer.name}”没有本地数据文件。")
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(source_path.parent)))
            return

    def _restore_deleted_layer(
        self,
        layer_snapshot: LayerSnapshot,
        old_index: int,
        was_active: bool,
    ) -> None:
        """撤销删除：按原位置、显隐与选择状态恢复已删除图层。

        add_layer 只把图层加到顶层且默认可见，需要 move_layer 归位、
        set_layer_visibility 恢复显隐；隐藏状态下文档不保留选择，
        故仅在原图层可见且存在选择时恢复选择。
        """
        self._application.add_layer(layer_snapshot.layer)
        self._application.move_layer(layer_snapshot.layer_id, old_index)
        if not layer_snapshot.visible:
            self._application.set_layer_visibility(layer_snapshot.layer_id, False)
        # 合并当前各图层选择与待恢复图层的原选择，避免单层 set_selection 互相覆盖。
        selections: dict[str, tuple[FeatureId, ...]] = self._capture_selections()
        if layer_snapshot.selected_feature_ids and layer_snapshot.visible:
            selections[layer_snapshot.layer_id] = layer_snapshot.selected_feature_ids
        if selections:
            try:
                self._application.restore_selections(selections)
            except ApplicationError:
                pass
        if was_active:
            try:
                self._application.set_active_layer(layer_snapshot.layer_id)
            except ApplicationError:
                pass

    def _remove_layer_record(self, current_layer_id: list[str]) -> None:
        """撤销打开数据：移除该条记录当前对应的图层。

        参数:
            current_layer_id: 可变单元，保存该记录当前实际存在的图层编号。
        """
        self._application.remove_layer(current_layer_id[0])

    def _reopen_data(
        self,
        data_path: Path,
        layer_name: str | None,
        current_layer_id: list[str],
    ) -> None:
        """重做打开数据：重新读取文件并刷新当前图层编号。

        重新打开会产生新的图层编号，因此撤销时只能移除当前编号的图层。

        参数:
            data_path: 待重新读取的空间数据文件路径。
            layer_name: 首次加载时解析的容器内部图层名；为空表示非容器格式。
            current_layer_id: 可变单元，更新为该次重开产生的图层编号。
        """
        result = self._application.open_data(data_path, layer_name)
        current_layer_id[0] = result.layer_id

    def _reload_database_layer(
        self, db_layer_id: int, current_layer_id: list[str]
    ) -> None:
        """重做加载数据库图层：重新按数据库编号加载并刷新当前图层编号。

        参数:
            db_layer_id: 数据库中的图层编号。
            current_layer_id: 可变单元，更新为该次加载产生的图层编号。
        """
        result = self._application.load_database_layer(db_layer_id)
        current_layer_id[0] = result.layer_id

    def _move_layer(self, layer_id: str, target_index: int) -> None:
        """按照图层面板请求调整真实地图图层顺序。

        参数:
            layer_id: 需要移动的真实图层编号。
            target_index: 图层在从底到顶显示顺序中的目标位置。
        """
        snapshot: WorkspaceSnapshot = self._application.snapshot()
        for i, layer in enumerate(snapshot.layers):
            if layer.layer_id == layer_id:
                old_index: int = i
                break
        else:
            return
        self._push_undo(
            "图层排序",
            undo_action=partial(self._application.move_layer, layer_id, old_index),
            redo_action=partial(self._application.move_layer, layer_id, target_index),
        )
        self._application.move_layer(layer_id, target_index)
        self._schedule_workspace_refresh()

    def _show_attribute_table(self, layer_id: str) -> None:
        """打开指定图层的可停靠属性表面板。

        参数:
            layer_id: 需要查看属性或元数据的真实图层编号。
        """
        snapshot: WorkspaceSnapshot = self._application.snapshot()
        layer_snapshot: LayerSnapshot | None = next(
            (layer for layer in snapshot.layers if layer.layer_id == layer_id),
            None,
        )
        if layer_snapshot is None:
            return
        self._attribute_table_panel.set_layer(layer_snapshot)
        self._show_attribute_table_panel()
        # 同步当前地图选择到属性表。
        if layer_snapshot.layer_id in (
            snapshot.active_layer_id or "",
        ):
            selected: set = set(layer_snapshot.selected_feature_ids)
            self._attribute_table_panel.highlight_features(selected)

    def _zoom_to_layer(self, layer_id: str) -> None:
        """将地图画布定位到指定工作区图层的完整范围。

        参数:
            layer_id: 图层面板右键请求定位的真实图层编号。
        """
        layer_snapshot: LayerSnapshot | None = next(
            (
                layer
                for layer in self._application.snapshot().layers
                if layer.layer_id == layer_id
            ),
            None,
        )
        if layer_snapshot is None:
            return
        self._map_canvas.zoom_to_layer(layer_snapshot.bounds)
        self._ready_label.setText(f"已缩放至图层  {layer_snapshot.name}")

    def _show_symbology(self, layer_id: str) -> None:
        """激活图层并打开含符号系统的显示设置对话框。"""
        try:
            self._application.set_active_layer(layer_id)
        except ApplicationError:
            return
        self._refresh_workspace()
        self._show_display_settings(active_tab=0)

    def _change_labeling_visibility(self, layer_id: str, enabled: bool) -> None:
        """响应图层右键菜单的标注开关，并在首次开启时创建默认标注类。"""
        layer_snapshot: LayerSnapshot | None = self._layer_snapshot(layer_id)
        if layer_snapshot is None or not isinstance(layer_snapshot.layer, VectorLayer):
            return
        before: LabelingConfig | None = layer_snapshot.layer.labeling
        if enabled:
            config: LabelingConfig = before or default_labeling_for_features(
                layer_snapshot.layer.features
            )
            if not config.classes:
                QMessageBox.information(
                    self,
                    "无法开启标注",
                    "当前图层没有可用于标注的属性字段。",
                )
                return
            after: LabelingConfig = replace(config, enabled=True)
        else:
            if before is None:
                return
            after = replace(before, enabled=False)
        try:
            self._application.set_layer_labeling(layer_id, after)
        except (ApplicationError, ValueError) as error:
            QMessageBox.warning(self, "标注更新失败", str(error))
            return
        self._push_undo(
            "切换图层标注",
            undo_action=partial(self._application.set_layer_labeling, layer_id, before),
            redo_action=partial(self._application.set_layer_labeling, layer_id, after),
        )
        self._refresh_workspace()

    def _show_labeling(self, layer_id: str) -> None:
        """打开目标图层的标注分类窗口，并在确认后一次性应用配置。"""
        layer_snapshot: LayerSnapshot | None = self._layer_snapshot(layer_id)
        if layer_snapshot is None or not isinstance(layer_snapshot.layer, VectorLayer):
            return
        try:
            self._application.set_active_layer(layer_id)
        except ApplicationError:
            return
        self._refresh_workspace()
        dialog: LabelingDialog = LabelingDialog(layer_snapshot, self)
        dialog.resize(820, 620)
        parent_geometry = self.frameGeometry()
        dialog_geometry = dialog.frameGeometry()
        dialog_geometry.moveCenter(parent_geometry.center())
        dialog.move(dialog_geometry.topLeft())
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result_config is None:
            return
        before: LabelingConfig | None = layer_snapshot.layer.labeling
        after: LabelingConfig = dialog.result_config
        try:
            self._application.set_layer_labeling(layer_id, after)
        except (ApplicationError, ValueError) as error:
            QMessageBox.warning(self, "标注更新失败", str(error))
            return
        self._push_undo(
            "修改标注分类",
            undo_action=partial(self._application.set_layer_labeling, layer_id, before),
            redo_action=partial(self._application.set_layer_labeling, layer_id, after),
        )
        self._refresh_workspace()

    def _layer_snapshot(self, layer_id: str) -> LayerSnapshot | None:
        """按图层编号返回当前工作区快照中的图层。"""
        return next(
            (
                layer
                for layer in self._application.snapshot().layers
                if layer.layer_id == layer_id
            ),
            None,
        )

    def _current_symbology(
        self, layer_id: str
    ) -> VectorSymbology | RasterSymbology | None:
        """读取指定图层当前符号配置；图层不存在时返回空值。"""
        for layer in self._application.snapshot().layers:
            if layer.layer_id == layer_id:
                return getattr(layer.layer, "symbology", None)
        return None

    def _apply_symbology_state(
        self,
        layer_id: str,
        symbology: VectorSymbology | RasterSymbology,
    ) -> None:
        """按符号类型将矢量或栅格符号配置应用到图层（撤销/重做共用）。"""
        if isinstance(symbology, VectorSymbology):
            self._application.apply_vector_symbology(layer_id, symbology)
        else:
            self._application.apply_raster_symbology(layer_id, symbology)

    def _apply_symbology(
        self,
        layer_id: str,
        symbology: VectorSymbology | RasterSymbology,
    ) -> None:
        """自动应用面板提交的完整矢量或栅格符号配置。"""
        before: VectorSymbology | RasterSymbology | None = self._current_symbology(
            layer_id
        )
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._apply_symbology_state(layer_id, symbology)
        except (ApplicationError, ValueError) as error:
            QMessageBox.warning(self, "符号系统更新失败", str(error))
            return
        finally:
            QApplication.restoreOverrideCursor()
        if before is not None:
            self._push_undo(
                "修改符号系统",
                undo_action=partial(self._apply_symbology_state, layer_id, before),
                redo_action=partial(self._apply_symbology_state, layer_id, symbology),
            )
        self._refresh_workspace()

    def _apply_unique_symbology(
        self,
        layer_id: str,
        field_name: str,
        color_scheme: str,
    ) -> None:
        """生成并自动应用唯一值符号。"""
        before: VectorSymbology | RasterSymbology | None = self._current_symbology(
            layer_id
        )
        try:
            self._application.apply_unique_value_symbology(
                layer_id,
                field_name,
                color_scheme,
            )
        except (ApplicationError, ValueError) as error:
            QMessageBox.warning(self, "唯一值符号更新失败", str(error))
            return
        after: VectorSymbology | RasterSymbology | None = self._current_symbology(
            layer_id
        )
        # 唯一值符号只能作用于矢量图层，应用成功前后符号必为矢量配置。
        if isinstance(before, VectorSymbology) and isinstance(after, VectorSymbology):
            self._push_undo(
                "唯一值符号",
                undo_action=partial(
                    self._application.apply_vector_symbology, layer_id, before
                ),
                redo_action=partial(
                    self._application.apply_vector_symbology, layer_id, after
                ),
            )
        self._refresh_workspace()

    def _apply_graduated_symbology(
        self,
        layer_id: str,
        field_name: str,
        color_scheme: str,
        method: str,
        class_count: int,
    ) -> None:
        """生成并自动应用数值分级颜色。"""
        before: VectorSymbology | RasterSymbology | None = self._current_symbology(
            layer_id
        )
        try:
            self._application.apply_graduated_symbology(
                layer_id,
                field_name,
                color_scheme,
                method,
                class_count,
            )
        except (ApplicationError, ValueError) as error:
            QMessageBox.warning(self, "分级颜色更新失败", str(error))
            return
        after: VectorSymbology | RasterSymbology | None = self._current_symbology(
            layer_id
        )
        # 分级颜色只能作用于矢量图层，应用成功前后符号必为矢量配置。
        if isinstance(before, VectorSymbology) and isinstance(after, VectorSymbology):
            self._push_undo(
                "分级颜色",
                undo_action=partial(
                    self._application.apply_vector_symbology, layer_id, before
                ),
                redo_action=partial(
                    self._application.apply_vector_symbology, layer_id, after
                ),
            )
        self._refresh_workspace()

    def _change_category_visibility(
        self,
        layer_id: str,
        category_index: int,
        visible: bool,
    ) -> None:
        """从图层树图例复选框更新单个矢量类别显隐。"""
        layer_snapshot = next(
            (
                layer
                for layer in self._application.snapshot().layers
                if layer.layer_id == layer_id
            ),
            None,
        )
        if layer_snapshot is None or not hasattr(layer_snapshot.layer, "symbology"):
            return
        symbology = layer_snapshot.layer.symbology
        if not isinstance(symbology, VectorSymbology):
            return
        if symbology.unique_classes:
            if category_index == len(symbology.unique_classes):
                updated = replace(symbology, other_visible=visible)
            elif 0 <= category_index < len(symbology.unique_classes):
                classes = list(symbology.unique_classes)
                classes[category_index] = replace(classes[category_index], visible=visible)
                updated = replace(symbology, unique_classes=tuple(classes))
            else:
                return
        elif 0 <= category_index < len(symbology.graduated_classes):
            classes2 = list(symbology.graduated_classes)
            classes2[category_index] = replace(classes2[category_index], visible=visible)
            updated = replace(symbology, graduated_classes=tuple(classes2))
        else:
            return
        self._application.apply_vector_symbology(layer_id, updated)
        self._push_undo(
            "类别显隐",
            undo_action=partial(
                self._application.apply_vector_symbology, layer_id, symbology
            ),
            redo_action=partial(
                self._application.apply_vector_symbology, layer_id, updated
            ),
        )
        self._refresh_workspace()

    def _show_active_attribute_table(self) -> None:
        """打开活动图层属性表；无图层时显示轻量提示。"""
        active_layer_id: str | None = self._application.snapshot().active_layer_id
        if active_layer_id is None:
            self.statusBar().showMessage("请先打开并选择一个图层。", 3500)
            return
        self._show_attribute_table(active_layer_id)

    def _on_table_selection_changed(
        self, layer_id: str, feature_ids: tuple
    ) -> None:
        """属性表选中行变化时同步更新地图要素选择和画布。

        参数:
            layer_id: 属性表当前展示的图层编号。
            feature_ids: 用户在表中选中的要素编号元组。
        """
        try:
            # 恢复属性表对应图层为活动图层，确保图层面板重新选中该图层。
            self._application.set_active_layer(layer_id)
            self._application.set_selection(layer_id, feature_ids)
        except ApplicationError:
            return
        self._refresh_workspace()

    def _on_table_zoom_requested(self, layer_id: str, fid: FeatureId) -> None:
        """双击属性表行时缩放到对应要素的几何范围。

        参数:
            layer_id: 属性表当前展示的图层编号。
            fid: 被双击要素的唯一编号（可能为 str 或 int）。
        """
        snapshot: WorkspaceSnapshot = self._application.snapshot()
        for layer in snapshot.layers:
            if layer.layer_id != layer_id:
                continue
            if not isinstance(layer.layer, VectorLayer):
                return
            for feature in layer.layer.features:
                # 使用字符串化比较避免 int/str 类型不匹配。
                if str(feature.fid) == str(fid):
                    self._map_canvas.zoom_to_feature(feature.geometry.bounds)
                    self._ready_label.setText(
                        f"已缩放至要素  FID {fid} · {layer.name}"
                    )
                    return
            self.statusBar().showMessage(
                f"未找到要素 FID {fid} · {layer.name}", 4000
            )
            return
        self.statusBar().showMessage("属性表对应图层已不在工作区中", 4000)

    def _on_attribute_table_query_requested(self, layer_id: str) -> None:
        """从底部属性表打开当前图层的属性查询。"""
        self._attribute_query(layer_id=layer_id, toggle=False)

    def _on_attribute_table_add_requested(self, layer_id: str) -> None:
        """从底部属性表启动当前图层的几何新增流程。"""
        layer_snapshot: LayerSnapshot | None = next(
            (
                layer
                for layer in self._application.snapshot().layers
                if layer.layer_id == layer_id
            ),
            None,
        )
        if layer_snapshot is None or not isinstance(layer_snapshot.layer, VectorLayer):
            return
        mode_by_family: dict[GeometryFamily, tuple[str, str]] = {
            GeometryFamily.POINT: ("point", "点"),
            GeometryFamily.LINE: ("line", "线"),
            GeometryFamily.POLYGON: ("polygon", "面"),
        }
        family = layer_snapshot.layer.geometry_family
        if family is None:
            QMessageBox.information(
                self,
                "新增要素",
                "当前图层无法确定几何类型，请先补充有效空间要素。",
            )
            return
        mode_label: tuple[str, str] | None = mode_by_family.get(family)
        if mode_label is None:
            QMessageBox.information(
                self,
                "新增要素",
                "当前图层是混合几何类型，请使用功能区中的点、线或面新增入口。",
            )
            return
        self._start_digitize(
            mode_label[0], mode_label[1], target_layer_id=layer_snapshot.layer_id
        )

    def _on_attribute_table_edit_requested(
        self, layer_id: str, fid: FeatureId
    ) -> None:
        """把属性表选中的要素交给现有属性编辑流程。"""
        try:
            self._application.set_selection(layer_id, (fid,))
        except ApplicationError:
            return
        self._edit_selected_feature()
        self._refresh_attribute_table()

    def _on_attribute_table_delete_requested(
        self, layer_id: str, fids: tuple
    ) -> None:
        """把属性表选中的要素交给现有删除流程。"""
        try:
            self._application.set_selection(layer_id, fids)
        except ApplicationError:
            return
        self._delete_selected_features()
        self._refresh_attribute_table()

    def _set_active_query_action(self, action_id: str | None) -> None:
        """同步当前查询模式和三个功能区按钮的互斥高亮状态。"""
        self._active_query_action = action_id
        for query_action in ("point_query", "rectangle_query", "attribute_query"):
            self._ribbon.set_action_checked(query_action, query_action == action_id)

    def _set_active_digitize_action(self, action_id: str | None) -> None:
        """同步当前数字化模式和三个功能区按钮的互斥高亮状态。"""
        self._active_digitize_action = action_id
        for digitize_action in ("add_point_feature", "add_line_feature", "add_polygon_feature"):
            self._ribbon.set_action_checked(
                digitize_action, digitize_action == action_id
            )

    def _on_map_tool_changed(self, tool_id: str) -> None:
        """地图工具变化时同步查询/数字化按钮，Esc 和其他工具切换也能取消高亮。"""
        query_action: str | None = {
            "point_query": "point_query",
            "rectangle_query": "rectangle_query",
        }.get(tool_id)
        self._set_active_query_action(query_action)
        digitize_action: str | None = {
            "digitize_point": "add_point_feature",
            "digitize_line": "add_line_feature",
            "digitize_polygon": "add_polygon_feature",
        }.get(tool_id)
        self._set_active_digitize_action(digitize_action)

    def _exit_query_mode(self) -> None:
        """退出当前查询模式并恢复默认平移工具。"""
        self._map_canvas.set_pan_tool()
        self._ready_label.setText("就绪")

    def _point_query(self, fast: bool = False, toggle: bool = True) -> None:
        """激活地图点选查询工具。

        参数:
            fast: True 为快速模式（直接取最近要素），False 为精确模式（多候选弹窗）。
            toggle: True 时再次选择点选按钮会退出查询；子菜单切换查询精度时为 False。
        """
        if toggle and self._active_query_action == "point_query":
            self._exit_query_mode()
            return
        if not self._map_canvas.has_map_data:
            self._set_active_query_action(None)
            self.statusBar().showMessage("请先打开一个图层。", 3500)
            return
        self._point_query_fast = fast
        self._map_canvas.set_point_query_tool()
        mode: str = "快速查询" if fast else "精确查询"
        self._ready_label.setText(
            f"{mode}：点击要素选择  |  Shift+点击追加/切换  |  Esc 退出"
        )

    def _rectangle_query(self) -> None:
        """激活地图框选查询工具。"""
        if self._active_query_action == "rectangle_query":
            self._exit_query_mode()
            return
        if not self._map_canvas.has_map_data:
            self._set_active_query_action(None)
            self.statusBar().showMessage("请先打开一个图层。", 3500)
            return
        self._map_canvas.set_rectangle_query_tool()
        self._ready_label.setText(
            "框选查询：拖拽矩形选择  |  Shift+拖拽追加"
        )

    def _attribute_query(
        self, layer_id: str | None = None, toggle: bool = True
    ) -> None:
        """打开属性查询对话框，按字段条件筛选要素。"""
        if toggle and self._active_query_action == "attribute_query":
            self._exit_query_mode()
            return
        snapshot: WorkspaceSnapshot = self._application.snapshot()
        vector_layers: tuple[LayerSnapshot, ...] = tuple(
            layer
            for layer in snapshot.layers
            if not layer.is_raster and (layer_id is None or layer.layer_id == layer_id)
        )
        if not vector_layers:
            self._set_active_query_action(None)
            self.statusBar().showMessage(
                "当前工作区没有可查询的矢量图层。", 4000
            )
            return
        # 属性查询不消费画布鼠标事件，但仍作为互斥查询模式持续高亮。
        self._map_canvas.set_pan_tool()
        self._set_active_query_action("attribute_query")
        dialog: AttributeQueryDialog = AttributeQueryDialog(
            vector_layers, self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        request: AttributeQueryRequest = dialog.request()
        before_selections: dict[str, tuple[FeatureId, ...]] = self._capture_selections()
        try:
            result = self._application.select_by_attribute(
                request.layer_id,
                request.field_name,
                request.operator,
                request.value,
            )
        except ApplicationError as error:
            QMessageBox.warning(self, "属性查询失败", str(error))
            return
        after_selections: dict[str, tuple[FeatureId, ...]] = self._capture_selections()
        self._push_selection_undo(
            "属性查询", before_selections, after_selections
        )
        self._refresh_workspace()
        self._ready_label.setText(
            f"属性查询：匹配 {result.count} 个要素"
        )

    def _add_point_feature(self) -> None:
        """激活点要素数字化工具。"""
        self._start_digitize("point", "点")

    def _add_line_feature(self) -> None:
        """激活线要素数字化工具。"""
        self._start_digitize("line", "线")

    def _add_polygon_feature(self) -> None:
        """激活面要素数字化工具。"""
        self._start_digitize("polygon", "面")

    def _start_digitize(
        self, mode: str, label: str, target_layer_id: str | None = None
    ) -> None:
        """激活数字化工具并更新状态栏。

        参数:
            mode: "point"/"line"/"polygon"。
            label: 中文几何类型名。
        """
        # 检查当前是否有 CRS。
        snapshot: WorkspaceSnapshot = self._application.snapshot()
        if snapshot.display_crs is None:
            QMessageBox.information(
                self,
                "新增要素",
                "请先打开一个具有坐标系的图层以确定地图坐标系。",
            )
            return
        # 新增的要素将追加到矢量图层；功能区入口让用户选择目标图层，
        # 属性表入口则传入当前图层编号，避免重复选择。
        expected_family: GeometryFamily = {
            "point": GeometryFamily.POINT,
            "line": GeometryFamily.LINE,
            "polygon": GeometryFamily.POLYGON,
        }[mode]
        options: list[TargetLayerOption] = []
        for layer in snapshot.layers:
            if not isinstance(layer.layer, VectorLayer):
                continue
            family: GeometryFamily | None = layer.geometry_family
            if family != GeometryFamily.MIXED and family != expected_family:
                continue
            source_path: Path | None = layer.layer.source_path
            if source_path is None or source_path.suffix.lower() not in {
                ".shp",
                ".geojson",
            }:
                continue
            options.append(
                TargetLayerOption(
                    layer_id=layer.layer_id,
                    name=layer.name,
                    description=(
                        f"{self._geometry_family_label(family)} · "
                        f"{len(layer.layer.features)} 个要素 · "
                        f"{source_path.suffix.lstrip('.')}"
                    ),
                )
            )
        if target_layer_id is None:
            if not options:
                QMessageBox.information(
                    self,
                    "新增要素",
                    f"没有可用于添加{label}要素的图层。\n"
                    "请先打开一个 Shapefile 或 GeoJSON 矢量图层。",
                )
                return
            dialog: TargetLayerDialog = TargetLayerDialog(
                tuple(options),
                label,
                snapshot.active_layer_id,
                self,
            )
            if dialog.exec() != TargetLayerDialog.DialogCode.Accepted:
                # 用户取消：不激活数字化工具。
                return
            target_layer_id = dialog.selected_layer_id()
        elif not any(option.layer_id == target_layer_id for option in options):
            QMessageBox.information(
                self,
                "新增要素",
                f"图层不支持新增{label}要素，请检查图层几何类型和文件格式。",
            )
            return
        if target_layer_id is None:
            return
        target_layer: LayerSnapshot | None = None
        for layer in snapshot.layers:
            if layer.layer_id == target_layer_id:
                target_layer = layer
                break
        if target_layer is None:
            return
        self._digitize_target_layer_id = target_layer.layer_id
        self._digitize_mode = mode
        if mode == "point":
            self._map_canvas.set_digitize_point_tool()
        elif mode == "line":
            self._map_canvas.set_digitize_line_tool()
        else:
            self._map_canvas.set_digitize_polygon_tool()
        self._ready_label.setText(
            f"数字化{label}：左键放置  |  双击完成  |  Esc 取消"
            f"  （将追加到「{target_layer.name}」）"
        )

    def _on_feature_digitized(self, geometry: BaseGeometry) -> None:
        """数字化完成回调：填写属性并追加到活动矢量图层。

        参数:
            geometry: 用户绘制的 Shapely 几何对象。
        """
        snapshot: WorkspaceSnapshot = self._application.snapshot()
        # 使用启动数字化时锁定的目标图层：绘制期间点击画布会清除
        # 活动图层，不能依赖当时的 active_layer_id。
        target_layer: LayerSnapshot | None = None
        if self._digitize_target_layer_id is not None:
            for layer in snapshot.layers:
                if layer.layer_id == self._digitize_target_layer_id:
                    target_layer = layer
                    break
        if target_layer is None:
            # 目标图层在数字化过程中被移除，直接取消本次创建。
            self._reactivate_digitize_tool()
            return
        label: str = self._geometry_label(geometry)
        # 从图层已有要素收集字段结构，作为属性表单的预填字段；
        # 没有字段时表单为空，用户仍可自行添加字段行。
        fields: dict[str, AttributeValue] = {}
        if isinstance(target_layer.layer, VectorLayer):
            for feature in target_layer.layer.features:
                for name in feature.attributes:
                    fields.setdefault(name, None)
        dialog: EditFeatureDialog = EditFeatureDialog(
            fields, f"新增{label}要素", self
        )
        if dialog.exec() != EditFeatureDialog.DialogCode.Accepted:
            # 用户取消：保持工具激活，不产生任何改动。
            self._reactivate_digitize_tool()
            return
        before: tuple[Feature, ...] = ()
        if isinstance(target_layer.layer, VectorLayer):
            before = target_layer.layer.features
        try:
            result_snap = self._application.append_feature(
                layer_id=target_layer.layer_id,
                geometry=geometry,
                attributes=dialog.attributes(),
            )
        except ApplicationError as error:
            QMessageBox.warning(self, "追加要素失败", str(error))
            self._reactivate_digitize_tool()
            return
        after: tuple[Feature, ...] = ()
        for layer in result_snap.layers:
            if (
                layer.layer_id == target_layer.layer_id
                and isinstance(layer.layer, VectorLayer)
            ):
                after = layer.layer.features
                break
        self._push_undo(
            f"新增{label}要素",
            undo_action=partial(
                self._application.replace_layer_features,
                target_layer.layer_id,
                before,
            ),
            redo_action=partial(
                self._application.replace_layer_features,
                target_layer.layer_id,
                after,
            ),
        )
        # 首个要素加入空图层后，先缩放到要素位置再刷新渲染，
        # 确保 map_units_per_pixel 在缩放后计算，点符号尺寸与视口一致。
        if len(before) == 0:
            self._map_canvas.zoom_to_feature(geometry.bounds)
        self._refresh_workspace()
        self._refresh_attribute_table()
        # 保持数字化工具激活，支持连续追加。
        self._reactivate_digitize_tool()
        self._ready_label.setText(
            f"已向图层「{target_layer.name}」添加{label}要素"
            f"（共 {len(after)} 个要素）"
        )

    @staticmethod
    def _geometry_family_label(family: GeometryFamily | None) -> str:
        """返回几何类别的中文名称，未知类别时显示为矢量。"""
        if family is None:
            return "矢量"
        return {
            GeometryFamily.POINT: "点",
            GeometryFamily.LINE: "线",
            GeometryFamily.POLYGON: "面",
            GeometryFamily.MIXED: "混合",
        }.get(family, "矢量")

    @staticmethod
    def _geometry_label(geometry: BaseGeometry) -> str:
        """返回几何类型的简短中文名称。"""
        geometry_type: str = geometry.geom_type
        if geometry_type == "Point":
            return "点"
        if geometry_type == "LineString":
            return "线"
        if geometry_type == "Polygon":
            return "面"
        return "要素"

    def _reactivate_digitize_tool(self) -> None:
        """重新激活当前数字化工具，支持连续追加要素。"""
        if self._digitize_mode == "point":
            self._map_canvas.set_digitize_point_tool()
        elif self._digitize_mode == "line":
            self._map_canvas.set_digitize_line_tool()
        else:
            self._map_canvas.set_digitize_polygon_tool()

    def _delete_selected_features(self) -> None:
        """删除当前所有选中的要素，删除前弹出确认。

        撤销操作会完整恢复被删要素的几何和属性。
        """
        snapshot: WorkspaceSnapshot = self._application.snapshot()
        # 收集待删要素的完整信息，用于撤销恢复。
        to_delete: list[tuple[str, FeatureId, Feature]] = []
        for layer in snapshot.layers:
            if not isinstance(layer.layer, VectorLayer):
                continue
            for feature in layer.layer.features:
                if feature.fid in layer.selected_feature_ids:
                    to_delete.append(
                        (layer.layer_id, feature.fid, feature)
                    )
        if not to_delete:
            QMessageBox.information(
                self, "删除要素",
                "当前没有选中的要素。\n请先使用点选/框选查询选中一个要素。",
            )
            return
        count: int = len(to_delete)
        answer = QMessageBox.question(
            self,
            "删除要素",
            f"确定删除选中的 {count} 个要素吗？\n此操作可撤销（Ctrl+Z）。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        # 记录每个被影响图层删前/删后的完整要素列表用于撤销。
        affected: dict[str, tuple[tuple[Feature, ...], tuple[Feature, ...]]] = {}
        selected_by_layer: dict[str, set[FeatureId]] = {}
        for layer_id, fid, _feature in to_delete:
            selected_by_layer.setdefault(layer_id, set()).add(fid)
        for layer_id, selected_fids in selected_by_layer.items():
            for layer in snapshot.layers:
                if layer.layer_id == layer_id and isinstance(
                    layer.layer, VectorLayer
                ):
                    before: tuple[Feature, ...] = layer.layer.features
                    after: tuple[Feature, ...] = tuple(
                        f for f in before if f.fid not in selected_fids
                    )
                    affected[layer_id] = (before, after)
                    break

        try:
            for layer_id, fid, _feature in to_delete:
                self._application.delete_feature(layer_id, fid)
        except ApplicationError as error:
            QMessageBox.warning(self, "删除失败", str(error))
            return

        self._push_undo(
            "删除要素",
            undo_action=partial(
                self._restore_layer_features, dict(affected), undo=True
            ),
            redo_action=partial(
                self._restore_layer_features, dict(affected), undo=False
            ),
        )
        self._refresh_workspace()
        self._refresh_attribute_table()
        self._ready_label.setText(f"已删除 {count} 个要素")

    def _restore_layer_features(
        self,
        affected: dict[str, tuple[tuple[Feature, ...], tuple[Feature, ...]]],
        undo: bool,
    ) -> None:
        """撤销/重做时恢复图层要素。

        参数:
            affected: {layer_id: (before_features, after_features)}。
            undo: True 时恢复删除前状态，False 时恢复删除后状态。
        """
        for layer_id, (before_features, after_features) in affected.items():
            features: tuple[Feature, ...] = (
                before_features if undo else after_features
            )
            try:
                self._application.replace_layer_features(layer_id, features)
            except ApplicationError:
                continue

    def _edit_selected_feature(self) -> None:
        """编辑当前选中要素的属性（仅支持单选）。"""
        snapshot: WorkspaceSnapshot = self._application.snapshot()
        if snapshot.selection_count == 0:
            self.statusBar().showMessage("请先选中一个要素。", 3500)
            return
        if snapshot.selection_count > 1:
            QMessageBox.information(
                self, "修改要素", "请只选中一个要素进行属性修改。"
            )
            return
        # 找到选中的要素。
        selected_layer_id: str | None = None
        selected_fid: FeatureId | None = None
        selected_attrs = None
        for layer in snapshot.layers:
            if layer.selected_feature_ids:
                selected_layer_id = layer.layer_id
                selected_fid = layer.selected_feature_ids[0]
                if isinstance(layer.layer, VectorLayer):
                    for f in layer.layer.features:
                        if f.fid == selected_fid:
                            selected_attrs = f.attributes
                            break
                break
        if selected_layer_id is None or selected_fid is None or selected_attrs is None:
            return
        label: str = f"FID {selected_fid}"
        dialog: EditFeatureDialog = EditFeatureDialog(selected_attrs, label, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_attrs: dict[str, AttributeValue] = dialog.attributes()
        # 捕获修前/修后完整要素集合用于撤销。
        before_features: tuple[Feature, ...] = ()
        for layer in snapshot.layers:
            if layer.layer_id == selected_layer_id and isinstance(
                layer.layer, VectorLayer
            ):
                before_features = layer.layer.features
                break
        try:
            self._application.update_feature_attributes(
                selected_layer_id, selected_fid, new_attrs
            )
        except ApplicationError as error:
            QMessageBox.warning(self, "修改属性失败", str(error))
            return
        after_snapshot = self._application.snapshot()
        after_features: tuple[Feature, ...] = ()
        for layer in after_snapshot.layers:
            if layer.layer_id == selected_layer_id and isinstance(
                layer.layer, VectorLayer
            ):
                after_features = layer.layer.features
                break
        self._push_undo(
            "修改要素属性",
            undo_action=partial(
                self._application.replace_layer_features,
                selected_layer_id,
                before_features,
            ),
            redo_action=partial(
                self._application.replace_layer_features,
                selected_layer_id,
                after_features,
            ),
        )
        self._refresh_workspace()
        self._refresh_attribute_table()
        self._ready_label.setText(
            f"已修改要素属性  FID {selected_fid}"
        )

    def _edit_selected_geometry(self) -> None:
        """激活顶点编辑工具，修改选中要素的几何。

        先使用点选查询选中一个要素，再点击本按钮进入顶点编辑模式。
        """
        self._ready_label.setText("编辑几何要素：检查选中状态…")
        snapshot: WorkspaceSnapshot = self._application.snapshot()
        if snapshot.selection_count == 0:
            QMessageBox.information(
                self, "编辑几何要素",
                "当前没有选中的要素。\n\n请先点击功能区 地图→点选查询，"
                "在地图上点击一个要素将其选中（高亮），\n"
                "然后再点击 编辑→编辑几何要素。",
            )
            self._ready_label.setText("就绪")
            return
        if snapshot.selection_count > 1:
            QMessageBox.information(
                self, "编辑几何要素", "请只选中一个要素进行几何编辑。\n"
                "当前选中了多个要素，请先清除选择后重新选取。"
            )
            self._ready_label.setText("就绪")
            return
        for layer in snapshot.layers:
            if not layer.selected_feature_ids:
                continue
            if not isinstance(layer.layer, VectorLayer):
                continue
            fid = layer.selected_feature_ids[0]
            for f in layer.layer.features:
                if f.fid == fid:
                    self._editing_layer_id = layer.layer_id
                    self._editing_fid = fid
                    self._editing_before_features: tuple[Feature, ...] = (
                        layer.layer.features
                    )
                    self._map_canvas.set_vertex_edit_tool(
                        f.geometry, layer.layer_id, fid
                    )
                    # 显示编辑工具栏（主窗口左上角）。
                    global_pos = self.mapToGlobal(QPointF(10, 10))
                    self._geom_edit_toolbar.show_at(
                        int(global_pos.x()), int(global_pos.y())
                    )
                    geom_type: str = f.geometry.geom_type
                    coords_count: int = 0
                    try:
                        if geom_type == "Point":
                            coords_count = 1
                        elif geom_type == "LineString":
                            coords_count = len(f.geometry.coords)
                        elif geom_type == "Polygon":
                            coords_count = len(f.geometry.exterior.coords) - 1
                        elif hasattr(f.geometry, "geoms") and f.geometry.geoms:
                            # Multi 类型显示首个部件顶点数。
                            first = f.geometry.geoms[0]
                            coords_count = (
                                1 if first.geom_type == "Point"
                                else len(first.coords) if first.geom_type == "LineString"
                                else len(first.exterior.coords) - 1
                            )
                    except Exception:
                        pass
                    self._ready_label.setText(
                        f"顶点编辑：{geom_type} · {coords_count} 个顶点  "
                        "|  拖拽移动  |  右键删除  |  点中点插入  "
                        "|  Enter 提交  |  Esc 取消"
                    )
                    return
        self.statusBar().showMessage("未找到选中要素。", 3500)

    def _get_layer_features(self, layer_id: str) -> tuple[Feature, ...]:
        """获取指定图层的当前要素集合。"""
        for layer in self._application.snapshot().layers:
            if layer.layer_id == layer_id and isinstance(layer.layer, VectorLayer):
                return layer.layer.features
        return ()

    def _select_all_vertices(self) -> None:
        """全选所有顶点（Ctrl+A 的工具栏按钮版）。"""
        canvas = self._map_canvas
        if canvas._vertex_edit_active:
            canvas._selected_vertex_indices = set(range(len(canvas._vertex_coords)))
            canvas._rebuild_vertex_markers(canvas._edit_geometry)

    def _on_geom_edit_mode(self, mode: str) -> None:
        """编辑几何要素模式切换。"""
        self._map_canvas._edit_mode = mode
        self._geom_edit_toolbar.set_mode(mode)
        if mode == "delete_vertex":
            self._ready_label.setText(
                "编辑几何要素：点击顶点删除  |  Ctrl+A 全选"
            )
        elif mode == "drag_vertex":
            self._ready_label.setText(
                "编辑几何要素：Ctrl+点击多选  |  Ctrl+A 全选  |  拖拽移动"
            )

    def _on_geom_edit_commit(self) -> None:
        """提交几何编辑。"""
        self._geom_edit_toolbar.hide()
        if self._map_canvas._vertex_edit_active:
            new_geom = self._map_canvas._commit_vertex_edit()
            if new_geom is not None:
                self._on_geometry_edited(new_geom)
            # 无论成功失败都退出顶点编辑，避免状态残留。
            self._map_canvas.set_pan_tool()

    def _on_geom_edit_cancel(self) -> None:
        """取消几何编辑。"""
        self._geom_edit_toolbar.hide()
        self._map_canvas.set_pan_tool()
        self._ready_label.setText("编辑几何要素：已取消")

    def _toggle_snapping(self) -> None:
        """切换顶点捕捉开关。"""
        self._snapping_enabled = not self._snapping_enabled
        self._map_canvas.set_snapping(self._snapping_enabled)
        self._ribbon.set_action_checked(
            "toggle_snapping", self._snapping_enabled
        )
        state: str = "开" if self._snapping_enabled else "关"
        self._ready_label.setText(f"顶点捕捉：{state}")

    def _get_single_selected(
        self,
    ) -> tuple[str | None, FeatureId | None, tuple[Feature, ...]]:
        """获取当前单选要素的图层ID、FID和所在图层的完整要素快照。

        返回:
            (layer_id, fid, before_features)；未选中或多选时返回 (None, None, ())。
        """
        snapshot: WorkspaceSnapshot = self._application.snapshot()
        if snapshot.selection_count != 1:
            return None, None, ()
        for layer in snapshot.layers:
            if not layer.selected_feature_ids or not isinstance(
                layer.layer, VectorLayer
            ):
                continue
            fid = layer.selected_feature_ids[0]
            return layer.layer_id, fid, layer.layer.features
        return None, None, ()

    def _simplify_selected(self) -> None:
        """对选中线要素执行 Douglas-Peucker 简化。"""
        layer_id, fid, before_features = self._get_single_selected()
        if layer_id is None or fid is None:
            QMessageBox.information(
                self, "线简化", "请先选中一个线或面要素。"
            )
            return
        # 缓存真原始几何（首次操作时从图层读取）。
        cache_key: tuple[str, FeatureId] = (layer_id, fid)
        if cache_key not in self._original_geoms:
            for f in before_features:
                if f.fid == fid:
                    self._original_geoms[cache_key] = f.geometry
                    break

        # 自动推荐容差：5 像素宽度对应的地图单位。
        mpp: float = self._map_canvas.map_units_per_pixel
        auto_tol: float = mpp * 5.0
        # 获取地图单位名称。
        snapshot: WorkspaceSnapshot = self._application.snapshot()
        unit: str = self._crs_unit_name(snapshot.display_crs)

        dialog = QDialog(self)
        dialog.setWindowTitle("线简化")
        layout: QFormLayout = QFormLayout(dialog)
        tol_edit: QLineEdit = QLineEdit(f"{auto_tol:.6f}")
        layout.addRow(f"容差（{unit}，越大顶点越少）：", tol_edit)
        from_original_cb: QCheckBox = QCheckBox("从原始几何重算")
        layout.addRow(from_original_cb)
        buttons: QDialogButtonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            tolerance: float = float(tol_edit.text().strip())
        except ValueError:
            QMessageBox.warning(self, "线简化", "请输入有效的数值。")
            return

        try:
            if from_original_cb.isChecked():
                true_orig = self._original_geoms.get(cache_key)
                if true_orig is not None:
                    simplified = true_orig.simplify(
                        tolerance, preserve_topology=True
                    )
                    self._application.update_feature_geometry(
                        layer_id, fid, simplified
                    )
            else:
                self._application.simplify_feature_geometry(
                    layer_id, fid, tolerance
                )
        except ApplicationError as error:
            QMessageBox.warning(self, "线简化失败", str(error))
            return
        after_snapshot = self._application.snapshot()
        after_features: tuple[Feature, ...] = ()
        for layer in after_snapshot.layers:
            if layer.layer_id == layer_id and isinstance(layer.layer, VectorLayer):
                after_features = layer.layer.features
                break
        simp_before: int = sum(
            len(f.geometry.coords) for f in before_features if f.fid == fid
        )
        simp_after: int = sum(
            len(f.geometry.coords) for f in after_features if f.fid == fid
        )
        self._push_undo(
            "线简化",
            undo_action=partial(
                self._application.replace_layer_features, layer_id, before_features
            ),
            redo_action=partial(
                self._application.replace_layer_features, layer_id, after_features
            ),
        )
        self._map_canvas.setUpdatesEnabled(False)
        try:
            self._refresh_workspace()
        finally:
            self._map_canvas.setUpdatesEnabled(True)
        self._ready_label.setText(
            f"线简化完成  顶点 {simp_before} → {simp_after}"
            f"（容差 {tolerance}）"
        )

    def _smooth_selected(self) -> None:
        """对选中线要素执行 Chaikin 平滑。"""
        layer_id, fid, before_features = self._get_single_selected()
        if layer_id is None or fid is None:
            QMessageBox.information(
                self, "线平滑", "请先选中一个线或面要素。"
            )
            return
        # 缓存真原始几何。
        cache_key: tuple[str, FeatureId] = (layer_id, fid)
        if cache_key not in self._original_geoms:
            for f in before_features:
                if f.fid == fid:
                    self._original_geoms[cache_key] = f.geometry
                    break

        dialog = QDialog(self)
        dialog.setWindowTitle("线平滑")
        layout: QFormLayout = QFormLayout(dialog)
        iter_edit: QLineEdit = QLineEdit("2")
        layout.addRow("迭代次数（1-5）：", iter_edit)
        from_original_cb: QCheckBox = QCheckBox("从原始几何重算（忽略之前平滑结果）")
        layout.addRow(from_original_cb)
        buttons: QDialogButtonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            iterations: int = int(iter_edit.text().strip())
            if not 1 <= iterations <= 5:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "线平滑", "请输入 1-5 之间的整数。")
            return

        try:
            if from_original_cb.isChecked():
                true_orig = self._original_geoms.get(cache_key)
                if true_orig is not None:
                    self._application.update_feature_geometry(
                        layer_id, fid,
                        _chaikin_smooth(true_orig, iterations),
                    )
            else:
                self._application.smooth_feature_geometry(
                    layer_id, fid, iterations
                )
        except ApplicationError as error:
            QMessageBox.warning(self, "线平滑失败", str(error))
            return
        after_snapshot = self._application.snapshot()
        after_features: tuple[Feature, ...] = ()
        for layer in after_snapshot.layers:
            if layer.layer_id == layer_id and isinstance(layer.layer, VectorLayer):
                after_features = layer.layer.features
                break
        before_count: int = sum(
            len(f.geometry.coords) for f in before_features if f.fid == fid
        )
        after_count: int = sum(
            len(f.geometry.coords) for f in after_features if f.fid == fid
        )
        self._push_undo(
            "线平滑",
            undo_action=partial(
                self._application.replace_layer_features, layer_id, before_features
            ),
            redo_action=partial(
                self._application.replace_layer_features, layer_id, after_features
            ),
        )
        self._map_canvas.setUpdatesEnabled(False)
        try:
            self._refresh_workspace()
        finally:
            self._map_canvas.setUpdatesEnabled(True)
        self._ready_label.setText(
            f"线平滑完成  顶点 {before_count} → {after_count}"
            f"（{iterations} 轮）"
        )

    def _on_geometry_edited(self, geometry: BaseGeometry) -> None:
        """顶点编辑提交回调：更新要素几何并记录可撤销状态。"""
        layer_id: str | None = self._editing_layer_id
        fid: FeatureId | None = self._editing_fid
        before_features: tuple[Feature, ...] = getattr(
            self, "_editing_before_features", ()
        )
        self._editing_layer_id = None
        self._editing_fid = None
        if layer_id is None or fid is None:
            return
        try:
            self._application.update_feature_geometry(
                layer_id, fid, geometry
            )
        except ApplicationError as error:
            QMessageBox.warning(self, "修改几何失败", str(error))
            return
        after_snapshot = self._application.snapshot()
        after_features: tuple[Feature, ...] = ()
        for layer in after_snapshot.layers:
            if layer.layer_id == layer_id and isinstance(
                layer.layer, VectorLayer
            ):
                after_features = layer.layer.features
                break
        self._push_undo(
            "修改要素几何",
            undo_action=partial(
                self._application.replace_layer_features, layer_id, before_features
            ),
            redo_action=partial(
                self._application.replace_layer_features, layer_id, after_features
            ),
        )
        self._map_canvas.setUpdatesEnabled(False)
        try:
            self._refresh_workspace()
        finally:
            self._map_canvas.setUpdatesEnabled(True)
        self._ready_label.setText(
            f"已修改要素几何  FID {fid}"
        )

    def _on_point_queried(self, point: Point, add_to_selection: bool) -> None:
        """点选查询结果处理。

        快速模式始终取最近要素；精确模式多候选时弹窗选择。
        选中后保持查询工具激活，支持 Shift 连续点选。

        参数:
            point: 地图坐标下的 Shapely Point。
            add_to_selection: Shift 按下时为 True，追加而非替换。
        """
        tolerance: float = 5.0 * self._map_canvas.map_units_per_pixel
        try:
            candidates = self._application.identify_features(
                point,
                tolerance,
                query_layer_ids=self._map_canvas.queryable_layer_ids(),
            )
        except (ApplicationError, ValueError):
            return

        if not candidates:
            self._ready_label.setText("点选查询：未命中要素")
            return

        fast: bool = getattr(self, "_point_query_fast", False)

        if fast or len(candidates) == 1:
            # 快速模式或单个候选：直接选最近要素。
            self._apply_point_selection(
                candidates[0], add_to_selection, description="点选查询"
            )
            return

        # 精确模式 + 多个候选：弹窗选择。
        choices: list[str] = [
            f"{c.layer_name}  ·  FID {c.feature.fid}"
            for c in candidates
        ]
        item, accepted = QInputDialog.getItem(
            self,
            "点选查询 — 选择要素",
            f"容差范围内找到 {len(candidates)} 个要素，请选择：",
            choices,
            editable=False,
        )
        if not accepted:
            self._ready_label.setText("点选查询：已取消")
            # 弹窗取消后重新激活查询工具。
            self._map_canvas.set_point_query_tool()
            return
        index: int = choices.index(item)
        self._apply_point_selection(
            candidates[index], add_to_selection, description="点选查询"
        )

    def _apply_point_selection(
        self,
        selected: SelectedFeature,
        add_to_selection: bool,
        description: str,
    ) -> None:
        """将选定的要素应用为当前选择，之后保持查询工具激活。

        参数:
            selected: 用户在候选列表中确认的 SelectedFeature。
            add_to_selection: 追加模式下切换选中状态。
            description: 撤销操作描述。
        """
        before_selections: dict[str, tuple[FeatureId, ...]] = self._capture_selections()
        try:
            self._application.set_selection(
                selected.layer_id,
                self._resolve_point_selection(
                    selected, add_to_selection
                ),
            )
        except ApplicationError:
            return
        after_selections: dict[str, tuple[FeatureId, ...]] = self._capture_selections()
        self._push_selection_undo(description, before_selections, after_selections)
        # 关掉刷新避免场景清空→重建的闪烁。
        self._map_canvas.setUpdatesEnabled(False)
        try:
            self._refresh_workspace()
        finally:
            self._map_canvas.setUpdatesEnabled(True)
        # 保持查询工具激活，支持连续点选。
        self._map_canvas.set_point_query_tool()
        action: str = "切换" if add_to_selection else "选中"
        self._ready_label.setText(
            f"点选查询：{action} FID {selected.feature.fid} · "
            f"{selected.layer_name}"
        )

    def _resolve_point_selection(
        self, selected: SelectedFeature, add_to_selection: bool
    ) -> tuple[FeatureId, ...]:
        """根据追加模式决定点选命中要素的最终选中集合。

        参数:
            selected: 用户选择的 SelectedFeature。
            add_to_selection: 追加模式标志。

        返回:
            更新后的要素编号元组。
        """
        if not add_to_selection:
            return (selected.feature.fid,)
        existing = self._application.snapshot()
        current: tuple[FeatureId, ...] = ()
        for layer in existing.layers:
            if layer.layer_id == selected.layer_id:
                current = layer.selected_feature_ids
                break
        if selected.feature.fid in current:
            return tuple(fid for fid in current if fid != selected.feature.fid)
        return current + (selected.feature.fid,)

    def _on_rectangle_queried(self, polygon: Polygon, add_to_selection: bool) -> None:
        """框选查询结果处理：执行空间矩形选择、记录撤销并刷新工作区。

        参数:
            polygon: 地图坐标下的 Shapely Polygon。
            add_to_selection: Shift 按下时为 True，追加而非替换。
        """
        before_selections: dict[str, tuple[FeatureId, ...]] = self._capture_selections()
        try:
            result = self._application.select_rectangle(
                polygon,
                add_to_selection=add_to_selection,
                query_layer_ids=self._map_canvas.queryable_layer_ids(),
            )
        except (ApplicationError, ValueError):
            self._map_canvas.set_pan_tool()
            return
        after_selections: dict[str, tuple[FeatureId, ...]] = self._capture_selections()
        self._push_selection_undo("框选查询", before_selections, after_selections)
        self._map_canvas.setUpdatesEnabled(False)
        try:
            self._refresh_workspace()
        finally:
            self._map_canvas.setUpdatesEnabled(True)
        # 保持框选查询工具激活，支持连续框选。
        self._map_canvas.set_rectangle_query_tool()
        self._ready_label.setText(
            f"框选查询：选中 {result.count} 个要素"
        )

    def _capture_selections(self) -> dict[str, tuple[FeatureId, ...]]:
        """捕获当前全部图层的选择状态，用于撤销恢复。

        返回:
            {layer_id: (feature_id, ...)} 的字典，不含未选中任何要素的图层。
        """
        selections: dict[str, tuple[FeatureId, ...]] = {}
        for layer in self._application.snapshot().layers:
            if layer.selected_feature_ids:
                selections[layer.layer_id] = layer.selected_feature_ids
        return selections

    def _push_selection_undo(
        self,
        description: str,
        before: dict[str, tuple[FeatureId, ...]],
        after: dict[str, tuple[FeatureId, ...]],
    ) -> None:
        """将选择变更推入撤销栈。

        参数:
            description: 操作描述。
            before: 操作前的 {layer_id: feature_ids}。
            after: 操作后的 {layer_id: feature_ids}。
        """
        self._push_undo(
            description,
            undo_action=partial(self._restore_selections, before),
            redo_action=partial(self._restore_selections, after),
        )

    def _restore_selections(
        self, selections: dict[str, tuple[FeatureId, ...]]
    ) -> None:
        """将多图层选择状态恢复到地图文档。

        参数:
            selections: {layer_id: feature_ids} 映射。
        """
        try:
            self._application.restore_selections(selections)
        except ApplicationError:
            return

    def _clear_selection(self) -> None:
        """清除已有矢量要素选择并刷新工作区；操作可撤销恢复原选择。"""
        before_selections: dict[str, tuple[FeatureId, ...]] = self._capture_selections()
        self._application.clear_selection()
        if before_selections:
            self._push_selection_undo("清除选择", before_selections, {})
        self._refresh_workspace()

    def _set_display_crs(self) -> None:
        """弹出坐标系选择对话框，设置地图显示坐标系并重建已有图层。"""
        snapshot: WorkspaceSnapshot = self._application.snapshot()

        dialog: QDialog = QDialog(self)
        dialog.setWindowTitle("设置地图坐标系")
        dialog.setMinimumWidth(520)

        crs_widget: CrsSelectWidget = CrsSelectWidget()
        crs_widget.set_placeholder("选择预设坐标系，或切换为自定义输入...")
        if snapshot.display_crs is not None:
            crs_widget.set_crs(snapshot.display_crs)

        buttons: QDialogButtonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)

        layout: QVBoxLayout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("选择或输入地图显示坐标系："))
        layout.addWidget(crs_widget)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        target_crs: CRS | None = crs_widget.crs()
        if target_crs is None:
            QMessageBox.warning(self, "坐标系设置失败", "请输入有效的坐标系标识。")
            return

        try:
            self._application.set_display_crs(target_crs)
        except (ApplicationError, ValueError) as error:
            QMessageBox.warning(self, "坐标系设置失败", str(error))
            return
        self._refresh_workspace(preserve_view=False)
        self._ready_label.setText(f"地图 CRS 已设置为 {self._format_crs(target_crs)}")

    def _buffer_analysis(self) -> None:
        """打开缓冲区参数窗口并执行真实分析结果写出。"""
        snapshot: WorkspaceSnapshot = self._application.snapshot()
        vector_layers: tuple[LayerSnapshot, ...] = tuple(
            layer for layer in snapshot.layers if not layer.is_raster
        )
        if not vector_layers:
            self.statusBar().showMessage("当前工作区没有可用于缓冲区分析的矢量图层。", 4000)
            return

        dialog: BufferAnalysisDialog = BufferAnalysisDialog(
            snapshot.layers,
            display_crs=snapshot.display_crs,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            result = self._application.buffer_analysis(dialog.request())
        except (ApplicationError, ValueError) as error:
            self._refresh_analysis_history()
            QMessageBox.warning(self, "缓冲区分析失败", str(error))
            return
        self._refresh_workspace()
        self._ready_label.setText(f"已生成缓冲区  {result.output_layer_name}")
        QMessageBox.information(
            self,
            "缓冲区分析完成",
            f"结果图层：{result.output_layer_name}\n"
            f"要素数量：{result.feature_count}\n"
            f"输出位置：\n{result.output_path}",
        )

    def _overlay_analysis(self) -> None:
        """打开叠加分析参数窗口并执行真实分析结果写出。"""
        snapshot: WorkspaceSnapshot = self._application.snapshot()
        vector_layers: tuple[LayerSnapshot, ...] = tuple(
            layer for layer in snapshot.layers if not layer.is_raster
        )
        if len(vector_layers) < 2:
            self.statusBar().showMessage("叠加分析需要至少两个矢量图层。", 4000)
            return

        dialog: OverlayAnalysisDialog = OverlayAnalysisDialog(
            snapshot.layers,
            display_crs=snapshot.display_crs,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            result = self._application.overlay_analysis(dialog.request())
        except (ApplicationError, ValueError) as error:
            self._refresh_analysis_history()
            QMessageBox.warning(self, "叠加分析失败", str(error))
            return
        self._refresh_workspace()
        self._ready_label.setText(f"已生成叠加结果  {result.output_layer_name}")
        QMessageBox.information(
            self,
            "叠加分析完成",
            f"结果图层：{result.output_layer_name}\n"
            f"要素数量：{result.feature_count}\n"
            f"输出位置：\n{result.output_path}",
        )

    def _raster_calculator(self) -> None:
        """打开栅格计算器对话框并执行逐像素表达式求值。

        计算失败时对话框保留所有已填内容，用户可直接修改后重试，
        无需重新打开对话框和重新设置变量映射。
        """
        snapshot: WorkspaceSnapshot = self._application.snapshot()
        raster_layers: tuple[LayerSnapshot, ...] = tuple(
            layer for layer in snapshot.layers if layer.is_raster
        )
        if not raster_layers:
            self.statusBar().showMessage(
                "当前工作区没有可用于栅格计算的栅格图层。", 4000
            )
            return
        try:
            dialog: RasterCalculatorDialog = RasterCalculatorDialog(
                snapshot.layers, parent=self
            )
        except ValueError:
            self.statusBar().showMessage(
                "当前工作区没有可用于栅格计算的栅格图层。", 4000
            )
            return
        # 计算失败时循环回到对话框，保留已填内容供用户修改。
        while True:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            try:
                result = self._application.raster_calculation(dialog.request())
            except (ApplicationError, ValueError) as error:
                self._refresh_analysis_history()
                QMessageBox.warning(
                    self,
                    "栅格计算失败",
                    f"{error}\n\n请修改参数后重试。",
                )
                # 对话框保留打开状态，进入下一次循环让用户修改。
                continue
            break
        self._refresh_workspace()
        self._ready_label.setText(
            f"栅格计算完成  {result.output_layer_name}"
        )
        QMessageBox.information(
            self,
            "栅格计算完成",
            f"结果图层：{result.output_layer_name}\n"
            f"表达式：{result.expression}\n"
            f"输出位置：\n{result.output_path}",
        )

    def _toggle_analysis_history(self) -> None:
        """切换分析历史面板的显示状态。"""
        if (
            self._workspace_panel_is_visible()
            and self._panel_tabs.currentIndex() == self._ANALYSIS_TAB_INDEX
        ):
            self._panel_dock.hide()
            return
        self._refresh_analysis_history()
        self._show_workspace_panel(self._ANALYSIS_TAB_INDEX)

    def _clear_analysis_history(self) -> None:
        """清除分析历史记录，但不删除已有结果图层和结果文件。"""
        if not self._application.analysis_runs:
            return
        answer = QMessageBox.question(
            self,
            "清除分析记录",
            "确定清除当前工程的全部分析记录吗？\n分析结果图层和文件不会被删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._application.clear_analysis_history()
        self._refresh_analysis_history()
        self._ready_label.setText("已清除分析记录")

    def _toggle_layer_panel(self) -> None:
        """切换左侧图层管理面板的显示状态。"""
        self._layer_panel.setVisible(not self._layer_panel.isVisible())

    def _exit_layout_mode(self) -> None:
        """退出布局视图，回到数据视图。"""
        if not self._layout_mode:
            return
        self._layout_mode = False
        self._layout_toolbar.hide()
        self._view_stack.setCurrentWidget(self._map_canvas)
        self._ribbon.set_action_checked("toggle_layout_view", False)

    def _restore_layout(
        self, layout_state: "Mapping[str, object] | None"
    ) -> None:
        """从工程状态恢复布局文档。"""
        if layout_state is None:
            return
        try:
            document = layout_from_dict(dict(layout_state))
            self._layout_view.set_document(document)
        except Exception:
            pass

    def _on_page_setup(self) -> None:
        """打开页面设置对话框并应用新纸张规格。"""
        document = self._layout_view.document()
        if document is None:
            return
        from app.presentation.widgets.page_setup_dialog import PageSetupDialog

        dialog = PageSetupDialog(document.page, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_page = dialog.page()
        if new_page is None:
            return
        old_page = document.page
        old_elements = document.elements
        new_document = LayoutDocument(page=new_page, elements=old_elements)
        self._layout_view.set_document(new_document)
        self._layout_view._push_undo(
            "页面设置",
            undo_action=lambda: self._layout_view.set_document(
                LayoutDocument(page=old_page, elements=old_elements)
            ),
            redo_action=lambda: self._layout_view.set_document(new_document),
        )

    def _on_edit_properties(self) -> None:
        """打开元素属性编辑对话框。"""
        element_id = self._layout_view.selected_element_id
        if element_id is None:
            return
        element = self._layout_view.find_element(element_id)
        if element is None:
            return
        from app.presentation.widgets.element_properties_dialog import (
            ElementPropertiesDialog,
        )

        dialog = ElementPropertiesDialog(self)
        dialog.set_element(element)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        changes = dialog.changes()
        if changes:
            self._layout_view.apply_element_changes(element_id, changes)
            self._layout_toolbar.set_undo_enabled(self._layout_view.can_undo())
            self._layout_toolbar.set_redo_enabled(self._layout_view.can_redo())

    def _export_layout(self) -> None:
        """导出布局为 PDF 或图片文件。"""
        document = self._layout_view.document()
        if document is None:
            return
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出布局",
            "",
            "PDF 文件 (*.pdf);;PNG 图片 (*.png);;JPEG 图片 (*.jpg *.jpeg)",
        )
        if not file_path:
            return

        # 用户未输入扩展名时自动补充
        fp = Path(file_path)
        if not fp.suffix:
            if "PDF" in selected_filter:
                fp = fp.with_suffix(".pdf")
            elif "JPEG" in selected_filter:
                fp = fp.with_suffix(".jpg")
            elif "PNG" in selected_filter:
                fp = fp.with_suffix(".png")
            file_path = str(fp)

        from app.presentation.renderers.layout_renderer import render_full_page

        snapshot = self._application.snapshot()
        page = document.page

        if selected_filter.startswith("PDF"):
            from PySide6.QtGui import QPageSize
            from PySide6.QtPrintSupport import QPrinter
            from PySide6.QtCore import QMarginsF, QRectF, QSizeF

            pixmap = render_full_page(document, snapshot, view_dpi=self._layout_view._view_dpi)

            # 删除 QFileDialog 可能创建的占位文件，避免 QPrinter 写入失败
            Path(file_path).unlink(missing_ok=True)

            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(file_path)
            printer.setPageSize(
                QPageSize(
                    QSizeF(page.width_mm, page.height_mm),
                    QPageSize.Unit.Millimeter,
                )
            )
            printer.setFullPage(True)
            printer.setPageMargins(QMarginsF(0, 0, 0, 0))

            painter = QPainter(printer)

            # 使用打印机实际 DPI 将所有尺寸从毫米换算到设备像素
            dpi_x: float = float(printer.logicalDpiX())
            dpi_y: float = float(printer.logicalDpiY())
            mm_to_px_x: Callable[[float], float] = lambda m: m / 25.4 * dpi_x
            mm_to_px_y: Callable[[float], float] = lambda m: m / 25.4 * dpi_y

            margin_mm: float = 5.0
            page_rect = printer.pageRect(QPrinter.Unit.DevicePixel)
            target_x: float = page_rect.x() + mm_to_px_x(margin_mm)
            target_y: float = page_rect.y() + mm_to_px_y(margin_mm)
            target_w: float = page_rect.width() - 2.0 * mm_to_px_x(margin_mm)
            target_h: float = page_rect.height() - 2.0 * mm_to_px_y(margin_mm)

            # 等比缩放 pixmap（300 DPI）到目标区域，保持宽高比
            scale: float = min(target_w / pixmap.width(), target_h / pixmap.height())
            draw_w: float = pixmap.width() * scale
            draw_h: float = pixmap.height() * scale
            draw_x: float = page_rect.x() + (page_rect.width() - draw_w) / 2.0
            draw_y: float = page_rect.y() + (page_rect.height() - draw_h) / 2.0

            painter.drawPixmap(
                QRectF(draw_x, draw_y, draw_w, draw_h),
                pixmap,
                QRectF(0, 0, pixmap.width(), pixmap.height()),
            )
            painter.end()
        else:
            pixmap = render_full_page(document, snapshot, view_dpi=self._layout_view._view_dpi)
            fmt = "JPEG" if "JPEG" in selected_filter or file_path.lower().endswith(
                (".jpg", ".jpeg")
            ) else "PNG"
            pixmap.save(file_path, fmt)

        self._ready_label.setText(f"已导出布局  {Path(file_path).name}")

    def _toggle_layout_view(self) -> None:
        """在数据视图与布局视图之间切换。"""
        if self._layout_mode:
            self._exit_layout_mode()
            return
        # 进入布局视图
        self._layout_mode = True
        snapshot = self._application.snapshot()
        self._layout_view.set_snapshot(snapshot)
        if not self._layout_view.has_content():
            doc = LayoutDocument.create_default()
            self._layout_view.set_document(doc)
        else:
            self._layout_view.refresh_map_frames()
        self._view_stack.setCurrentWidget(self._layout_view)
        self._layout_toolbar.show()
        self._layout_toolbar.raise_()
        self._layout_toolbar.set_delete_enabled(
            self._layout_view.selected_element_id is not None
        )
        self._layout_toolbar.set_undo_enabled(self._layout_view.can_undo())
        self._layout_toolbar.set_redo_enabled(self._layout_view.can_redo())
        self._position_layout_toolbar()
        self._ribbon.set_action_checked("toggle_layout_view", True)

    def _position_layout_toolbar(self) -> None:
        """将布局工具栏定位在布局视图顶部居中（相对于主窗口）。"""
        if not self._layout_toolbar.isVisible():
            return
        toolbar_w: int = self._layout_toolbar.sizeHint().width()
        toolbar_h: int = self._layout_toolbar.sizeHint().height()
        # 计算 _view_stack 在主窗口中的位置
        view_pos = self._view_stack.mapTo(self, self._view_stack.rect().topLeft())
        x: int = view_pos.x() + max(0, (self._view_stack.width() - toolbar_w) // 2)
        y: int = view_pos.y() + 10
        self._layout_toolbar.move(x, y)
        self._layout_toolbar.resize(toolbar_w, toolbar_h)

    def _show_display_settings(self, active_tab: int = 1) -> None:
        """打开显示设置对话框，管理图层属性、符号系统、比例尺、全局显示和书签。

        参数:
            active_tab: 对话框打开时激活的标签页（0=符号系统，1=显示设置）。
        """
        dialog: DisplaySettingsDialog = DisplaySettingsDialog(self)
        dialog.opacity_requested.connect(self._change_layer_opacity)
        dialog.blend_mode_requested.connect(self._change_layer_blend_mode)
        dialog.scale_range_requested.connect(self._change_layer_scale_range)
        dialog.bookmark_add_requested.connect(self._add_bookmark)
        dialog.bookmark_jump_requested.connect(self._jump_to_bookmark)
        dialog.bookmark_delete_requested.connect(self._delete_bookmark)
        dialog.symbology_changed.connect(self._apply_symbology)
        dialog.unique_requested.connect(self._apply_unique_symbology)
        dialog.graduated_requested.connect(self._apply_graduated_symbology)
        dialog.global_display_changed.connect(lambda: self._refresh_workspace())
        snapshot: WorkspaceSnapshot = self._application.snapshot()
        dialog.set_layers(snapshot.layers, snapshot.active_layer_id)
        dialog.set_bookmarks(self._application.bookmarks())
        dialog._tabs.setCurrentIndex(active_tab)
        # 居中于主窗口，避免贴靠屏幕顶端。
        dialog.resize(520, 680)
        parent_geo = self.frameGeometry()
        dialog_geo = dialog.frameGeometry()
        dialog_geo.moveCenter(parent_geo.center())
        dialog.move(dialog_geo.topLeft())
        dialog.exec()

    def _change_layer_opacity(self, layer_id: str, opacity: float) -> None:
        """应用指定图层的新透明度，并刷新工作区。"""
        before: float = 1.0
        for layer in self._application.snapshot().layers:
            if layer.layer_id == layer_id:
                before = layer.opacity
                break
        try:
            self._application.set_layer_opacity(layer_id, opacity)
        except (ApplicationError, ValueError) as error:
            self.statusBar().showMessage(f"透明度设置失败：{error}", 4000)
            return
        self._push_undo(
            "调整图层透明度",
            undo_action=partial(
                self._application.set_layer_opacity, layer_id, before
            ),
            redo_action=partial(
                self._application.set_layer_opacity, layer_id, opacity
            ),
        )
        self._schedule_workspace_refresh()

    def _change_layer_blend_mode(self, layer_id: str, blend_mode: str) -> None:
        """应用指定图层的新混合模式，并刷新工作区。"""
        before: str = "normal"
        for layer in self._application.snapshot().layers:
            if layer.layer_id == layer_id:
                before = layer.blend_mode
                break
        try:
            self._application.set_layer_blend_mode(layer_id, blend_mode)
        except (ApplicationError, ValueError) as error:
            self.statusBar().showMessage(f"混合模式设置失败：{error}", 4000)
            return
        self._push_undo(
            "调整图层混合模式",
            undo_action=partial(
                self._application.set_layer_blend_mode, layer_id, before
            ),
            redo_action=partial(
                self._application.set_layer_blend_mode, layer_id, blend_mode
            ),
        )
        self._schedule_workspace_refresh()

    def _change_layer_scale_range(
        self,
        layer_id: str,
        min_scale: float | None,
        max_scale: float | None,
    ) -> None:
        """应用图层的新显示比例范围并刷新工作区。"""
        before_min: float | None = None
        before_max: float | None = None
        for layer in self._application.snapshot().layers:
            if layer.layer_id == layer_id:
                before_min = layer.min_scale_percent
                before_max = layer.max_scale_percent
                break
        try:
            self._application.set_layer_scale_range(layer_id, min_scale, max_scale)
        except (ApplicationError, ValueError) as error:
            self.statusBar().showMessage(f"显示比例范围设置失败：{error}", 4000)
            return
        self._push_undo(
            "调整显示比例范围",
            undo_action=partial(
                self._application.set_layer_scale_range,
                layer_id,
                before_min,
                before_max,
            ),
            redo_action=partial(
                self._application.set_layer_scale_range,
                layer_id,
                min_scale,
                max_scale,
            ),
        )
        self._schedule_workspace_refresh()

    def _add_bookmark(self, name: str) -> None:
        """把当前地图视图捕获为命名书签。"""
        try:
            self._application.add_bookmark(name, self._map_canvas.capture_view_state())
        except ValueError as error:
            self.statusBar().showMessage(f"添加书签失败：{error}", 4000)
            return
        self._ready_label.setText(f"已添加书签  {name}")

    def _jump_to_bookmark(self, name: str) -> None:
        """定位到指定名称的地图书签。"""
        view_state: MapViewState | None = next(
            (
                bookmark.view_state
                for bookmark in self._application.bookmarks()
                if bookmark.name == name
            ),
            None,
        )
        if view_state is not None:
            self._map_canvas.restore_view_state(view_state)
            self._ready_label.setText(f"已定位到书签  {name}")

    def _delete_bookmark(self, name: str) -> None:
        """删除指定名称的地图书签。"""
        try:
            self._application.remove_bookmark(name)
        except ValueError as error:
            self.statusBar().showMessage(f"删除书签失败：{error}", 4000)
            return
        self._ready_label.setText(f"已删除书签  {name}")

    def _show_placeholder(self, feature_name: str) -> None:
        """为尚未实现的业务能力提供清晰且不伪造结果的界面反馈。

        参数:
            feature_name: 当前预留接口对应的中文功能名称。

        状态变化:
            更新状态栏并弹出“接口已预留”说明，不生成任何业务数据。
        """
        self._ready_label.setText(f"{feature_name} · 接口已预留")
        QMessageBox.information(
            self,
            feature_name,
            f"“{feature_name}”界面入口已经集成。\n业务实现将在后续模块中接入。",
        )

    def _show_about(self) -> None:
        """展示平台定位和当前界面集成阶段信息。"""
        QMessageBox.about(
            self,
            "关于 GIS 桌面通用平台",
            "GIS 桌面通用平台\n\n基于 PySide6、GeoPandas 与 Rasterio 构建。\n当前版本已完成统一主界面和功能接口集成。",
        )

    def _refresh_workspace(
        self,
        view_state: MapViewState | None = None,
        preserve_view: bool = True,
    ) -> None:
        """将应用层最新快照同步到界面。

        参数:
            view_state: 工程恢复时明确指定的地图中心和缩放状态。
            preserve_view: 已被 set_snapshot 内部处理，保留此参数以兼容调用方。

        说明:
            set_snapshot 内部在非首次加载时自动保存并恢复视图中心，
            无需在此处重复捕获/恢复；仅工程加载需显式传入 view_state。
        """
        snapshot: WorkspaceSnapshot = self._application.snapshot()
        self._layer_panel.apply_snapshot(snapshot)
        self._map_canvas.set_snapshot(snapshot)
        if view_state is not None:
            self._map_canvas.restore_view_state(view_state)
        if self._layout_mode:
            self._layout_view.set_snapshot(snapshot)
            self._layout_view.refresh_map_frames()
        active_name: str = "无"
        for layer in snapshot.layers:
            if layer.layer_id == snapshot.active_layer_id:
                active_name = layer.name
        self._layer_label.setText(f"当前图层  {active_name}")
        self._selection_label.setText(f"选中要素  {snapshot.selection_count}")
        crs_name: str = self._format_crs(snapshot.display_crs)
        self._crs_label.setText(f"坐标系  {crs_name}")
        self._refresh_analysis_history(snapshot)
        if self._attribute_table_panel.isVisible():
            table_layer_snapshot: LayerSnapshot | None = next(
                (
                    layer
                    for layer in snapshot.layers
                    if layer.layer_id == self._attribute_table_panel.layer_id
                ),
                None,
            )
            if table_layer_snapshot is not None:
                # 仅同步选择高亮，不重建表格，避免触发表→图→表的反馈循环。
                self._attribute_table_panel.highlight_features(
                    set(table_layer_snapshot.selected_feature_ids)
                )
            else:
                self._attribute_table_panel.set_layer(None)
                self._hide_attribute_table()
        self._update_window_title()

    def _refresh_analysis_history(self, snapshot: WorkspaceSnapshot | None = None) -> None:
        """将当前工程的分析历史和图层名称同步到分析记录面板。"""
        resolved_snapshot: WorkspaceSnapshot = snapshot or self._application.snapshot()
        layer_names: dict[str, str] = {
            layer.layer_id: layer.name for layer in resolved_snapshot.layers
        }
        self._analysis_history_panel.set_history(
            self._application.analysis_runs,
            layer_names,
        )

    def _refresh_attribute_table(self) -> None:
        """在要素属性发生变化后重建底部属性表内容。"""
        if not self._attribute_table_panel.isVisible():
            return
        layer_snapshot: LayerSnapshot | None = next(
            (
                layer
                for layer in self._application.snapshot().layers
                if layer.layer_id == self._attribute_table_panel.layer_id
            ),
            None,
        )
        if layer_snapshot is None:
            self._attribute_table_panel.set_layer(None)
            self._hide_attribute_table()
            return
        self._attribute_table_panel.refresh_layer(layer_snapshot)

    def _schedule_workspace_refresh(self) -> None:
        """在当前 Qt 事件结束后合并执行一次完整工作区刷新。

        图层树节点发出的激活、显隐、删除和排序信号仍处于原生鼠标事件调用栈中。
        若同步清空树节点，Qt 会继续访问已经销毁的节点并触发访问冲突。
        """
        if self._workspace_refresh_scheduled:
            return
        self._workspace_refresh_scheduled = True
        QTimer.singleShot(0, self._run_scheduled_workspace_refresh)

    def _run_scheduled_workspace_refresh(self) -> None:
        """执行已经离开控件事件调用栈的工作区刷新。"""
        self._workspace_refresh_scheduled = False
        self._refresh_workspace()

    def _confirm_project_switch(self) -> bool:
        """在新建、打开或关闭工程前处理未保存修改。"""
        if not self._application.is_modified or self._application.project_store is None:
            return True
        answer = QMessageBox.question(
            self,
            "保存工程修改",
            "当前工程有未保存修改，是否先保存？",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self._save_project()
        return answer == QMessageBox.StandardButton.Discard

    def _update_window_title(self) -> None:
        """根据工程名称和修改状态更新窗口标题。"""
        title: str = self._application.project_name
        if self._application.is_modified:
            title += " *"
        self.setWindowTitle(f"{title} · GIS桌面通用平台")

    # ── 撤销 ────────────────────────────────────────────────

    def _update_undo_buttons(self) -> None:
        """按撤销/重做栈是否为空同步功能区按钮的可用状态。"""
        self._ribbon.set_action_enabled("undo", bool(self._undo_stack))
        self._ribbon.set_action_enabled("redo", bool(self._redo_stack))

    def _clear_undo_history(self) -> None:
        """切换工程后清空撤销与重做历史，避免旧工程操作作用到新工程。"""
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._update_undo_buttons()

    def _push_undo(
        self,
        description: str,
        undo_action: Callable[[], object],
        redo_action: Callable[[], object],
    ) -> None:
        """将一条可撤销操作压入栈，同时清空重做栈。

        参数:
            description: 撤销操作的中文描述，用于状态栏提示。
            undo_action: 执行撤销的可调用对象。
            redo_action: 执行重做的可调用对象。
        """
        self._undo_stack.append((description, undo_action, redo_action))
        self._redo_stack.clear()
        # 限制栈深度，丢弃最早的记录。
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)
        self._update_undo_buttons()

    def _undo(self) -> None:
        """Ctrl+Z：撤销最近一次地图修改，并将其移入重做栈。"""
        if not self._undo_stack:
            self.statusBar().showMessage("没有可撤销的操作", 3000)
            return
        description, undo_action, redo_action = self._undo_stack.pop()
        try:
            undo_action()
        except (ApplicationError, ValueError) as error:
            # 场景失效（图层/文件已不存在、坐标系已变更）时丢弃该条记录，
            # 避免同一记录反复失败；已弹出栈，不回填重做栈。
            self.statusBar().showMessage(f"撤销失败：{error}", 5000)
            self._refresh_workspace()
            self._update_undo_buttons()
            return
        self._redo_stack.append((description, undo_action, redo_action))
        self._update_undo_buttons()
        self._refresh_workspace()
        self._refresh_attribute_table()
        self._ready_label.setText(f"已撤销  {description}")

    def _redo(self) -> None:
        """Ctrl+Shift+Z：重做最近一次撤销，并将其移回撤销栈。"""
        if not self._redo_stack:
            self.statusBar().showMessage("没有可重做的操作", 3000)
            return
        description, undo_action, redo_action = self._redo_stack.pop()
        try:
            redo_action()
        except (ApplicationError, ValueError) as error:
            # 场景失效（文件已删除、数据库已断开）时丢弃该条记录，不回填撤销栈。
            self.statusBar().showMessage(f"重做失败：{error}", 5000)
            self._refresh_workspace()
            self._update_undo_buttons()
            return
        self._undo_stack.append((description, undo_action, redo_action))
        self._update_undo_buttons()
        self._refresh_workspace()
        self._refresh_attribute_table()
        self._ready_label.setText(f"已重做  {description}")

    def closeEvent(self, event: QCloseEvent) -> None:
        """关闭窗口前清空查询选择和要素选择，避免未保存修改被静默丢弃。"""
        self._cleanup_query_and_selection()
        if self._confirm_project_switch():
            if self._application.database_is_connected:
                self._application.disconnect_database()
            event.accept()
        else:
            event.ignore()

    def resizeEvent(self, event) -> None:
        """主窗口缩放时重新定位布局工具栏。"""
        super().resizeEvent(event)
        self._position_layout_toolbar()

    def moveEvent(self, event) -> None:
        """主窗口移动时重新定位布局工具栏。"""
        super().moveEvent(event)
        self._position_layout_toolbar()

    @staticmethod
    def _crs_unit_name(crs: CRS | None) -> str:
        """获取 CRS 的单位名称。"""
        if crs is None:
            return "地图单位"
        if crs.is_geographic:
            return "度"
        unit_name: str = crs.axis_info[0].unit_name if crs.axis_info else ""
        if unit_name == "metre":
            return "米"
        if unit_name:
            return unit_name
        return "地图单位"

    @staticmethod
    def _format_crs(crs: CRS | None) -> str:
        """格式化坐标系权威编号和名称，区分 EPSG 与 ESRI 编码。"""
        if crs is None:
            return "未设置"
        authority: tuple[str, str] | None = crs.to_authority()
        if authority is None:
            return crs.name
        authority_name, authority_code = authority
        return f"{authority_name}:{authority_code} · {crs.name}"

    @staticmethod
    def _status_separator() -> QLabel:
        """创建用于分隔状态栏信息组的细竖线标签。"""
        separator: QLabel = QLabel("│")
        separator.setObjectName("statusSeparator")
        return separator

    @staticmethod
    def _with_export_suffix(path: Path, selected_filter: str, is_raster: bool) -> Path:
        """用户未输入扩展名时，根据所选格式补充稳定后缀。"""
        if path.suffix:
            return path
        if is_raster:
            return path.with_suffix(".tif")
        if "Shapefile" in selected_filter:
            return path.with_suffix(".shp")
        if "GeoPackage" in selected_filter:
            return path.with_suffix(".gpkg")
        return path.with_suffix(".geojson")
