"""栅格掩膜裁剪对话框：选择栅格和面矢量掩膜。"""

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.application.raster_analysis import RasterClipRequest
from app.application.results import LayerSnapshot
from app.domain.layer_style import GeometryFamily
from app.domain.raster_layer import RasterLayer
from app.domain.vector_layer import VectorLayer
from app.presentation.widgets.raster_output_fields import RasterOutputNameBinder


class RasterClipDialog(QDialog):
    """收集栅格掩膜裁剪的输入图层和裁剪选项。"""

    def __init__(
        self,
        layers: tuple[LayerSnapshot, ...],
        parent: QWidget | None = None,
    ) -> None:
        """初始化掩膜裁剪对话框。

        异常:
            ValueError: 缺少栅格或面矢量图层。
        """
        super().__init__(parent)
        self.setWindowTitle("栅格掩膜裁剪")
        self.setMinimumWidth(480)

        self._raster_layers: tuple[LayerSnapshot, ...] = tuple(
            layer for layer in layers if isinstance(layer.layer, RasterLayer)
        )
        self._polygon_layers: tuple[LayerSnapshot, ...] = tuple(
            layer
            for layer in layers
            if isinstance(layer.layer, VectorLayer)
            and layer.geometry_family == GeometryFamily.POLYGON
        )
        if not self._raster_layers:
            raise ValueError("当前工作区没有可用于裁剪的栅格图层。")
        if not self._polygon_layers:
            raise ValueError("当前工作区没有面矢量图层可作为掩膜。")

        self._build_ui()

    def _build_ui(self) -> None:
        """构建对话框布局。"""
        layout: QVBoxLayout = QVBoxLayout(self)

        # ── 输入 ──
        input_group: QGroupBox = QGroupBox("输入")
        input_form: QFormLayout = QFormLayout(input_group)

        self._raster_combo: QComboBox = QComboBox()
        for layer in self._raster_layers:
            self._raster_combo.addItem(layer.name, layer.layer_id)
        input_form.addRow("栅格图层:", self._raster_combo)

        self._mask_combo: QComboBox = QComboBox()
        for layer in self._polygon_layers:
            self._mask_combo.addItem(layer.name, layer.layer_id)
        input_form.addRow("掩膜（面图层）:", self._mask_combo)
        layout.addWidget(input_group)

        # ── 裁剪选项 ──
        opt_group: QGroupBox = QGroupBox("裁剪选项")
        opt_form: QFormLayout = QFormLayout(opt_group)
        self._crop_check: QCheckBox = QCheckBox("按掩膜范围裁剪输出尺寸")
        self._crop_check.setChecked(True)
        opt_form.addRow("", self._crop_check)
        self._all_touched_check: QCheckBox = QCheckBox(
            "所有被边界触碰的像元视为有效"
        )
        opt_form.addRow("", self._all_touched_check)
        self._invert_check: QCheckBox = QCheckBox("反转掩膜（保留矢量范围外）")
        opt_form.addRow("", self._invert_check)
        layout.addWidget(opt_group)

        # ── 输出 ──
        out_group: QGroupBox = QGroupBox("输出")
        out_form: QFormLayout = QFormLayout(out_group)
        self._name_edit: QLineEdit = QLineEdit("裁剪结果")
        out_form.addRow("图层名称:", self._name_edit)
        path_row: QHBoxLayout = QHBoxLayout()
        self._path_edit: QLineEdit = QLineEdit(
            str(Path.cwd() / "clip_result.tif")
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

        buttons: QDialogButtonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_request)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse_output_path(self) -> None:
        """打开保存文件对话框。"""
        from PySide6.QtWidgets import QFileDialog

        path_str, _ = QFileDialog.getSaveFileName(
            self, "保存裁剪结果",
            str(Path.cwd() / "clip_result.tif"),
            "GeoTIFF (*.tif *.tiff)",
        )
        if path_str:
            self._output_fields.set_path(path_str)

    def _accept_request(self) -> None:
        """校验参数后确认。"""
        name_error = self._output_fields.validation_error()
        if name_error is not None:
            QMessageBox.warning(self, "输出文件名无效", name_error)
            return
        self.accept()

    def request(self) -> RasterClipRequest:
        """构建不可变的掩膜裁剪请求。"""
        output_path = self._output_fields.output_path
        return RasterClipRequest(
            raster_layer_id=str(self._raster_combo.currentData()),
            mask_layer_id=str(self._mask_combo.currentData()),
            crop=self._crop_check.isChecked(),
            all_touched=self._all_touched_check.isChecked(),
            invert=self._invert_check.isChecked(),
            output_layer_name=self._output_fields.output_name,
            output_path=output_path,
        )
