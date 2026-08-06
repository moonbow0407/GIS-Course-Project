"""选择数字化目标图层的对话框。"""

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True, slots=True)
class TargetLayerOption:
    """可供数字化追加的图层选项。"""

    # 图层编号：用于锁定追加目标。
    layer_id: str

    # 图层显示名称。
    name: str

    # 选项描述：几何类型、要素数量和源文件格式。
    description: str


class TargetLayerDialog(QDialog):
    """列出可承载数字化要素的矢量图层，供用户选择追加目标。"""

    def __init__(
        self,
        options: tuple[TargetLayerOption, ...],
        geometry_label: str,
        default_layer_id: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """创建目标图层选择对话框。

        参数:
            options: 可选的图层列表，进入对话框前已按几何类型和
                可写回格式过滤。
            geometry_label: 数字化几何的中文名称（点/线/面）。
            default_layer_id: 默认选中的图层编号；为空时选中第一项。
            parent: 父窗口控件。
        """
        super().__init__(parent)
        self.setWindowTitle(f"新增{geometry_label}要素")
        self.setMinimumWidth(420)

        self._list: QListWidget = QListWidget()
        for option in options:
            item: QListWidgetItem = QListWidgetItem(
                f"{option.name}（{option.description}）"
            )
            item.setData(Qt.ItemDataRole.UserRole, option.layer_id)
            self._list.addItem(item)
            if option.layer_id == default_layer_id:
                self._list.setCurrentItem(item)
        if self._list.currentItem() is None and self._list.count() > 0:
            self._list.setCurrentRow(0)

        buttons: QDialogButtonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout: QVBoxLayout = QVBoxLayout(self)
        layout.addWidget(QLabel("选择要添加要素的目标图层："))
        layout.addWidget(self._list, 1)
        layout.addWidget(buttons)

    def selected_layer_id(self) -> str | None:
        """返回用户选中的图层编号；取消或未选择时返回空值。"""
        item: QListWidgetItem | None = self._list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)
