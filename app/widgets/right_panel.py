"""右侧属性与分析面板。"""

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
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
        self.setObjectName("inspectorPanel")
        self._tabs = QTabWidget()
        self._tabs.setObjectName("rightPanelTabs")
        self._attribute_table = QTableWidget()
        self._empty_state = self._create_empty_state()
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
        self._empty_state.hide()
        self._attribute_summary.setText(f"已选择 1 个要素 · {len(attributes)} 个字段")
        self._attribute_summary.show()
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
        self._attribute_summary.hide()
        self._empty_state.show()

    def _create_ui(self) -> None:
        self._create_attribute_tab()
        self._create_analysis_tab()

        header = QFrame()
        header.setObjectName("inspectorHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 0, 14, 0)
        header_layout.addWidget(QLabel("检视器"))
        header_layout.addStretch()
        context = QLabel("当前图层 · 道路")
        context.setObjectName("inspectorContext")
        header_layout.addWidget(context)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(self._tabs, 1)

    def _create_attribute_tab(self) -> None:
        tab = QWidget()
        title = QLabel("要素属性")
        title.setObjectName("sectionTitle")
        self._attribute_summary = QLabel()
        self._attribute_summary.setObjectName("attributeSummary")

        self._attribute_table.setColumnCount(2)
        self._attribute_table.setHorizontalHeaderLabels(["字段", "值"])
        self._attribute_table.verticalHeader().setVisible(False)
        self._attribute_table.horizontalHeader().setStretchLastSection(True)
        self._attribute_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        layout = QVBoxLayout(tab)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(self._attribute_summary)
        layout.addWidget(self._empty_state)
        layout.addWidget(self._attribute_table, 1)
        layout.addStretch()
        self._tabs.addTab(tab, "属性")

    def _create_empty_state(self) -> QFrame:
        state = QFrame()
        state.setObjectName("attributeEmptyState")
        icon = QLabel("◎")
        icon.setObjectName("emptyStateIcon")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("尚未选择要素")
        title.setObjectName("emptyStateTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description = QLabel("在地图上使用点选或框选工具\n即可在此查看属性信息")
        description.setObjectName("emptyStateDescription")
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(state)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(5)
        layout.addWidget(icon)
        layout.addWidget(title)
        layout.addWidget(description)
        return state

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
        layout.setContentsMargins(14, 14, 14, 14)
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
