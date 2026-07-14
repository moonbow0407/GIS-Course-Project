"""真实地图文档对应的图层管理控件。"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.application.results import WorkspaceSnapshot


class LayerPanel(QWidget):
    """展示工作区图层，不创建演示数据并通过信号请求业务操作。"""

    # 图层激活信号：携带用户当前选中的图层编号。
    layer_activated = Signal(str)
    # 图层显隐信号：携带图层编号及目标可见状态。
    layer_visibility_changed = Signal(str, bool)
    # 属性查看信号：请求显示指定图层的属性或元数据。
    layer_attribute_requested = Signal(str)
    # 图层删除信号：请求从地图文档移除指定图层。
    layer_removed = Signal(str)
    # 图层移动信号：携带图层编号及其在地图文档中的目标位置。
    layer_move_requested = Signal(str, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建空的图层管理控件。

        参数:
            parent: 父控件；为空时由工作区分隔器接管所有权。

        状态变化:
            创建搜索框和空图层树，不添加任何演示或测试图层。
        """
        super().__init__(parent)
        # 图层树：按地图显示顺序展示图层名称和显隐复选框。
        self._tree: QTreeWidget = QTreeWidget()
        # 快照更新标记：防止程序刷新控件时反向触发业务信号。
        self._updating: bool = False
        # 图层搜索框：根据名称即时筛选当前真实图层，不创建额外数据。
        self._search_input: QLineEdit = QLineEdit()
        self._create_ui()

    def apply_snapshot(self, snapshot: WorkspaceSnapshot) -> None:
        """按照工作区快照刷新图层名称、顺序、显隐和活动状态。

        参数:
            snapshot: 应用层提供的完整只读工作区状态。

        状态变化:
            原子替换图层树节点，并保持已有搜索条件继续生效。
        """
        self._updating = True
        self._tree.clear()
        # 地图文档按底到顶保存，图层面板按用户习惯将最顶层显示在列表最上方。
        for layer_snapshot in reversed(snapshot.layers):
            layer_kind: str = "栅格" if layer_snapshot.is_raster else "矢量"
            item: QTreeWidgetItem = QTreeWidgetItem([f"[{layer_kind}] {layer_snapshot.name}"])
            item.setData(0, Qt.ItemDataRole.UserRole, layer_snapshot.layer_id)
            item.setCheckState(0, Qt.CheckState.Checked if layer_snapshot.visible else Qt.CheckState.Unchecked)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            self._tree.addTopLevelItem(item)
            if layer_snapshot.layer_id == snapshot.active_layer_id:
                self._tree.setCurrentItem(item)
        self._updating = False
        self._filter_layers(self._search_input.text())

    def _create_ui(self) -> None:
        """创建标题、空图层树和排序按钮。"""
        title: QLabel = QLabel("图层")
        title.setObjectName("panelTitle")
        panel_hint: QLabel = QLabel("内容列表")
        panel_hint.setObjectName("panelHint")
        title_row: QHBoxLayout = QHBoxLayout()
        title_row.setContentsMargins(2, 0, 2, 0)
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(panel_hint)

        self._search_input.setObjectName("layerSearch")
        self._search_input.setPlaceholderText("搜索图层…")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(self._filter_layers)
        self._tree.setHeaderHidden(True)
        self._tree.setObjectName("layerTree")
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.currentItemChanged.connect(self._on_current_item_changed)
        self._tree.itemChanged.connect(self._on_item_changed)
        self._tree.customContextMenuRequested.connect(self._on_context_menu_requested)
        up_button: QToolButton = QToolButton()
        up_button.setText("↑")
        up_button.setToolTip("上移图层")
        down_button: QToolButton = QToolButton()
        down_button.setText("↓")
        down_button.setToolTip("下移图层")
        up_button.clicked.connect(lambda: self._move_current(-1))
        down_button.clicked.connect(lambda: self._move_current(1))
        remove_button: QToolButton = QToolButton()
        remove_button.setText("×")
        remove_button.setToolTip("删除当前图层")
        remove_button.clicked.connect(self._remove_current)
        buttons: QHBoxLayout = QHBoxLayout()
        buttons.setContentsMargins(2, 0, 2, 0)
        buttons.addWidget(up_button)
        buttons.addWidget(down_button)
        buttons.addWidget(remove_button)
        buttons.addStretch()
        layout: QVBoxLayout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 10, 8)
        layout.setSpacing(8)
        layout.addLayout(title_row)
        layout.addWidget(self._search_input)
        layout.addWidget(self._tree, 1)
        layout.addLayout(buttons)

    def _on_current_item_changed(self, current: QTreeWidgetItem | None) -> None:
        """将用户选择的当前树节点转换为活动图层请求。

        参数:
            current: 当前图层树节点；为空表示树中没有当前节点。

        说明:
            程序同步工作区快照期间忽略节点变化，防止刷新再次触发刷新。
        """
        if self._updating or current is None:
            return
        layer_id: str = str(current.data(0, Qt.ItemDataRole.UserRole))
        self.layer_activated.emit(layer_id)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        """将复选框变化转换为图层显隐请求。

        参数:
            item: 显隐状态发生变化的图层树节点。
            column: 发生变化的列编号，当前仅处理名称列零。
        """
        if self._updating or column != 0:
            return
        layer_id: str = str(item.data(0, Qt.ItemDataRole.UserRole))
        visible: bool = item.checkState(0) == Qt.CheckState.Checked
        self.layer_visibility_changed.emit(layer_id, visible)

    def _on_context_menu_requested(self, position) -> None:
        """显示图层属性表和删除等上下文操作。

        参数:
            position: 相对于图层树视口的上下文菜单请求位置。
        """
        item: QTreeWidgetItem | None = self._tree.itemAt(position)
        if item is None:
            return
        menu: QMenu = QMenu(self)
        attribute_action = menu.addAction("打开属性表")
        remove_action = menu.addAction("删除图层")
        selected_action: object | None = menu.exec(self._tree.viewport().mapToGlobal(position))
        layer_id: str = str(item.data(0, Qt.ItemDataRole.UserRole))
        if selected_action is attribute_action:
            self.layer_attribute_requested.emit(layer_id)
        elif selected_action is remove_action:
            self.layer_removed.emit(layer_id)

    def _move_current(self, offset: int) -> None:
        """请求把当前图层移动到相邻的有效位置。

        参数:
            offset: 相对当前位置的移动量，负一向上、正一向下。
        """
        item: QTreeWidgetItem | None = self._tree.currentItem()
        if item is None:
            return
        row: int = self._tree.indexOfTopLevelItem(item)
        target_row: int = row + offset
        if 0 <= target_row < self._tree.topLevelItemCount():
            layer_id: str = str(item.data(0, Qt.ItemDataRole.UserRole))
            target_index: int = self._tree.topLevelItemCount() - 1 - target_row
            self.layer_move_requested.emit(layer_id, target_index)

    def _remove_current(self) -> None:
        """请求删除图层树中当前选中的真实图层。"""
        item: QTreeWidgetItem | None = self._tree.currentItem()
        if item is None:
            return
        layer_id: str = str(item.data(0, Qt.ItemDataRole.UserRole))
        self.layer_removed.emit(layer_id)

    def _filter_layers(self, search_text: str) -> None:
        """按名称筛选已有图层树节点。

        参数:
            search_text: 用户输入的大小写不敏感名称片段。
        """
        normalized_text: str = search_text.strip().casefold()
        row: int
        for row in range(self._tree.topLevelItemCount()):
            item: QTreeWidgetItem | None = self._tree.topLevelItem(row)
            if item is not None:
                item.setHidden(normalized_text not in item.text(0).casefold())
