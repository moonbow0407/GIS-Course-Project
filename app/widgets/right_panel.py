"""右侧属性与分析面板。"""

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


class RightPanel(QWidget):
    """属性展示和空间分析参数面板。"""

    run_buffer_analysis_requested = Signal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tabs = QTabWidget()
        self._tabs.setObjectName("rightPanelTabs")
        self._attribute_table = QTableWidget()
        self._empty_label = QLabel("当前未选择要素")
        self._create_ui()
        self._connect_signals()
        self.clear_attributes()

    def set_layer_names(self, layer_names: list[str]) -> None:
        current = self._input_layer_combo.currentText()
        self._input_layer_combo.clear()
        self._input_layer_combo.addItems(layer_names)
        if current in layer_names:
            self._input_layer_combo.setCurrentText(current)

    def set_attributes(self, attributes: dict[str, Any]) -> None:
        self._empty_label.hide()
        self._attribute_table.show()
        self._attribute_table.setRowCount(len(attributes))
        for row, (field, value) in enumerate(attributes.items()):
            field_item = QTableWidgetItem(str(field))
            value_item = QTableWidgetItem(str(value))
            self._attribute_table.setItem(row, 0, field_item)
            self._attribute_table.setItem(row, 1, value_item)
        self._attribute_table.resizeColumnsToContents()

    def clear_attributes(self) -> None:
        self._attribute_table.setRowCount(0)
        self._attribute_table.hide()
        self._empty_label.show()

    def _create_ui(self) -> None:
        self._create_attribute_tab()
        self._create_analysis_tab()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self._tabs)

    def _create_attribute_tab(self) -> None:
        tab = QWidget()
        title = QLabel("要素属性")
        title.setObjectName("sectionTitle")

        self._attribute_table.setColumnCount(2)
        self._attribute_table.setHorizontalHeaderLabels(["字段", "值"])
        self._attribute_table.verticalHeader().setVisible(False)
        self._attribute_table.horizontalHeader().setStretchLastSection(True)
        self._attribute_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        layout = QVBoxLayout(tab)
        layout.addWidget(title)
        layout.addWidget(self._empty_label)
        layout.addWidget(self._attribute_table)
        self._tabs.addTab(tab, "属性")

    def _create_analysis_tab(self) -> None:
        tab = QWidget()
        group = QGroupBox("缓冲区分析")

        self._input_layer_combo = QComboBox()
        self._input_layer_combo.addItems(["行政区", "道路", "河流", "高程栅格", "POI点"])

        self._distance_spin = QDoubleSpinBox()
        self._distance_spin.setRange(0.01, 1_000_000)
        self._distance_spin.setDecimals(2)
        self._distance_spin.setValue(100)
        self._distance_spin.setSuffix("")

        self._unit_combo = QComboBox()
        self._unit_combo.addItems(["米", "千米"])

        self._output_name_edit = QLineEdit("道路_缓冲区100m")
        self._merge_check = QCheckBox("合并结果")

        form = QFormLayout(group)
        form.addRow("输入图层", self._input_layer_combo)
        form.addRow("缓冲距离", self._distance_spin)
        form.addRow("单位", self._unit_combo)
        form.addRow("输出图层名", self._output_name_edit)
        form.addRow("", self._merge_check)

        self._run_button = QPushButton("运行")
        self._run_button.setObjectName("primaryButton")
        self._reset_button = QPushButton("重置")

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        button_layout.addWidget(self._run_button)
        button_layout.addWidget(self._reset_button)

        layout = QVBoxLayout(tab)
        layout.addWidget(group)
        layout.addLayout(button_layout)
        layout.addStretch()
        self._tabs.addTab(tab, "分析")

    def _connect_signals(self) -> None:
        self._run_button.clicked.connect(self._on_run_buffer_clicked)
        self._reset_button.clicked.connect(self._on_reset_buffer_clicked)

    def _on_run_buffer_clicked(self) -> None:
        output_name = self._output_name_edit.text().strip()
        if not output_name:
            QMessageBox.warning(self, "参数不完整", "输出图层名称不能为空。")
            return
        if self._distance_spin.value() <= 0:
            QMessageBox.warning(self, "参数不合法", "缓冲距离必须大于 0。")
            return

        params = {
            "input_layer": self._input_layer_combo.currentText(),
            "distance": self._distance_spin.value(),
            "unit": self._unit_combo.currentText(),
            "output_layer_name": output_name,
            "merge_result": self._merge_check.isChecked(),
        }
        self.run_buffer_analysis_requested.emit(params)

    def _on_reset_buffer_clicked(self) -> None:
        self._input_layer_combo.setCurrentIndex(0)
        self._distance_spin.setValue(100)
        self._unit_combo.setCurrentText("米")
        self._output_name_edit.setText("道路_缓冲区100m")
        self._merge_check.setChecked(False)
