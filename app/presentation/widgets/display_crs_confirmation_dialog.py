"""首图层地图显示坐标系确认对话框。"""

from pyproj import CRS
from pyproj.exceptions import CRSError
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)


class DisplayCrsConfirmationDialog(QDialog):
    """让用户保留首图层 CRS，或选择独立的地图显示 CRS。"""

    _RECOMMENDATIONS: tuple[tuple[str, str], ...] = (
        ("EPSG:4490", "EPSG:4490 · 中国范围数据"),
        ("EPSG:4326", "EPSG:4326 · 全球通用"),
        ("EPSG:3857", "EPSG:3857 · 网络底图"),
    )
    _EXPLANATION = "该选择只设置地图显示 CRS，不会修改图层自身 CRS 和坐标值。"

    def __init__(
        self,
        source_crs: CRS,
        reason: str,
        parent: QWidget | None = None,
    ) -> None:
        """创建包含固定推荐项和自定义输入的确认框。"""
        super().__init__(parent)
        self._source_crs = source_crs
        self.setWindowTitle("确认地图显示坐标系")
        self.setMinimumWidth(560)

        reason_label = QLabel(reason)
        reason_label.setWordWrap(True)
        explanation_label = QLabel(self._EXPLANATION)
        explanation_label.setWordWrap(True)

        self._choice = QComboBox()
        self._choice.addItem(
            f"继续使用首图层 CRS · {self._format_crs(source_crs)}",
            "SOURCE",
        )
        for code, label in self._RECOMMENDATIONS:
            self._choice.addItem(label, code)
        self._choice.addItem("自定义显示 CRS…", "CUSTOM")

        self._custom_edit = QLineEdit()
        self._custom_edit.setPlaceholderText("输入 EPSG、PROJ 或 WKT")
        self._custom_edit.setEnabled(False)
        self._choice.currentIndexChanged.connect(self._on_choice_changed)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(reason_label)
        layout.addWidget(explanation_label)
        layout.addWidget(self._choice)
        layout.addWidget(self._custom_edit)
        layout.addWidget(buttons)

    def selected_crs(self) -> CRS | None:
        """返回当前选择的地图显示 CRS；自定义输入无效时返回空值。"""
        code = str(self._choice.currentData())
        if code == "SOURCE":
            return self._source_crs
        text = self._custom_edit.text().strip() if code == "CUSTOM" else code
        if not text:
            return None
        try:
            return CRS.from_user_input(text)
        except CRSError:
            return None

    def set_custom_text(self, text: str) -> None:
        """切换到自定义项并填写 CRS，供交互恢复和测试使用。"""
        self._choice.setCurrentIndex(self._choice.count() - 1)
        self._custom_edit.setText(text)

    def option_codes(self) -> tuple[str, ...]:
        """返回当前选项代码，供界面行为测试使用。"""
        return tuple(str(self._choice.itemData(index)) for index in range(self._choice.count()))

    def explanation_text(self) -> str:
        """返回显示 CRS 与图层 CRS 的关系说明。"""
        return self._EXPLANATION

    def _on_choice_changed(self, _index: int) -> None:
        """只在自定义模式启用输入框。"""
        is_custom = str(self._choice.currentData()) == "CUSTOM"
        self._custom_edit.setEnabled(is_custom)
        if is_custom:
            self._custom_edit.setFocus()

    @staticmethod
    def _format_crs(crs: CRS) -> str:
        """优先返回权威代码，并附加坐标系名称。"""
        authority = crs.to_authority()
        code = f"{authority[0]}:{authority[1]}" if authority is not None else crs.to_string()
        return f"{code} · {crs.name}" if crs.name else code
