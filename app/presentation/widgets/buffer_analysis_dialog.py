"""缓冲区分析参数对话框。"""

from pathlib import Path
from typing import cast

from pyproj import CRS
from pyproj.exceptions import CRSError
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.application.buffer_analysis import (
    BufferCapStyleName,
    BufferJoinStyleName,
    BufferRequest,
)
from app.application.errors import ApplicationError
from app.application.results import LayerSnapshot
from app.domain.vector_layer import VectorLayer


class BufferAnalysisDialog(QDialog):
    """收集输入图层、输出位置和常用缓冲区几何参数。"""

    def __init__(
        self,
        layers: tuple[LayerSnapshot, ...],
        display_crs: CRS | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """使用当前工作区图层构造缓冲区分析参数窗口。"""
        super().__init__(parent)
        self.setWindowTitle("缓冲区分析")
        self.setMinimumWidth(520)

        self._vector_layers: tuple[LayerSnapshot, ...] = tuple(
            layer for layer in layers if isinstance(layer.layer, VectorLayer)
        )
        if not self._vector_layers:
            raise ValueError("当前工作区没有可用于缓冲区分析的矢量图层。")

        self._input_layer_combo: QComboBox = QComboBox()
        layer: LayerSnapshot
        for layer in self._vector_layers:
            self._input_layer_combo.addItem(layer.name, layer.layer_id)

        first_layer: LayerSnapshot = self._vector_layers[0]
        self._output_name_edit: QLineEdit = QLineEdit(f"{first_layer.name}_buffer")
        self._output_path_edit: QLineEdit = QLineEdit()
        self._output_path_edit.setPlaceholderText("请选择输出文件位置")
        browse_button: QPushButton = QPushButton("浏览…")
        browse_button.clicked.connect(self._browse_output_path)
        output_path_widget: QWidget = QWidget()
        output_path_layout: QHBoxLayout = QHBoxLayout(output_path_widget)
        output_path_layout.setContentsMargins(0, 0, 0, 0)
        output_path_layout.addWidget(self._output_path_edit, 1)
        output_path_layout.addWidget(browse_button)

        self._distance_spin: QDoubleSpinBox = QDoubleSpinBox()
        self._distance_spin.setRange(0.000001, 1_000_000_000_000.0)
        self._distance_spin.setDecimals(6)
        self._distance_spin.setValue(10.0)
        self._distance_spin.setToolTip("距离单位等于分析坐标系的坐标单位。")

        self._segments_spin: QSpinBox = QSpinBox()
        self._segments_spin.setRange(1, 256)
        self._segments_spin.setValue(8)

        self._cap_style_combo: QComboBox = QComboBox()
        self._cap_style_combo.addItem("圆角", "round")
        self._cap_style_combo.addItem("平头", "flat")
        self._cap_style_combo.addItem("方头", "square")

        self._join_style_combo: QComboBox = QComboBox()
        self._join_style_combo.addItem("圆角", "round")
        self._join_style_combo.addItem("斜接", "mitre")
        self._join_style_combo.addItem("倒角", "bevel")

        self._mitre_limit_spin: QDoubleSpinBox = QDoubleSpinBox()
        self._mitre_limit_spin.setRange(0.01, 1_000_000.0)
        self._mitre_limit_spin.setDecimals(3)
        self._mitre_limit_spin.setValue(5.0)

        self._dissolve_check: QCheckBox = QCheckBox("融合相互重叠的缓冲结果")
        self._analysis_crs_edit: QLineEdit = QLineEdit(
            display_crs.to_string() if display_crs is not None else ""
        )
        self._analysis_crs_edit.setPlaceholderText("留空使用地图 CRS")

        form_layout: QFormLayout = QFormLayout()
        form_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form_layout.addRow("输入图层", self._input_layer_combo)
        form_layout.addRow("输出图层名", self._output_name_edit)
        form_layout.addRow("输出位置", output_path_widget)
        form_layout.addRow("缓冲距离", self._distance_spin)
        form_layout.addRow("圆弧分段数", self._segments_spin)
        form_layout.addRow("端点样式", self._cap_style_combo)
        form_layout.addRow("连接样式", self._join_style_combo)
        form_layout.addRow("斜接比", self._mitre_limit_spin)
        form_layout.addRow("分析坐标系", self._analysis_crs_edit)
        form_layout.addRow(QLabel(""), self._dissolve_check)

        hint_label: QLabel = QLabel(
            "距离使用分析坐标系单位；需要按米计算时，请选择米制投影 CRS。"
        )
        hint_label.setWordWrap(True)
        hint_label.setObjectName("bufferAnalysisHint")

        button_box: QDialogButtonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._accept_request)
        button_box.rejected.connect(self.reject)

        layout: QVBoxLayout = QVBoxLayout(self)
        layout.addLayout(form_layout)
        layout.addWidget(hint_label)
        layout.addWidget(button_box)

    def request(self) -> BufferRequest:
        """返回当前控件内容构造的缓冲区分析请求。"""
        input_layer_id: str = str(self._input_layer_combo.currentData())
        output_path_text: str = self._output_path_edit.text().strip()
        if not output_path_text:
            raise ApplicationError("请选择缓冲区分析输出位置。")
        output_path: Path = self._with_output_suffix(Path(output_path_text))

        analysis_crs: CRS | None = None
        analysis_crs_text: str = self._analysis_crs_edit.text().strip()
        if analysis_crs_text:
            try:
                analysis_crs = CRS.from_user_input(analysis_crs_text)
            except CRSError as error:
                raise ApplicationError(f"无法识别分析坐标系：{analysis_crs_text}") from error

        return BufferRequest(
            input_layer_id=input_layer_id,
            output_path=output_path,
            output_layer_name=self._output_name_edit.text().strip(),
            distance=self._distance_spin.value(),
            segments=self._segments_spin.value(),
            cap_style=cast(BufferCapStyleName, str(self._cap_style_combo.currentData())),
            join_style=cast(BufferJoinStyleName, str(self._join_style_combo.currentData())),
            mitre_limit=self._mitre_limit_spin.value(),
            dissolve=self._dissolve_check.isChecked(),
            analysis_crs=analysis_crs,
        )

    def _accept_request(self) -> None:
        """校验对话框参数后关闭窗口；错误保留在窗口中供用户修正。"""
        try:
            self.request()
        except (ApplicationError, ValueError) as error:
            QMessageBox.warning(self, "缓冲区参数无效", str(error))
            return
        self.accept()

    def _browse_output_path(self) -> None:
        """选择缓冲区结果写出路径并自动补充所选格式后缀。"""
        suggested_name: str = self._output_name_edit.text().strip() or "buffer_result"
        path_string, selected_filter = QFileDialog.getSaveFileName(
            self,
            "保存缓冲区结果",
            suggested_name,
            "GeoJSON (*.geojson);;Shapefile (*.shp);;GeoPackage (*.gpkg)",
        )
        if not path_string:
            return
        self._output_path_edit.setText(
            str(self._with_output_suffix(Path(path_string), selected_filter))
        )

    @staticmethod
    def _with_output_suffix(path: Path, selected_filter: str = "") -> Path:
        """根据用户选择的格式为无扩展名路径补充后缀。"""
        if path.suffix:
            return path
        if "Shapefile" in selected_filter:
            return path.with_suffix(".shp")
        if "GeoPackage" in selected_filter:
            return path.with_suffix(".gpkg")
        return path.with_suffix(".geojson")
