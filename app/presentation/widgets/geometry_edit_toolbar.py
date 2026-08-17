"""地图画布内的要素编辑上下文栏。"""

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)


class GeometryEditToolbar(QWidget):
    """固定在地图顶部，呈现操作名、图层名、参数以及应用和取消入口。"""

    # 模式切换： "drag_vertex" / "delete_vertex"
    mode_changed = Signal(str)
    # 提交 / 取消 / 全选
    commit_requested = Signal()
    cancel_requested = Signal()
    select_all_requested = Signal()
    parameters_changed = Signal(dict)
    topology_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("geometryEditToolbar")
        # 独立顶层 QWidget 在深色系统主题下不会自动绘制 QSS 背景；显式
        # 启用样式背景，保证与应用内浅色对话框保持一致。
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWindowTitle("要素编辑")
        self.setMaximumHeight(88)
        if parent is not None:
            parent.installEventFilter(self)

        layout: QHBoxLayout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(6)

        self._title_label: QLabel = QLabel("要素编辑")
        self._title_label.setObjectName("editContextTitle")
        self._title_label.setWordWrap(False)
        self._title_label.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        self._context_label: QLabel = QLabel("")
        self._context_label.setObjectName("editContextDetail")
        self._context_label.setWordWrap(False)
        self._context_label.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        # 操作提示改到工具提示，避免与右侧模式按钮抢宽度。
        self._hint_label: QLabel = QLabel("")
        self._hint_label.setObjectName("editContextHint")
        self._hint_label.hide()
        self._full_layer_name: str = ""

        self._drag_btn: QPushButton = QPushButton("拖拽顶点")
        self._drag_btn.setCheckable(True)
        self._drag_btn.setChecked(True)
        self._drag_btn.clicked.connect(lambda: self._set_mode("drag_vertex"))

        self._delete_btn: QPushButton = QPushButton("删除顶点")
        self._delete_btn.setCheckable(True)
        self._delete_btn.clicked.connect(lambda: self._set_mode("delete_vertex"))

        self._select_all_btn: QPushButton = QPushButton("全选")
        self._select_all_btn.clicked.connect(self.select_all_requested.emit)

        self._topology_check: QCheckBox = QCheckBox("拓扑联动")
        self._topology_check.setChecked(False)
        self._topology_check.toggled.connect(self.topology_changed.emit)

        self._first_value: QDoubleSpinBox = QDoubleSpinBox()
        self._first_value.setDecimals(6)
        self._first_value.setRange(-1_000_000_000.0, 1_000_000_000.0)
        self._first_value.setSingleStep(1.0)
        self._first_value.valueChanged.connect(self._emit_parameters)
        self._second_value: QDoubleSpinBox = QDoubleSpinBox()
        self._second_value.setDecimals(6)
        self._second_value.setRange(-1_000_000_000.0, 1_000_000_000.0)
        self._second_value.setSingleStep(1.0)
        self._second_value.valueChanged.connect(self._emit_parameters)
        self._parameter_mode: str = ""

        self._commit_btn: QPushButton = QPushButton("✓ 应用")
        self._commit_btn.setObjectName("commitButton")
        self._commit_btn.clicked.connect(self.commit_requested.emit)

        self._cancel_btn: QPushButton = QPushButton("✗ 取消")
        self._cancel_btn.clicked.connect(self.cancel_requested.emit)

        layout.addWidget(self._title_label)
        layout.addWidget(self._context_label)
        layout.addWidget(self._drag_btn)
        layout.addWidget(self._delete_btn)
        layout.addWidget(self._select_all_btn)
        layout.addWidget(self._topology_check)
        layout.addWidget(self._first_value)
        layout.addWidget(self._second_value)
        layout.addWidget(self._commit_btn)
        layout.addWidget(self._cancel_btn)

        self._mode: str = "drag_vertex"
        self.configure(operation="vertex", layer_name="", fid=None, hint="拖拽顶点后应用")
        self.hide()

    def configure(
        self,
        *,
        operation: str,
        layer_name: str,
        fid: object,
        hint: str,
        topology_enabled: bool = False,
        affected_count: int = 0,
    ) -> None:
        """按当前会话配置操作名、图层名和参数控件。"""
        labels = {
            "add": "新增要素",
            "vertex": "顶点编辑",
            "move": "移动要素",
            "rotate": "旋转要素",
            "scale": "缩放要素",
            "split": "拆分要素",
            "merge": "合并要素",
            "simplify": "简化要素",
            "smooth": "平滑要素",
        }
        self._title_label.setText(labels.get(operation, "要素编辑"))
        self._full_layer_name = layer_name.strip()
        self._context_label.setText(self._full_layer_name)
        self._context_label.setVisible(bool(self._full_layer_name))
        self._hint_label.clear()
        self._hint_label.hide()
        tooltip_parts = [self._title_label.text()]
        if self._full_layer_name:
            tooltip_parts.append(self._full_layer_name)
        if fid is not None:
            tooltip_parts.append(f"FID {fid}")
        if hint.strip():
            tooltip_parts.append(hint.strip())
        if affected_count:
            tooltip_parts.append(f"影响 {affected_count} 个相邻要素")
        self.setToolTip(" · ".join(tooltip_parts))
        is_vertex = operation == "vertex"
        for widget in (self._drag_btn, self._delete_btn, self._select_all_btn):
            widget.setVisible(is_vertex)
        self._topology_check.setVisible(is_vertex)
        self._topology_check.blockSignals(True)
        self._topology_check.setChecked(topology_enabled)
        self._topology_check.blockSignals(False)
        self._parameter_mode = operation if operation in ("move", "rotate", "scale") else ""
        self._first_value.setVisible(bool(self._parameter_mode))
        self._second_value.setVisible(operation == "move")
        self._first_value.blockSignals(True)
        self._second_value.blockSignals(True)
        if operation == "move":
            self._first_value.setPrefix("ΔX ")
            self._first_value.setSuffix("")
            self._first_value.setRange(-1_000_000_000.0, 1_000_000_000.0)
            self._second_value.setPrefix("ΔY ")
            self._second_value.setSuffix("")
            self._second_value.setRange(-1_000_000_000.0, 1_000_000_000.0)
            self._first_value.setValue(0.0)
            self._second_value.setValue(0.0)
        elif operation == "rotate":
            self._first_value.setPrefix("角度 ")
            self._first_value.setSuffix("°")
            self._first_value.setRange(-360.0, 360.0)
            self._first_value.setValue(0.0)
        elif operation == "scale":
            self._first_value.setPrefix("比例 ")
            self._first_value.setSuffix("")
            self._first_value.setRange(0.01, 100.0)
            self._first_value.setValue(1.0)
        self._first_value.blockSignals(False)
        self._second_value.blockSignals(False)
        self.set_apply_state(dirty=False, valid=True)
        self._position_in_parent()

    def set_apply_state(self, *, dirty: bool, valid: bool, reason: str = "") -> None:
        """按预览状态控制应用按钮，并显示校验原因。"""
        self._commit_btn.setEnabled(dirty and valid)
        self._commit_btn.setToolTip(reason if reason else "应用当前预览（Enter）")

    def set_parameter_values(self, first: float, second: float | None = None) -> None:
        """从画布手势同步数值参数，不触发重复预览。"""
        self._first_value.blockSignals(True)
        self._second_value.blockSignals(True)
        self._first_value.setValue(first)
        if second is not None:
            self._second_value.setValue(second)
        self._first_value.blockSignals(False)
        self._second_value.blockSignals(False)

    def _emit_parameters(self) -> None:
        """把当前数值输入转换为稳定参数名称。"""
        if self._parameter_mode == "move":
            self.parameters_changed.emit(
                {"dx": self._first_value.value(), "dy": self._second_value.value()}
            )
        elif self._parameter_mode == "rotate":
            self.parameters_changed.emit({"angle": self._first_value.value()})
        elif self._parameter_mode == "scale":
            self.parameters_changed.emit({"scale": self._first_value.value()})

    def _set_mode(self, mode: str) -> None:
        """切换编辑模式并同步按钮状态（用户点击按钮时调用）。"""
        self._mode = mode
        self._sync_buttons(mode)
        self.mode_changed.emit(mode)

    def set_mode(self, mode: str) -> None:
        """程序化切换编辑模式（不触发 mode_changed，避免信号循环）。"""
        self._mode = mode
        self._sync_buttons(mode)

    def _sync_buttons(self, mode: str) -> None:
        """仅同步按钮选中状态，不发射信号。"""
        self._drag_btn.setChecked(mode == "drag_vertex")
        self._delete_btn.setChecked(mode == "delete_vertex")

    @property
    def edit_mode(self) -> str:
        return self._mode

    def show_at(self, x: int, y: int) -> None:
        """兼容旧调用方，在父画布顶部显示上下文栏。"""
        self.adjustSize()
        self._position_in_parent()
        self.show()
        self.raise_()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """父画布尺寸变化时保持上下文栏贴合顶部。"""
        if watched is self.parent() and event.type() == QEvent.Type.Resize:
            self._position_in_parent()
        return super().eventFilter(watched, event)

    def _position_in_parent(self) -> None:
        """按操作名和图层名的内容宽度居中贴顶，不超过父画布可用宽度。"""
        self._context_label.setText(self._full_layer_name)
        self.setMaximumWidth(16_777_215)
        self.adjustSize()
        content = self.sizeHint()
        margin = 12
        parent = self.parentWidget()
        if parent is None:
            self.resize(content.width(), max(content.height(), 40))
            return
        available = max(parent.width() - margin * 2, 160)
        if content.width() > available:
            overflow = content.width() - available
            context_width = max(self._context_label.sizeHint().width() - overflow, 24)
            self._context_label.setText(
                self._context_label.fontMetrics().elidedText(
                    self._full_layer_name,
                    Qt.TextElideMode.ElideRight,
                    context_width,
                )
            )
            self.adjustSize()
            content = self.sizeHint()
        width = min(content.width(), available)
        height = max(min(content.height(), self.maximumHeight()), 40)
        self.setGeometry(
            max((parent.width() - width) // 2, 0),
            margin,
            width,
            height,
        )
