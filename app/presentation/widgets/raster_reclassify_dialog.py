"""栅格重分类对话框：配置规则、自动分级和未匹配策略。"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.application.errors import ApplicationError
from app.application.raster_analysis import (
    RasterReclassifyRequest,
    ReclassRule,
    build_equal_interval_rules,
    build_quantile_rules,
    build_unique_value_rules,
)
from app.application.raster_analysis_service import RasterAnalysisService
from app.application.results import LayerSnapshot
from app.domain.raster_layer import RasterLayer
from app.presentation.widgets.raster_output_fields import RasterOutputNameBinder

_DTYPES = ("int16", "int32", "uint8", "uint16", "float32")
_POLICIES = {"保留原值": "keep", "设为 NoData": "nodata", "使用常量": "constant"}
_RULE_MODES = {"按范围分类": "range", "按唯一值分类": "unique"}
_CLASS_METHODS = ("等距间隔", "分位数")


class RasterReclassifyDialog(QDialog):
    """收集重分类规则、未匹配策略和输出参数。"""

    def __init__(
        self,
        layers: tuple[LayerSnapshot, ...],
        parent: QWidget | None = None,
    ) -> None:
        """初始化重分类对话框。

        异常:
            ValueError: 当前工作区没有栅格图层。
        """
        super().__init__(parent)
        self.setWindowTitle("栅格重分类")
        self.setMinimumWidth(600)

        self._raster_layers: tuple[LayerSnapshot, ...] = tuple(
            layer for layer in layers if isinstance(layer.layer, RasterLayer)
        )
        if not self._raster_layers:
            raise ValueError("当前工作区没有可用于重分类的栅格图层。")

        self._build_ui()
        self._populate_layer_combo()
        self._on_rule_mode_changed(self._mode_combo.currentText())

    def _build_ui(self) -> None:
        """构建对话框布局。"""
        layout: QVBoxLayout = QVBoxLayout(self)

        # ── 输入 ──
        input_group: QGroupBox = QGroupBox("输入")
        input_form: QFormLayout = QFormLayout(input_group)
        self._layer_combo: QComboBox = QComboBox()
        self._layer_combo.currentIndexChanged.connect(self._on_layer_changed)
        input_form.addRow("栅格图层:", self._layer_combo)
        self._band_spin: QSpinBox = QSpinBox()
        self._band_spin.setMinimum(1)
        input_form.addRow("波段:", self._band_spin)
        layout.addWidget(input_group)

        # ── 规则表格 ──
        rule_group: QGroupBox = QGroupBox("重分类规则")
        rule_layout: QVBoxLayout = QVBoxLayout(rule_group)
        self._table: QTableWidget = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["最小值", "最大值", "输出值", "含下限", "含上限"]
        )
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        header = self._table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setMinimumHeight(160)
        rule_layout.addWidget(self._table)

        auto_row: QHBoxLayout = QHBoxLayout()
        auto_row.addWidget(QLabel("自动生成:"))
        self._mode_combo: QComboBox = QComboBox()
        self._mode_combo.addItems(list(_RULE_MODES.keys()))
        self._mode_combo.currentTextChanged.connect(self._on_rule_mode_changed)
        auto_row.addWidget(self._mode_combo)
        auto_row.addWidget(QLabel("分类数:"))
        self._class_count_spin: QSpinBox = QSpinBox()
        self._class_count_spin.setRange(2, 20)
        self._class_count_spin.setValue(5)
        auto_row.addWidget(self._class_count_spin)
        auto_row.addWidget(QLabel("方法:"))
        self._class_method_combo: QComboBox = QComboBox()
        self._class_method_combo.addItems(_CLASS_METHODS)
        auto_row.addWidget(self._class_method_combo)
        generate_btn = QPushButton("生成规则")
        generate_btn.clicked.connect(self._generate_rules)
        auto_row.addWidget(generate_btn)
        auto_row.addStretch()
        rule_layout.addLayout(auto_row)

        btn_row: QHBoxLayout = QHBoxLayout()
        add_btn = QPushButton("添加规则")
        add_btn.clicked.connect(self._add_rule_row)
        del_btn = QPushButton("删除选中规则")
        del_btn.clicked.connect(self._delete_rule_row)
        clear_btn = QPushButton("清空规则")
        clear_btn.clicked.connect(self._clear_rules)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        rule_layout.addLayout(btn_row)
        layout.addWidget(rule_group)

        # ── 未匹配策略 ──
        policy_group: QGroupBox = QGroupBox("未匹配像元")
        policy_form: QFormLayout = QFormLayout(policy_group)
        self._policy_combo: QComboBox = QComboBox()
        self._policy_combo.addItems(list(_POLICIES.keys()))
        self._policy_combo.currentTextChanged.connect(self._on_policy_changed)
        policy_form.addRow("策略:", self._policy_combo)
        self._constant_edit: QLineEdit = QLineEdit()
        self._constant_edit.setEnabled(False)
        policy_form.addRow("常量值:", self._constant_edit)
        layout.addWidget(policy_group)

        # ── 输出 ──
        out_group: QGroupBox = QGroupBox("输出")
        out_form: QFormLayout = QFormLayout(out_group)
        self._dtype_combo: QComboBox = QComboBox()
        self._dtype_combo.addItems(_DTYPES)
        out_form.addRow("输出类型:", self._dtype_combo)
        self._nodata_edit: QLineEdit = QLineEdit("-9999")
        out_form.addRow("NoData:", self._nodata_edit)
        self._name_edit: QLineEdit = QLineEdit("重分类结果")
        out_form.addRow("图层名称:", self._name_edit)
        path_row: QHBoxLayout = QHBoxLayout()
        self._path_edit: QLineEdit = QLineEdit(
            str(Path.cwd() / "reclass_result.tif")
        )
        self._path_edit.setReadOnly(True)
        path_row.addWidget(self._path_edit)
        browse_btn = QPushButton("浏览…")
        browse_btn.clicked.connect(self._browse_output_path)
        path_row.addWidget(browse_btn)
        out_form.addRow("输出路径:", path_row)
        self._output_fields = RasterOutputNameBinder(
            self._name_edit,
            self._path_edit,
            self._path_edit.text(),
        )
        layout.addWidget(out_group)

        self._rule_hint: QLabel = QLabel(
            "区间默认为半开 [下限, 上限)；"
            "最小值/最大值留空表示该侧无界；输出值使用整数类别码。"
        )
        self._rule_hint.setWordWrap(True)
        self._rule_hint.setStyleSheet("color: #6b7280; font-size: 11px;")
        layout.addWidget(self._rule_hint)

        buttons: QDialogButtonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_request)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _populate_layer_combo(self) -> None:
        """用栅格图层填充下拉框。"""
        self._layer_combo.clear()
        for layer in self._raster_layers:
            if isinstance(layer.layer, RasterLayer):
                label = f"{layer.name}  [{layer.layer.band_count} 波段]"
                self._layer_combo.addItem(label, layer.layer_id)

    def _on_layer_changed(self) -> None:
        """切换图层时更新波段上限。"""
        layer_id = self._layer_combo.currentData()
        if layer_id is None:
            return
        for layer in self._raster_layers:
            if layer.layer_id == layer_id and isinstance(layer.layer, RasterLayer):
                self._band_spin.setMaximum(layer.layer.band_count)
                break

    def _on_rule_mode_changed(self, mode_text: str) -> None:
        """按自动生成模式启用或禁用分类数。"""
        is_range = _RULE_MODES.get(mode_text) == "range"
        self._class_count_spin.setEnabled(is_range)
        self._class_method_combo.setEnabled(is_range)

    def _selected_raster_layer(self) -> RasterLayer:
        """返回当前选择的栅格图层。"""
        layer_id = self._layer_combo.currentData()
        for snapshot in self._raster_layers:
            if snapshot.layer_id == layer_id and isinstance(snapshot.layer, RasterLayer):
                return snapshot.layer
        raise ValueError("当前没有选中的栅格图层。")

    def _generate_rules(self) -> None:
        """按唯一值、等距间隔或分位数自动填充规则表。"""
        try:
            layer = self._selected_raster_layer()
            data, valid_mask, sampled = RasterAnalysisService().sample_band_values(
                layer, self._band_spin.value()
            )
            if _RULE_MODES.get(self._mode_combo.currentText()) == "unique":
                rules = build_unique_value_rules(data, valid_mask)
            elif self._class_method_combo.currentText() == "分位数":
                rules = build_quantile_rules(
                    data, valid_mask, self._class_count_spin.value()
                )
            else:
                rules = build_equal_interval_rules(
                    data, valid_mask, self._class_count_spin.value()
                )
        except (ApplicationError, ValueError) as error:
            QMessageBox.warning(self, "自动生成规则失败", str(error))
            return

        self._replace_rule_rows(rules)
        source_hint = "（基于降采样样本）" if sampled else ""
        self._table.setFocus(Qt.FocusReason.OtherFocusReason)
        self._table.selectRow(0)
        self.setWindowTitle(f"栅格重分类 · 已生成 {len(rules)} 条规则")
        self._rule_hint.setText(
            f"已生成 {len(rules)} 条规则{source_hint}；可直接编辑输出值和边界。"
        )

    def _replace_rule_rows(self, rules: tuple[ReclassRule, ...]) -> None:
        """用自动生成的规则替换当前表格内容。"""
        self._table.setRowCount(0)
        for rule in rules:
            row = self._table.rowCount()
            self._table.insertRow(row)
            values = (
                "" if rule.lower is None else f"{rule.lower:g}",
                "" if rule.upper is None else f"{rule.upper:g}",
                f"{rule.output_value:g}",
                "是" if rule.include_lower else "否",
                "是" if rule.include_upper else "否",
            )
            for column, value in enumerate(values):
                self._table.setItem(row, column, QTableWidgetItem(value))

    def _clear_rules(self) -> None:
        """清空规则表，便于重新录入或切换自动生成方式。"""
        self._table.setRowCount(0)
        self.setWindowTitle("栅格重分类")
        self._rule_hint.setText(
            "区间默认为半开 [下限, 上限)；最小值/最大值留空表示该侧无界；"
            "输出值使用整数类别码。"
        )

    def _add_rule_row(self) -> None:
        """添加一条空规则行。"""
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(""))
        self._table.setItem(row, 1, QTableWidgetItem(""))
        self._table.setItem(row, 2, QTableWidgetItem(""))
        self._table.setItem(row, 3, QTableWidgetItem("是"))
        self._table.setItem(row, 4, QTableWidgetItem("否"))
        self._table.setCurrentCell(row, 0)

    def _delete_rule_row(self) -> None:
        """删除所有选中的规则行。"""
        rows = sorted(
            {index.row() for index in self._table.selectionModel().selectedRows()},
            reverse=True,
        )
        if not rows and self._table.currentRow() >= 0:
            rows = [self._table.currentRow()]
        for row in rows:
            self._table.removeRow(row)

    def _on_policy_changed(self) -> None:
        """切换策略时启用/禁用常量输入。"""
        is_constant = _POLICIES.get(self._policy_combo.currentText()) == "constant"
        self._constant_edit.setEnabled(is_constant)

    def _browse_output_path(self) -> None:
        """打开保存文件对话框。"""
        from PySide6.QtWidgets import QFileDialog

        path_str, _ = QFileDialog.getSaveFileName(
            self, "保存重分类结果",
            str(Path.cwd() / "reclass_result.tif"),
            "GeoTIFF (*.tif *.tiff)",
        )
        if path_str:
            self._output_fields.set_path(path_str)

    def _accept_request(self) -> None:
        """校验参数后确认。"""
        if self._table.rowCount() == 0:
            QMessageBox.warning(self, "缺少规则", "请至少添加一条重分类规则。")
            return
        name_error = self._output_fields.validation_error()
        if name_error is not None:
            QMessageBox.warning(self, "输出文件名无效", name_error)
            return
        try:
            self._build_rules()
            self.request()
        except ValueError as exc:
            QMessageBox.warning(self, "规则无效", str(exc))
            return
        self.accept()

    def _build_rules(self) -> tuple[ReclassRule, ...]:
        """从表格构建规则元组。"""
        rules: list[ReclassRule] = []
        for row in range(self._table.rowCount()):
            lower_text = self._table.item(row, 0)
            upper_text = self._table.item(row, 1)
            output_text = self._table.item(row, 2)
            inc_lower_text = self._table.item(row, 3)
            inc_upper_text = self._table.item(row, 4)
            if not output_text or not output_text.text().strip():
                continue
            lower = float(lower_text.text()) if lower_text and lower_text.text().strip() else None
            upper = float(upper_text.text()) if upper_text and upper_text.text().strip() else None
            output_val = float(output_text.text())
            if not output_val.is_integer():
                raise ValueError("输出值必须是整数，请按类别编码填写。")
            inc_lower = inc_lower_text.text() == "是" if inc_lower_text else True
            inc_upper = inc_upper_text.text() == "是" if inc_upper_text else False
            rules.append(ReclassRule(
                lower=lower, upper=upper, output_value=output_val,
                include_lower=inc_lower, include_upper=inc_upper,
            ))
        if not rules:
            raise ValueError("至少需要一条有效的重分类规则。")
        return tuple(rules)

    def request(self) -> RasterReclassifyRequest:
        """构建不可变的重分类请求。仅在对话框 Accepted 后调用。"""
        policy = _POLICIES.get(self._policy_combo.currentText(), "keep")
        constant: float | None = None
        if policy == "constant":
            constant = float(self._constant_edit.text())
        output_path = self._output_fields.output_path
        nodata_text = self._nodata_edit.text().strip()
        nodata = float(nodata_text) if nodata_text else None
        return RasterReclassifyRequest(
            input_layer_id=str(self._layer_combo.currentData()),
            band_index=self._band_spin.value(),
            rules=self._build_rules(),
            unmatched_policy=policy,  # type: ignore[arg-type]
            unmatched_constant=constant,
            output_dtype=self._dtype_combo.currentText(),
            output_nodata=nodata,
            output_layer_name=self._output_fields.output_name,
            output_path=output_path,
        )
