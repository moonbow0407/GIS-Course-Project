"""迁移后的 GIS 主窗口。"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStatusBar,
    QToolBar,
)

from app.application.errors import ApplicationError
from app.application.gis_application import GisApplication
from app.application.results import LayerSnapshot, WorkspaceSnapshot
from app.infrastructure.file_io.auto_reader import AutoDataReader
from app.presentation.widgets.attribute_table import AttributeTableDialog
from app.presentation.widgets.layer_panel import LayerPanel
from app.presentation.widgets.map_canvas import MapCanvas


class MainWindow(QMainWindow):
    """使用应用层和目标控件包组装 GIS 主窗口。"""

    def __init__(self) -> None:
        """创建空工作区主窗口。"""
        super().__init__()
        self._application: GisApplication = GisApplication(AutoDataReader())
        self._layer_panel: LayerPanel = LayerPanel()
        self._map_canvas: MapCanvas = MapCanvas()
        self._layer_label: QLabel = QLabel("当前图层：无")
        self._selection_label: QLabel = QLabel("选中要素：0")
        self._create_ui()
        self._connect_signals()
        self._refresh_workspace()

    def _create_ui(self) -> None:
        """创建菜单、工具栏、左右工作区和状态栏。"""
        self.setWindowTitle("GIS桌面通用平台")
        self.resize(1600, 900)
        toolbar: QToolBar = QToolBar("主工具栏", self)
        toolbar.setMovable(False)
        open_action = toolbar.addAction("打开数据")
        open_action.setObjectName("openDataAction")
        open_action.triggered.connect(self._open_data)
        toolbar.addSeparator()
        toolbar.addAction("平移").triggered.connect(self._map_canvas.set_pan_tool)
        toolbar.addAction("全图显示").triggered.connect(self._map_canvas.zoom_to_full_extent)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)
        splitter: QSplitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._layer_panel)
        splitter.addWidget(self._map_canvas)
        splitter.setSizes([260, 1340])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)
        status_bar: QStatusBar = QStatusBar(self)
        status_bar.addWidget(self._layer_label)
        status_bar.addWidget(self._selection_label)
        self.setStatusBar(status_bar)

    def _connect_signals(self) -> None:
        """连接图层面板请求与应用层命令。"""
        self._layer_panel.layer_activated.connect(self._activate_layer)
        self._layer_panel.layer_visibility_changed.connect(self._change_visibility)
        self._layer_panel.layer_removed.connect(self._remove_layer)
        self._layer_panel.layer_attribute_requested.connect(self._show_attribute_table)

    def _open_data(self) -> None:
        """选择空间数据文件并交给应用层自动判断和读取。"""
        path_string: str = QFileDialog.getOpenFileName(
            self,
            "打开空间数据",
            "",
            "空间数据 (*.shp *.geojson *.json *.tif *.tiff *.img *.dem);;所有文件 (*.*)",
        )[0]
        if not path_string:
            return
        try:
            result = self._application.open_vector(Path(path_string))
        except ApplicationError as error:
            QMessageBox.warning(self, "打开数据失败", str(error))
            return
        self._map_canvas.set_snapshot(result.snapshot)
        self._refresh_workspace()
        if result.warning:
            self.statusBar().showMessage(result.warning, 5000)

    def _activate_layer(self, layer_id: str) -> None:
        """设置活动图层并刷新工作区。"""
        self._application.set_active_layer(layer_id)
        self._refresh_workspace()

    def _change_visibility(self, layer_id: str, visible: bool) -> None:
        """更新图层显隐状态并刷新工作区。"""
        self._application.set_layer_visibility(layer_id, visible)
        self._refresh_workspace()

    def _remove_layer(self, layer_id: str) -> None:
        """删除指定图层并刷新工作区。"""
        self._application.remove_layer(layer_id)
        self._refresh_workspace()

    def _show_attribute_table(self, layer_id: str) -> None:
        """打开右键指定图层的只读属性表。"""
        snapshot: WorkspaceSnapshot = self._application.snapshot()
        layer_snapshot: LayerSnapshot | None = next(
            (layer for layer in snapshot.layers if layer.layer_id == layer_id),
            None,
        )
        if layer_snapshot is None:
            return
        dialog: AttributeTableDialog = AttributeTableDialog(layer_snapshot, self)
        dialog.exec()

    def _refresh_workspace(self) -> None:
        """将应用层最新快照同步到图层面板、地图和状态栏。"""
        snapshot: WorkspaceSnapshot = self._application.snapshot()
        self._layer_panel.apply_snapshot(snapshot)
        self._map_canvas.set_snapshot(snapshot)
        active_name: str = "无"
        for layer in snapshot.layers:
            if layer.layer_id == snapshot.active_layer_id:
                active_name = layer.name
        self._layer_label.setText(f"当前图层：{active_name}")
        self._selection_label.setText(f"选中要素：{snapshot.selection_count}")
