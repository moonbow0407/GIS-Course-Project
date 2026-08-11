"""捕捉设置对话框：容差、捕捉类型、图层范围。"""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)


def _snap_settings() -> QSettings:
    """延迟初始化 QSettings。"""
    return QSettings()


SNAP_GROUP: str = "GlobalDisplay/Snapping"

# 默认值。
DEFAULT_TOLERANCE: int = 12
DEFAULT_TYPES: str = "vertex,edge"
DEFAULT_ALL_LAYERS: bool = True


def load_snap_tolerance() -> float:
    """加载捕捉容差（像素）。"""
    raw: object = _snap_settings().value(
        f"{SNAP_GROUP}/tolerance", DEFAULT_TOLERANCE
    )
    try:
        return max(1.0, min(float(str(raw)), 100.0))
    except (ValueError, TypeError):
        return float(DEFAULT_TOLERANCE)


def save_snap_tolerance(value: float) -> None:
    """持久化捕捉容差。"""
    s = _snap_settings()
    s.setValue(f"{SNAP_GROUP}/tolerance", int(value))
    s.sync()


def load_snap_types() -> set[str]:
    """加载启用的捕捉类型。"""
    raw: object = _snap_settings().value(
        f"{SNAP_GROUP}/types", DEFAULT_TYPES
    )
    types: set[str] = set(str(raw).split(","))
    return types & {"vertex", "edge", "endpoint", "midpoint"} or {"vertex", "edge"}


def save_snap_types(types: set[str]) -> None:
    """持久化捕捉类型。"""
    s = _snap_settings()
    s.setValue(f"{SNAP_GROUP}/types", ",".join(sorted(types)))
    s.sync()


def load_snap_all_layers() -> bool:
    """加载图层范围设置。"""
    raw: object = _snap_settings().value(
        f"{SNAP_GROUP}/all_layers", DEFAULT_ALL_LAYERS
    )
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.lower() in ("true", "1")
    return DEFAULT_ALL_LAYERS


def save_snap_all_layers(value: bool) -> None:
    """持久化图层范围。"""
    s = _snap_settings()
    s.setValue(f"{SNAP_GROUP}/all_layers", value)
    s.sync()


class SnappingSettingsDialog(QDialog):
    """捕捉设置对话框。

    信号:
        settings_changed: 设置变更后发出，主窗口连接以更新引擎。
    """

    settings_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建捕捉设置对话框，从 QSettings 加载当前值。

        参数:
            parent: 父窗口，用于模态对话框居中。
        """
        super().__init__(parent)
        self.setWindowTitle("捕捉设置")
        self.setMinimumWidth(360)

        layout: QVBoxLayout = QVBoxLayout(self)

        # ── 容差组 ──
        tol_group: QGroupBox = QGroupBox("捕捉容差（像素）")
        tol_layout: QVBoxLayout = QVBoxLayout(tol_group)

        slider_layout: QHBoxLayout = QHBoxLayout()
        self._tol_slider: QSlider = QSlider(Qt.Orientation.Horizontal)
        self._tol_slider.setRange(1, 50)
        self._tol_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._tol_slider.setTickInterval(5)
        slider_layout.addWidget(self._tol_slider)

        self._tol_label: QLabel = QLabel()
        self._tol_label.setFixedWidth(50)
        self._tol_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        slider_layout.addWidget(self._tol_label)

        tol_layout.addLayout(slider_layout)
        layout.addWidget(tol_group)

        # ── 捕捉类型组 ──
        type_group: QGroupBox = QGroupBox("捕捉类型")
        type_layout: QVBoxLayout = QVBoxLayout(type_group)

        self._vertex_cb: QCheckBox = QCheckBox("顶点 (Vertex) — 吸附到几何顶点")
        self._edge_cb: QCheckBox = QCheckBox("边 (Edge) — 吸附到线段上的最近点")
        self._endpoint_cb: QCheckBox = QCheckBox("端点 (Endpoint) — 优先吸附到线段首尾")
        self._midpoint_cb: QCheckBox = QCheckBox("中点 (Midpoint) — 吸附到线段中点")

        type_layout.addWidget(self._vertex_cb)
        type_layout.addWidget(self._edge_cb)
        type_layout.addWidget(self._endpoint_cb)
        type_layout.addWidget(self._midpoint_cb)

        layout.addWidget(type_group)

        # ── 图层范围组 ──
        layer_group: QGroupBox = QGroupBox("捕捉图层")
        layer_layout: QVBoxLayout = QVBoxLayout(layer_group)

        self._all_layers_rb: QRadioButton = QRadioButton("所有可见图层")
        self._active_layer_rb: QRadioButton = QRadioButton("仅活动图层")
        layer_layout.addWidget(self._all_layers_rb)
        layer_layout.addWidget(self._active_layer_rb)

        layout.addWidget(layer_group)

        # ── 按钮 ──
        button_box: QDialogButtonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        # 从 QSettings 加载当前值。
        self._load_values()

        # 连接信号：滑块变化实时更新标签。
        self._tol_slider.valueChanged.connect(self._on_slider_changed)

    def _load_values(self) -> None:
        """从 QSettings 加载设置到控件。"""
        tol: int = int(load_snap_tolerance())
        self._tol_slider.setValue(tol)
        self._tol_label.setText(f"{tol} px")

        types: set[str] = load_snap_types()
        self._vertex_cb.setChecked("vertex" in types)
        self._edge_cb.setChecked("edge" in types)
        self._endpoint_cb.setChecked("endpoint" in types)
        self._midpoint_cb.setChecked("midpoint" in types)

        all_layers: bool = load_snap_all_layers()
        self._all_layers_rb.setChecked(all_layers)
        self._active_layer_rb.setChecked(not all_layers)

    def _on_slider_changed(self, value: int) -> None:
        """更新容差标签。"""
        self._tol_label.setText(f"{value} px")

    def _on_accept(self) -> None:
        """保存设置到 QSettings 并发出信号。"""
        tol: int = self._tol_slider.value()
        save_snap_tolerance(tol)

        types: set[str] = set()
        if self._vertex_cb.isChecked():
            types.add("vertex")
        if self._edge_cb.isChecked():
            types.add("edge")
        if self._endpoint_cb.isChecked():
            types.add("endpoint")
        if self._midpoint_cb.isChecked():
            types.add("midpoint")
        # 至少保留一种类型。
        if not types:
            types = {"vertex"}
        save_snap_types(types)

        all_layers: bool = self._all_layers_rb.isChecked()
        save_snap_all_layers(all_layers)

        self.settings_changed.emit()
        self.accept()
