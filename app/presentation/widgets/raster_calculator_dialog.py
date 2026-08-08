"""栅格计算器对话框：配置波段变量映射和逐像素表达式。"""

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)

from app.application.raster_calculator import (
    BandMapping,
    RasterCalculatorRequest,
)
from app.application.results import LayerSnapshot
from app.domain.raster_layer import RasterLayer


class RasterCalculatorDialog(QDialog):
    """收集栅格波段变量映射、表达式和输出参数。"""

    def __init__(
        self,
        layers: tuple[LayerSnapshot, ...],
        parent: QDialog | None = None,
    ) -> None:
        """初始化栅格计算器对话框。

        参数:
            layers: 当前工作区的所有图层快照。
            parent: 父对话框。

        异常:
            ValueError: 当前工作区没有栅格图层。
        """
        super().__init__(parent)
        self.setWindowTitle("栅格计算器")
        self.setMinimumWidth(560)

        self._raster_layers: tuple[LayerSnapshot, ...] = tuple(
            layer for layer in layers if isinstance(layer.layer, RasterLayer)
        )
        if not self._raster_layers:
            raise ValueError("当前工作区没有可用于栅格计算的栅格图层。")

        self._mappings: list[BandMapping] = []

        self._build_ui()
        self._populate_layer_combo()

    # ── UI 构建 ────────────────────────────────────────────

    def _build_ui(self) -> None:
        """构建完整的对话框布局。"""
        layout: QVBoxLayout = QVBoxLayout(self)

        # ── 提示 ──
        hint: QLabel = QLabel(
            "为栅格波段设置变量名（如 dem、slope），"
            "然后在表达式中用引号引用它们（如 \"dem\" * 2）。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # ── 波段变量 ──
        band_group: QGroupBox = QGroupBox("波段变量")
        band_layout: QVBoxLayout = QVBoxLayout(band_group)

        # 添加行
        add_row: QHBoxLayout = QHBoxLayout()
        add_row.addWidget(QLabel("变量名:"))
        self._alias_edit: QLineEdit = QLineEdit()
        self._alias_edit.setPlaceholderText("如 dem、slope")
        add_row.addWidget(self._alias_edit)
        add_row.addWidget(QLabel("图层:"))
        self._layer_combo: QComboBox = QComboBox()
        self._layer_combo.currentIndexChanged.connect(self._on_layer_changed)
        add_row.addWidget(self._layer_combo)
        add_row.addWidget(QLabel("波段:"))
        self._band_spin: QSpinBox = QSpinBox()
        self._band_spin.setMinimum(1)
        self._band_spin.setValue(1)
        add_row.addWidget(self._band_spin)

        add_btn: QPushButton = QPushButton("添加")
        add_btn.clicked.connect(self._add_mapping)
        add_row.addWidget(add_btn)
        band_layout.addLayout(add_row)

        # 已添加列表
        self._mapping_list: QListWidget = QListWidget()
        self._mapping_list.setMaximumHeight(120)
        band_layout.addWidget(self._mapping_list)

        # 删除按钮
        remove_row: QHBoxLayout = QHBoxLayout()
        remove_btn: QPushButton = QPushButton("移除选中变量")
        remove_btn.clicked.connect(self._remove_mapping)
        remove_row.addWidget(remove_btn)
        remove_row.addStretch()
        band_layout.addLayout(remove_row)

        layout.addWidget(band_group)

        # ── 表达式 ──
        expr_group: QGroupBox = QGroupBox("表达式")
        expr_layout: QVBoxLayout = QVBoxLayout(expr_group)
        self._expr_edit: QTextEdit = QTextEdit()
        self._expr_edit.setObjectName("rasterCalculatorExpression")
        self._expr_edit.setPlaceholderText(
            '例如: ("dem" > 200) & ("slope" < 15)\n'
            '      "dem" * 3.28084\n'
            '      where("ndvi" > 0.5, 1, 0)'
        )
        self._expr_edit.setMaximumHeight(80)
        self._expr_edit.setFontFamily("Consolas, monospace")
        expr_layout.addWidget(self._expr_edit)
        layout.addWidget(expr_group)

        # ── 输出 ──
        out_group: QGroupBox = QGroupBox("输出")
        out_form: QFormLayout = QFormLayout(out_group)

        self._name_edit: QLineEdit = QLineEdit("栅格计算结果")
        out_form.addRow("图层名称:", self._name_edit)

        path_row: QHBoxLayout = QHBoxLayout()
        self._path_edit: QLineEdit = QLineEdit(
            str(Path.cwd() / "raster_calc_result.tif")
        )
        self._path_edit.setReadOnly(True)
        path_row.addWidget(self._path_edit)
        browse_btn: QPushButton = QPushButton("浏览…")
        browse_btn.clicked.connect(self._browse_output_path)
        path_row.addWidget(browse_btn)
        out_form.addRow("输出路径:", path_row)

        layout.addWidget(out_group)

        # ── 函数提示 ──
        func_hint: QLabel = QLabel(
            "可用函数: where(cond, t, f), abs, sqrt, sin, cos, tan, "
            "log, log10, exp, clip(x, lo, hi), maximum, minimum\n"
            "可用常量: pi, e　　运算符: + - * / ** > < >= <= == != & | ~"
        )
        func_hint.setWordWrap(True)
        func_hint.setStyleSheet("color: #6b7280; font-size: 11px;")
        layout.addWidget(func_hint)

        # ── 按钮 ──
        buttons: QDialogButtonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_request)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ── 波段变量管理 ────────────────────────────────────────

    def _populate_layer_combo(self) -> None:
        """用当前工作区的栅格图层填充下拉框。"""
        self._layer_combo.clear()
        for layer in self._raster_layers:
            if isinstance(layer.layer, RasterLayer):
                label: str = f"{layer.name}  [{layer.layer.band_count} 波段]"
                self._layer_combo.addItem(label, layer.layer_id)

    def _on_layer_changed(self) -> None:
        """切换图层时更新波段选择器的上限。"""
        layer_id: str | None = self._layer_combo.currentData()
        if layer_id is None:
            return
        for layer in self._raster_layers:
            if layer.layer_id == layer_id and isinstance(layer.layer, RasterLayer):
                self._band_spin.setMaximum(layer.layer.band_count)
                break

    def _add_mapping(self) -> None:
        """校验并添加一条波段变量映射。"""
        alias: str = self._alias_edit.text().strip()
        if not alias:
            QMessageBox.warning(self, "变量名无效", "请输入波段变量名。")
            return

        layer_id: str | None = self._layer_combo.currentData()
        if layer_id is None:
            QMessageBox.warning(self, "图层未选择", "请选择栅格图层。")
            return

        band_index: int = self._band_spin.value()

        try:
            mapping: BandMapping = BandMapping(
                alias=alias, layer_id=str(layer_id), band_index=band_index
            )
        except ValueError as exc:
            QMessageBox.warning(self, "变量名无效", str(exc))
            return

        # 检查重复 alias
        for existing in self._mappings:
            if existing.alias == alias:
                QMessageBox.warning(
                    self, "变量名重复", f"变量名「{alias}」已存在。"
                )
                return

        self._mappings.append(mapping)
        self._update_mapping_list()
        self._alias_edit.clear()
        self._alias_edit.setFocus()

    def _remove_mapping(self) -> None:
        """移除选中的波段变量映射。"""
        current: QListWidgetItem | None = self._mapping_list.currentItem()
        if current is None:
            return
        row: int = self._mapping_list.row(current)
        if 0 <= row < len(self._mappings):
            self._mappings.pop(row)
        self._update_mapping_list()

    def _update_mapping_list(self) -> None:
        """将当前映射刷新到列表控件中。"""
        self._mapping_list.clear()
        for mapping in self._mappings:
            # 查找图层名
            layer_name: str = mapping.layer_id
            for layer in self._raster_layers:
                if layer.layer_id == mapping.layer_id:
                    layer_name = layer.name
                    break
            text: str = (
                f"「{mapping.alias}」 ← {layer_name}  波段 {mapping.band_index}"
            )
            self._mapping_list.addItem(text)

    # ── 输出路径 ────────────────────────────────────────────

    def _browse_output_path(self) -> None:
        """打开保存文件对话框选择 GeoTIFF 输出路径。"""
        from PySide6.QtWidgets import QFileDialog

        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "保存栅格计算结果",
            str(Path.cwd() / "raster_calc_result.tif"),
            "GeoTIFF (*.tif *.tiff)",
        )
        if path_str:
            self._path_edit.setText(path_str)

    # ── 确认提交 ────────────────────────────────────────────

    def _accept_request(self) -> None:
        """校验所有参数并通过 request() 确认。"""
        if not self._mappings:
            QMessageBox.warning(self, "缺少变量", "请至少添加一个波段变量映射。")
            return

        expression: str = self._expr_edit.toPlainText().strip()
        if not expression:
            QMessageBox.warning(self, "缺少表达式", "请输入计算表达式。")
            return

        output_name: str = self._name_edit.text().strip()
        if not output_name:
            QMessageBox.warning(self, "缺少名称", "请输入输出图层名称。")
            return

        output_path: Path = Path(self._path_edit.text().strip())
        if not str(output_path):
            QMessageBox.warning(self, "缺少路径", "请指定输出文件路径。")
            return

        if output_path.suffix.lower() not in (".tif", ".tiff"):
            output_path = output_path.with_suffix(".tif")

        # 确认数据存在（映射的图层仍在工作区中）
        valid_layer_ids: set[str] = {
            layer.layer_id for layer in self._raster_layers
        }
        for mapping in self._mappings:
            if mapping.layer_id not in valid_layer_ids:
                QMessageBox.warning(
                    self,
                    "波段变量失效",
                    f"变量「{mapping.alias}」对应的图层已不存在。",
                )
                return

        self.accept()

    def request(self) -> RasterCalculatorRequest:
        """构建不可变的栅格计算请求。

        仅在对话框以 Accepted 返回后调用。
        """
        expression: str = self._expr_edit.toPlainText().strip()
        output_name: str = self._name_edit.text().strip()
        output_path: Path = Path(self._path_edit.text().strip())
        if output_path.suffix.lower() not in (".tif", ".tiff"):
            output_path = output_path.with_suffix(".tif")

        return RasterCalculatorRequest(
            expression=expression,
            band_mappings=tuple(self._mappings),
            output_layer_name=output_name,
            output_path=output_path,
        )
