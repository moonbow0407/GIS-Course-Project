"""要素属性编辑对话框。"""

from collections.abc import Mapping

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.domain.feature import AttributeValue


class EditFeatureDialog(QDialog):
    """编辑要素的属性字段。"""

    def __init__(
        self,
        attributes: Mapping[str, AttributeValue],
        feature_label: str,
        parent: QWidget | None = None,
    ) -> None:
        """使用当前要素属性填充编辑表。

        参数:
            attributes: 要素的现有属性映射（只读）。
            feature_label: 用于标题的要素标识文本。
            parent: 父窗口控件。
        """
        super().__init__(parent)
        self.setWindowTitle(f"修改要素属性 — {feature_label}")
        self.setMinimumWidth(450)

        attr_group: QGroupBox = QGroupBox("属性字段")
        self._table: QTableWidget = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["字段名", "值"])
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )

        # 填充现有属性。
        for name, value in attributes.items():
            self._add_row(name, str(value) if value is not None else "")

        btn_row: QHBoxLayout = QHBoxLayout()
        add_btn: QPushButton = QPushButton("＋ 添加字段")
        add_btn.clicked.connect(lambda: self._add_row("", ""))
        del_btn: QPushButton = QPushButton("－ 删除选中")
        del_btn.clicked.connect(self._remove_selected)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()

        attr_layout: QVBoxLayout = QVBoxLayout(attr_group)
        attr_layout.addWidget(self._table)
        attr_layout.addLayout(btn_row)

        buttons: QDialogButtonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout: QVBoxLayout = QVBoxLayout(self)
        layout.addWidget(attr_group)
        layout.addWidget(buttons)

        if not attributes:
            self._add_row("", "")

    def attributes(self) -> dict[str, AttributeValue]:
        """返回编辑后的属性字典。"""
        result: dict[str, AttributeValue] = {}
        for row in range(self._table.rowCount()):
            name_item: QTableWidgetItem | None = self._table.item(row, 0)
            value_item: QTableWidgetItem | None = self._table.item(row, 1)
            if name_item is None:
                continue
            name: str = name_item.text().strip()
            if not name:
                continue
            value_str: str = value_item.text().strip() if value_item else ""
            # 尝试数值转换。
            try:
                if "." in value_str:
                    result[name] = float(value_str)
                else:
                    result[name] = int(value_str)
            except ValueError:
                result[name] = value_str
        return result

    # ── 内部 ────────────────────────────────────────────────────

    def _add_row(self, name: str, value: str) -> None:
        """添加一行字段。"""
        row: int = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(name))
        self._table.setItem(row, 1, QTableWidgetItem(value))

    def _remove_selected(self) -> None:
        """删除选中行。"""
        selected: set[int] = {
            idx.row()
            for idx in self._table.selectionModel().selectedRows()
        }
        for row in sorted(selected, reverse=True):
            self._table.removeRow(row)
