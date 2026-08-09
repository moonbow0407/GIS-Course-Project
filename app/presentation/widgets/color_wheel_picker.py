"""HSB 色轮颜色选择器 —— 替换 QColorDialog 供符号系统使用。"""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSettings, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
    QIcon,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QRadialGradient,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

# ── 预设色板 (Material Design 色系, 5 行 × 6 列 = 30 色) ──────────────

_PRESET_COLORS: tuple[tuple[str, str], ...] = (
    # Row 1: Reds & Pinks
    ("#FFEBEE", "浅红"), ("#FFCDD2", "淡粉"), ("#F44336", "红"),
    ("#E91E63", "玫红"), ("#F48FB1", "浅玫红"), ("#C2185B", "深玫红"),
    # Row 2: Purples & Indigos
    ("#F3E5F5", "浅紫"), ("#CE93D8", "淡紫"), ("#9C27B0", "紫"),
    ("#673AB7", "深紫"), ("#3F51B5", "靛蓝"), ("#1A237E", "深靛蓝"),
    # Row 3: Blues & Cyans
    ("#E3F2FD", "浅蓝"), ("#64B5F6", "淡蓝"), ("#2196F3", "蓝"),
    ("#00BCD4", "青"), ("#0097A7", "深青"), ("#006064", "墨青"),
    # Row 4: Greens & Yellows
    ("#E8F5E9", "浅绿"), ("#81C784", "淡绿"), ("#4CAF50", "绿"),
    ("#FFEB3B", "黄"), ("#FFC107", "琥珀"), ("#FF9800", "橙"),
    # Row 5: Oranges, Browns & Greys
    ("#FF5722", "深橙"), ("#795548", "棕"), ("#8D6E63", "浅棕"),
    ("#9E9E9E", "灰"), ("#607D8B", "蓝灰"), ("#37474F", "深灰"),
)

_PRESET_COLS: int = 6

_QSETTINGS_GROUP: str = "ColorWheelPicker"
_QSETTINGS_KEY: str = "recent_colors"
_MAX_RECENT: int = 8


# ── 辅助函数 ──────────────────────────────────────────────────────────

def _solid_color_pixmap(color: QColor, width: int, height: int) -> QPixmap:
    """绘制带浅灰边框的纯色方块。"""
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#CBD5E1"), 1))
    painter.setBrush(QBrush(color))
    painter.drawRoundedRect(QRectF(1, 1, width - 2, height - 2), 2, 2)
    painter.end()
    return pixmap


def _load_recent_colors() -> list[str]:
    """从 QSettings 加载最近使用的颜色列表。"""
    settings = QSettings()
    settings.beginGroup(_QSETTINGS_GROUP)
    raw_value: object = settings.value(_QSETTINGS_KEY, "")
    settings.endGroup()
    if not isinstance(raw_value, str) or not raw_value:
        return []
    return [c.strip() for c in raw_value.split(",") if QColor(c.strip()).isValid()]


def _save_recent_colors(colors: list[str]) -> None:
    """持久化最近使用的颜色列表至 QSettings。"""
    settings = QSettings()
    settings.beginGroup(_QSETTINGS_GROUP)
    settings.setValue(_QSETTINGS_KEY, ",".join(colors[:_MAX_RECENT]))
    settings.endGroup()


def _add_recent_color(hex_str: str) -> list[str]:
    """将颜色加入 LRU 列表并返回更新后的列表。"""
    recent = _load_recent_colors()
    hex_str = QColor(hex_str).name()
    if hex_str in recent:
        recent.remove(hex_str)
    recent.insert(0, hex_str)
    recent = recent[:_MAX_RECENT]
    _save_recent_colors(recent)
    return recent


# ── 色轮控件 ──────────────────────────────────────────────────────────

class _ColorWheel(QWidget):
    """交互式 HSB 色轮: 色相由角度决定, 饱和度由距圆心距离决定。

    信号:
        hsb_changed(hue: float, saturation: float)
            色相 0-360, 饱和度 0-1。
    """

    hsb_changed = Signal(float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建固定比例色轮并启用鼠标追踪。"""
        super().__init__(parent)
        self._hue: float = 0.0
        self._saturation: float = 0.0
        self.setMinimumSize(200, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

    # ── 公共接口 ──────────────────────────────────────────────────

    def set_hsb(self, hue: float, saturation: float) -> None:
        """程序更新色相/饱和度并重绘指示器。"""
        self._hue = hue % 360.0
        self._saturation = max(0.0, min(1.0, saturation))
        self.update()

    def hue(self) -> float:
        """返回当前色相 (0-360)。"""
        return self._hue

    def saturation(self) -> float:
        """返回当前饱和度 (0-1)。"""
        return self._saturation

    # ── 绘制 ──────────────────────────────────────────────────────

    def paintEvent(self, event: QPaintEvent) -> None:
        """绘制 HSB 色轮圆盘和当前位置指示器。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        wheel_rect = self._wheel_rect()
        if wheel_rect.width() < 4:
            return

        center = wheel_rect.center()
        radius = wheel_rect.width() / 2.0 - 2

        # 1. 色相环 (锥形渐变, 全亮度全饱和度)
        conical = QConicalGradient(center, 0.0)
        for i in range(7):
            hue_angle = i * 60.0
            conical.setColorAt(i / 6.0, QColor.fromHsvF(hue_angle / 360.0, 1.0, 1.0))
        painter.setBrush(QBrush(conical))

        # 2. 饱和度遮罩 (圆心白 → 边缘透明, 径向渐变)
        radial = QRadialGradient(center, radius)
        radial.setColorAt(0.0, QColor(255, 255, 255, 255))
        radial.setColorAt(1.0, QColor(255, 255, 255, 0))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(wheel_rect.adjusted(2, 2, -2, -2))
        painter.setBrush(QBrush(radial))
        painter.drawEllipse(wheel_rect.adjusted(2, 2, -2, -2))

        # 3. 外圈边框
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#CBD5E1"), 2))
        painter.drawEllipse(wheel_rect.adjusted(3, 3, -3, -3))

        # 4. 当前位置指示器
        angle_rad = math.radians(self._hue)
        dist = self._saturation * radius
        ix = center.x() + dist * math.cos(angle_rad)
        iy = center.y() - dist * math.sin(angle_rad)

        # 指示器外圈 (深色阴影)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(0, 0, 0, 80), 4))
        painter.drawEllipse(QPointF(ix, iy), 6, 6)
        # 指示器内圈 (白色)
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.drawEllipse(QPointF(ix, iy), 6, 6)

        painter.end()

    # ── 鼠标交互 ──────────────────────────────────────────────────

    def _pick_color(self, pos: QPoint) -> None:
        """根据控件内坐标计算色相与饱和度。"""
        wheel_rect = self._wheel_rect()
        center = wheel_rect.center()
        radius = max(wheel_rect.width() / 2.0 - 2, 1.0)

        dx = pos.x() - center.x()
        dy = pos.y() - center.y()
        dist = math.sqrt(dx * dx + dy * dy) / radius
        saturation = max(0.0, min(1.0, dist))

        if dist < 0.01:
            # 接近圆心, 保留当前色相以避免闪烁。
            hue = self._hue
        else:
            angle = math.degrees(math.atan2(-dy, dx))
            if angle < 0:
                angle += 360.0
            hue = angle

        self._hue = hue
        self._saturation = saturation
        self.update()
        self.hsb_changed.emit(hue, saturation)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """按下鼠标立即取色。"""
        if event.button() == Qt.MouseButton.LeftButton:
            self._pick_color(event.position().toPoint())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """拖拽鼠标连续取色。"""
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._pick_color(event.position().toPoint())

    def resizeEvent(self, event: QResizeEvent) -> None:
        """尺寸变化时重绘以确保圆形居中。"""
        super().resizeEvent(event)
        self.update()

    # ── 内部 ──────────────────────────────────────────────────────

    def _wheel_rect(self) -> QRect:
        """返回控件内最大内接正方形区域。"""
        side = min(self.width(), self.height())
        x = (self.width() - side) // 2
        y = (self.height() - side) // 2
        return QRect(x, y, side, side)

    def sizeHint(self) -> QSize:
        """建议尺寸：正方形。"""
        return QSize(200, 200)


# ── 亮度滑条 ──────────────────────────────────────────────────────────

class _BrightnessSlider(QWidget):
    """垂直亮度滑条：显示当前色相/饱和度从全亮到黑的渐变。

    信号:
        brightness_changed(value: int) — 亮度值 0-255。
    """

    brightness_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建固定宽度的垂直亮度滑条。"""
        super().__init__(parent)
        self._hue: float = 0.0
        self._saturation: float = 0.0
        self._brightness: int = 255
        self.setFixedWidth(28)
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

    def set_hsb(self, hue: float, saturation: float, brightness: int) -> None:
        """同步色相/饱和度(来自色轮) 和亮度值。"""
        self._hue = hue
        self._saturation = saturation
        self._brightness = max(0, min(255, brightness))
        self.update()

    def brightness(self) -> int:
        """返回当前亮度值 (0-255)。"""
        return self._brightness

    def paintEvent(self, event: QPaintEvent) -> None:
        """绘制垂直亮度渐变条和指示器。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        bar_rect = QRectF(4, 10, self.width() - 8, self.height() - 20)
        if bar_rect.height() < 2:
            painter.end()
            return

        # 渐变: 顶部 = 当前 HS 全亮度, 底部 = 黑
        gradient = QLinearGradient(bar_rect.topLeft(), bar_rect.bottomLeft())
        full_color = QColor.fromHsvF(self._hue / 360.0, self._saturation, 1.0)
        gradient.setColorAt(0.0, full_color)
        gradient.setColorAt(1.0, QColor(0, 0, 0))

        painter.setPen(QPen(QColor("#CBD5E1"), 1))
        painter.setBrush(QBrush(gradient))
        painter.drawRoundedRect(bar_rect, 3, 3)

        # 指示器三角
        indicator_y = bar_rect.top() + (1.0 - self._brightness / 255.0) * bar_rect.height()
        indicator_x = self.width() - 2
        painter.setBrush(QBrush(QColor(255, 255, 255)))
        painter.setPen(QPen(QColor("#475569"), 1))
        triangle = QPointF(indicator_x, indicator_y), QPointF(
            indicator_x + 6, indicator_y - 4
        ), QPointF(indicator_x + 6, indicator_y + 4)
        painter.drawPolygon(triangle)

        painter.end()

    def _pick_brightness(self, y: int) -> None:
        """根据鼠标 Y 坐标计算亮度值。"""
        bar_top = 10
        bar_height = max(self.height() - 20, 1)
        ratio = (y - bar_top) / bar_height
        ratio = max(0.0, min(1.0, ratio))
        self._brightness = int((1.0 - ratio) * 255)
        self.update()
        self.brightness_changed.emit(self._brightness)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """按下鼠标设置亮度。"""
        if event.button() == Qt.MouseButton.LeftButton:
            self._pick_brightness(int(event.position().y()))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """拖拽调整亮度。"""
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._pick_brightness(int(event.position().y()))


# ── 完整对话框 ────────────────────────────────────────────────────────

class ColorWheelPicker(QDialog):
    """HSB 色轮颜色选择对话框。

    用法:
        color = ColorWheelPicker.get_color(QColor("#FF0000"), parent=self)
        if color is not None:
            # 使用 color.name() 获取 hex 字符串
    """

    def __init__(self, initial: QColor, parent: QWidget | None = None) -> None:
        """创建色轮对话框并初始化所有控件。

        参数:
            initial: 对话框打开时的默认颜色。
            parent: 父控件; 用于设置模态和居中。
        """
        super().__init__(parent)
        self.setWindowTitle("选择颜色")
        self.setModal(True)
        self.setMinimumWidth(520)

        self._current_color: QColor = QColor(initial)
        self._original_color: QColor = QColor(initial)
        self._updating: bool = False
        self._recent_colors: list[str] = _load_recent_colors()

        self._setup_ui()
        self._apply_initial(initial)
        self._connect_signals()
        self._apply_light_palette()

    @staticmethod
    def get_color(
        initial: QColor, parent: QWidget | None = None
    ) -> QColor | None:
        """弹出色轮对话框并返回用户选中的颜色。

        参数:
            initial: 默认颜色。
            parent: 父控件。

        返回:
            用户确认的颜色; 取消时返回 None。
        """
        dialog = ColorWheelPicker(initial, parent)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog._current_color
        return None

    # ── UI 构建 ───────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        """组装四列主布局: 色轮 | 亮度 | 信息 | 按钮。"""
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 16)
        root.setSpacing(12)

        # ── 顶部行: 色轮 + 亮度 + 预览/输入 ──────────────────────
        top_row = QHBoxLayout()
        top_row.setSpacing(12)

        # 色轮
        self._wheel = _ColorWheel()
        self._wheel.setMinimumSize(200, 200)
        top_row.addWidget(self._wheel, 1)

        # 亮度滑条
        self._brightness_slider = _BrightnessSlider()
        top_row.addWidget(self._brightness_slider)

        # 颜色预览 + 精确输入
        info_layout = QVBoxLayout()
        info_layout.setSpacing(8)

        # 新颜色大块预览
        new_label = QLabel("新颜色")
        new_label.setStyleSheet("color: #64748b; font-size: 11px;")
        self._new_preview = QLabel()
        self._new_preview.setFixedSize(120, 52)
        self._new_preview.setFrameShape(QFrame.Shape.StyledPanel)

        # 当前颜色小块
        old_label = QLabel("当前")
        old_label.setStyleSheet("color: #64748b; font-size: 11px;")
        self._old_preview = QLabel()
        self._old_preview.setFixedSize(120, 22)
        self._old_preview.setFrameShape(QFrame.Shape.StyledPanel)

        info_layout.addWidget(new_label)
        info_layout.addWidget(self._new_preview)
        info_layout.addWidget(old_label)
        info_layout.addWidget(self._old_preview)

        # Hex 输入
        hex_layout = QHBoxLayout()
        hex_layout.addWidget(QLabel("Hex"))
        self._hex_input = QLineEdit()
        self._hex_input.setPlaceholderText("#000000")
        self._hex_input.setMaxLength(7)
        self._hex_input.setFixedWidth(80)
        hex_layout.addWidget(self._hex_input)
        hex_layout.addStretch()
        info_layout.addLayout(hex_layout)

        # RGB 输入
        self._r_spin = QSpinBox()
        self._r_spin.setRange(0, 255)
        self._r_spin.setFixedWidth(64)
        self._g_spin = QSpinBox()
        self._g_spin.setRange(0, 255)
        self._g_spin.setFixedWidth(64)
        self._b_spin = QSpinBox()
        self._b_spin.setRange(0, 255)
        self._b_spin.setFixedWidth(64)

        for label_text, spin in (
            ("R", self._r_spin),
            ("G", self._g_spin),
            ("B", self._b_spin),
        ):
            row = QHBoxLayout()
            row.addWidget(QLabel(label_text))
            row.addWidget(spin)
            row.addStretch()
            info_layout.addLayout(row)

        info_layout.addStretch()
        top_row.addLayout(info_layout)
        root.addLayout(top_row)

        # ── 预设色板 ──────────────────────────────────────────────
        preset_label = QLabel("预设颜色")
        preset_label.setStyleSheet("color: #64748b; font-size: 11px; font-weight: bold;")
        root.addWidget(preset_label)

        self._preset_buttons: list[QPushButton] = []
        preset_grid = QGridLayout()
        preset_grid.setSpacing(2)
        for index, (hex_str, tip) in enumerate(_PRESET_COLORS):
            btn = QPushButton()
            btn.setFixedSize(26, 26)
            btn.setToolTip(f"{tip}  {hex_str}")
            btn.setIcon(self._make_swatch_icon(hex_str, 20, 20))
            btn.setIconSize(QSize(20, 20))
            btn.setStyleSheet(
                "QPushButton { border: 1px solid #CBD5E1; border-radius: 3px; padding: 0; }"
                "QPushButton:hover { border-color: #3B82F6; }"
            )
            btn.clicked.connect(lambda checked, h=hex_str: self._on_preset_clicked(h))
            preset_grid.addWidget(btn, index // _PRESET_COLS, index % _PRESET_COLS)
            self._preset_buttons.append(btn)
        root.addLayout(preset_grid)

        # ── 最近使用 ──────────────────────────────────────────────
        recent_label = QLabel("最近使用")
        recent_label.setStyleSheet("color: #64748b; font-size: 11px; font-weight: bold;")
        root.addWidget(recent_label)

        self._recent_container = QHBoxLayout()
        self._recent_container.setSpacing(2)
        self._recent_buttons: list[QPushButton] = []
        self._rebuild_recent_buttons()
        self._recent_container.addStretch()
        root.addLayout(self._recent_container)

        # ── 确定 / 取消 ──────────────────────────────────────────
        button_row = QHBoxLayout()
        button_row.addStretch()
        ok_btn = QPushButton("确定")
        ok_btn.setFixedWidth(80)
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedWidth(80)
        cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(ok_btn)
        button_row.addWidget(cancel_btn)
        root.addLayout(button_row)

    def _connect_signals(self) -> None:
        """连接色轮、亮度滑条和输入控件的信号。"""
        self._wheel.hsb_changed.connect(self._on_wheel_changed)
        self._brightness_slider.brightness_changed.connect(self._on_brightness_changed)
        self._hex_input.editingFinished.connect(self._on_hex_edited)
        self._r_spin.valueChanged.connect(self._on_rgb_spin_changed)
        self._g_spin.valueChanged.connect(self._on_rgb_spin_changed)
        self._b_spin.valueChanged.connect(self._on_rgb_spin_changed)

    # ── 信号处理 ─────────────────────────────────────────────────

    def _on_wheel_changed(self, hue: float, saturation: float) -> None:
        """色轮 H/S 变更 → 更新亮度条渐变、预览和输入。"""
        if self._updating:
            return
        brightness = self._brightness_slider.brightness()
        self._set_color_from_hsb(hue, saturation, brightness)

    def _on_brightness_changed(self, brightness: int) -> None:
        """亮度滑条变更 → 更新预览和输入。"""
        if self._updating:
            return
        hue = self._wheel.hue()
        saturation = self._wheel.saturation()
        self._set_color_from_hsb(hue, saturation, brightness)

    def _on_hex_edited(self) -> None:
        """Hex 输入框回车/失去焦点 → 解析并同步。"""
        if self._updating:
            return
        text = self._hex_input.text().strip()
        if not text.startswith("#"):
            text = f"#{text}"
        color = QColor(text)
        if color.isValid():
            self._set_color_from_rgb(color)

    def _on_rgb_spin_changed(self) -> None:
        """RGB 数值变更 → 同步所有控件。"""
        if self._updating:
            return
        color = QColor(self._r_spin.value(), self._g_spin.value(), self._b_spin.value())
        self._set_color_from_rgb(color)

    def _on_preset_clicked(self, hex_str: str) -> None:
        """点击预设色块 → 立即设置为当前颜色。"""
        self._set_color_from_rgb(QColor(hex_str))

    # ── 颜色同步核心 ─────────────────────────────────────────────

    def _set_color_from_hsb(self, hue: float, saturation: float, brightness: int) -> None:
        """以 HSB 为源同步全部控件。"""
        self._updating = True
        try:
            color = QColor.fromHsv(int(hue), int(saturation * 255), brightness)
            self._current_color = color
            self._sync_inputs(color)
            self._sync_previews(color)
            # 更新亮度条渐变 (顶部颜色随色相/饱和度变化)
            self._brightness_slider.set_hsb(hue, saturation, brightness)
        finally:
            self._updating = False

    def _set_color_from_rgb(self, color: QColor) -> None:
        """以 RGB QColor 为源同步全部控件。"""
        if not color.isValid():
            return
        self._updating = True
        try:
            self._current_color = color
            h = color.hue() if color.saturation() > 0 or color.value() > 0 else 0.0
            s = color.saturationF()
            v = color.value()
            if v == 0:
                s = 0.0
            self._wheel.set_hsb(float(h), float(s))
            self._brightness_slider.set_hsb(float(h), float(s), v)
            self._sync_inputs(color)
            self._sync_previews(color)
        finally:
            self._updating = False

    def _sync_inputs(self, color: QColor) -> None:
        """将颜色值写回 Hex 和 RGB 控件。"""
        self._hex_input.setText(color.name())
        self._r_spin.setValue(color.red())
        self._g_spin.setValue(color.green())
        self._b_spin.setValue(color.blue())

    def _sync_previews(self, color: QColor) -> None:
        """更新新旧颜色预览框。"""
        self._new_preview.setPixmap(_solid_color_pixmap(color, 118, 50))
        self._old_preview.setPixmap(_solid_color_pixmap(self._original_color, 118, 20))

    # ── 初始状态 ─────────────────────────────────────────────────

    def _apply_initial(self, color: QColor) -> None:
        """用初始颜色设置色轮、亮度和预览的起始状态。"""
        h = color.hue() if color.saturation() > 0 or color.value() > 0 else 0.0
        s = color.saturationF()
        v = color.value()
        if v == 0:
            s = 0.0
        self._current_color = QColor(color)
        self._wheel.set_hsb(float(h), float(s))
        self._brightness_slider.set_hsb(float(h), float(s), v)
        self._sync_inputs(color)
        self._sync_previews(color)

    # ── 最近颜色按钮 ─────────────────────────────────────────────

    def _rebuild_recent_buttons(self) -> None:
        """根据 _recent_colors 列表重建最近使用色块按钮。"""
        # 清除旧按钮。
        for btn in self._recent_buttons:
            self._recent_container.removeWidget(btn)
            btn.deleteLater()
        self._recent_buttons.clear()

        for hex_str in self._recent_colors:
            btn = QPushButton()
            btn.setFixedSize(26, 26)
            btn.setToolTip(hex_str)
            btn.setIcon(self._make_swatch_icon(hex_str, 20, 20))
            btn.setIconSize(QSize(20, 20))
            btn.setStyleSheet(
                "QPushButton { border: 1px solid #CBD5E1; border-radius: 3px; padding: 0; }"
                "QPushButton:hover { border-color: #3B82F6; }"
            )
            btn.clicked.connect(lambda checked, h=hex_str: self._on_preset_clicked(h))
            self._recent_container.insertWidget(
                self._recent_container.count() - 1, btn
            )
            self._recent_buttons.append(btn)

    def accept(self) -> None:
        """确定：记录最近颜色并关闭对话框。"""
        _add_recent_color(self._current_color.name())
        super().accept()

    # ── 工具方法 ─────────────────────────────────────────────────

    @staticmethod
    def _make_swatch_icon(hex_str: str, w: int, h: int) -> QIcon:
        """创建指定尺寸的纯色图标。"""
        pixmap = QPixmap(w, h)
        pixmap.fill(QColor(hex_str))
        return QIcon(pixmap)

    def _apply_light_palette(self) -> None:
        """统一应用浅色调色板，避免继承系统深色主题。"""
        palette = self.palette()
        palette.setColor(palette.ColorRole.Window, QColor("#FFFFFF"))
        palette.setColor(palette.ColorRole.WindowText, QColor("#1E293B"))
        palette.setColor(palette.ColorRole.Base, QColor("#FFFFFF"))
        palette.setColor(palette.ColorRole.AlternateBase, QColor("#F8FAFC"))
        palette.setColor(palette.ColorRole.Text, QColor("#1E293B"))
        palette.setColor(palette.ColorRole.Button, QColor("#FFFFFF"))
        palette.setColor(palette.ColorRole.ButtonText, QColor("#1E293B"))
        palette.setColor(palette.ColorRole.Highlight, QColor("#DBEAFE"))
        palette.setColor(palette.ColorRole.HighlightedText, QColor("#1E40AF"))
        self.setPalette(palette)
        for child in self.findChildren(QWidget):
            child.setPalette(palette)
