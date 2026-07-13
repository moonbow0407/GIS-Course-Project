"""主窗口。"""

from collections.abc import Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QStyle,
    QToolBar,
    QToolButton,
)

from app.widgets.layer_panel import LayerPanel
from app.widgets.map_canvas import MapCanvas
from app.widgets.right_panel import RightPanel


class MainWindow(QMainWindow):
    """GIS桌面通用平台主窗口。"""

    def __init__(self) -> None:
        super().__init__()
        self._actions: dict[str, QAction] = {}
        self._tool_group = QActionGroup(self)
        self._current_layer = "道路"

        self.layer_panel = LayerPanel()
        self.map_canvas = MapCanvas()
        self.right_panel = RightPanel()

        self._create_actions()
        self._create_menu_bar()
        self._create_tool_bar()
        self._create_central_widget()
        self._create_status_bar()
        self._connect_signals()
        self._initialize_window()

    def _initialize_window(self) -> None:
        self.setWindowTitle("GIS桌面通用平台")
        self.resize(1600, 900)
        self.right_panel.set_layer_names(self.layer_panel.layer_names())
        self._actions["pan"].setChecked(True)
        self.map_canvas.set_map_tool("pan")

    def _create_actions(self) -> None:
        self._add_action("open_vector", "打开矢量数据", self._show_developing_message, icon=self._standard_icon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self._add_action("open_raster", "打开栅格数据", self._show_developing_message, icon=self._standard_icon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self._add_action("save_layer", "保存当前图层", self._show_developing_message, icon=self._standard_icon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self._add_action("export_layer", "导出图层", self._show_developing_message, icon=self._glyph_icon("↗", "#2f7de1"))
        self._add_action("exit", "退出", self.close)

        self._add_action("undo", "撤销", self._show_developing_message)
        self._add_action("redo", "重做", self._show_developing_message)
        self._add_action("add_feature", "新增要素", self._show_developing_message, icon=self._standard_icon(QStyle.StandardPixmap.SP_FileDialogNewFolder))
        self._add_action("delete_feature", "删除要素", self._show_developing_message, icon=self._standard_icon(QStyle.StandardPixmap.SP_DialogCancelButton))
        self._add_action("edit_feature", "修改要素", self._show_developing_message, icon=self._standard_icon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        self._add_action("save_edit", "保存编辑", self._show_developing_message)

        self._add_action("point_query", "点选查询", self._show_developing_message)
        self._add_action("box_query", "框选查询", self._show_developing_message)
        self._add_action("attribute_query", "属性查询", self._show_developing_message)
        self._add_action("clear_selection", "清除选择", self._on_clear_selection)

        self._add_action("buffer_analysis", "缓冲区分析", self._show_developing_message, icon=self._standard_icon(QStyle.StandardPixmap.SP_BrowserReload))
        self._add_action("overlay_analysis", "叠加分析", self._show_developing_message)
        self._add_action("simplify_line", "线要素简化", self._show_developing_message)
        self._add_action("smooth_line", "线要素平滑", self._show_developing_message)

        self._add_action("connect_db", "连接数据库", self._show_developing_message, icon=self._standard_icon(QStyle.StandardPixmap.SP_DriveHDIcon))
        self._add_action("load_db_layer", "从数据库加载图层", self._show_developing_message)
        self._add_action("import_layer_db", "导入当前图层到数据库", self._show_developing_message)
        self._add_action("disconnect_db", "断开连接", self._show_developing_message)

        self._add_action("help", "使用说明", self._show_developing_message)
        self._add_action("about", "关于系统", self._on_about)

        self._add_tool_action("pan", "平移", "pan", QStyle.StandardPixmap.SP_ArrowUp, icon=self._glyph_icon("✥", "#168f89"))
        self._add_tool_action("zoom_in_tool", "放大", "zoom_in", QStyle.StandardPixmap.SP_ComputerIcon, checkable=False)
        self._add_tool_action("zoom_out_tool", "缩小", "zoom_out", QStyle.StandardPixmap.SP_ComputerIcon, checkable=False)
        self._add_tool_action("full_extent", "全图显示", "full_extent", QStyle.StandardPixmap.SP_DialogResetButton, checkable=False)
        self._add_tool_action("point_select", "点选", "point_select", QStyle.StandardPixmap.SP_ArrowRight, icon=self._glyph_icon("●", "#745bc7"))
        self._add_tool_action("box_select", "框选", "box_select", QStyle.StandardPixmap.SP_TitleBarMaxButton, icon=self._glyph_icon("□", "#745bc7"))
        self._add_tool_action("add_tool", "新增", "add", QStyle.StandardPixmap.SP_FileDialogNewFolder)
        self._add_tool_action("edit_tool", "修改", "edit", QStyle.StandardPixmap.SP_FileDialogDetailedView)

    def _add_action(
        self,
        key: str,
        text: str,
        slot: Callable[[], None],
        icon: QIcon | None = None,
    ) -> QAction:
        action = QAction(icon or QIcon(), text, self)
        action.triggered.connect(slot)
        self._actions[key] = action
        return action

    def _add_tool_action(
        self,
        key: str,
        text: str,
        tool_name: str,
        icon_id: QStyle.StandardPixmap,
        checkable: bool = True,
        icon: QIcon | None = None,
    ) -> QAction:
        action = QAction(icon or self._standard_icon(icon_id), text, self)
        action.setCheckable(checkable)
        action.setData(tool_name)
        if checkable:
            self._tool_group.addAction(action)
        self._actions[key] = action
        return action

    def _create_menu_bar(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("文件")
        file_menu.addAction(self._actions["open_vector"])
        file_menu.addAction(self._actions["open_raster"])
        file_menu.addSeparator()
        file_menu.addAction(self._actions["save_layer"])
        file_menu.addAction(self._actions["export_layer"])
        file_menu.addSeparator()
        file_menu.addAction(self._actions["exit"])

        edit_menu = menu_bar.addMenu("编辑")
        for key in ["undo", "redo", "add_feature", "delete_feature", "edit_feature", "save_edit"]:
            edit_menu.addAction(self._actions[key])

        query_menu = menu_bar.addMenu("查询")
        for key in ["point_query", "box_query", "attribute_query", "clear_selection"]:
            query_menu.addAction(self._actions[key])

        analysis_menu = menu_bar.addMenu("分析")
        for key in ["buffer_analysis", "overlay_analysis", "simplify_line", "smooth_line"]:
            analysis_menu.addAction(self._actions[key])

        database_menu = menu_bar.addMenu("数据库")
        for key in ["connect_db", "load_db_layer", "import_layer_db", "disconnect_db"]:
            database_menu.addAction(self._actions[key])

        help_menu = menu_bar.addMenu("帮助")
        help_menu.addAction(self._actions["help"])
        help_menu.addAction(self._actions["about"])

    def _create_tool_bar(self) -> None:
        toolbar = QToolBar("主工具栏", self)
        toolbar.setObjectName("mainToolBar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(24, 24))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

        for key in ["open_vector", "save_layer", "export_layer"]:
            toolbar.addAction(self._actions[key])
        toolbar.addSeparator()
        for key in ["pan", "zoom_in_tool", "zoom_out_tool", "full_extent", "point_select", "box_select"]:
            toolbar.addAction(self._actions[key])
        toolbar.addSeparator()
        for key in ["add_tool", "delete_feature", "edit_tool"]:
            toolbar.addAction(self._actions[key])
        toolbar.addSeparator()
        toolbar.addAction(self._actions["buffer_analysis"])
        toolbar.addAction(self._actions["connect_db"])

        for action in toolbar.actions():
            widget = toolbar.widgetForAction(action)
            if isinstance(widget, QToolButton):
                widget.setMinimumWidth(76)

    def _create_central_widget(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.layer_panel)
        splitter.addWidget(self.map_canvas)
        splitter.addWidget(self.right_panel)
        splitter.setSizes([240, 900, 320])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        self.setCentralWidget(splitter)

    def _create_status_bar(self) -> None:
        status_bar = QStatusBar(self)
        self.setStatusBar(status_bar)

        self._coordinate_label = QLabel("坐标：118.78, 32.04")
        self._scale_label = QLabel("比例尺：1:10000")
        self._layer_label = QLabel("当前图层：道路")
        self._selection_label = QLabel("选中要素：1")
        self._database_label = QLabel("数据库：未连接")
        self._database_label.setObjectName("databaseDisconnected")

        for label in [
            self._coordinate_label,
            self._scale_label,
            self._layer_label,
            self._selection_label,
            self._database_label,
        ]:
            status_bar.addWidget(label)
            status_bar.addWidget(self._separator())

    def _connect_signals(self) -> None:
        self._tool_group.triggered.connect(self._on_tool_action_triggered)
        self._actions["zoom_in_tool"].triggered.connect(self.map_canvas.zoom_in)
        self._actions["zoom_out_tool"].triggered.connect(self.map_canvas.zoom_out)
        self._actions["full_extent"].triggered.connect(self.map_canvas.zoom_to_full_extent)

        self.layer_panel.layer_selected.connect(self._on_layer_selected)
        self.layer_panel.layer_visibility_changed.connect(self._on_layer_visibility_changed)
        self.layer_panel.layer_order_changed.connect(self._on_layer_order_changed)
        self.layer_panel.layer_removed.connect(self._on_layer_removed)
        self.layer_panel.zoom_to_layer_requested.connect(self.map_canvas.zoom_to_layer)

        self.map_canvas.coordinate_changed.connect(self._on_coordinate_changed)
        self.map_canvas.feature_selected.connect(self._on_feature_selected)
        self.map_canvas.features_selected.connect(self._on_features_selected)
        self.map_canvas.selection_count_changed.connect(self._on_selection_count_changed)

        self.right_panel.run_buffer_analysis_requested.connect(self._on_run_buffer_analysis_requested)

    def _standard_icon(self, icon_id: QStyle.StandardPixmap) -> QIcon:
        icon = self.style().standardIcon(icon_id)
        return icon if not icon.isNull() else QIcon()

    def _glyph_icon(self, glyph: str, color: str) -> QIcon:
        """生成不受系统深浅主题影响的高对比度工具图标。"""
        pixmap = QPixmap(30, 30)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(1, 1, 28, 28, 6, 6)
        painter.setPen(Qt.GlobalColor.white)
        painter.setFont(QFont("Segoe UI Symbol", 11, QFont.Weight.Bold))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, glyph)
        painter.end()
        return QIcon(pixmap)

    def _separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        line.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        return line

    def _on_tool_action_triggered(self, action: QAction) -> None:
        tool = action.data()
        if isinstance(tool, str):
            self.map_canvas.set_map_tool(tool)

    def _on_layer_selected(self, layer_name: str) -> None:
        self._current_layer = layer_name
        self._layer_label.setText(f"当前图层：{layer_name}")

    def _on_layer_visibility_changed(self, layer_name: str, visible: bool) -> None:
        state = "显示" if visible else "隐藏"
        self.statusBar().showMessage(f"{layer_name} 已{state}", 2000)

    def _on_layer_order_changed(self, layer_names: list[str]) -> None:
        self.right_panel.set_layer_names(layer_names)
        self.statusBar().showMessage("图层顺序已更新", 2000)

    def _on_layer_removed(self, layer_name: str) -> None:
        self.statusBar().showMessage(f"{layer_name} 已删除", 2000)

    def _on_coordinate_changed(self, longitude: float, latitude: float) -> None:
        self._coordinate_label.setText(f"坐标：{longitude:.4f}, {latitude:.4f}")

    def _on_feature_selected(self, feature: dict) -> None:
        if feature:
            self.right_panel.set_attributes(feature)
        else:
            self.right_panel.clear_attributes()

    def _on_features_selected(self, features: list[dict]) -> None:
        if features:
            self.right_panel.set_attributes(features[0])
        else:
            self.right_panel.clear_attributes()

    def _on_selection_count_changed(self, count: int) -> None:
        self._selection_label.setText(f"选中要素：{count}")

    def _on_run_buffer_analysis_requested(self, params: dict) -> None:
        QMessageBox.information(
            self,
            "缓冲区分析参数",
            "\n".join(
                [
                    f"输入图层：{params['input_layer']}",
                    f"缓冲距离：{params['distance']} {params['unit']}",
                    f"输出图层名：{params['output_layer_name']}",
                    f"是否合并结果：{'是' if params['merge_result'] else '否'}",
                    "",
                    "当前阶段仅展示参数，真实空间分析功能正在开发中。",
                ]
            ),
        )

    def _on_clear_selection(self) -> None:
        self.right_panel.clear_attributes()
        self._selection_label.setText("选中要素：0")
        self.statusBar().showMessage("选择已清除", 2000)

    def _show_developing_message(self) -> None:
        QMessageBox.information(self, "功能开发中", "该功能正在开发中。")

    def _on_about(self) -> None:
        QMessageBox.about(
            self,
            "关于系统",
            "GIS桌面通用平台\n\n基于 Python 3.10 与 PySide6 的桌面 GIS 主界面骨架。",
        )
