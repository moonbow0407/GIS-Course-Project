"""真实地图文档对应的图层管理控件。"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.application.results import WorkspaceSnapshot


class LayerPanel(QWidget):
    """展示工作区图层，不创建演示数据并通过信号请求业务操作。"""

    layer_activated = Signal(str)
    layer_visibility_changed = Signal(str, bool)
    layer_attribute_requested = Signal(str)
    layer_removed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建空的图层管理控件。"""
        super().__init__(parent)
        self._tree: QTreeWidget = QTreeWidget()
        self._updating: bool = False
        self._create_ui()

    def apply_snapshot(self, snapshot: WorkspaceSnapshot) -> None:
        """按照工作区快照刷新图层名称、顺序、显隐和活动状态。"""
        self._updating = True
        self._tree.clear()
        for layer_snapshot in snapshot.layers:
            item: QTreeWidgetItem = QTreeWidgetItem([layer_snapshot.name])
            item.setData(0, Qt.ItemDataRole.UserRole, layer_snapshot.layer_id)
            item.setCheckState(0, Qt.CheckState.Checked if layer_snapshot.visible else Qt.CheckState.Unchecked)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            self._tree.addTopLevelItem(item)
            if layer_snapshot.layer_id == snapshot.active_layer_id:
                self._tree.setCurrentItem(item)
        self._updating = False

    def _create_ui(self) -> None:
        """创建标题、空图层树和排序按钮。"""
        title: QLabel = QLabel("图层管理")
        title.setObjectName("panelTitle")
        self._tree.setHeaderHidden(True)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.currentItemChanged.connect(self._on_current_item_changed)
        self._tree.itemChanged.connect(self._on_item_changed)
        self._tree.customContextMenuRequested.connect(self._on_context_menu_requested)
        up_button: QPushButton = QPushButton("↑")
        down_button: QPushButton = QPushButton("↓")
        up_button.clicked.connect(lambda: self._move_current(-1))
        down_button.clicked.connect(lambda: self._move_current(1))
        buttons: QHBoxLayout = QHBoxLayout()
        buttons.addWidget(up_button)
        buttons.addWidget(down_button)
        buttons.addStretch()
        layout: QVBoxLayout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(self._tree, 1)
        layout.addLayout(buttons)

    def _on_current_item_changed(self, current: QTreeWidgetItem | None) -> None:
        """将当前树节点转换为活动图层请求。"""
        if current is not None:
            layer_id: str = str(current.data(0, Qt.ItemDataRole.UserRole))
            self.layer_activated.emit(layer_id)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        """将复选框变化转换为图层显隐请求。"""
        if self._updating or column != 0:
            return
        layer_id: str = str(item.data(0, Qt.ItemDataRole.UserRole))
        visible: bool = item.checkState(0) == Qt.CheckState.Checked
        self.layer_visibility_changed.emit(layer_id, visible)

    def _on_context_menu_requested(self, position) -> None:
        """显示图层属性表和删除等上下文操作。"""
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
        """移动当前节点的视觉位置，业务层将在后续迁移阶段接管排序。"""
        item: QTreeWidgetItem | None = self._tree.currentItem()
        if item is None:
            return
        row: int = self._tree.indexOfTopLevelItem(item)
        target_row: int = row + offset
        if 0 <= target_row < self._tree.topLevelItemCount():
            moved_item: QTreeWidgetItem | None = self._tree.takeTopLevelItem(row)
            if moved_item is not None:
                self._tree.insertTopLevelItem(target_row, moved_item)
                self._tree.setCurrentItem(moved_item)
