"""可复用的坐标参考系统选择控件。"""

from pyproj import CRS
from pyproj.exceptions import CRSError
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QWidget,
)

# 预设坐标系列表（标识字符串 → 显示标签，名称从 pyproj 动态获取）。
_PRESETS: tuple[tuple[str, str], ...] = (
    ("EPSG:4326", "WGS 84"),
    ("EPSG:3857", "Web Mercator"),
    ("EPSG:4490", "CGCS2000"),
    ("EPSG:4549", "CGCS2000 / 3-degree Gauss-Kruger CM 120E"),
    ("EPSG:4527", "CGCS2000 / 3-degree Gauss-Kruger zone 39"),
    ("ESRI:102026", "Asia North Equidistant Conic"),
)

_CUSTOM_INDEX: int = len(_PRESETS)


def _make_preset_label(auth_code: str, friendly: str) -> str:
    """组合权威代码和友好名称，同时尝试从 pyproj 获取官方名称。"""
    try:
        crs_obj: CRS = CRS.from_user_input(auth_code)
        if crs_obj.name and crs_obj.name != "unknown":
            return f"{friendly} ({auth_code}) — {crs_obj.name}"
    except CRSError:
        pass
    return f"{friendly} ({auth_code})"


class CrsSelectWidget(QWidget):
    """预设下拉 + 自定义输入的坐标系选择控件。

    选择预设时自动填充并禁用文本输入；选择"自定义"时启用手动输入。
    """

    # CRS 文本发生变化时发射。
    crs_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建坐标系选择控件。"""
        super().__init__(parent)

        self._combo: QComboBox = QComboBox()
        for auth_code, friendly in _PRESETS:
            self._combo.addItem(_make_preset_label(auth_code, friendly), auth_code)
        self._combo.addItem("自定义…", "")
        self._combo.setCurrentIndex(_CUSTOM_INDEX)

        self._edit: QLineEdit = QLineEdit()

        layout: QHBoxLayout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._combo, 1)
        layout.addWidget(self._edit, 2)

        self._combo.currentIndexChanged.connect(self._on_combo_changed)

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def crs(self) -> CRS | None:
        """解析当前输入并返回 CRS 对象，无效时返回 None。"""
        text: str = self.crs_text()
        if not text:
            return None
        try:
            return CRS.from_user_input(text)
        except CRSError:
            return None

    def crs_text(self) -> str:
        """返回当前坐标系文本。"""
        data: str = str(self._combo.currentData())
        if data:
            return data
        return self._edit.text().strip()

    def set_crs(self, crs: CRS | None) -> None:
        """根据 CRS 对象设置控件当前值。"""
        if crs is None:
            self._combo.setCurrentIndex(_CUSTOM_INDEX)
            self._edit.clear()
            return
        try:
            auth_code: str | None = crs.to_authority()
            if auth_code is not None:
                code: str = f"{auth_code[0]}:{auth_code[1]}"
                for i in range(len(_PRESETS)):
                    if str(self._combo.itemData(i)) == code:
                        self._combo.setCurrentIndex(i)
                        return
        except (CRSError, AttributeError):
            pass
        # 不在预设中，切到自定义模式。
        self._combo.setCurrentIndex(_CUSTOM_INDEX)
        self._edit.setText(crs.to_string())

    def set_placeholder(self, hint: str) -> None:
        """设置自定义输入框的占位提示文字。"""
        self._edit.setPlaceholderText(hint)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _on_combo_changed(self, index: int) -> None:
        """下拉选择变化时同步文本框状态。"""
        data: str = str(self._combo.currentData())
        if data:
            # 预设模式：填充并禁用编辑。
            self._edit.setText(data)
            self._edit.setEnabled(False)
        else:
            # 自定义模式：清空并启用编辑。
            self._edit.clear()
            self._edit.setEnabled(True)
            self._edit.setFocus()
        self.crs_changed.emit()
