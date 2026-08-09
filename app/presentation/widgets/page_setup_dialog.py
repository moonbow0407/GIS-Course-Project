"""页面设置对话框 —— 配置布局视图的纸张大小、方向、DPI 和页边距。"""

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from app.domain.layout import _PAPER_SIZES_MM, LayoutPage, PageOrientation


class PageSetupDialog(QDialog):
    """页面设置对话框。

    参数:
        current_page: 当前纸张规格，用于初始化对话框。
        parent: 父组件。
    """

    def __init__(
        self,
        current_page: LayoutPage,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("页面设置")
        self._current_page = current_page
        self._result_page: LayoutPage | None = None
        self._create_ui()
        self._load_current()

    def _create_ui(self) -> None:
        """创建对话框界面。"""
        main_layout: QVBoxLayout = QVBoxLayout(self)

        form: QFormLayout = QFormLayout()

        self._paper_combo: QComboBox = QComboBox()
        for name in _PAPER_SIZES_MM:
            self._paper_combo.addItem(name)
        form.addRow("纸张大小:", self._paper_combo)

        self._orientation_combo: QComboBox = QComboBox()
        self._orientation_combo.addItem("纵向", PageOrientation.PORTRAIT)
        self._orientation_combo.addItem("横向", PageOrientation.LANDSCAPE)
        form.addRow("方向:", self._orientation_combo)

        self._dpi_spin: QDoubleSpinBox = QDoubleSpinBox()
        self._dpi_spin.setRange(72.0, 600.0)
        self._dpi_spin.setDecimals(0)
        self._dpi_spin.setSuffix(" DPI")
        form.addRow("分辨率:", self._dpi_spin)

        self._margin_spin: QDoubleSpinBox = QDoubleSpinBox()
        self._margin_spin.setRange(0.0, 50.0)
        self._margin_spin.setDecimals(1)
        self._margin_spin.setSuffix(" mm")
        form.addRow("页边距:", self._margin_spin)

        self._preview_label: QLabel = QLabel()
        form.addRow("预览:", self._preview_label)

        main_layout.addLayout(form)

        # 信号
        self._paper_combo.currentIndexChanged.connect(self._update_preview)
        self._orientation_combo.currentIndexChanged.connect(self._update_preview)
        self._dpi_spin.valueChanged.connect(self._update_preview)
        self._margin_spin.valueChanged.connect(self._update_preview)

        # 按钮
        from PySide6.QtWidgets import QDialogButtonBox

        button_box: QDialogButtonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)

    def _load_current(self) -> None:
        """从当前页面加载值。"""
        page = self._current_page
        index = self._paper_combo.findText(page.name)
        if index >= 0:
            self._paper_combo.setCurrentIndex(index)
        orient_index = self._orientation_combo.findData(page.orientation)
        if orient_index >= 0:
            self._orientation_combo.setCurrentIndex(orient_index)
        self._dpi_spin.setValue(page.dpi)
        self._margin_spin.setValue(page.margin_mm)
        self._update_preview()

    def _update_preview(self) -> None:
        """更新尺寸预览。"""
        name = self._paper_combo.currentText()
        orientation = self._orientation_combo.currentData()
        w, h = _PAPER_SIZES_MM.get(name, (210.0, 297.0))
        if orientation == PageOrientation.LANDSCAPE:
            w, h = h, w
        margin = self._margin_spin.value()
        self._preview_label.setText(
            f"{w:.0f} x {h:.0f} mm，可打印 {w - 2 * margin:.0f} x {h - 2 * margin:.0f} mm"
        )

    def _on_accept(self) -> None:
        """确认并构建结果。"""
        name = self._paper_combo.currentText()
        orientation = self._orientation_combo.currentData()
        self._result_page = LayoutPage.from_preset(
            name=name,
            orientation=orientation,
            dpi=self._dpi_spin.value(),
            margin_mm=self._margin_spin.value(),
        )
        self.accept()

    def page(self) -> LayoutPage | None:
        """返回用户配置的新纸张规格。"""
        return self._result_page
