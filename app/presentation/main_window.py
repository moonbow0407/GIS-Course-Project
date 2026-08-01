"""GIS 桌面通用平台主窗口。"""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from pyproj import CRS
from pyproj.exceptions import CRSError
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStatusBar,
)

from app.application.database_service import DatabaseService
from app.application.errors import ApplicationError
from app.application.gis_application import GisApplication
from app.application.project_models import MapViewState
from app.application.results import LayerSnapshot, WorkspaceSnapshot
from app.domain.symbology import RasterSymbology, VectorSymbology
from app.infrastructure.database.postgis_database_gateway import PostgisDatabaseGateway
from app.infrastructure.file_io.auto_reader import AutoDataReader
from app.infrastructure.file_io.auto_writer import AutoDataWriter
from app.infrastructure.project.json_project_store import JsonProjectStore
from app.presentation.widgets.analysis_history_panel import AnalysisHistoryPanel
from app.presentation.widgets.attribute_table import AttributeTablePanel
from app.presentation.widgets.buffer_analysis_dialog import BufferAnalysisDialog
from app.presentation.widgets.database_dialogs import (
    DatabaseConnectionDialog,
    DatabaseLayerDialog,
)
from app.presentation.widgets.layer_panel import LayerPanel
from app.presentation.widgets.map_canvas import MapCanvas
from app.presentation.widgets.ribbon_bar import RibbonBar
from app.presentation.widgets.symbology_panel import SymbologyPanel


class MainWindow(QMainWindow):
    """组装功能区、图层面板、地图画布和状态栏的 GIS 工作台。"""

    def __init__(self) -> None:
        """创建不含任何演示数据的空白 GIS 工作区。"""
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
        # 符号系统面板：右侧停靠并跟随当前活动图层。
        self._symbology_panel: SymbologyPanel = SymbologyPanel()
        self._symbology_dock: QDockWidget = QDockWidget("符号系统", self)
        # 分析历史面板：右侧停靠展示空间分析执行记录。
        self._analysis_history_panel: AnalysisHistoryPanel = AnalysisHistoryPanel()
        self._analysis_history_dock: QDockWidget = QDockWidget("分析记录", self)
        # 属性表面板：右侧停靠展示矢量要素属性和栅格元数据。
        self._attribute_table_panel: AttributeTablePanel = AttributeTablePanel()
        self._attribute_table_dock: QDockWidget = QDockWidget("属性表", self)
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
        self._undo_stack: list[tuple[str, Callable[[], None], Callable[[], None]]] = []
        # 重做栈：撤销后暂存被撤销的操作，新操作执行时清空。
        self._redo_stack: list[tuple[str, Callable[[], None], Callable[[], None]]] = []
        self._create_ui()
        self._connect_signals()
        # Ctrl+Z 撤销最近一次地图修改。
        QShortcut(QKeySequence.StandardKey.Undo, self, activated=self._undo)
        # Ctrl+Shift+Z 重做最近一次撤销。
        QShortcut(QKeySequence.StandardKey.Redo, self, activated=self._redo)
        self._refresh_workspace(preserve_view=False)

    def _create_ui(self) -> None:
        """创建功能区、双栏工作区和多信息状态栏。"""
        self.setObjectName("mainWindow")
        self.setWindowTitle("GIS桌面通用平台")
        self.resize(1680, 940)
        self.setMinimumSize(1120, 720)
        self.setMenuWidget(self._ribbon)

        workspace: QSplitter = QSplitter(Qt.Orientation.Horizontal)
        workspace.setObjectName("workspaceSplitter")
        workspace.setChildrenCollapsible(False)
        workspace.addWidget(self._layer_panel)
        workspace.addWidget(self._map_canvas)
        workspace.setSizes([300, 1380])
        workspace.setStretchFactor(0, 0)
        workspace.setStretchFactor(1, 1)
        self.setCentralWidget(workspace)
        self._symbology_dock.setObjectName("symbologyDock")
        self._symbology_dock.setWidget(self._symbology_panel)
        self._symbology_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._symbology_dock)
        self._symbology_dock.hide()
        self._analysis_history_dock.setObjectName("analysisHistoryDock")
        self._analysis_history_dock.setWidget(self._analysis_history_panel)
        self._analysis_history_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._analysis_history_dock)
        self._analysis_history_dock.hide()
        self._attribute_table_dock.setObjectName("attributeTableDock")
        self._attribute_table_dock.setWidget(self._attribute_table_panel)
        self._attribute_table_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.BottomDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._attribute_table_dock)
        self._attribute_table_dock.hide()

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
        self._layer_panel.layer_attribute_requested.connect(self._show_attribute_table)
        self._layer_panel.layer_zoom_requested.connect(self._zoom_to_layer)
        self._layer_panel.layer_symbology_requested.connect(self._show_symbology)
        self._layer_panel.category_visibility_changed.connect(
            self._change_category_visibility
        )
        self._layer_panel.layer_move_requested.connect(self._move_layer)
        self._layer_panel.selection_cleared.connect(self._clear_active_layer)
        self._symbology_panel.symbology_changed.connect(self._apply_symbology)
        self._symbology_panel.unique_requested.connect(self._apply_unique_symbology)
        self._symbology_panel.graduated_requested.connect(self._apply_graduated_symbology)
        self._analysis_history_panel.clear_requested.connect(self._clear_analysis_history)
        self._map_canvas.coordinate_changed.connect(self._coordinate_label.setText)
        self._map_canvas.view_scale_changed.connect(self._scale_label.setText)
        self._map_canvas.canvas_clicked.connect(self._on_canvas_clicked)
        self._attribute_table_panel.selection_changed.connect(
            self._on_table_selection_changed
        )
        self._attribute_table_panel.feature_zoom_requested.connect(
            self._on_table_zoom_requested
        )

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
            "analysis_history": self._toggle_analysis_history,
            "toggle_layers": self._toggle_layer_panel,
            "show_attributes": self._show_active_attribute_table,
            "set_crs": self._set_display_crs,
            "about": self._show_about,
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
        try:
            result = self._application.load_database_layer(dialog.selected_layer_id())
        except (ApplicationError, ValueError) as error:
            QMessageBox.warning(self, "加载数据库图层失败", str(error))
            return
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
            "空间数据 (*.shp *.geojson *.json *.gpkg *.tif *.tiff *.img *.dem);;所有文件 (*.*)",
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

    def _new_project(self) -> None:
        """新建空白工程，并在需要时处理当前未保存修改。"""
        if not self._confirm_project_switch():
            return
        self._application.new_project()
        self._refresh_workspace(preserve_view=False)
        self._ready_label.setText("已新建空白工程")

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
        if not self._confirm_project_switch():
            return
        try:
            result = self._application.open_project(Path(path_string))
        except (ApplicationError, ValueError) as error:
            QMessageBox.warning(self, "打开工程失败", str(error))
            return
        self._refresh_workspace(result.view_state)
        self._ready_label.setText(f"已打开工程  {result.path.name}")
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
            )
        except (ApplicationError, ValueError) as error:
            QMessageBox.warning(self, "保存工程失败", str(error))
            return False
        self._ready_label.setText(f"工程已保存  {result.path.name}")
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
        # 符号系统面板若已打开需跟随活动图层切换。
        if self._symbology_dock.isVisible():
            active_snapshot: LayerSnapshot | None = next(
                (
                    layer
                    for layer in snapshot.layers
                    if layer.layer_id == snapshot.active_layer_id
                ),
                None,
            )
            self._symbology_panel.set_layer(active_snapshot)

    def _clear_active_layer(self) -> None:
        """点击图层面板空白处时清除活动图层。"""
        try:
            self._application.clear_active_layer()
        except ApplicationError:
            pass
        self._layer_label.setText("当前图层  无")
        self._symbology_panel.set_layer(None)

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
            undo_action=lambda lid=layer_id, vis=not visible: self._application.set_layer_visibility(
                lid, vis
            ),
            redo_action=lambda lid=layer_id, vis=visible: self._application.set_layer_visibility(
                lid, vis
            ),
        )
        self._application.set_layer_visibility(layer_id, visible)
        self._schedule_workspace_refresh()

    def _remove_layer(self, layer_id: str) -> None:
        """删除指定图层并刷新工作区。

        参数:
            layer_id: 需要从地图文档移除的真实图层编号。
        """
        self._application.remove_layer(layer_id)
        self._schedule_workspace_refresh()

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
            undo_action=lambda lid=layer_id, idx=old_index: self._application.move_layer(lid, idx),
            redo_action=lambda lid=layer_id, idx=target_index: self._application.move_layer(lid, idx),
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
        self._attribute_table_dock.show()
        self._attribute_table_dock.raise_()
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
        """激活图层并显示跟随活动图层的右侧符号系统面板。"""
        try:
            self._application.set_active_layer(layer_id)
        except ApplicationError:
            return
        self._symbology_dock.show()
        self._refresh_workspace()

    def _apply_symbology(
        self,
        layer_id: str,
        symbology: VectorSymbology | RasterSymbology,
    ) -> None:
        """自动应用面板提交的完整矢量或栅格符号配置。"""
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if isinstance(symbology, VectorSymbology):
                self._application.apply_vector_symbology(layer_id, symbology)
            else:
                self._application.apply_raster_symbology(layer_id, symbology)
        except (ApplicationError, ValueError) as error:
            QMessageBox.warning(self, "符号系统更新失败", str(error))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self._refresh_workspace()

    def _apply_unique_symbology(
        self,
        layer_id: str,
        field_name: str,
        color_scheme: str,
    ) -> None:
        """生成并自动应用唯一值符号。"""
        try:
            self._application.apply_unique_value_symbology(
                layer_id,
                field_name,
                color_scheme,
            )
        except (ApplicationError, ValueError) as error:
            QMessageBox.warning(self, "唯一值符号更新失败", str(error))
            return
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
            self._application.set_selection(layer_id, feature_ids)
        except ApplicationError:
            return
        self._refresh_workspace()

    def _on_table_zoom_requested(self, layer_id: str, fid: object) -> None:
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

    def _clear_selection(self) -> None:
        """清除已有矢量要素选择并刷新工作区。"""
        self._application.clear_selection()
        self._refresh_workspace()

    def _set_display_crs(self) -> None:
        """通过 CRS 标识设置地图显示坐标系并重建已有图层。"""
        snapshot: WorkspaceSnapshot = self._application.snapshot()
        current_crs: str = snapshot.display_crs.to_string() if snapshot.display_crs else ""
        crs_text, accepted = QInputDialog.getText(
            self,
            "设置地图坐标系",
            "输入 CRS 标识（例如 EPSG:4326 或 ESRI:102026）：",
            text=current_crs,
        )
        normalized_text: str = crs_text.strip()
        if not accepted or not normalized_text:
            return
        try:
            target_crs: CRS = CRS.from_user_input(normalized_text)
        except CRSError as error:
            QMessageBox.warning(self, "坐标系设置失败", f"无法识别坐标系：{normalized_text}")
            self.statusBar().showMessage(str(error), 5000)
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

    def _toggle_analysis_history(self) -> None:
        """切换分析历史面板的显示状态。"""
        if self._analysis_history_dock.isVisible():
            self._analysis_history_dock.hide()
            return
        self._refresh_analysis_history()
        self._analysis_history_dock.show()
        self._analysis_history_dock.raise_()

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
        active_name: str = "无"
        for layer in snapshot.layers:
            if layer.layer_id == snapshot.active_layer_id:
                active_name = layer.name
        self._layer_label.setText(f"当前图层  {active_name}")
        self._selection_label.setText(f"选中要素  {snapshot.selection_count}")
        crs_name: str = self._format_crs(snapshot.display_crs)
        self._crs_label.setText(f"坐标系  {crs_name}")
        self._refresh_analysis_history(snapshot)
        if self._symbology_dock.isVisible():
            active_snapshot: LayerSnapshot | None = next(
                (
                    layer
                    for layer in snapshot.layers
                    if layer.layer_id == snapshot.active_layer_id
                ),
                None,
            )
            self._symbology_panel.set_layer(active_snapshot)
        if self._attribute_table_dock.isVisible():
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

    def _push_undo(
        self,
        description: str,
        undo_action: Callable[[], None],
        redo_action: Callable[[], None],
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

    def _undo(self) -> None:
        """Ctrl+Z：撤销最近一次地图修改，并将其移入重做栈。"""
        if not self._undo_stack:
            self.statusBar().showMessage("没有可撤销的操作", 3000)
            return
        description, undo_action, redo_action = self._undo_stack.pop()
        undo_action()
        self._redo_stack.append((description, undo_action, redo_action))
        self._refresh_workspace()
        self._ready_label.setText(f"已撤销  {description}")

    def _redo(self) -> None:
        """Ctrl+Shift+Z：重做最近一次撤销，并将其移回撤销栈。"""
        if not self._redo_stack:
            self.statusBar().showMessage("没有可重做的操作", 3000)
            return
        description, undo_action, redo_action = self._redo_stack.pop()
        redo_action()
        self._undo_stack.append((description, undo_action, redo_action))
        self._refresh_workspace()
        self._ready_label.setText(f"已重做  {description}")

    def closeEvent(self, event: QCloseEvent) -> None:
        """关闭窗口前避免未保存工程修改被静默丢弃。"""
        if self._confirm_project_switch():
            if self._application.database_is_connected:
                self._application.disconnect_database()
            event.accept()
        else:
            event.ignore()

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
