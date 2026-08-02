"""新建要素对话框 — 填写属性并设置输出图层。"""

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


@dataclass
class NewFeatureRequest:
    """新建要素请求参数。"""

    layer_name: str
    output_path: Path
    attributes: dict[str, object] = field(default_factory=dict)


class NewFeatureDialog(QDialog):
    """收集新建要素的属性和图层输出信息。"""

    def __init__(
        self,
        geometry_type: str,
        parent: QWidget | None = None,
    ) -> None:
        """创建要素属性对话框。

        参数:
            geometry_type: 几何类型提示（"点"/"线"/"面"）。
            parent: 父窗口控件。
        """
        super().__init__(parent)
        self.setWindowTitle(f"新建{geometry_type}要素")
        self.setMinimumWidth(480)

        # ── 属性表 ──
        attr_group: QGroupBox = QGroupBox("要素属性")
        self._attr_table: QTableWidget = QTableWidget(0, 2)
        self._attr_table.setHorizontalHeaderLabels(["字段名", "值"])
        self._attr_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._attr_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        attr_buttons: QHBoxLayout = QHBoxLayout()
        add_btn: QPushButton = QPushButton("＋ 添加字段")
        add_btn.clicked.connect(self._add_field_row)
        del_btn: QPushButton = QPushButton("－ 删除选中")
        del_btn.clicked.connect(self._remove_selected_rows)
        attr_buttons.addWidget(add_btn)
        attr_buttons.addWidget(del_btn)
        attr_buttons.addStretch()
        attr_layout: QVBoxLayout = QVBoxLayout(attr_group)
        attr_layout.addWidget(self._attr_table)
        attr_layout.addLayout(attr_buttons)

        # ── 图层设置 ──
        layer_group: QGroupBox = QGroupBox("输出图层")
        self._layer_name: QLineEdit = QLineEdit(f"新建{geometry_type}图层")
        self._format_combo: QComboBox = QComboBox()
        self._format_combo.addItem("GeoJSON (*.geojson)", ".geojson")
        self._format_combo.addItem("Shapefile (*.shp)", ".shp")
        self._format_combo.addItem("GeoPackage (*.gpkg)", ".gpkg")
        self._format_combo.currentIndexChanged.connect(self._on_format_changed)
        self._path_edit: QLineEdit = QLineEdit()
        self._path_edit.setReadOnly(True)
        browse_btn: QPushButton = QPushButton("浏览…")
        browse_btn.clicked.connect(self._browse_path)
        path_row: QHBoxLayout = QHBoxLayout()
        path_row.addWidget(self._path_edit, 1)
        path_row.addWidget(browse_btn)
        layer_form: QFormLayout = QFormLayout(layer_group)
        layer_form.addRow("图层名称", self._layer_name)
        layer_form.addRow("输出格式", self._format_combo)
        layer_form.addRow("输出路径", path_row)

        # ── 确定/取消 ──
        buttons: QDialogButtonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)

        layout: QVBoxLayout = QVBoxLayout(self)
        layout.addWidget(attr_group)
        layout.addWidget(layer_group)
        layout.addWidget(buttons)

        # 默认添加一行。
        self._add_field_row()

    def request(self) -> NewFeatureRequest:
        """返回用户确认的要素创建参数。"""
        attributes: dict[str, object] = {}
        for row in range(self._attr_table.rowCount()):
            name_item: QTableWidgetItem | None = self._attr_table.item(row, 0)
            value_item: QTableWidgetItem | None = self._attr_table.item(row, 1)
            if name_item is None:
                continue
            name: str = name_item.text().strip()
            if not name:
                continue
            value: str = value_item.text().strip() if value_item else ""
            # 尝试将数值字符串转为数字。
            try:
                if "." in value:
                    attributes[name] = float(value)
                else:
                    attributes[name] = int(value)
            except ValueError:
                attributes[name] = value
        return NewFeatureRequest(
            layer_name=self._layer_name.text().strip(),
            output_path=Path(self._path_edit.text()),
            attributes=attributes,
        )

    # ── 内部 ────────────────────────────────────────────────────

    def _add_field_row(self) -> None:
        """添加一行空字段。"""
        row: int = self._attr_table.rowCount()
        self._attr_table.insertRow(row)
        self._attr_table.setItem(row, 0, QTableWidgetItem(""))
        self._attr_table.setItem(row, 1, QTableWidgetItem(""))

    def _remove_selected_rows(self) -> None:
        """删除选中行。"""
        selected: set[int] = {
            index.row() for index in self._attr_table.selectionModel().selectedRows()
        }
        for row in sorted(selected, reverse=True):
            self._attr_table.removeRow(row)

    def _on_format_changed(self, _index: int) -> None:
        """格式变化时更新路径后缀。"""
        suffix: str | None = self._format_combo.currentData()
        if suffix is None:
            return
        current: str = self._path_edit.text()
        if current and "." in current:
            base: str = current.rsplit(".", 1)[0]
            self._path_edit.setText(base + suffix)

    def _browse_path(self) -> None:
        """打开保存文件对话框。"""
        suffix: str | None = self._format_combo.currentData()
        if suffix is None:
            return
        filter_str: str = f"GIS 文件 (*{suffix});;所有文件 (*)"
        path, _ = QFileDialog.getSaveFileName(
            self, "保存新图层", str(Path.home()), filter_str
        )
        if path:
            self._path_edit.setText(path)

    def _validate_and_accept(self) -> None:
        """校验输入后接受。"""
        if not self._layer_name.text().strip():
            QMessageBox.warning(self, "新建要素", "请输入图层名称。")
            return
        if not self._path_edit.text().strip():
            QMessageBox.warning(self, "新建要素", "请选择输出路径。")
            return
        output_path: Path = Path(self._path_edit.text())
        if output_path.exists():
            answer = QMessageBox.question(
                self,
                "文件已存在",
                f"文件 {output_path.name} 已存在，是否覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.accept()
