"""左侧图层管理面板。"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
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


class LayerPanel(QWidget):
    """图层列表组件，只发出信号，不直接操作地图画布。"""

    layer_selected = Signal(str)
    layer_visibility_changed = Signal(str, bool)
    layer_order_changed = Signal(list)
    layer_removed = Signal(str)
    zoom_to_layer_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._updating = False
        self._tree = QTreeWidget()
        self._create_ui()
        self._connect_signals()
        self._populate_mock_layers()

    def layer_names(self) -> list[str]:
        """返回当前图层顺序。"""
        return [self._tree.topLevelItem(i).text(0) for i in range(self._tree.topLevelItemCount())]

    def _create_ui(self) -> None:
        title = QLabel("图层管理")
        title.setObjectName("panelTitle")

        self._tree.setHeaderHidden(True)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.setAlternatingRowColors(False)
        self._tree.setRootIsDecorated(False)
        self._tree.setIndentation(10)

        self._move_up_button = QPushButton("↑")
        self._move_down_button = QPushButton("↓")
        self._move_up_button.setToolTip("上移一层")
        self._move_down_button.setToolTip("下移一层")
        self._move_up_button.setFixedWidth(42)
        self._move_down_button.setFixedWidth(42)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self._move_up_button)
        button_layout.addWidget(self._move_down_button)
        button_layout.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        layout.addWidget(title)
        layout.addWidget(self._tree, 1)
        layout.addLayout(button_layout)

    def _connect_signals(self) -> None:
        self._tree.currentItemChanged.connect(self._on_current_item_changed)
        self._tree.itemChanged.connect(self._on_item_changed)
        self._tree.customContextMenuRequested.connect(self._on_context_menu_requested)
        self._move_up_button.clicked.connect(self._move_selected_up)
        self._move_down_button.clicked.connect(self._move_selected_down)

    def _populate_mock_layers(self) -> None:
        self._updating = True
        mock_layers = [
            ("行政区", QColor("#9fc86f"), "面"),
            ("道路", QColor("#f28c28"), "线"),
            ("河流", QColor("#2c73c9"), "线"),
            ("高程栅格", QColor("#808080"), "栅格"),
            ("POI点", QColor("#6b4cc2"), "点"),
        ]
        for name, color, layer_type in mock_layers:
            item = QTreeWidgetItem([name])
            item.setCheckState(0, Qt.CheckState.Checked)
            item.setIcon(0, self._legend_icon(color, layer_type))
            item.setData(0, Qt.ItemDataRole.UserRole, layer_type)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable)
            self._tree.addTopLevelItem(item)
        self._updating = False
        self._tree.setCurrentItem(self._tree.topLevelItem(1))

    def _legend_icon(self, color: QColor, layer_type: str) -> QIcon:
        pixmap = QPixmap(26, 18)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(color.darker(125))
        painter.setBrush(color.lighter(140) if layer_type in {"面", "栅格"} else color)
        if layer_type == "点":
            painter.drawEllipse(8, 4, 10, 10)
        elif layer_type == "线":
            painter.setPen(color)
            painter.drawLine(4, 10, 10, 8)
            painter.drawLine(10, 8, 16, 10)
            painter.drawLine(16, 10, 22, 8)
        else:
            painter.drawRoundedRect(4, 3, 18, 12, 2, 2)
        painter.end()
        return QIcon(pixmap)

    def _on_current_item_changed(self, current: QTreeWidgetItem | None) -> None:
        if current is not None:
            self.layer_selected.emit(current.text(0))

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or column != 0:
            return
        visible = item.checkState(0) == Qt.CheckState.Checked
        self.layer_visibility_changed.emit(item.text(0), visible)

    def _on_context_menu_requested(self, position) -> None:
        item = self._tree.itemAt(position)
        if item is None:
            return

        menu = QMenu(self)
        zoom_action = menu.addAction("缩放到图层")
        menu.addAction("透明度").setEnabled(False)
        attr_action = menu.addAction("查看属性")
        rename_action = menu.addAction("重命名")
        menu.addSeparator()
        top_action = menu.addAction("移至顶层")
        up_action = menu.addAction("上移一层")
        down_action = menu.addAction("下移一层")
        bottom_action = menu.addAction("移至底层")
        menu.addSeparator()
        remove_action = menu.addAction("删除图层")

        selected_action = menu.exec(self._tree.viewport().mapToGlobal(position))
        if selected_action is None:
            return
        self._handle_context_action(item, selected_action, {
            zoom_action: "zoom",
            attr_action: "attr",
            rename_action: "rename",
            top_action: "top",
            up_action: "up",
            down_action: "down",
            bottom_action: "bottom",
            remove_action: "remove",
        })

    def _handle_context_action(
        self,
        item: QTreeWidgetItem,
        action: QAction,
        action_map: dict[QAction, str],
    ) -> None:
        command = action_map.get(action)
        if command == "zoom":
            self.zoom_to_layer_requested.emit(item.text(0))
        elif command == "attr":
            self.layer_selected.emit(item.text(0))
        elif command == "rename":
            self._tree.editItem(item, 0)
        elif command == "top":
            self._move_item(item, 0)
        elif command == "up":
            self._move_item(item, max(0, self._tree.indexOfTopLevelItem(item) - 1))
        elif command == "down":
            self._move_item(item, min(self._tree.topLevelItemCount() - 1, self._tree.indexOfTopLevelItem(item) + 1))
        elif command == "bottom":
            self._move_item(item, self._tree.topLevelItemCount() - 1)
        elif command == "remove":
            row = self._tree.indexOfTopLevelItem(item)
            removed = self._tree.takeTopLevelItem(row)
            if removed is not None:
                self.layer_removed.emit(removed.text(0))
                self.layer_order_changed.emit(self.layer_names())

    def _move_selected_up(self) -> None:
        item = self._tree.currentItem()
        if item is not None:
            self._move_item(item, max(0, self._tree.indexOfTopLevelItem(item) - 1))

    def _move_selected_down(self) -> None:
        item = self._tree.currentItem()
        if item is not None:
            self._move_item(item, min(self._tree.topLevelItemCount() - 1, self._tree.indexOfTopLevelItem(item) + 1))

    def _move_item(self, item: QTreeWidgetItem, target_row: int) -> None:
        current_row = self._tree.indexOfTopLevelItem(item)
        if current_row < 0 or current_row == target_row:
            return
        taken = self._tree.takeTopLevelItem(current_row)
        self._tree.insertTopLevelItem(target_row, taken)
        self._tree.setCurrentItem(taken)
        self.layer_order_changed.emit(self.layer_names())
