"""布局图例设置对话框 —— 改标题、字号，并用名称字段覆盖图例文字。"""

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.application.legend_model import (
    LegendLayerBlock,
    LegendPatch,
    build_legend_model,
    suggested_legend_label_field,
    unique_patch_labels_from_field,
    vector_attribute_names,
)
from app.application.results import WorkspaceSnapshot
from app.domain.layout import LegendElement
from app.domain.vector_layer import VectorLayer


class LegendSettingsDialog(QDialog):
    """编辑布局图例的标题、排版和条目文字。

    颜色仍来自图层符号系统；这里只改图例上显示的标签，
    便于把面积等分类值换成省名。
    """

    def __init__(
        self,
        element: LegendElement,
        snapshot: WorkspaceSnapshot | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("图例设置")
        self.resize(460, 520)
        self._element = element
        self._snapshot = snapshot
        self._blocks: tuple[LegendLayerBlock, ...] = (
            build_legend_model(snapshot) if snapshot is not None else ()
        )
        self._label_edits: dict[str, QLineEdit] = {}
        self._field_combos: dict[str, QComboBox] = {}
        self._result: dict[str, object] = {}
        self._create_ui()

    def _create_ui(self) -> None:
        """构建标题、条目列表和按钮。"""
        root = QVBoxLayout(self)
        hint = QLabel(
            "图例颜色来自符号系统。若分类字段是面积等数值，"
            "可在下方选择省名/名称字段填入图例文字，不必改符号化。"
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        form = QFormLayout()
        self._title_edit = QLineEdit(self._element.title)
        form.addRow("标题:", self._title_edit)
        self._title_size = QDoubleSpinBox()
        self._title_size.setRange(0.5, 20.0)
        self._title_size.setDecimals(1)
        self._title_size.setValue(self._element.title_font_size_mm)
        form.addRow("标题字号 (mm):", self._title_size)
        self._item_size = QDoubleSpinBox()
        self._item_size.setRange(0.5, 20.0)
        self._item_size.setDecimals(1)
        self._item_size.setValue(self._element.item_font_size_mm)
        form.addRow("条目字号 (mm):", self._item_size)
        self._columns = QSpinBox()
        self._columns.setRange(1, 10)
        self._columns.setValue(self._element.column_count)
        form.addRow("列数:", self._columns)
        self._show_title = QCheckBox("显示标题")
        self._show_title.setChecked(self._element.show_title)
        form.addRow(self._show_title)
        self._show_headings = QCheckBox("显示图层名")
        self._show_headings.setChecked(self._element.show_layer_headings)
        form.addRow(self._show_headings)
        root.addLayout(form)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        self._body_layout = QVBoxLayout(body)
        if not self._blocks:
            self._body_layout.addWidget(QLabel("当前没有可图例化的可见图层。"))
        for block in self._blocks:
            self._add_block(block)
        self._body_layout.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _add_block(self, block: LegendLayerBlock) -> None:
        """为一个图层添加标签编辑区和可选的字段填充。"""
        header = QLabel(block.layer_name)
        header.setStyleSheet("font-weight: 600; margin-top: 8px;")
        self._body_layout.addWidget(header)
        layer = self._layer_by_id(block.layer_id)
        if isinstance(layer, VectorLayer) and any(
            patch.patch_id.startswith(f"{block.layer_id}|unique|")
            for patch in block.patches
        ):
            row = QHBoxLayout()
            row.addWidget(QLabel("标签字段:"))
            combo = QComboBox()
            combo.addItem("（保持当前标签）", "")
            for name in vector_attribute_names(layer):
                combo.addItem(name, name)
            suggested = suggested_legend_label_field(layer)
            if suggested:
                index = combo.findData(suggested)
                if index >= 0:
                    combo.setCurrentIndex(index)
            self._field_combos[block.layer_id] = combo
            apply_btn = QPushButton("按字段填写")
            apply_btn.clicked.connect(
                lambda _=False, lid=block.layer_id: self._fill_from_field(lid)
            )
            row.addWidget(combo, 1)
            row.addWidget(apply_btn)
            self._body_layout.addLayout(row)
        for patch in block.patches:
            if not patch.patch_id:
                continue
            self._body_layout.addLayout(self._patch_row(patch))

    def _patch_row(self, patch: LegendPatch) -> QHBoxLayout:
        """一行：色块 + 可编辑标签。"""
        row = QHBoxLayout()
        swatch = QLabel()
        swatch.setFixedSize(22, 14)
        color = _patch_color(patch)
        swatch.setStyleSheet(
            f"background: {color}; border: 1px solid #9ca3af; border-radius: 2px;"
        )
        edit = QLineEdit(self._element.label_overrides.get(patch.patch_id, patch.label))
        self._label_edits[patch.patch_id] = edit
        row.addWidget(swatch)
        row.addWidget(edit, 1)
        return row

    def _fill_from_field(self, layer_id: str) -> None:
        """用所选属性字段覆盖该图层唯一值图例文字。"""
        combo = self._field_combos.get(layer_id)
        layer = self._layer_by_id(layer_id)
        if combo is None or not isinstance(layer, VectorLayer):
            return
        field_name = str(combo.currentData() or "")
        if not field_name:
            return
        for patch_id, label in unique_patch_labels_from_field(layer, field_name).items():
            edit = self._label_edits.get(patch_id)
            if edit is not None:
                edit.setText(label)

    def _layer_by_id(self, layer_id: str) -> object:
        """按编号取快照中的领域图层。"""
        if self._snapshot is None:
            return None
        for layer_snap in self._snapshot.layers:
            if layer_snap.layer_id == layer_id:
                return layer_snap.layer
        return None

    def _on_accept(self) -> None:
        """收集标题、字号和自定义标签。"""
        overrides = {
            patch_id: edit.text().strip()
            for patch_id, edit in self._label_edits.items()
            if edit.text().strip()
        }
        self._result = {
            "title": self._title_edit.text(),
            "title_font_size_mm": self._title_size.value(),
            "item_font_size_mm": self._item_size.value(),
            "column_count": self._columns.value(),
            "show_title": self._show_title.isChecked(),
            "show_layer_headings": self._show_headings.isChecked(),
            "label_overrides": overrides,
        }
        self.accept()

    def changes(self) -> dict[str, object]:
        """返回确认后的属性修改。"""
        return dict(self._result)


def _patch_color(patch: LegendPatch) -> str:
    """取补丁预览色。"""
    if patch.colors:
        return patch.colors[0]
    if patch.style is not None:
        if patch.style.fill_color and patch.style.fill_color != "transparent":
            return patch.style.fill_color
        return patch.style.stroke_color
    return "#9ca3af"
