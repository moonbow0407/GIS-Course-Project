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
    BufferDistanceUnitName,
    BufferJoinStyleName,
    BufferRequest,
    BufferSideTypeName,
)
from app.application.errors import ApplicationError
from app.application.results import LayerSnapshot
from app.domain.layer_style import GeometryFamily
from app.domain.vector_layer import VectorLayer


class BufferAnalysisDialog(QDialog):
    """收集输入图层、输出位置和常用缓冲区几何参数。"""

    _GEOMETRY_LABELS: dict[GeometryFamily, str] = {
        GeometryFamily.POINT: "点（Point）",
        GeometryFamily.LINE: "线（Polyline）",
        GeometryFamily.POLYGON: "面（Polygon）",
        GeometryFamily.MIXED: "混合几何（不支持）",
    }

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
        self._output_path_auto: bool = True

        self._input_layer_combo: QComboBox = QComboBox()
        layer: LayerSnapshot
        for layer in self._vector_layers:
            self._input_layer_combo.addItem(layer.name, layer.layer_id)

        first_layer: LayerSnapshot = self._vector_layers[0]
        self._output_name_edit: QLineEdit = QLineEdit(f"{first_layer.name}_buffer")
        self._output_path_edit: QLineEdit = QLineEdit()
        self._output_path_edit.setPlaceholderText("默认保存到输入图层所在目录")
        self._output_path_edit.textEdited.connect(self._mark_output_path_manual)
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
        self._distance_spin.setToolTip("距离数值按照右侧选择的线性单位解释。")
        self._distance_unit_combo: QComboBox = QComboBox()
        self._distance_unit_combo.addItem("毫米（mm）", "millimeter")
        self._distance_unit_combo.addItem("厘米（cm）", "centimeter")
        self._distance_unit_combo.addItem("米（m）", "meter")
        self._distance_unit_combo.addItem("千米（km）", "kilometer")
        self._distance_unit_combo.addItem("英尺（ft）", "foot")
        self._distance_unit_combo.addItem("英里（mi）", "mile")
        self._distance_unit_combo.setCurrentIndex(2)
        distance_input_widget: QWidget = QWidget()
        distance_input_layout: QHBoxLayout = QHBoxLayout(distance_input_widget)
        distance_input_layout.setContentsMargins(0, 0, 0, 0)
        distance_input_layout.addWidget(self._distance_spin, 1)
        distance_input_layout.addWidget(self._distance_unit_combo)

        self._segments_spin: QSpinBox = QSpinBox()
        self._segments_spin.setRange(1, 256)
        self._segments_spin.setValue(8)

        self._side_type_label: QLabel = QLabel("侧类型")
        self._side_type_combo: QComboBox = QComboBox()

        self._cap_style_label: QLabel = QLabel("端点样式")
        self._cap_style_combo: QComboBox = QComboBox()
        self._cap_style_combo.addItem("圆角", "round")
        self._cap_style_combo.addItem("平头", "flat")
        self._cap_style_combo.addItem("方头", "square")

        self._join_style_label: QLabel = QLabel("连接样式")
        self._join_style_combo: QComboBox = QComboBox()
        self._join_style_combo.addItem("圆角", "round")
        self._join_style_combo.addItem("斜接", "mitre")
        self._join_style_combo.addItem("倒角", "bevel")

        self._mitre_limit_label: QLabel = QLabel("斜接比")
        self._mitre_limit_spin: QDoubleSpinBox = QDoubleSpinBox()
        self._mitre_limit_spin.setRange(0.01, 1_000_000.0)
        self._mitre_limit_spin.setDecimals(3)
        self._mitre_limit_spin.setValue(5.0)

        self._dissolve_check: QCheckBox = QCheckBox("融合相互重叠的缓冲结果")
        self._analysis_crs_edit: QLineEdit = QLineEdit()
        current_crs_hint: str = display_crs.to_string() if display_crs is not None else "未设置"
        self._analysis_crs_edit.setPlaceholderText(
            f"留空自动选择米制计算 CRS（当前地图：{current_crs_hint}）"
        )
        self._geometry_type_label: QLabel = QLabel()

        form_layout: QFormLayout = QFormLayout()
        form_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form_layout.addRow("输入图层", self._input_layer_combo)
        form_layout.addRow("识别类型", self._geometry_type_label)
        form_layout.addRow("输出图层名", self._output_name_edit)
        form_layout.addRow("输出位置", output_path_widget)
        form_layout.addRow("缓冲距离", distance_input_widget)
        form_layout.addRow("圆弧分段数", self._segments_spin)
        form_layout.addRow(self._side_type_label, self._side_type_combo)
        form_layout.addRow(self._cap_style_label, self._cap_style_combo)
        form_layout.addRow(self._join_style_label, self._join_style_combo)
        form_layout.addRow(self._mitre_limit_label, self._mitre_limit_spin)
        form_layout.addRow("计算 CRS（可选）", self._analysis_crs_edit)
        form_layout.addRow(QLabel(""), self._dissolve_check)

        self._hint_label: QLabel = QLabel()
        self._hint_label.setWordWrap(True)
        self._hint_label.setObjectName("bufferAnalysisHint")

        button_box: QDialogButtonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._accept_request)
        button_box.rejected.connect(self.reject)

        layout: QVBoxLayout = QVBoxLayout(self)
        layout.addLayout(form_layout)
        layout.addWidget(self._hint_label)
        layout.addWidget(button_box)

        self._input_layer_combo.currentIndexChanged.connect(self._on_input_layer_changed)
        self._output_name_edit.textChanged.connect(self._on_output_name_changed)
        self._on_input_layer_changed(0)

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
            distance_unit=cast(
                BufferDistanceUnitName,
                str(self._distance_unit_combo.currentData()),
            ),
            side_type=cast(BufferSideTypeName, str(self._side_type_combo.currentData())),
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
        current_path: str = self._output_path_edit.text().strip()
        suggested_path: str = current_path or suggested_name
        if self._output_path_auto and current_path:
            # 默认路径带有 GeoJSON 后缀；交给文件对话框按用户选择的过滤器重新补后缀。
            suggested_path = str(Path(current_path).with_suffix(""))
        path_string, selected_filter = QFileDialog.getSaveFileName(
            self,
            "保存缓冲区结果",
            suggested_path,
            "GeoJSON (*.geojson);;Shapefile (*.shp);;GeoPackage (*.gpkg)",
        )
        if not path_string:
            return
        self._output_path_auto = False
        self._output_path_edit.setText(
            str(self._with_output_suffix(Path(path_string), selected_filter))
        )

    def _on_input_layer_changed(self, index: int) -> None:
        """切换输入图层时刷新默认路径和点线面专用参数。"""
        if index < 0 or index >= len(self._vector_layers):
            return
        layer_snapshot: LayerSnapshot = self._vector_layers[index]
        self._output_path_auto = True
        self._output_name_edit.setText(f"{layer_snapshot.name}_buffer")
        self._update_default_output_path()
        self._update_geometry_controls(layer_snapshot)

    def _on_output_name_changed(self) -> None:
        """用户仍使用默认路径时，随输出图层名同步文件名。"""
        if self._output_path_auto:
            self._update_default_output_path()

    def _mark_output_path_manual(self) -> None:
        """记录用户手动编辑了输出位置，之后不再被名称变化覆盖。"""
        self._output_path_auto = False

    def _update_default_output_path(self) -> None:
        """把默认输出文件放到当前输入数据的同一目录。"""
        index: int = self._input_layer_combo.currentIndex()
        if index < 0 or index >= len(self._vector_layers):
            return
        layer: VectorLayer = cast(VectorLayer, self._vector_layers[index].layer)
        output_name: str = self._output_name_edit.text().strip() or "buffer_result"
        source_directory: Path = (
            layer.source_path.parent if layer.source_path is not None else Path.cwd()
        )
        output_stem: str = self._safe_filename_stem(output_name)
        self._output_path_edit.setText(str((source_directory / f"{output_stem}.geojson").resolve()))

    def _update_geometry_controls(self, layer_snapshot: LayerSnapshot) -> None:
        """根据输入图层点、线、面类别显示对应的 ArcGIS 风格参数。"""
        family: GeometryFamily = layer_snapshot.geometry_family or GeometryFamily.MIXED
        self._geometry_type_label.setText(self._GEOMETRY_LABELS[family])

        is_polygon: bool = family is GeometryFamily.POLYGON
        current_distance: float = self._distance_spin.value()
        self._distance_spin.setMinimum(-1_000_000_000_000.0 if is_polygon else 0.000001)
        if current_distance <= 0 or (not is_polygon and current_distance < 0):
            self._distance_spin.setValue(10.0)

        self._side_type_combo.blockSignals(True)
        self._side_type_combo.clear()
        if family is GeometryFamily.LINE:
            self._side_type_combo.addItem("两侧（Full）", "full")
            self._side_type_combo.addItem("左侧（Left）", "left")
            self._side_type_combo.addItem("右侧（Right）", "right")
        elif family is GeometryFamily.POLYGON:
            self._side_type_combo.addItem("包含原面（Full）", "full")
            self._side_type_combo.addItem("仅外侧（Outside Only）", "outside")
        else:
            self._side_type_combo.addItem("不适用", "full")
        self._side_type_combo.blockSignals(False)

        self._set_parameter_row_visible(
            self._side_type_label,
            self._side_type_combo,
            family in {GeometryFamily.LINE, GeometryFamily.POLYGON},
        )
        self._set_parameter_row_visible(
            self._cap_style_label,
            self._cap_style_combo,
            family is GeometryFamily.LINE,
        )
        self._set_parameter_row_visible(
            self._join_style_label,
            self._join_style_combo,
            family in {GeometryFamily.LINE, GeometryFamily.POLYGON},
        )
        self._set_parameter_row_visible(
            self._mitre_limit_label,
            self._mitre_limit_spin,
            family in {GeometryFamily.LINE, GeometryFamily.POLYGON},
        )

        if family is GeometryFamily.POINT:
            hint: str = "点图层：使用距离生成圆形缓冲；端点、连接和侧类型不适用。"
        elif family is GeometryFamily.LINE:
            hint = "线图层：可选择两侧、左侧或右侧，并设置端点和折点样式。"
        elif family is GeometryFamily.POLYGON:
            hint = "面图层：可包含原面或仅生成外侧环带；负距离表示向内缓冲。"
        else:
            hint = "当前图层包含混合几何，无法执行点、线、面专用缓冲区分析。"
        self._hint_label.setText(
            f"{hint}\n距离按照所选单位输入；程序内部统一换算为米制坐标进行计算。"
        )

    @staticmethod
    def _set_parameter_row_visible(label: QLabel, widget: QWidget, visible: bool) -> None:
        """同步隐藏参数标签和控件，避免留下空白表单行。"""
        label.setVisible(visible)
        widget.setVisible(visible)

    @staticmethod
    def _safe_filename_stem(output_name: str) -> str:
        """将图层名转换为 Windows 和常见矢量驱动均可接受的文件名主体。"""
        invalid_characters: frozenset[str] = frozenset('<>:"/\\|?*')
        safe_name: str = "".join(
            "_" if character in invalid_characters else character for character in output_name
        ).strip()
        safe_name = safe_name.rstrip(".")
        return safe_name or "buffer_result"

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
