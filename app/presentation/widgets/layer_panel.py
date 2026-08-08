"""真实地图文档对应的图层管理控件。"""

from PySide6.QtCore import QModelIndex, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDropEvent, QMouseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
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
from app.domain.layer_style import LayerStyle
from app.domain.raster_layer import RasterLayer
from app.domain.symbology import GraduatedClass, UniqueValueClass, VectorRendererType
from app.domain.vector_layer import VectorLayer


class _LayerTreeWidget(QTreeWidget):
    """在 InternalMove 拖拽完成后通知面板的图层树控件。

    QTreeWidget 的 InternalMove 不经过 model.moveRows，
    导致 rowsMoved 信号不会发射。本子类通过比对拖拽前后
    的图层 ID 顺序来可靠检测被移动节点，不依赖 currentItem。
    """

    # 拖拽排序完成信号：携带被移动的顶层节点供面板换算索引。
    rows_reordered = Signal(QTreeWidgetItem)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """点击空白区域时清除当前选中节点。"""
        super().mousePressEvent(event)
        if self.itemAt(event.position().toPoint()) is None:
            self.clearSelection()
            self.setCurrentIndex(QModelIndex())

    def dropEvent(self, event: QDropEvent) -> None:
        """记录拖拽前顺序，完成 InternalMove 后比对找出移动节点。"""
        # 拖拽前：记录当前顶层图层 ID 的快照顺序。
        pre_order: list[str] = []
        for index in range(self.topLevelItemCount()):
            item: QTreeWidgetItem | None = self.topLevelItem(index)
            if item is not None:
                pre_order.append(str(item.data(0, Qt.ItemDataRole.UserRole)))
        super().dropEvent(event)
        # 延迟比对：确保 QTreeWidget 内部状态完全落定。
        QTimer.singleShot(0, lambda: self._detect_reorder(pre_order))

    def _detect_reorder(self, pre_order: list[str]) -> None:
        """比对拖拽前后顺序，找出位移最大的节点即为被拖拽节点。

        向下拖拽时，源位置和目标位置之间的节点会向上移位填补空档；
        向上拖拽时中间节点向下移位。无论方向，被拖拽节点的位移绝对值
        始终最大——它跨越了所有中间节点。
        """
        post_order: list[str] = []
        for index in range(self.topLevelItemCount()):
            item: QTreeWidgetItem | None = self.topLevelItem(index)
            if item is not None:
                post_order.append(str(item.data(0, Qt.ItemDataRole.UserRole)))
        if post_order == pre_order:
            return  # 拖到原位，无变化。
        best_item: QTreeWidgetItem | None = None
        best_delta: int = 0
        for i, layer_id in enumerate(post_order):
            try:
                old_pos: int = pre_order.index(layer_id)
            except ValueError:
                continue
            delta: int = abs(i - old_pos)
            if delta > best_delta:
                best_delta = delta
                best_item = self.topLevelItem(i)
        if best_item is not None and best_item.parent() is None:
            self.rows_reordered.emit(best_item)

_CATEGORY_ROLE: int = int(Qt.ItemDataRole.UserRole) + 1


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
    # 图层文件夹请求信号：请求打开指定图层数据文件所在文件夹。
    layer_folder_requested = Signal(str)
    # 图层移动信号：携带图层编号及其在地图文档中的目标位置。
    layer_move_requested = Signal(str, int)
    # 图层定位信号：请求画布缩放至指定图层的完整范围。
    layer_zoom_requested = Signal(str)
    # 符号系统信号：请求打开指定图层的符号编辑面板。
    layer_symbology_requested = Signal(str)
    # 类别显隐信号：携带图层编号、类别索引和目标状态。
    category_visibility_changed = Signal(str, int, bool)
    # 选择清除信号：点击图层树空白区域时发出，请求取消活动图层。
    selection_cleared = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建空的图层管理控件。

        参数:
            parent: 父控件；为空时由工作区分隔器接管所有权。

        状态变化:
            创建搜索框和空图层树，不添加任何演示或测试图层。
        """
        super().__init__(parent)
        # 图层树：按地图显示顺序展示图层名称和显隐复选框。
        self._tree: _LayerTreeWidget = _LayerTreeWidget()
        # 快照更新标记：防止程序刷新控件时反向触发业务信号。
        self._updating: bool = False
        # 图层搜索框：根据名称即时筛选当前真实图层，不创建额外数据。
        self._search_input: QLineEdit = QLineEdit()
        self._create_ui()

    def clear_layer_selection(self) -> None:
        """取消图层树中当前选中节点，触发 selection_cleared 信号。"""
        self._tree.clearSelection()
        self._tree.setCurrentIndex(QModelIndex())

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
            item.setFlags(
                item.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsDragEnabled
                | Qt.ItemFlag.ItemIsDropEnabled
            )
            self._tree.addTopLevelItem(item)
            self._add_legend_items(item, layer_snapshot.layer)
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
        self._tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._tree.setDropIndicatorShown(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.currentItemChanged.connect(self._on_current_item_changed)
        self._tree.itemChanged.connect(self._on_item_changed)
        self._tree.customContextMenuRequested.connect(self._on_context_menu_requested)
        self._tree.rows_reordered.connect(self._on_rows_reordered)
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
        layout.addLayout(buttons)
        layout.addWidget(self._tree, 1)

    def _on_current_item_changed(self, current: QTreeWidgetItem | None) -> None:
        """将用户选择的当前树节点转换为活动图层请求。

        参数:
            current: 当前图层树节点；为空表示树中没有当前节点。

        说明:
            程序同步工作区快照期间忽略节点变化，防止刷新再次触发刷新。
        """
        if self._updating:
            return
        if current is None:
            self.selection_cleared.emit()
            return
        if current.parent() is not None:
            current = current.parent()
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
        parent: QTreeWidgetItem | None = item.parent()
        if parent is not None:
            parent_layer_id = str(parent.data(0, Qt.ItemDataRole.UserRole))
            category_index = int(item.data(0, _CATEGORY_ROLE))
            category_visible = item.checkState(0) == Qt.CheckState.Checked
            self.category_visibility_changed.emit(
                parent_layer_id,
                category_index,
                category_visible,
            )
            return
        layer_id: str = str(item.data(0, Qt.ItemDataRole.UserRole))
        visible: bool = item.checkState(0) == Qt.CheckState.Checked
        self.layer_visibility_changed.emit(layer_id, visible)

    def _on_context_menu_requested(self, position: QPoint) -> None:
        """显示图层属性表和删除等上下文操作。

        参数:
            position: 相对于图层树视口的上下文菜单请求位置。
        """
        item: QTreeWidgetItem | None = self._tree.itemAt(position)
        if item is None:
            return
        # 子节点（如图例类别）不直接持有 layer_id，需向上查找父节点。
        if item.parent() is not None:
            item = item.parent()
        menu: QMenu = QMenu(self)
        zoom_action = menu.addAction("缩放至图层")
        symbology_action = menu.addAction("符号系统")
        attribute_action = menu.addAction("打开属性表")
        open_folder_action = menu.addAction("打开文件夹")
        remove_action = menu.addAction("删除图层")
        selected_action: object | None = self._execute_context_menu(menu, position)
        layer_id: str = str(item.data(0, Qt.ItemDataRole.UserRole))
        if selected_action is zoom_action:
            self.layer_zoom_requested.emit(layer_id)
        elif selected_action is symbology_action:
            self.layer_symbology_requested.emit(layer_id)
        elif selected_action is attribute_action:
            self.layer_attribute_requested.emit(layer_id)
        elif selected_action is open_folder_action:
            self.layer_folder_requested.emit(layer_id)
        elif selected_action is remove_action:
            self.layer_removed.emit(layer_id)

    def _execute_context_menu(self, menu: QMenu, position: QPoint) -> object | None:
        """在图层树请求位置显示上下文菜单并返回所选操作。"""
        return menu.exec(self._tree.viewport().mapToGlobal(position))

    def _on_rows_reordered(self, item: QTreeWidgetItem) -> None:
        """拖拽排序完成后，根据节点面板位置换算地图文档索引。

        参数:
            item: dropEvent 中捕获的被拖拽顶层节点。
        """
        if self._updating:
            return
        target_row: int = self._tree.indexOfTopLevelItem(item)
        if target_row < 0:
            return
        layer_id: str = str(item.data(0, Qt.ItemDataRole.UserRole))
        # 面板从顶到底显示，文档按底到顶保存，因此目标索引需要反向换算。
        target_index: int = self._tree.topLevelItemCount() - 1 - target_row
        self.layer_move_requested.emit(layer_id, target_index)

    def _on_rows_moved(self, *args: object) -> None:
        """↑↓ 按钮或测试代码触发的图层重排回调。"""
        if self._updating:
            return
        item: QTreeWidgetItem | None = self._tree.currentItem()
        if item is None:
            return
        target_row: int = self._tree.indexOfTopLevelItem(item)
        if target_row < 0:
            return
        layer_id: str = str(item.data(0, Qt.ItemDataRole.UserRole))
        # 面板从顶到底显示，文档却从底到顶保存，需要反向换算索引。
        target_index: int = self._tree.topLevelItemCount() - 1 - target_row
        self.layer_move_requested.emit(layer_id, target_index)

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
            # 面板从顶到底显示，文档却从底到顶保存，需要反向换算索引。
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

    def _add_legend_items(
        self,
        parent: QTreeWidgetItem,
        layer: VectorLayer | RasterLayer,
    ) -> None:
        """在图层节点下添加符号类别或栅格色带摘要。"""
        if isinstance(layer, RasterLayer):
            raster_symbology = layer.symbology
            if raster_symbology is None:
                return
            label = (
                "RGB 合成"
                if raster_symbology.renderer_type.value == "rgb"
                else (
                    f"{raster_symbology.color_scheme} · "
                    f"波段 {raster_symbology.stretch_band + 1}"
                )
            )
            child = QTreeWidgetItem([f"▰  {label}"])
            child.setFlags(Qt.ItemFlag.ItemIsEnabled)
            parent.addChild(child)
            return
        vector_symbology = layer.symbology
        if vector_symbology is None:
            return
        if vector_symbology.renderer_type is VectorRendererType.SIMPLE:
            self._add_symbol_child(
                parent,
                0,
                "单一符号",
                vector_symbology.base_symbol,
                True,
                False,
            )
            return
        classes: tuple[UniqueValueClass | GraduatedClass, ...] = (
            tuple(vector_symbology.unique_classes)
            if vector_symbology.unique_classes
            else tuple(vector_symbology.graduated_classes)
        )
        for index, category in enumerate(classes):
            self._add_symbol_child(
                parent,
                index,
                category.label,
                category.symbol,
                category.visible,
                True,
            )
        if (
            vector_symbology.renderer_type is VectorRendererType.UNIQUE
            and vector_symbology.other_symbol
        ):
            self._add_symbol_child(
                parent,
                len(classes),
                "其他值",
                vector_symbology.other_symbol,
                vector_symbology.other_visible,
                True,
            )

    @staticmethod
    def _add_symbol_child(
        parent: QTreeWidgetItem,
        index: int,
        label: str,
        symbol: LayerStyle,
        visible: bool,
        checkable: bool,
    ) -> None:
        """添加带颜色预览的单个图例子项。"""
        color = symbol.stroke_color if symbol.fill_color == "transparent" else symbol.fill_color
        child = QTreeWidgetItem([f"■  {label}"])
        child.setForeground(0, QColor(color))
        child.setData(0, _CATEGORY_ROLE, index)
        flags = Qt.ItemFlag.ItemIsEnabled
        if checkable:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
            child.setCheckState(
                0,
                Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked,
            )
        child.setFlags(flags)
        parent.addChild(child)
