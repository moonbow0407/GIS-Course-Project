"""图层属性只读对话框。"""

from pyproj import CRS
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.application.crs_utils import crs_equivalent
from app.application.results import LayerSnapshot
from app.domain.layer_style import GeometryFamily
from app.domain.vector_layer import VectorLayer

_GEOMETRY_LABELS: dict[GeometryFamily, str] = {
    GeometryFamily.POINT: "点",
    GeometryFamily.LINE: "线",
    GeometryFamily.POLYGON: "面",
    GeometryFamily.MIXED: "混合几何",
}


class LayerPropertiesDialog(QDialog):
    """展示图层属性，并提供工程内定义/修正 CRS 的入口。"""

    crs_definition_requested = Signal(str)
    reprojection_requested = Signal(str)
    raster_resampling_requested = Signal(str, str)

    def __init__(
        self,
        layer_snapshot: LayerSnapshot,
        display_crs: CRS | None,
        parent: QWidget | None = None,
    ) -> None:
        """使用工作区快照构造属性窗口。"""
        super().__init__(parent)
        self.setObjectName("layerPropertiesDialog")
        self.setWindowTitle(f"图层属性 · {layer_snapshot.name}")
        self.setMinimumSize(660, 520)
        self._values: dict[str, str] = {}
        self._create_ui(layer_snapshot, display_crs)

    def property_value(self, key: str) -> str | None:
        """返回指定属性的展示文本，便于界面自动化检查。"""
        return self._values.get(key)

    def _create_ui(
        self,
        layer_snapshot: LayerSnapshot,
        display_crs: CRS | None,
    ) -> None:
        """创建概览、数据源与数据内容三个只读页签。"""
        tabs: QTabWidget = QTabWidget()
        tabs.setObjectName("layerPropertiesTabs")
        tabs.addTab(self._create_overview_tab(layer_snapshot, display_crs), "概览")
        tabs.addTab(self._create_source_tab(layer_snapshot), "数据源与关联")
        tabs.addTab(self._create_content_tab(layer_snapshot), "数据内容")

        button_box: QDialogButtonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Close
        )
        button_box.rejected.connect(self.reject)
        close_button = button_box.button(QDialogButtonBox.StandardButton.Close)
        if close_button is not None:
            close_button.clicked.connect(self.close)

        define_crs_button: QPushButton = QPushButton("定义/修正 CRS")
        define_crs_button.setObjectName("defineLayerCrsButton")
        define_crs_button.clicked.connect(
            lambda: self.crs_definition_requested.emit(layer_snapshot.layer_id)
        )
        reproject_button: QPushButton = QPushButton("重投影为新图层")
        reproject_button.setObjectName("reprojectLayerButton")
        reproject_button.clicked.connect(
            lambda: self.reprojection_requested.emit(layer_snapshot.layer_id)
        )

        layout: QVBoxLayout = QVBoxLayout(self)
        layout.addWidget(tabs, 1)
        layout.addWidget(define_crs_button)
        layout.addWidget(reproject_button)
        layout.addWidget(button_box)

    def _create_overview_tab(
        self,
        snapshot: LayerSnapshot,
        display_crs: CRS | None,
    ) -> QWidget:
        """创建图层标识、显示状态和空间参考信息。"""
        tab: QWidget = QWidget()
        layout: QVBoxLayout = QVBoxLayout(tab)

        basic_form: QFormLayout = self._new_form()
        self._add_value(basic_form, "名称", "name", snapshot.name)
        self._add_value(
            basic_form,
            "图层类型",
            "layer_type",
            "栅格图层" if snapshot.is_raster else "矢量图层",
        )
        self._add_value(basic_form, "图层编号", "layer_id", snapshot.layer_id)
        self._add_value(basic_form, "可见状态", "visible", "可见" if snapshot.visible else "隐藏")
        self._add_value(basic_form, "透明度", "opacity", f"{snapshot.opacity:.0%}")
        self._add_value(basic_form, "混合模式", "blend_mode", snapshot.blend_mode)
        self._add_value(
            basic_form,
            "显示比例范围",
            "scale_range",
            self._format_scale_range(snapshot.min_scale_percent, snapshot.max_scale_percent),
        )
        layout.addWidget(self._group("基本信息", basic_form))

        spatial_form: QFormLayout = self._new_form()
        layer_crs: CRS | None = snapshot.layer.crs
        self._add_value(spatial_form, "当前坐标系", "crs", self._format_crs(layer_crs))
        self._add_value(
            spatial_form,
            "地图显示坐标系",
            "display_crs",
            self._format_crs(display_crs),
        )
        self._add_value(
            spatial_form,
            "坐标系关系",
            "crs_relation",
            self._format_crs_relation(layer_crs, display_crs),
        )
        self._add_value(spatial_form, "空间范围", "bounds", self._format_bounds(snapshot.bounds))
        layout.addWidget(self._group("空间参考", spatial_form))
        layout.addStretch()
        return tab

    def _create_source_tab(self, snapshot: LayerSnapshot) -> QWidget:
        """创建本地文件、容器子图层和数据库关联信息。"""
        tab: QWidget = QWidget()
        layout: QVBoxLayout = QVBoxLayout(tab)
        form: QFormLayout = self._new_form()
        layer = snapshot.layer

        database_layer_id: int | None = (
            layer.database_layer_id if isinstance(layer, VectorLayer) else None
        )
        if database_layer_id is not None:
            source_type: str = "PostgreSQL / PostGIS 数据库"
        elif layer.source_path is not None:
            source_type = "本地文件"
        else:
            source_type = "内存图层（未关联外部数据源）"

        self._add_value(form, "数据源类型", "source_type", source_type)
        self._add_value(
            form,
            "源文件",
            "source_path",
            str(layer.source_path) if layer.source_path is not None else "未关联本地文件",
        )
        source_layer_name: str | None = (
            layer.source_layer_name if isinstance(layer, VectorLayer) else None
        )
        self._add_value(
            form,
            "容器子图层",
            "source_layer_name",
            source_layer_name or "不适用",
        )
        self._add_value(
            form,
            "数据库图层 ID",
            "database_layer_id",
            str(database_layer_id) if database_layer_id is not None else "未关联",
        )
        self._add_value(
            form,
            "地图文档关联",
            "workspace_relation",
            f"工作区图层 {snapshot.layer_id}",
        )
        layout.addWidget(self._group("数据源与关联", form))
        layout.addStretch()
        return tab

    def _create_content_tab(self, snapshot: LayerSnapshot) -> QWidget:
        """按矢量或栅格类型创建数据内容摘要。"""
        tab: QWidget = QWidget()
        layout: QVBoxLayout = QVBoxLayout(tab)
        form: QFormLayout = self._new_form()
        layer = snapshot.layer
        if isinstance(layer, VectorLayer):
            geometry_label: str = (
                _GEOMETRY_LABELS.get(layer.geometry_family, "未知")
                if layer.geometry_family is not None
                else "未知"
            )
            field_names: set[str] = {
                name for feature in layer.features for name in feature.attributes
            }
            self._add_value(form, "几何类型", "geometry_type", geometry_label)
            self._add_value(form, "要素数量", "feature_count", str(len(layer.features)))
            self._add_value(form, "字段数量", "field_count", str(len(field_names)))
            self._add_value(
                form,
                "已选要素",
                "selected_feature_count",
                str(len(snapshot.selected_feature_ids)),
            )
            title: str = "矢量数据"
        else:
            height, width = layer.raster_shape
            self._add_value(form, "波段数量", "band_count", str(layer.band_count))
            self._add_value(form, "栅格尺寸", "raster_size", f"{width} × {height} 像元")
            self._add_value(
                form,
                "像元大小",
                "pixel_size",
                f"{abs(layer.transform.a):.8g} × {abs(layer.transform.e):.8g}",
            )
            self._add_value(
                form,
                "NoData",
                "nodata",
                str(layer.nodata) if layer.nodata is not None else "未设置",
            )
            resampling_combo: QComboBox = QComboBox()
            resampling_combo.addItem("自动（分类最近邻 / 连续双线性）", "")
            resampling_combo.addItem("最近邻", "nearest")
            resampling_combo.addItem("双线性", "bilinear")
            resampling_combo.addItem("三次卷积", "cubic")
            resampling_combo.addItem("平均值", "average")
            resampling_combo.addItem("众数", "mode")
            current_resampling: str = snapshot.raster_display_resampling or ""
            current_index: int = resampling_combo.findData(current_resampling)
            resampling_combo.setCurrentIndex(max(0, current_index))
            resampling_combo.currentIndexChanged.connect(
                lambda _index: self.raster_resampling_requested.emit(
                    snapshot.layer_id,
                    str(resampling_combo.currentData() or ""),
                )
            )
            form.addRow("显示重采样", resampling_combo)
            title = "栅格数据"
        layout.addWidget(self._group(title, form))
        layout.addStretch()
        return tab

    def _add_value(self, form: QFormLayout, label: str, key: str, value: str) -> None:
        """向表单添加可选择复制的只读文本。"""
        field: QLineEdit = QLineEdit(value)
        field.setObjectName(f"layerProperty_{key}")
        field.setReadOnly(True)
        field.setCursorPosition(0)
        field.setToolTip(value)
        form.addRow(label, field)
        self._values[key] = value

    @staticmethod
    def _new_form() -> QFormLayout:
        """创建字段可横向扩展的统一只读表单。"""
        form: QFormLayout = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        return form

    @staticmethod
    def _group(title: str, form: QFormLayout) -> QGroupBox:
        """使用给定表单创建属性分组。"""
        group: QGroupBox = QGroupBox(title)
        group.setLayout(form)
        return group

    @staticmethod
    def _format_crs(crs: CRS | None) -> str:
        """格式化坐标系权威编号和名称。"""
        if crs is None:
            return "未设置"
        authority: tuple[str, str] | None = crs.to_authority()
        if authority is None:
            return crs.name or crs.to_string()
        return f"{authority[0]}:{authority[1]} · {crs.name}"

    @staticmethod
    def _format_crs_relation(layer_crs: CRS | None, display_crs: CRS | None) -> str:
        """说明图层坐标系与地图显示坐标系的关系。"""
        if layer_crs is None and display_crs is None:
            return "图层与地图均未设置坐标系"
        if layer_crs is None:
            return "图层未设置坐标系"
        if display_crs is None:
            return "地图未设置显示坐标系"
        return (
            "与地图显示坐标系一致"
            if crs_equivalent(layer_crs, display_crs)
            else "与地图显示坐标系不一致"
        )

    @staticmethod
    def _format_bounds(bounds: tuple[float, float, float, float]) -> str:
        """格式化最小和最大坐标。"""
        minimum_x, minimum_y, maximum_x, maximum_y = bounds
        return (
            f"{minimum_x:.8g}, {minimum_y:.8g}  —  "
            f"{maximum_x:.8g}, {maximum_y:.8g}"
        )

    @staticmethod
    def _format_scale_range(minimum: float | None, maximum: float | None) -> str:
        """格式化图层可见比例范围。"""
        if minimum is None and maximum is None:
            return "不限制"
        minimum_text: str = f"{minimum:g}%" if minimum is not None else "不限"
        maximum_text: str = f"{maximum:g}%" if maximum is not None else "不限"
        return f"{minimum_text} — {maximum_text}"
