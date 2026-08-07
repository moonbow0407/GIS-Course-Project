"""新建空白图层对话框。"""

from pyproj import CRS
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

from app.domain.layer_style import GeometryFamily
from app.presentation.widgets.crs_select_widget import CrsSelectWidget


_GEOMETRY_ITEMS: tuple[tuple[str, GeometryFamily], ...] = (
    ("点 (Point)", GeometryFamily.POINT),
    ("线 (Polyline)", GeometryFamily.LINE),
    ("面 (Polygon)", GeometryFamily.POLYGON),
)


class NewLayerDialog(QDialog):
    """收集图层名称、几何类型和坐标系的新建图层参数窗口。"""

    def __init__(
        self,
        display_crs: CRS | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """构造新建图层参数窗口。

        参数:
            display_crs: 当前地图显示坐标系，用于提示信息。
            parent: 父窗口。
        """
        super().__init__(parent)
        self.setWindowTitle("新建空白图层")
        self.setMinimumWidth(420)

        # --- 图层名称 ---
        self._name_edit: QLineEdit = QLineEdit("新建图层")

        # --- 几何类型 ---
        self._geometry_combo: QComboBox = QComboBox()
        for label, family in _GEOMETRY_ITEMS:
            self._geometry_combo.addItem(label, family)

        # --- 坐标系 ---
        crs_hint: str = display_crs.to_string() if display_crs is not None else "未设置"
        self._crs_widget: CrsSelectWidget = CrsSelectWidget()
        self._crs_widget.set_placeholder(f"留空使用地图坐标系（当前：{crs_hint}）")
        if display_crs is not None:
            self._crs_widget.set_crs(display_crs)
        crs_tip: QLabel = QLabel(
            "可从预设坐标系中选择，或切换为自定义输入 EPSG 编号、PROJ 字符串或 WKT；留空则使用当前地图坐标系。"
        )
        crs_tip.setWordWrap(True)
        crs_tip.setObjectName("newLayerCrsTip")

        # --- 布局 ---
        form_layout: QFormLayout = QFormLayout()
        form_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form_layout.addRow("图层名称", self._name_edit)
        form_layout.addRow("几何类型", self._geometry_combo)
        form_layout.addRow("坐标系", self._crs_widget)

        button_box: QDialogButtonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._accept)
        button_box.rejected.connect(self.reject)

        layout: QVBoxLayout = QVBoxLayout(self)
        layout.addLayout(form_layout)
        layout.addWidget(crs_tip)
        layout.addWidget(button_box)

    # ------------------------------------------------------------------
    # 公共属性
    # ------------------------------------------------------------------

    def layer_name(self) -> str:
        """返回用户输入的图层名称。"""
        return self._name_edit.text().strip()

    def geometry_family(self) -> GeometryFamily:
        """返回用户选择的几何类型。"""
        return self._geometry_combo.currentData()

    def crs_text(self) -> str:
        """返回用户输入的坐标系文本，为空表示使用地图 CRS。"""
        return self._crs_widget.crs_text()

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _accept(self) -> None:
        """校验输入后关闭对话框。"""
        name: str = self.layer_name()
        if not name:
            QMessageBox.warning(self, "参数无效", "图层名称不能为空。")
            return

        crs_input: str = self.crs_text()
        if crs_input and self._crs_widget.crs() is None:
            QMessageBox.warning(
                self,
                "坐标系无效",
                f"无法识别坐标系输入：{crs_input}\n"
                "请使用 EPSG 编号（如 EPSG:4326）、PROJ 字符串或 WKT 格式。",
            )
            return

        self.accept()
