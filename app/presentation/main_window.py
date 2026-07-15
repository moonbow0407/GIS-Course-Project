"""GIS 桌面通用平台主窗口。"""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStatusBar,
)

from app.application.errors import ApplicationError
from app.application.gis_application import GisApplication
from app.application.results import LayerSnapshot, WorkspaceSnapshot
from app.infrastructure.file_io.auto_reader import AutoDataReader
from app.presentation.widgets.attribute_table import AttributeTableDialog
from app.presentation.widgets.layer_panel import LayerPanel
from app.presentation.widgets.map_canvas import MapCanvas
from app.presentation.widgets.ribbon_bar import RibbonBar


class MainWindow(QMainWindow):
    """组装功能区、图层面板、地图画布和状态栏的 GIS 工作台。"""

    def __init__(self) -> None:
        """创建不含任何演示数据的空白 GIS 工作区。"""
        super().__init__()
        # 应用服务：统一编排空间数据读取和地图文档操作。
        self._application: GisApplication = GisApplication(AutoDataReader())
        # 顶部功能区：集中呈现文档规划的全部现有及预留功能入口。
        self._ribbon: RibbonBar = RibbonBar()
        # 图层面板：展示并操作当前地图文档中的真实图层。
        self._layer_panel: LayerPanel = LayerPanel()
        # 地图画布：显示矢量与栅格图层并提供基础导航能力。
        self._map_canvas: MapCanvas = MapCanvas()
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
        self._create_ui()
        self._connect_signals()
        self._refresh_workspace()

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
        self._layer_panel.layer_move_requested.connect(self._move_layer)
        self._map_canvas.coordinate_changed.connect(self._coordinate_label.setText)
        self._map_canvas.view_scale_changed.connect(self._scale_label.setText)

    def _handle_action(self, action_id: str) -> None:
        """把稳定功能编号路由到已实现能力或预留接口。

        参数:
            action_id: 功能区按钮发出的稳定操作编号。

        说明:
            当前仅接入文件打开、地图导航、选择清除和已有属性表；其余入口
            明确保留为界面接口，不伪造业务结果或测试数据。
        """
        # 使用操作编号映射处理函数，避免大量重复的条件分支。
        implemented_actions: dict[str, Callable[[], None]] = {
            "open_data": self._open_data,
            "zoom_in": self._map_canvas.zoom_in,
            "zoom_out": self._map_canvas.zoom_out,
            "pan": self._map_canvas.set_pan_tool,
            "full_extent": self._map_canvas.zoom_to_full_extent,
            "refresh_map": self._refresh_workspace,
            "clear_selection": self._clear_selection,
            "toggle_layers": self._toggle_layer_panel,
            "show_attributes": self._show_active_attribute_table,
            "about": self._show_about,
        }
        handler: Callable[[], None] | None = implemented_actions.get(action_id)
        if handler is not None:
            handler()
            return
        self._show_placeholder(RibbonBar.action_title(action_id) or "该功能")

    def _open_data(self) -> None:
        """选择真实空间数据文件并交给应用层识别和读取。"""
        # Qt 同时返回文件路径和所选过滤器，这里只需要第一个值。
        path_string: str = QFileDialog.getOpenFileName(
            self,
            "打开空间数据",
            "",
            "空间数据 (*.shp *.geojson *.json *.tif *.tiff *.img *.dem);;所有文件 (*.*)",
        )[0]
        if not path_string:
            return
        try:
            result = self._application.open_data(Path(path_string))
        except (ApplicationError, ValueError) as error:
            QMessageBox.warning(self, "打开数据失败", str(error))
            return
        self._refresh_workspace()
        self._ready_label.setText(f"已加载  {Path(path_string).name}")
        if result.warning:
            self.statusBar().showMessage(result.warning, 5000)

    def _activate_layer(self, layer_id: str) -> None:
        """设置活动图层并刷新工作区。

        参数:
            layer_id: 图层面板选中的真实图层编号。
        """
        self._application.set_active_layer(layer_id)
        self._refresh_workspace()

    def _change_visibility(self, layer_id: str, visible: bool) -> None:
        """更新图层显隐状态并刷新工作区。

        参数:
            layer_id: 需要更新的真实图层编号。
            visible: 图层是否参与地图绘制和空间查询。
        """
        self._application.set_layer_visibility(layer_id, visible)
        self._refresh_workspace()

    def _remove_layer(self, layer_id: str) -> None:
        """删除指定图层并刷新工作区。

        参数:
            layer_id: 需要从地图文档移除的真实图层编号。
        """
        self._application.remove_layer(layer_id)
        self._refresh_workspace()

    def _move_layer(self, layer_id: str, target_index: int) -> None:
        """按照图层面板请求调整真实地图图层顺序。

        参数:
            layer_id: 需要移动的真实图层编号。
            target_index: 图层在从底到顶显示顺序中的目标位置。
        """
        self._application.move_layer(layer_id, target_index)
        self._refresh_workspace()

    def _show_attribute_table(self, layer_id: str) -> None:
        """打开指定真实图层的只读属性或栅格元数据窗口。

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
        dialog: AttributeTableDialog = AttributeTableDialog(layer_snapshot, self)
        dialog.exec()

    def _show_active_attribute_table(self) -> None:
        """打开活动图层属性表；无图层时显示轻量提示。"""
        active_layer_id: str | None = self._application.snapshot().active_layer_id
        if active_layer_id is None:
            self.statusBar().showMessage("请先打开并选择一个图层。", 3500)
            return
        self._show_attribute_table(active_layer_id)

    def _clear_selection(self) -> None:
        """清除已有矢量要素选择并刷新工作区。"""
        self._application.clear_selection()
        self._refresh_workspace()

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

    def _refresh_workspace(self) -> None:
        """将应用层最新快照同步到图层面板、地图和状态栏。"""
        snapshot: WorkspaceSnapshot = self._application.snapshot()
        self._layer_panel.apply_snapshot(snapshot)
        self._map_canvas.set_snapshot(snapshot)
        active_name: str = "无"
        for layer in snapshot.layers:
            if layer.layer_id == snapshot.active_layer_id:
                active_name = layer.name
        self._layer_label.setText(f"当前图层  {active_name}")
        self._selection_label.setText(f"选中要素  {snapshot.selection_count}")
        crs_name: str = snapshot.display_crs.to_string() if snapshot.display_crs else "未设置"
        self._crs_label.setText(f"坐标系  {crs_name}")

    @staticmethod
    def _status_separator() -> QLabel:
        """创建用于分隔状态栏信息组的细竖线标签。"""
        separator: QLabel = QLabel("│")
        separator.setObjectName("statusSeparator")
        return separator
