"""DEM 地形分析对话框：配置坡度/坡向/山体阴影参数。"""

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.application.raster_analysis import DemAnalysisRequest
from app.application.results import LayerSnapshot
from app.domain.raster_layer import RasterLayer
from app.presentation.widgets.raster_output_fields import RasterOutputNameBinder

_MODES: dict[str, str] = {
    "坡度（度）": "slope",
    "坡向（度，北为0顺时针）": "aspect",
    "山体阴影（0-255）": "hillshade",
}
_ELEV_UNITS = {"米": "meter", "英尺": "foot"}


class DemAnalysisDialog(QDialog):
    """收集 DEM 分析类型、高程单位和输出参数。"""

    def __init__(
        self,
        layers: tuple[LayerSnapshot, ...],
        parent: QWidget | None = None,
    ) -> None:
        """初始化 DEM 分析对话框。

        异常:
            ValueError: 当前工作区没有栅格图层。
        """
        super().__init__(parent)
        self.setWindowTitle("DEM 地形分析")
        self.setMinimumWidth(500)

        self._raster_layers: tuple[LayerSnapshot, ...] = tuple(
            layer for layer in layers if isinstance(layer.layer, RasterLayer)
        )
        if not self._raster_layers:
            raise ValueError("当前工作区没有可用于 DEM 分析的栅格图层。")

        self._build_ui()
        self._populate_layer_combo()
        self._on_mode_changed()

    def _build_ui(self) -> None:
        """构建对话框布局。"""
        layout: QVBoxLayout = QVBoxLayout(self)

        # ── 输入 ──
        input_group: QGroupBox = QGroupBox("输入 DEM")
        input_form: QFormLayout = QFormLayout(input_group)
        self._layer_combo: QComboBox = QComboBox()
        self._layer_combo.currentIndexChanged.connect(self._on_layer_changed)
        input_form.addRow("栅格图层:", self._layer_combo)
        self._band_spin: QSpinBox = QSpinBox()
        self._band_spin.setMinimum(1)
        input_form.addRow("波段:", self._band_spin)
        layout.addWidget(input_group)

        # ── 分析类型 ──
        mode_group: QGroupBox = QGroupBox("分析类型")
        mode_form: QFormLayout = QFormLayout(mode_group)
        self._mode_combo: QComboBox = QComboBox()
        self._mode_combo.addItems(list(_MODES.keys()))
        self._mode_combo.currentTextChanged.connect(self._on_mode_changed)
        mode_form.addRow("类型:", self._mode_combo)

        self._elev_combo: QComboBox = QComboBox()
        self._elev_combo.addItems(list(_ELEV_UNITS.keys()))
        mode_form.addRow("高程单位:", self._elev_combo)

        self._zfactor_spin: QDoubleSpinBox = QDoubleSpinBox()
        self._zfactor_spin.setRange(0.0, 100.0)
        self._zfactor_spin.setValue(1.0)
        self._zfactor_spin.setDecimals(4)
        self._zfactor_spin.setSpecialValueText("自动")
        mode_form.addRow("Z 因子:", self._zfactor_spin)
        layout.addWidget(mode_group)

        # ── 阴影参数 ──
        self._shade_group: QGroupBox = QGroupBox("山体阴影参数")
        shade_form: QFormLayout = QFormLayout(self._shade_group)
        self._azimuth_spin: QDoubleSpinBox = QDoubleSpinBox()
        self._azimuth_spin.setRange(0.0, 359.9)
        self._azimuth_spin.setValue(315.0)
        shade_form.addRow("太阳方位角（度）:", self._azimuth_spin)
        self._altitude_spin: QDoubleSpinBox = QDoubleSpinBox()
        self._altitude_spin.setRange(0.1, 89.9)
        self._altitude_spin.setValue(45.0)
        shade_form.addRow("太阳高度角（度）:", self._altitude_spin)
        layout.addWidget(self._shade_group)

        # ── 输出 ──
        out_group: QGroupBox = QGroupBox("输出")
        out_form: QFormLayout = QFormLayout(out_group)
        self._name_edit: QLineEdit = QLineEdit("DEM 分析结果")
        out_form.addRow("图层名称:", self._name_edit)
        path_row: QHBoxLayout = QHBoxLayout()
        self._path_edit: QLineEdit = QLineEdit(
            str(Path.cwd() / "dem_result.tif")
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

        hint: QLabel = QLabel(
            "DEM 地形分析要求投影坐标系（米制）。"
            "地理坐标系（经纬度）请先重投影到米制 CRS。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6b7280; font-size: 11px;")
        layout.addWidget(hint)

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
                self._layer_combo.addItem(
                    f"{layer.name}  [{layer.layer.band_count} 波段]",
                    layer.layer_id,
                )
        if self._layer_combo.count() > 0:
            self._on_layer_changed()

    def _on_layer_changed(self) -> None:
        """切换图层时更新波段上限。"""
        layer_id = self._layer_combo.currentData()
        if layer_id is None:
            return
        for layer in self._raster_layers:
            if layer.layer_id == layer_id and isinstance(layer.layer, RasterLayer):
                self._band_spin.setMaximum(layer.layer.band_count)
                break

    def _on_mode_changed(self) -> None:
        """切换分析类型时显示/隐藏阴影参数。"""
        mode = _MODES.get(self._mode_combo.currentText(), "slope")
        self._shade_group.setVisible(mode == "hillshade")

    def _browse_output_path(self) -> None:
        """打开保存文件对话框。"""
        from PySide6.QtWidgets import QFileDialog

        path_str, _ = QFileDialog.getSaveFileName(
            self, "保存 DEM 分析结果",
            str(Path.cwd() / "dem_result.tif"),
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

    def request(self) -> DemAnalysisRequest:
        """构建不可变的 DEM 分析请求。"""
        mode = _MODES.get(self._mode_combo.currentText(), "slope")
        elev_unit = _ELEV_UNITS.get(self._elev_combo.currentText(), "meter")
        z_factor: float | None = self._zfactor_spin.value()
        if z_factor == self._zfactor_spin.minimum():
            z_factor = None
        output_path = self._output_fields.output_path
        return DemAnalysisRequest(
            input_layer_id=str(self._layer_combo.currentData()),
            band_index=self._band_spin.value(),
            mode=mode,  # type: ignore[arg-type]
            elevation_unit=elev_unit,  # type: ignore[arg-type]
            z_factor=z_factor,
            azimuth=self._azimuth_spin.value(),
            altitude=self._altitude_spin.value(),
            output_layer_name=self._output_fields.output_name,
            output_path=output_path,
        )
