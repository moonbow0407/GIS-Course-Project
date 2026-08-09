"""叠加分析参数对话框。"""

from pathlib import Path
from typing import cast

from pyproj import CRS
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.application.errors import ApplicationError
from app.application.overlay_analysis import (
    _GEOMETRIC_OVERLAY_OPS,
    _SPATIAL_JOIN_OPS,
    OverlayOperationName,
    OverlayRequest,
    SJoinHowName,
    SJoinPredicateName,
    operation_label,
)
from app.application.results import LayerSnapshot
from app.domain.layer_style import GeometryFamily


class OverlayAnalysisDialog(QDialog):
    """收集两个输入图层、叠加操作类型和输出位置的参数窗口。"""

    _GEOMETRY_LABELS: dict[GeometryFamily, str] = {
        GeometryFamily.POINT: "点（Point）",
        GeometryFamily.LINE: "线（Polyline）",
        GeometryFamily.POLYGON: "面（Polygon）",
        GeometryFamily.MIXED: "混合几何",
    }

    _OPERATION_ITEMS: tuple[tuple[str, str], ...] = (
        ("相交 (Intersection)", "intersection"),
        ("联合 (Union)", "union"),
        ("识别 (Identity)", "identity"),
        ("擦除 (Difference)", "difference"),
        ("更新 (Update)", "update"),
        ("对称差异 (Symmetric Difference)", "symmetric_difference"),
        ("点面叠置 (Point-in-Polygon)", "point_in_polygon"),
        ("线面叠置 (Line-in-Polygon)", "line_in_polygon"),
    )

    _PREDICATE_ITEMS: tuple[tuple[str, str], ...] = (
        ("相交 (Intersects)", "intersects"),
        ("包含 (Contains)", "contains"),
        ("位于内部 (Within)", "within"),
        ("接触 (Touches)", "touches"),
        ("穿越 (Crosses)", "crosses"),
        ("叠加 (Overlaps)", "overlaps"),
    )

    _SJOIN_HOW_ITEMS: tuple[tuple[str, str], ...] = (
        ("内连接 (Inner)", "inner"),
        ("左连接 (Left)", "left"),
        ("右连接 (Right)", "right"),
    )

    _HINTS: dict[str, str] = {
        "intersection": "相交：保留两个面图层空间重叠的区域，输出要素同时包含双方的属性字段。",
        "union": "联合：合并两个面图层的全部区域，在边界处分割为独立要素，非重叠区域保留各自属性。",
        "identity": "识别：保留主输入图层的全部区域，在叠加图层范围内分割边界并附加叠加图层属性。",
        "difference": "擦除：从主输入图层中去除与叠加图层重叠的区域，仅保留主输入图层的属性。",
        "symmetric_difference": "对称差异：保留两个面图层不重叠的区域，即相交区域的补集。",
        "update": "更新：用叠加图层的几何和属性替换主输入图层中与之重叠的区域，等价于擦除后合并叠加图层。",
        "point_in_polygon": "点面叠置：将面图层的属性字段附加到落入其内部的点要素上，属于空间属性连接。",
        "line_in_polygon": "线面叠置：将面图层的属性字段附加到与之相交或落入其内部的线要素上，属于空间属性连接。",
    }

    def __init__(
        self,
        layers: tuple[LayerSnapshot, ...],
        display_crs: CRS | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """使用当前工作区图层构造叠加分析参数窗口。

        参数:
            layers: 当前地图文档的全部图层快照。
            display_crs: 地图显示坐标系，用于提示信息。
            parent: 父窗口。

        异常:
            ValueError: 可用矢量图层不足两个时抛出。
        """
        super().__init__(parent)
        self.setWindowTitle("叠加分析")
        self.setMinimumWidth(540)

        self._all_layers: tuple[LayerSnapshot, ...] = layers
        self._vector_layers: tuple[LayerSnapshot, ...] = tuple(
            layer for layer in layers if not layer.is_raster
        )
        if len(self._vector_layers) < 2:
            raise ValueError("叠加分析需要至少两个矢量图层。")
        self._output_path_auto: bool = True

        # --- 操作类型 ---
        self._operation_combo: QComboBox = QComboBox()
        for label, data in self._OPERATION_ITEMS:
            self._operation_combo.addItem(label, data)

        # --- 主输入图层 ---
        self._input_layer_label: QLabel = QLabel("主输入图层")
        self._input_layer_combo: QComboBox = QComboBox()

        # --- 叠加图层 ---
        self._overlay_layer_label: QLabel = QLabel("叠加图层")
        self._overlay_layer_combo: QComboBox = QComboBox()

        # --- 几何叠加选项 ---
        self._keep_geom_type_check: QCheckBox = QCheckBox("仅保留与输入几何类型相同的结果要素")
        self._keep_geom_type_check.setChecked(True)
        self._make_valid_check: QCheckBox = QCheckBox("计算前自动修复无效几何")
        self._make_valid_check.setChecked(True)

        # --- 空间连接选项 ---
        self._predicate_label: QLabel = QLabel("空间谓词")
        self._predicate_combo: QComboBox = QComboBox()
        for label, data in self._PREDICATE_ITEMS:
            self._predicate_combo.addItem(label, data)

        self._sjoin_how_label: QLabel = QLabel("连接方式")
        self._sjoin_how_combo: QComboBox = QComboBox()
        for label, data in self._SJOIN_HOW_ITEMS:
            self._sjoin_how_combo.addItem(label, data)

        # --- 输出 ---
        self._output_name_edit: QLineEdit = QLineEdit()
        self._output_path_edit: QLineEdit = QLineEdit()
        self._output_path_edit.setPlaceholderText("默认保存到主输入图层所在目录")
        self._output_path_edit.textEdited.connect(self._mark_output_path_manual)
        browse_button: QPushButton = QPushButton("浏览…")
        browse_button.clicked.connect(self._browse_output_path)
        output_path_widget: QWidget = QWidget()
        output_path_layout: QHBoxLayout = QHBoxLayout(output_path_widget)
        output_path_layout.setContentsMargins(0, 0, 0, 0)
        output_path_layout.addWidget(self._output_path_edit, 1)
        output_path_layout.addWidget(browse_button)

        # --- 提示 ---
        self._hint_label: QLabel = QLabel()
        self._hint_label.setWordWrap(True)
        self._hint_label.setObjectName("overlayAnalysisHint")

        # --- 布局 ---
        form_layout: QFormLayout = QFormLayout()
        form_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form_layout.addRow("叠加类型", self._operation_combo)
        form_layout.addRow(self._input_layer_label, self._input_layer_combo)
        form_layout.addRow(self._overlay_layer_label, self._overlay_layer_combo)
        form_layout.addRow(self._predicate_label, self._predicate_combo)
        form_layout.addRow(self._sjoin_how_label, self._sjoin_how_combo)
        form_layout.addRow("输出图层名", self._output_name_edit)
        form_layout.addRow("输出位置", output_path_widget)
        form_layout.addRow(QLabel(""), self._keep_geom_type_check)
        form_layout.addRow(QLabel(""), self._make_valid_check)

        button_box: QDialogButtonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._accept_request)
        button_box.rejected.connect(self.reject)

        layout: QVBoxLayout = QVBoxLayout(self)
        layout.addLayout(form_layout)
        layout.addWidget(self._hint_label)
        layout.addWidget(button_box)

        # --- 信号连接 ---
        self._operation_combo.currentIndexChanged.connect(self._on_operation_changed)
        self._input_layer_combo.currentIndexChanged.connect(self._on_input_layer_changed)
        self._output_name_edit.textChanged.connect(self._on_output_name_changed)

        # --- 初始化 ---
        self._on_operation_changed(0)

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------

    def request(self) -> OverlayRequest:
        """返回当前控件内容构造的叠加分析请求。

        返回:
            不可变叠加分析请求。
        """
        operation: OverlayOperationName = cast(
            OverlayOperationName,
            str(self._operation_combo.currentData()),
        )
        output_path_text: str = self._output_path_edit.text().strip()
        if not output_path_text:
            raise ApplicationError("请选择叠加分析输出位置。")

        is_geometric: bool = operation in _GEOMETRIC_OVERLAY_OPS

        return OverlayRequest(
            input_layer_id=str(self._input_layer_combo.currentData()),
            overlay_layer_id=str(self._overlay_layer_combo.currentData()),
            operation=operation,
            output_path=self._with_output_suffix(Path(output_path_text)),
            output_layer_name=self._output_name_edit.text().strip(),
            keep_geom_type=self._keep_geom_type_check.isChecked() if is_geometric else True,
            make_valid=self._make_valid_check.isChecked() if is_geometric else True,
            sjoin_predicate=cast(SJoinPredicateName, str(self._predicate_combo.currentData()))
            if not is_geometric
            else "intersects",
            sjoin_how=cast(SJoinHowName, str(self._sjoin_how_combo.currentData()))
            if not is_geometric
            else "inner",
        )

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _accept_request(self) -> None:
        """校验对话框参数后关闭窗口；错误保留在窗口中供用户修正。"""
        try:
            self.request()
        except (ApplicationError, ValueError) as error:
            QMessageBox.warning(self, "叠加分析参数无效", str(error))
            return
        self.accept()

    def _on_operation_changed(self, index: int) -> None:
        """切换叠加操作类型时刷新图层过滤和可选控件。"""
        if index < 0:
            return
        operation: str = str(self._operation_combo.currentData())
        is_geometric: bool = operation in _GEOMETRIC_OVERLAY_OPS
        is_spatial_join: bool = operation in _SPATIAL_JOIN_OPS

        # 更新图层标签和过滤
        if operation == "point_in_polygon":
            self._input_layer_label.setText("点图层")
            self._overlay_layer_label.setText("面图层")
        elif operation == "line_in_polygon":
            self._input_layer_label.setText("线图层")
            self._overlay_layer_label.setText("面图层")
        else:
            self._input_layer_label.setText("主输入图层（面）")
            self._overlay_layer_label.setText("叠加图层（面）")

        self._populate_layer_combos(operation)

        # 显示/隐藏控件
        self._set_row_visible(QLabel(""), self._keep_geom_type_check, is_geometric)
        self._set_row_visible(QLabel(""), self._make_valid_check, is_geometric)
        self._set_row_visible(self._predicate_label, self._predicate_combo, is_spatial_join)
        self._set_row_visible(self._sjoin_how_label, self._sjoin_how_combo, is_spatial_join)

        # 更新提示
        self._hint_label.setText(self._HINTS.get(operation, ""))

        # 更新输出名称
        self._update_default_output_name()

    def _on_input_layer_changed(self, index: int) -> None:
        """切换输入图层时刷新默认输出名称和路径。"""
        if index < 0:
            return
        self._output_path_auto = True
        self._update_default_output_name()
        self._update_default_output_path()

    def _on_output_name_changed(self) -> None:
        """用户仍使用默认路径时，随输出图层名同步文件名。"""
        if self._output_path_auto:
            self._update_default_output_path()

    def _mark_output_path_manual(self) -> None:
        """记录用户手动编辑了输出位置，之后不再被名称变化覆盖。"""
        self._output_path_auto = False

    def _populate_layer_combos(self, operation: str) -> None:
        """根据操作类型动态过滤两个图层选择器的候选图层。

        参数:
            operation: 叠加操作类型。
        """
        # 保存当前选择
        prev_input_id: str | None = (
            str(self._input_layer_combo.currentData())
            if self._input_layer_combo.currentIndex() >= 0
            else None
        )
        prev_overlay_id: str | None = (
            str(self._overlay_layer_combo.currentData())
            if self._overlay_layer_combo.currentIndex() >= 0
            else None
        )

        if operation in _GEOMETRIC_OVERLAY_OPS:
            input_candidates = [
                layer for layer in self._vector_layers
                if layer.geometry_family == GeometryFamily.POLYGON
            ]
            overlay_candidates = input_candidates
        elif operation == "point_in_polygon":
            input_candidates = [
                layer for layer in self._vector_layers
                if layer.geometry_family == GeometryFamily.POINT
            ]
            overlay_candidates = [
                layer for layer in self._vector_layers
                if layer.geometry_family == GeometryFamily.POLYGON
            ]
        elif operation == "line_in_polygon":
            input_candidates = [
                layer for layer in self._vector_layers
                if layer.geometry_family == GeometryFamily.LINE
            ]
            overlay_candidates = [
                layer for layer in self._vector_layers
                if layer.geometry_family == GeometryFamily.POLYGON
            ]
        else:
            input_candidates = list(self._vector_layers)
            overlay_candidates = list(self._vector_layers)

        # 重新填充
        self._input_layer_combo.blockSignals(True)
        self._input_layer_combo.clear()
        for layer in input_candidates:
            geom_label: str = self._GEOMETRY_LABELS.get(
                (layer.geometry_family or GeometryFamily.MIXED), "未知"
            )
            self._input_layer_combo.addItem(f"{layer.name}  [{geom_label}]", layer.layer_id)
        if prev_input_id and any(
            str(layer.layer_id) == prev_input_id for layer in input_candidates
        ):
            for i in range(self._input_layer_combo.count()):
                if str(self._input_layer_combo.itemData(i)) == prev_input_id:
                    self._input_layer_combo.setCurrentIndex(i)
                    break
        self._input_layer_combo.blockSignals(False)

        self._overlay_layer_combo.blockSignals(True)
        self._overlay_layer_combo.clear()
        for layer in overlay_candidates:
            geom_label = self._GEOMETRY_LABELS.get(
                (layer.geometry_family or GeometryFamily.MIXED), "未知"
            )
            self._overlay_layer_combo.addItem(f"{layer.name}  [{geom_label}]", layer.layer_id)
        if prev_overlay_id and any(
            str(layer.layer_id) == prev_overlay_id for layer in overlay_candidates
        ):
            for i in range(self._overlay_layer_combo.count()):
                if str(self._overlay_layer_combo.itemData(i)) == prev_overlay_id:
                    self._overlay_layer_combo.setCurrentIndex(i)
                    break
        self._overlay_layer_combo.blockSignals(False)

        # 确保两个图层选择不同
        if (
            self._input_layer_combo.count() > 1
            and self._input_layer_combo.currentData() == self._overlay_layer_combo.currentData()
        ):
            next_index: int = 1 if self._overlay_layer_combo.currentIndex() == 0 else 0
            self._overlay_layer_combo.setCurrentIndex(next_index)

    def _update_default_output_name(self) -> None:
        """根据操作类型和输入图层自动生成输出图层名。"""
        if self._input_layer_combo.count() == 0:
            return
        input_name: str = self._input_layer_combo.currentText().split("  [")[0]
        operation: str = str(self._operation_combo.currentData())
        label: str = operation_label(cast(OverlayOperationName, operation))
        self._output_name_edit.setText(f"{input_name}_{label}")

    def _update_default_output_path(self) -> None:
        """把默认输出文件放到主输入图层所在目录。"""
        input_id: str = str(self._input_layer_combo.currentData())
        source_directory: Path = Path.cwd()
        for layer in self._vector_layers:
            if str(layer.layer_id) == input_id:
                if layer.layer.source_path is not None:
                    source_directory = layer.layer.source_path.parent
                break
        output_name: str = self._output_name_edit.text().strip() or "overlay_result"
        output_stem: str = self._safe_filename_stem(output_name)
        self._output_path_edit.setText(
            str((source_directory / f"{output_stem}.geojson").resolve())
        )

    def _browse_output_path(self) -> None:
        """选择叠加分析结果写出路径并自动补充所选格式后缀。"""
        suggested_name: str = self._output_name_edit.text().strip() or "overlay_result"
        current_path: str = self._output_path_edit.text().strip()
        suggested_path: str = current_path or suggested_name
        if self._output_path_auto and current_path:
            suggested_path = str(Path(current_path).with_suffix(""))
        path_string, selected_filter = QFileDialog.getSaveFileName(
            self,
            "保存叠加分析结果",
            suggested_path,
            "GeoJSON (*.geojson);;Shapefile (*.shp);;GeoPackage (*.gpkg)",
        )
        if not path_string:
            return
        self._output_path_auto = False
        self._output_path_edit.setText(
            str(self._with_output_suffix(Path(path_string), selected_filter))
        )

    # ------------------------------------------------------------------
    # 静态工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _set_row_visible(label: QLabel, widget: QWidget, visible: bool) -> None:
        """同步隐藏标签和控件，避免留下空白表单行。"""
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
        return safe_name or "overlay_result"

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
