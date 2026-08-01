"""属性查询对话框 — 按字段条件筛选要素。"""

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from app.application.results import LayerSnapshot
from app.domain.vector_layer import VectorLayer


@dataclass(frozen=True, slots=True)
class AttributeQueryRequest:
    """属性查询参数，由对话框收集后传给应用层。"""

    layer_id: str
    field_name: str
    operator: str
    value: str


# 支持的运算符及其显示名称。
_OPERATORS: dict[str, str] = {
    "=": "=  等于",
    "!=": "≠  不等于",
    ">": ">  大于",
    "<": "<  小于",
    ">=": "≥  大于等于",
    "<=": "≤  小于等于",
    "contains": "包含",
    "is_null": "为空",
    "not_null": "不为空",
}


class AttributeQueryDialog(QDialog):
    """收集属性查询条件：图层、字段、运算符和值。"""

    def __init__(
        self,
        layers: tuple[LayerSnapshot, ...],
        parent: QWidget | None = None,
    ) -> None:
        """使用当前工作区矢量图层快照创建查询参数对话框。

        参数:
            layers: 可供查询的矢量图层快照元组。
            parent: 父窗口控件。
        """
        super().__init__(parent)
        self.setWindowTitle("属性查询")
        self.setMinimumWidth(420)
        self._layers: tuple[LayerSnapshot, ...] = layers

        # ── 图层选择 ──
        self._layer_combo: QComboBox = QComboBox()
        for layer in layers:
            self._layer_combo.addItem(
                f"{layer.name}  [{layer.feature_count} 个要素]",
                layer.layer_id,
            )
        self._layer_combo.currentIndexChanged.connect(self._on_layer_changed)

        # ── 字段选择 ──
        self._field_combo: QComboBox = QComboBox()

        # ── 运算符 ──
        self._operator_combo: QComboBox = QComboBox()
        for op_id, op_label in _OPERATORS.items():
            self._operator_combo.addItem(op_label, op_id)
        self._operator_combo.currentIndexChanged.connect(self._on_operator_changed)

        # ── 值输入 ──
        self._value_edit: QLineEdit = QLineEdit()
        self._value_edit.setPlaceholderText("输入查询值…")

        # ── 表达式预览 ──
        self._preview_label: QLabel = QLabel()
        self._preview_label.setObjectName("queryPreview")
        self._preview_label.setWordWrap(True)

        # ── 布局 ──
        form: QFormLayout = QFormLayout()
        form.addRow("目标图层", self._layer_combo)
        form.addRow("字段", self._field_combo)
        form.addRow("运算符", self._operator_combo)
        form.addRow("值", self._value_edit)

        buttons: QDialogButtonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)

        layout: QVBoxLayout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addSpacing(8)
        layout.addWidget(self._preview_label)
        layout.addSpacing(8)
        layout.addWidget(buttons)

        # 初始化：填充第一个图层的字段。
        self._on_layer_changed(0)
        self._update_preview()

        # 值输入变化时更新预览。
        self._value_edit.textChanged.connect(self._update_preview)
        self._field_combo.currentIndexChanged.connect(self._update_preview)
        self._operator_combo.currentIndexChanged.connect(self._update_preview)

    def request(self) -> AttributeQueryRequest:
        """返回用户确认的查询参数。

        仅在对话框以 Accepted 结果关闭后调用。
        """
        return AttributeQueryRequest(
            layer_id=self._layer_combo.currentData(),
            field_name=self._field_combo.currentData() or "",
            operator=self._operator_combo.currentData(),
            value=self._value_edit.text().strip(),
        )

    # ── 内部 ────────────────────────────────────────────────────

    def _on_layer_changed(self, _index: int) -> None:
        """图层切换时重新填充当前图层的字段列表。"""
        self._field_combo.clear()
        layer_id: str | None = self._layer_combo.currentData()
        if layer_id is None:
            return
        for layer in self._layers:
            if layer.layer_id != layer_id:
                continue
            if not isinstance(layer.layer, VectorLayer):
                continue
            fields: list[str] = []
            for feature in layer.layer.features:
                for field_name in feature.attributes:
                    if field_name not in fields:
                        fields.append(field_name)
            for field_name in fields:
                self._field_combo.addItem(field_name, field_name)
            break
        self._update_preview()

    def _on_operator_changed(self, _index: int) -> None:
        """运算符切换时根据是否需要值来启用/禁用值输入框。"""
        op_id: str | None = self._operator_combo.currentData()
        needs_value: bool = op_id not in ("is_null", "not_null")
        self._value_edit.setEnabled(needs_value)
        self._update_preview()

    def _update_preview(self) -> None:
        """更新表达式预览标签。"""
        field: str | None = self._field_combo.currentData()
        op_id: str | None = self._operator_combo.currentData()
        if field is None or op_id is None:
            self._preview_label.setText("")
            return
        if op_id in ("is_null", "not_null"):
            expr: str = f'"{field}" {_OPERATORS[op_id]}'
        else:
            value: str = self._value_edit.text().strip()
            quoted: str = f'"{value}"' if value else "…"
            expr = f'"{field}" {_OPERATORS[op_id]} {quoted}'
        self._preview_label.setText(f"表达式：{expr}")

    def _validate_and_accept(self) -> None:
        """校验输入后接受对话框。"""
        if self._field_combo.currentData() is None:
            QMessageBox.warning(self, "属性查询", "请选择一个字段。")
            return
        op_id: str | None = self._operator_combo.currentData()
        if op_id is None:
            return
        if op_id not in ("is_null", "not_null"):
            if not self._value_edit.text().strip():
                QMessageBox.warning(self, "属性查询", "请输入查询值。")
                return
        self.accept()
