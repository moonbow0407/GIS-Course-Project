"""布局视图 — 虚拟纸张上的制图元素排版与预览。

LayoutView 是一个独立的 QGraphicsView，与 MapCanvas 平级。
它通过 QStackedWidget 切换显示，不依赖地图画布的内部状态。
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QInputDialog,
    QMenu,
)

from app.application.results import WorkspaceSnapshot
from app.domain.layout import (
    LayoutDocument,
    LayoutElement,
    LegendElement,
    MapFrameElement,
    NorthArrowElement,
    ScaleBarElement,
    TextElement,
)
from app.presentation.renderers.layout_renderer import (
    render_legend,
    render_map_frame,
    render_north_arrow,
    render_scale_bar,
    render_text,
)

# ---------------------------------------------------------------------------
# 颜色常量
# ---------------------------------------------------------------------------

_BACKGROUND_COLOR: QColor = QColor("#d1d5db")
_PAPER_COLOR: QColor = QColor("#ffffff")
_SHADOW_COLOR: QColor = QColor(0, 0, 0, 40)
_GRID_COLOR: QColor = QColor("#e5e7eb")
_GRID_10CM_COLOR: QColor = QColor("#d1d5db")
_MARGIN_COLOR: QColor = QColor("#f87171")
_SELECTION_COLOR: QColor = QColor("#2563eb")


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _mm_to_px(mm: float, dpi: float) -> float:
    """毫米转像素（基于 DPI）。"""
    return mm / 25.4 * dpi


def _snapshot_element(elem, eid: str):
    """创建元素的轻量快照，用于撤销栈的恢复。"""
    common = {
        "element_id": eid,
        "x_mm": elem.x_mm,
        "y_mm": elem.y_mm,
        "width_mm": elem.width_mm,
        "height_mm": elem.height_mm,
        "rotation": elem.rotation,
    }
    if isinstance(elem, MapFrameElement):
        return MapFrameElement(
            **common,
            map_center_x=elem.map_center_x,
            map_center_y=elem.map_center_y,
            map_units_per_pixel=elem.map_units_per_pixel,
            border_color=elem.border_color,
            border_width_mm=elem.border_width_mm,
            background_color=elem.background_color,
        )
    elif isinstance(elem, ScaleBarElement):
        return ScaleBarElement(
            **common,
            linked_frame_id=elem.linked_frame_id,
            style=elem.style,
            unit=elem.unit,
            num_segments=elem.num_segments,
            label_font_size_mm=elem.label_font_size_mm,
            color=elem.color,
        )
    elif isinstance(elem, LegendElement):
        return LegendElement(
            **common,
            linked_frame_id=elem.linked_frame_id,
            title=elem.title,
            title_font_size_mm=elem.title_font_size_mm,
            item_font_size_mm=elem.item_font_size_mm,
            column_count=elem.column_count,
        )
    elif isinstance(elem, NorthArrowElement):
        return NorthArrowElement(
            **common,
            style=elem.style,
            color=elem.color,
        )
    elif isinstance(elem, TextElement):
        return TextElement(
            **common,
            text=elem.text,
            font_size_mm=elem.font_size_mm,
            color=elem.color,
            bold=elem.bold,
            italic=elem.italic,
            alignment=elem.alignment,
        )
    return elem


# ---------------------------------------------------------------------------
# 布局视图
# ---------------------------------------------------------------------------


class LayoutView(QGraphicsView):
    """显示虚拟纸张并支持制图元素的排版预览。

    交互:
        - 滚轮缩放（锚定鼠标位置）
        - 空白区手形拖拽平移
        - 左键选择 / 拖拽移动制图元素（命中叠压中的顶层）
        - Alt/Ctrl + 左键：在叠压元素间循环选中下一层
        - Shift + 左键拖拽已选中的地图框：平移地图内容
        - 双击：打开选中元素属性
        - Ctrl + 滚轮精细缩放

    坐标系:
        场景原点 (0, 0) = 页面左上角，X 向右，Y 向下。
        所有元素位置以毫米为单位存储，渲染时按 DPI 换算为像素。
    """

    # 选中元素变化时发出
    element_selected = Signal(str)  # element_id
    # 布局元素增删时发出（用于同步工具栏按钮状态）
    elements_changed = Signal()
    # 撤销/重做状态变化时发出
    undo_state_changed = Signal(bool, bool)  # can_undo, can_redo

    def __init__(self, parent: QFrame | None = None) -> None:
        super().__init__(parent)
        self._scene: QGraphicsScene = QGraphicsScene(self)
        self.setScene(self._scene)

        # 视图外观 — 使用 ScrollHandDrag 原生平移（与 MapCanvas 一致）
        self.setBackgroundBrush(QBrush(_BACKGROUND_COLOR))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse
        )
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMouseTracking(True)

        # 内部状态
        self._document: LayoutDocument | None = None
        self._snapshot: WorkspaceSnapshot | None = None
        self._paper_item: QGraphicsRectItem | None = None
        self._shadow_item: QGraphicsRectItem | None = None
        self._grid_items: list[QGraphicsItem] = []
        self._margin_items: list[QGraphicsItem] = []

        # 元素渲染缓存：{element_id: (list[QGraphicsItem], QGraphicsRectItem)}
        #   - list[QGraphicsItem]: 所有渲染内容图元
        #   - QGraphicsRectItem: 边界矩形（命中检测 + 选中高亮）
        self._element_items: dict[
            str, tuple[list[QGraphicsItem], QGraphicsRectItem]
        ] = {}
        self._needs_initial_fit: bool = True

        # 元素拖拽状态
        self._dragging_element_id: str | None = None
        self._drag_start_pos: QPointF | None = None
        self._drag_start_mm: tuple[float, float] = (0.0, 0.0)

        # 选中的元素
        self._selected_element_id: str | None = None

        # 撤销栈：每项 (描述, 撤销函数, 重做函数)
        self._undo_stack: list[tuple[str, Callable[[], object], Callable[[], object]]] = []
        self._redo_stack: list[tuple[str, Callable[[], object], Callable[[], object]]] = []

        # 中键平移状态
        self._panning: bool = False
        self._pan_start: QPointF | None = None

        # Shift+拖拽 地图内容平移状态
        self._map_panning: bool = False
        self._map_pan_center: tuple[float, float] = (0.0, 0.0)

        # 缩放手柄状态
        self._resize_handles: list[QGraphicsRectItem] = []
        self._resizing_handle_index: int | None = None
        self._resize_start_pos: QPointF | None = None
        self._resize_start_rect: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

        # 快捷键
        QShortcut(QKeySequence.StandardKey.Delete, self, self._delete_selected)
        QShortcut(QKeySequence.StandardKey.Undo, self, self._undo)
        QShortcut(QKeySequence.StandardKey.Redo, self, self._redo)

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    @property
    def _view_dpi(self) -> float:
        """返回屏幕逻辑 DPI，供交互式视图渲染使用。

        页面设置中的 page.dpi (300) 仅用于导出，屏幕显示使用此值 (~96)，
        确保纸张在屏幕上接近物理尺寸，实现"所见即所得"的纸张预览。
        """
        return float(self.logicalDpiX())

    def set_map_canvas(self, canvas) -> None:
        """设置关联的地图画布引用，用于布局视图同步数据视图的缩放状态。"""
        self._map_canvas = canvas

    def set_snapshot(self, snapshot: WorkspaceSnapshot) -> None:
        """设置当前工作区快照，供地图框渲染使用。"""
        self._snapshot = snapshot

    def set_document(self, document: LayoutDocument) -> None:
        """设置当前排版文档并重建场景。

        参数:
            document: 包含页面规格和制图元素的布局文档。
        """
        self._document = document
        self._needs_initial_fit = True
        self._rebuild_scene()
        self._fit_page()

    def document(self) -> LayoutDocument | None:
        """返回当前排版文档，未设置时返回 None。"""
        return self._document

    def fit_page(self) -> None:
        """将纸张适配到当前视口大小。"""
        self._fit_page()

    def fit_selected_or_page(self) -> None:
        """智能适配：选中地图框时适配地图内容，否则适配页面到视口。

        供工具栏"⊡ 适配"使用，让地图框内的地图数据一键重新充满地图框。
        """
        if self._selected_element_id is not None:
            elem = self._find_element(self._selected_element_id)
            if isinstance(elem, MapFrameElement):
                self.fit_map_content(elem)
                return
        self._fit_page()

    def fit_map_content(self, frame: MapFrameElement) -> None:
        """将全部可见图层数据自动适配到指定地图框内（可撤销）。

        重新计算地图中心与比例，使所有可见图层的数据充满地图框，
        用于地图框内容无法自动适配或数据变化后的一键适配。
        """
        extent = self._data_extent()
        if extent is None:
            return
        old_cx, old_cy = frame.map_center_x, frame.map_center_y
        old_mupp = frame.map_units_per_pixel
        frame_px_w = _mm_to_px(frame.width_mm, self._view_dpi)
        frame_px_h = _mm_to_px(frame.height_mm, self._view_dpi)
        cx = (extent[0] + extent[2]) / 2.0
        cy = (extent[1] + extent[3]) / 2.0
        data_w = max(extent[2] - extent[0], 1e-9)
        data_h = max(extent[3] - extent[1], 1e-9)
        mupp = max(data_w / frame_px_w, data_h / frame_px_h)
        self._restore_map_state(frame, cx, cy, mupp)
        self._push_undo(
            "适配地图内容",
            undo_action=partial(
                self._restore_map_state, frame, old_cx, old_cy, old_mupp
            ),
            redo_action=partial(self._restore_map_state, frame, cx, cy, mupp),
        )

    def zoom_in(self) -> None:
        """放大视图（以视口中心为锚点）。"""
        self._apply_scale(1.25)

    def zoom_out(self) -> None:
        """缩小视图（以视口中心为锚点）。"""
        self._apply_scale(0.8)

    def has_content(self) -> bool:
        """是否已设置排版文档。"""
        return self._document is not None

    def add_map_frame(self) -> str | None:
        """在页面中心添加默认大小地图框（每种元素最多一个）。

        切换行为:
            - 不存在地图框 → 添加并选中（保持高亮）。
            - 已存在但未选中 → 选中（保持高亮）。
            - 已存在且已选中 → 删除。

        返回:
            新元素或已有元素的 ID；删除时返回 None。
        """
        if self._document is None or self._document.page is None:
            return None
        existing = self._find_existing(MapFrameElement)
        if existing is not None:
            return self._toggle_existing(existing)
        page = self._document.page
        # 默认地图框占据可打印区域的 70%
        pw: float = page.printable_width_mm
        ph: float = page.printable_height_mm
        fw: float = pw * 0.7
        fh: float = ph * 0.7
        fx: float = page.margin_mm + (pw - fw) / 2
        fy: float = page.margin_mm + (ph - fh) / 2

        # 优先从数据视图同步当前可见范围和中心；不可用时回退到全图适配。
        cx, cy = 0.0, 0.0
        mupp: float = 0.0  # 0 表示尚未设置
        frame_px_w: float = _mm_to_px(fw, self._view_dpi)
        frame_px_h: float = _mm_to_px(fh, self._view_dpi)

        map_canvas = getattr(self, "_map_canvas", None)
        if map_canvas is not None:
            extent = map_canvas.capture_view_extent()
            if extent is not None:
                v_cx, v_cy, v_w, v_h = extent
                if v_w > 1e-9 and v_h > 1e-9:
                    cx = v_cx
                    cy = v_cy
                    mupp = max(v_w / frame_px_w, v_h / frame_px_h)

        if mupp <= 0:
            # 回退：从快照估算，使全图数据适配地图框
            if self._snapshot is not None and self._snapshot.layers:
                first = self._snapshot.layers[0]
                b = first.bounds
                cx = (b[0] + b[2]) / 2.0
                cy = (b[1] + b[3]) / 2.0
                data_w = max(b[2] - b[0], 1e-9)
                data_h = max(b[3] - b[1], 1e-9)
                mupp = max(data_w / frame_px_w, data_h / frame_px_h)
            else:
                mupp = 1.0

        frame = MapFrameElement(
            x_mm=fx,
            y_mm=fy,
            width_mm=fw,
            height_mm=fh,
            map_center_x=cx,
            map_center_y=cy,
            map_units_per_pixel=mupp,
        )
        self._add_element(frame)
        self._select_element(frame.element_id)
        self._push_undo(
            "添加地图框",
            undo_action=partial(self.remove_element, frame.element_id),
            redo_action=lambda: self._add_element(frame),
        )
        return frame.element_id

    def add_scale_bar(self) -> str | None:
        """在页面底部添加比例尺（每种元素最多一个）。

        切换行为:
            - 不存在比例尺 → 添加并选中（保持高亮）。
            - 已存在但未选中 → 选中（保持高亮）。
            - 已存在且已选中 → 删除。

        返回:
            新元素或已有元素的 ID；删除时返回 None。
        """
        if self._document is None:
            return None
        existing = self._find_existing(ScaleBarElement)
        if existing is not None:
            return self._toggle_existing(existing)
        page = self._document.page

        # 查找第一个地图框来关联并定位
        linked_id: str = ""
        pos_x: float = page.margin_mm
        pos_y: float = page.height_mm - page.margin_mm - 8
        for e in self._document.elements:
            if isinstance(e, MapFrameElement):
                linked_id = e.element_id
                pos_x = e.x_mm
                pos_y = e.y_mm + e.height_mm + 5
                break

        element = ScaleBarElement(
            x_mm=pos_x,
            y_mm=pos_y,
            width_mm=80,
            height_mm=8,
            linked_frame_id=linked_id,
        )
        self._add_element(element)
        self._select_element(element.element_id)
        self._push_undo(
            "添加比例尺",
            undo_action=partial(self.remove_element, element.element_id),
            redo_action=partial(self._add_element, element),
        )
        return element.element_id

    def add_legend(self) -> str | None:
        """在页面右侧添加图例（每种元素最多一个）。

        切换行为:
            - 不存在图例 → 添加并选中（保持高亮）。
            - 已存在但未选中 → 选中（保持高亮）。
            - 已存在且已选中 → 删除。

        返回:
            新元素或已有元素的 ID；删除时返回 None。
        """
        if self._document is None:
            return None
        existing = self._find_existing(LegendElement)
        if existing is not None:
            return self._toggle_existing(existing)
        page = self._document.page

        linked_id: str = ""
        pos_x: float = page.margin_mm
        pos_y: float = page.margin_mm
        for e in self._document.elements:
            if isinstance(e, MapFrameElement):
                linked_id = e.element_id
                pos_x = e.x_mm + e.width_mm + 5
                pos_y = e.y_mm
                break

        # 根据可见图层数估算初始高度
        visible_count = (
            sum(1 for s in self._snapshot.layers if s.visible)
            if self._snapshot else 0
        )
        est_height = 8.0 + visible_count * 7.0  # 标题 + 每行约 7mm

        element = LegendElement(
            x_mm=pos_x,
            y_mm=pos_y,
            width_mm=50,
            height_mm=est_height,
            linked_frame_id=linked_id,
        )
        self._add_element(element)
        self._select_element(element.element_id)
        self._push_undo(
            "添加图例",
            undo_action=partial(self.remove_element, element.element_id),
            redo_action=partial(self._add_element, element),
        )
        return element.element_id

    def add_north_arrow(self) -> str | None:
        """在页面右上方添加指北针（每种元素最多一个）。

        切换行为:
            - 不存在指北针 → 添加并选中（保持高亮）。
            - 已存在但未选中 → 选中（保持高亮）。
            - 已存在且已选中 → 删除。

        返回:
            新元素或已有元素的 ID；删除时返回 None。
        """
        if self._document is None:
            return None
        existing = self._find_existing(NorthArrowElement)
        if existing is not None:
            return self._toggle_existing(existing)
        page = self._document.page

        pos_x: float = page.width_mm - page.margin_mm - 20
        pos_y: float = page.margin_mm + 5
        # 如果有地图框，放在地图框右上方
        for e in self._document.elements:
            if isinstance(e, MapFrameElement):
                pos_x = e.x_mm + e.width_mm - 20
                pos_y = e.y_mm + 5
                break

        element = NorthArrowElement(
            x_mm=pos_x,
            y_mm=pos_y,
            width_mm=15,
            height_mm=20,
        )
        self._add_element(element)
        self._select_element(element.element_id)
        self._push_undo(
            "添加指北针",
            undo_action=partial(self.remove_element, element.element_id),
            redo_action=partial(self._add_element, element),
        )
        return element.element_id

    def _next_text_position(self) -> tuple[float, float]:
        """计算下一个文本元素的左上角位置（mm）。

        以页面中心为基准，按当前已有文本数量做行列偏移：同一行向右错开
        h_step 形成 cascade，便于点选与阅读；每行铺满 per_row 个后回绕
        换行并整体下移 v_step。采用"当前文本数"而非单调递增计数，删除
        文本后新添加会回退到更靠近页面中心的空位——简单、可预期。
        """
        page = self._document.page
        text_count = sum(
            1 for e in self._document.elements if isinstance(e, TextElement)
        )
        text_w: float = 60.0
        text_h: float = 12.0
        h_step: float = 12.0  # 同行水平错位（mm），拉开前缘便于点选/阅读
        v_step: float = 16.0  # 换行纵向步进 = 文本高 12mm + 4mm 间距
        per_row: int = 4
        col = text_count % per_row
        row = text_count // per_row
        pos_x: float = page.width_mm / 2 - text_w / 2 + col * h_step
        pos_y: float = page.height_mm / 2 - text_h / 2 + row * v_step
        return pos_x, pos_y

    def add_text_element(self) -> str | None:
        """弹出输入框让用户输入文本，然后在页面中心附近添加文本元素。

        文本元素支持多个实例：每次调用都会新建一个，位置按当前已有文本
        数量自动偏移，避免多个文本完全重叠。删除交给工具栏 ✕ 删除按钮
        或 Del 键完成（本方法不再负责选中已有/删除切换）。

        返回:
            新元素的 ID；用户取消输入或文档为空时返回 None。
        """
        if self._document is None:
            return None
        text, ok = QInputDialog.getText(
            self, "添加文本", "请输入文本内容:",
            text="文本",
        )
        if not ok or not text:
            return None
        pos_x, pos_y = self._next_text_position()
        element = TextElement(
            x_mm=pos_x,
            y_mm=pos_y,
            width_mm=60,
            height_mm=12,
            text=text,
            font_size_mm=5.0,
        )
        self._add_element(element)
        self._select_element(element.element_id)
        self._push_undo(
            "添加文本",
            undo_action=partial(self.remove_element, element.element_id),
            redo_action=partial(self._add_element, element),
        )
        return element.element_id

    def refresh_map_frames(self) -> None:
        """重绘所有地图框及关联的装饰元素（数据变化后调用）。"""
        if self._document is None:
            return
        for elem in self._document.elements:
            if isinstance(elem, MapFrameElement):
                self._render_element(elem)
        # 刷新关联到地图框的装饰元素
        for elem in self._document.elements:
            if isinstance(elem, (ScaleBarElement, LegendElement)):
                self._render_element(elem)

    def find_element(self, element_id: str) -> LayoutElement | None:
        """按 ID 查找布局元素（公开接口）。"""
        return self._find_element(element_id)

    def apply_element_changes(
        self, element_id: str, changes: dict[str, object]
    ) -> None:
        """应用属性对话框返回的修改。

        参数:
            element_id: 目标元素 ID。
            changes: 属性名到新值的映射。
        """
        elem = self._find_element(element_id)
        if elem is None:
            return
        old_values: dict[str, object] = {
            k: getattr(elem, k) for k in changes if hasattr(elem, k)
        }
        for key, value in changes.items():
            if hasattr(elem, key):
                setattr(elem, key, value)
        self._render_element(elem)
        if self._selected_element_id == element_id:
            self._select_element(element_id)
        self._push_undo(
            "修改元素属性",
            undo_action=partial(self._restore_props, elem, old_values),
            redo_action=partial(self._restore_props, elem, changes),
        )

    def _restore_props(
        self, element: LayoutElement, values: dict[str, object]
    ) -> None:
        """恢复元素属性（用于撤销/重做）。"""
        for key, value in values.items():
            setattr(element, key, value)
        self._render_element(element)
        if self._selected_element_id == element.element_id:
            self._select_element(element.element_id)

    def _restore_map_center(
        self, element: MapFrameElement, center_x: float, center_y: float
    ) -> None:
        """恢复地图框中心坐标并重绘（用于撤销/重做）。"""
        element.map_center_x = center_x
        element.map_center_y = center_y
        self._render_element(element)

    def _restore_map_state(
        self, frame: MapFrameElement, cx: float, cy: float, mupp: float
    ) -> None:
        """恢复地图框中心与比例并重绘（用于撤销/重做与适配）。"""
        frame.map_center_x = cx
        frame.map_center_y = cy
        frame.map_units_per_pixel = mupp
        self._render_element(frame)
        if self._selected_element_id == frame.element_id:
            self._select_element(frame.element_id)

    def _data_extent(self) -> tuple[float, float, float, float] | None:
        """返回快照中全部可见图层的联合范围 (min_x, min_y, max_x, max_y)。

        无可见图层数据时返回 None。
        """
        if self._snapshot is None or not self._snapshot.layers:
            return None
        extent: list[float] | None = None
        for layer_snap in self._snapshot.layers:
            if not layer_snap.visible:
                continue
            b = layer_snap.bounds
            if extent is None:
                extent = [b[0], b[1], b[2], b[3]]
            else:
                extent[0] = min(extent[0], b[0])
                extent[1] = min(extent[1], b[1])
                extent[2] = max(extent[2], b[2])
                extent[3] = max(extent[3], b[3])
        return tuple(extent) if extent is not None else None

    # ------------------------------------------------------------------
    # 内部：元素管理
    # ------------------------------------------------------------------

    def _add_element(self, element) -> None:
        """将元素加入文档并渲染。"""
        if self._document is None:
            return
        # LayoutDocument 是 frozen，用 object.__setattr__ 绕过
        # 实际做法：创建新的 mutable elements list
        current = list(self._document.elements)
        current.append(element)
        object.__setattr__(self._document, "elements", tuple(current))
        self._render_element(element)
        self.elements_changed.emit()

    def _render_element(self, element) -> None:
        """渲染单个布局元素到场景。"""
        if self._document is None:
            return
        dpi: float = self._view_dpi

        # 移除旧图元
        old = self._element_items.pop(element.element_id, None)
        if old is not None:
            items_list, bounds_rect = old
            for item in items_list:
                self._scene.removeItem(item)
            self._scene.removeItem(bounds_rect)

        if isinstance(element, MapFrameElement):
            self._render_map_frame_element(element, dpi)
        elif isinstance(element, ScaleBarElement):
            self._render_scale_bar_element(element, dpi)
        elif isinstance(element, LegendElement):
            self._render_legend_element(element, dpi)
        elif isinstance(element, NorthArrowElement):
            self._render_north_arrow_element(element, dpi)
        elif isinstance(element, TextElement):
            self._render_text_element(element, dpi)

        # 应用旋转
        if element.rotation != 0:
            cached = self._element_items.get(element.element_id)
            if cached is not None:
                items_list, bounds_rect = cached
                cx = bounds_rect.rect().center().x()
                cy = bounds_rect.rect().center().y()
                for item in items_list:
                    item.setTransformOriginPoint(cx, cy)
                    item.setRotation(element.rotation)

    def _render_map_frame_element(
        self, frame: MapFrameElement, dpi: float
    ) -> None:
        """渲染地图框：生成像素图并创建图元。"""
        # 渲染地图内容
        pixmap: QPixmap
        if self._snapshot is not None and self._snapshot.layers:
            pixmap = render_map_frame(frame, self._snapshot, dpi)
        else:
            pix_w = max(1, round(_mm_to_px(frame.width_mm, dpi)))
            pix_h = max(1, round(_mm_to_px(frame.height_mm, dpi)))
            pixmap = QPixmap(pix_w, pix_h)
            pixmap.fill(QColor(frame.background_color))

        # 在场景中的像素位置
        px: float = _mm_to_px(frame.x_mm, dpi)
        py: float = _mm_to_px(frame.y_mm, dpi)
        pw: float = _mm_to_px(frame.width_mm, dpi)
        ph: float = _mm_to_px(frame.height_mm, dpi)

        # 地图内容图元
        pix_item: QGraphicsPixmapItem = QGraphicsPixmapItem(pixmap)
        pix_item.setPos(px, py)
        pix_item.setZValue(10)
        pix_item.setData(0, frame.element_id)
        pix_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)

        # 可见边框
        border_pen: QPen = QPen(
            QColor(frame.border_color),
            _mm_to_px(frame.border_width_mm, dpi),
        )
        border_pen.setCosmetic(True)
        border_item: QGraphicsRectItem = QGraphicsRectItem(
            QRectF(px, py, pw, ph)
        )
        border_item.setPen(border_pen)
        border_item.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        border_item.setZValue(11)
        border_item.setData(0, frame.element_id)
        border_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)

        # 边界矩形（透明，用于命中检测 + 选中高亮）
        bounds_rect: QGraphicsRectItem = QGraphicsRectItem(
            QRectF(px, py, pw, ph)
        )
        bounds_rect.setPen(QPen(Qt.PenStyle.NoPen))
        bounds_rect.setZValue(15)
        bounds_rect.setData(0, frame.element_id)
        bounds_rect.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)

        self._scene.addItem(pix_item)
        self._scene.addItem(border_item)
        self._scene.addItem(bounds_rect)
        self._element_items[frame.element_id] = (
            [pix_item, border_item], bounds_rect,
        )

    # ------------------------------------------------------------------
    # 比例尺 / 图例 / 指北针 渲染
    # ------------------------------------------------------------------

    @staticmethod
    def _content_bounds(items: list[QGraphicsItem]) -> QRectF | None:
        """计算内容图元的联合包围盒；无图元时返回 None。"""
        if not items:
            return None
        combined = items[0].sceneBoundingRect()
        for item in items[1:]:
            combined = combined.united(item.sceneBoundingRect())
        return combined

    def _make_bounds_rect(
        self, items: list[QGraphicsItem], fallback: QRectF
    ) -> QGraphicsRectItem:
        """构建命中矩形：优先紧贴渲染内容，无内容时回退到元素盒。

        内容包围盒外扩 2px 便于点击，避免命中区与视觉内容不一致。
        """
        combined = self._content_bounds(items)
        if combined is not None:
            combined.adjust(-2, -2, 2, 2)
            rect: QRectF = combined
        else:
            rect = fallback
        return QGraphicsRectItem(rect)

    def _render_scale_bar_element(
        self, element: ScaleBarElement, dpi: float
    ) -> None:
        """渲染比例尺。"""
        if self._document is None:
            return

        # 查找关联的地图框
        linked_frame: MapFrameElement | None = None
        if element.linked_frame_id:
            found = self._find_element(element.linked_frame_id)
            if isinstance(found, MapFrameElement):
                linked_frame = found
        if linked_frame is None:
            # 自动关联到第一个地图框
            for e in self._document.elements:
                if isinstance(e, MapFrameElement):
                    linked_frame = e
                    element.linked_frame_id = e.element_id
                    break

        items: list[QGraphicsItem] = render_scale_bar(
            element, self._scene, dpi, linked_frame,
        )

        # 边界矩形 —— 紧贴渲染内容，命中区与视觉一致
        px: float = _mm_to_px(element.x_mm, dpi)
        py: float = _mm_to_px(element.y_mm, dpi)
        pw: float = _mm_to_px(element.width_mm, dpi) if element.width_mm > 0 else 120
        ph: float = _mm_to_px(element.height_mm, dpi) if element.height_mm > 0 else 8
        bounds_rect: QGraphicsRectItem = self._make_bounds_rect(
            items, QRectF(px, py, pw, ph),
        )
        bounds_rect.setPen(QPen(Qt.PenStyle.NoPen))
        bounds_rect.setZValue(15)
        bounds_rect.setData(0, element.element_id)
        bounds_rect.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self._scene.addItem(bounds_rect)

        self._element_items[element.element_id] = (items, bounds_rect)

    def _render_legend_element(
        self, element: LegendElement, dpi: float
    ) -> None:
        """渲染图例。"""
        if self._document is None:
            return

        # 查找关联的地图框
        if element.linked_frame_id:
            found = self._find_element(element.linked_frame_id)
            if not isinstance(found, MapFrameElement):
                element.linked_frame_id = ""
        if not element.linked_frame_id:
            for e in self._document.elements:
                if isinstance(e, MapFrameElement):
                    element.linked_frame_id = e.element_id
                    break

        items: list[QGraphicsItem] = render_legend(
            element, self._scene, dpi, self._snapshot,
        )

        # 边界矩形 —— 紧贴渲染内容，命中区与视觉一致
        px: float = _mm_to_px(element.x_mm, dpi)
        py: float = _mm_to_px(element.y_mm, dpi)
        pw: float = _mm_to_px(element.width_mm, dpi) if element.width_mm > 0 else 80
        ph: float = _mm_to_px(element.height_mm, dpi) if element.height_mm > 0 else 20
        bounds_rect: QGraphicsRectItem = self._make_bounds_rect(
            items, QRectF(px, py, pw, ph),
        )
        bounds_rect.setPen(QPen(Qt.PenStyle.NoPen))
        bounds_rect.setZValue(15)
        bounds_rect.setData(0, element.element_id)
        bounds_rect.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self._scene.addItem(bounds_rect)

        self._element_items[element.element_id] = (items, bounds_rect)

    def _render_north_arrow_element(
        self, element: NorthArrowElement, dpi: float
    ) -> None:
        """渲染指北针。"""
        if self._document is None:
            return

        items: list[QGraphicsItem] = render_north_arrow(
            element, self._scene, dpi,
        )

        # 边界矩形 —— 紧贴渲染内容，命中区与视觉一致
        px: float = _mm_to_px(element.x_mm, dpi)
        py: float = _mm_to_px(element.y_mm, dpi)
        pw: float = _mm_to_px(element.width_mm, dpi) if element.width_mm > 0 else 15
        ph: float = _mm_to_px(element.height_mm, dpi) if element.height_mm > 0 else 20
        bounds_rect: QGraphicsRectItem = self._make_bounds_rect(
            items, QRectF(px, py, pw, ph),
        )
        bounds_rect.setPen(QPen(Qt.PenStyle.NoPen))
        bounds_rect.setZValue(15)
        bounds_rect.setData(0, element.element_id)
        bounds_rect.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self._scene.addItem(bounds_rect)

        self._element_items[element.element_id] = (items, bounds_rect)

    def _render_text_element(
        self, element: TextElement, dpi: float
    ) -> None:
        """渲染文本元素。"""
        if self._document is None:
            return

        items: list[QGraphicsItem] = render_text(
            element, self._scene, dpi,
        )

        # 使用实际文本图元的包围盒计算命中检测区域
        if items:
            combined = items[0].sceneBoundingRect()
            for it in items[1:]:
                combined = combined.united(it.sceneBoundingRect())
            # 添加一点内边距便于点击
            combined.adjust(-4, -4, 4, 4)
            bounds_rect: QGraphicsRectItem = QGraphicsRectItem(combined)
        else:
            px: float = _mm_to_px(element.x_mm, dpi)
            py: float = _mm_to_px(element.y_mm, dpi)
            pw: float = _mm_to_px(element.width_mm, dpi) if element.width_mm > 0 else 40
            ph: float = _mm_to_px(element.height_mm, dpi) if element.height_mm > 0 else 8
            bounds_rect = QGraphicsRectItem(QRectF(px, py, pw, ph))
        bounds_rect.setPen(QPen(Qt.PenStyle.NoPen))
        bounds_rect.setZValue(15)
        bounds_rect.setData(0, element.element_id)
        bounds_rect.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self._scene.addItem(bounds_rect)

        self._element_items[element.element_id] = (items, bounds_rect)

    # ------------------------------------------------------------------
    # 场景重建
    # ------------------------------------------------------------------

    def _rebuild_scene(self) -> None:
        """清空场景并重建纸张和全部元素。"""
        self._scene.clear()
        self._grid_items.clear()
        self._margin_items.clear()
        self._element_items.clear()
        self._paper_item = None
        self._shadow_item = None

        if self._document is None:
            self._scene.setSceneRect(0, 0, 1000, 700)
            return

        page = self._document.page
        dpi: float = self._view_dpi

        paper_w: float = _mm_to_px(page.width_mm, dpi)
        paper_h: float = _mm_to_px(page.height_mm, dpi)

        # --- 阴影 ---
        shadow_offset: float = _mm_to_px(2.0, dpi)
        self._shadow_item = self._scene.addRect(
            QRectF(shadow_offset, shadow_offset, paper_w, paper_h),
            QPen(Qt.PenStyle.NoPen),
            QBrush(_SHADOW_COLOR),
        )
        self._shadow_item.setZValue(-1)

        # --- 纸张 ---
        paper_rect: QRectF = QRectF(0, 0, paper_w, paper_h)
        self._paper_item = self._scene.addRect(
            paper_rect,
            QPen(QColor("#9ca3af"), 1.0),
            QBrush(_PAPER_COLOR),
        )
        self._paper_item.setZValue(0)

        # --- 页边距线 ---
        margin: float = _mm_to_px(page.margin_mm, dpi)
        margin_pen: QPen = QPen(_MARGIN_COLOR, 0.5, Qt.PenStyle.DashLine)
        margin_item: QGraphicsRectItem = self._scene.addRect(
            QRectF(margin, margin, paper_w - 2 * margin, paper_h - 2 * margin),
            margin_pen,
            QBrush(Qt.BrushStyle.NoBrush),
        )
        margin_item.setZValue(1)
        self._margin_items.append(margin_item)

        # --- 网格 ---
        self._draw_grid(paper_w, paper_h, dpi)

        # --- 渲染所有元素 ---
        for element in self._document.elements:
            self._render_element(element)

        # 场景矩形留出拖拽空间
        padding: float = max(paper_w, paper_h) * 0.5
        self._scene.setSceneRect(
            -padding, -padding,
            paper_w + 2 * padding, paper_h + 2 * padding,
        )

    def _draw_grid(
        self, paper_w: float, paper_h: float, dpi: float
    ) -> None:
        """在纸张范围内绘制厘米网格。"""
        cm_px: float = _mm_to_px(10.0, dpi)
        thin_pen: QPen = QPen(_GRID_COLOR, 0.3)
        thick_pen: QPen = QPen(_GRID_10CM_COLOR, 0.6)

        x: float = cm_px
        step: int = 1
        while x < paper_w:
            pen: QPen = thick_pen if step % 10 == 0 else thin_pen
            line = self._scene.addLine(x, 0, x, paper_h, pen)
            line.setZValue(0.5)
            self._grid_items.append(line)
            x += cm_px
            step += 1

        y: float = cm_px
        step = 1
        while y < paper_h:
            pen = thick_pen if step % 10 == 0 else thin_pen
            line = self._scene.addLine(0, y, paper_w, y, pen)
            line.setZValue(0.5)
            self._grid_items.append(line)
            y += cm_px
            step += 1

    # ------------------------------------------------------------------
    # 视图控制
    # ------------------------------------------------------------------

    def _fit_page(self) -> None:
        """将纸张缩放到适配当前视口大小。"""
        if self._paper_item is None:
            return
        paper_rect: QRectF = self._paper_item.rect()
        margin_factor: float = 1.08
        fit_rect: QRectF = QRectF(
            paper_rect.x() - paper_rect.width() * 0.04,
            paper_rect.y() - paper_rect.height() * 0.04,
            paper_rect.width() * margin_factor,
            paper_rect.height() * margin_factor,
        )
        self.fitInView(fit_rect, Qt.AspectRatioMode.KeepAspectRatio)
        self._needs_initial_fit = False

    # ------------------------------------------------------------------
    # 元素命中检测
    # ------------------------------------------------------------------

    def _elements_at(self, scene_pos: QPointF) -> list[str]:
        """返回场景坐标处命中的所有元素 ID（从顶层到底层）。

        以文档元素顺序为 z 序（后添加的绘制在上层），从顶层向底层
        逐个检测；每个元素内容图元命中最优先，其次才是命中矩形。
        这样叠压时，视觉上位于上方的元素在其整个区域内都拥有点击。
        """
        if self._document is None:
            return []
        stacked: list[str] = []
        for elem in reversed(self._document.elements):
            cached = self._element_items.get(elem.element_id)
            if cached is None:
                continue
            items, bounds_rect = cached
            hit: bool = bounds_rect.contains(scene_pos)
            if not hit:
                for item in items:
                    if item.contains(item.mapFromScene(scene_pos)):
                        hit = True
                        break
            if hit:
                stacked.append(elem.element_id)
        return stacked

    def _element_at(self, scene_pos: QPointF) -> str | None:
        """返回场景坐标处最上层的元素 ID，未命中返回 None。"""
        stacked = self._elements_at(scene_pos)
        return stacked[0] if stacked else None

    def _element_above_content(
        self, scene_pos: QPointF, selected_id: str
    ) -> bool:
        """该点是否被位于 selected_id 之上的其他元素内容命中。

        用于已选中元素的拖动判定：若点被更上层元素的内容命中，
        说明用户意图指向上层元素，不应拖动当前选中元素。
        从顶层向底层遍历，遇到 selected_id 即停止（其下元素不算）。
        """
        if self._document is None:
            return False
        for elem in reversed(self._document.elements):
            if elem.element_id == selected_id:
                return False
            cached = self._element_items.get(elem.element_id)
            if cached is None:
                continue
            items, bounds_rect = cached
            if bounds_rect.contains(scene_pos):
                return True
            for item in items:
                if item.contains(item.mapFromScene(scene_pos)):
                    return True
        return False

    def _cycle_select(self, stacked: list[str]) -> None:
        """在叠压元素间循环选中下一层。

        当前选中元素不在栈中时选中顶层；否则选中其下一层，
        到底后回绕到顶层。
        """
        if not stacked:
            return
        current = self._selected_element_id
        if current in stacked:
            index = stacked.index(current)
            next_id = stacked[(index + 1) % len(stacked)]
        else:
            next_id = stacked[0]
        self._select_element(next_id)

    # ------------------------------------------------------------------
    # Qt 事件
    # ------------------------------------------------------------------

    def enterEvent(self, event) -> None:
        """鼠标进入时显示手形光标。"""
        super().enterEvent(event)
        self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._needs_initial_fit and self._paper_item is not None:
            self._fit_page()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._needs_initial_fit and self._paper_item is not None:
            self._fit_page()
        # 进入布局模式后不再重置视图，用户可自由缩放/平移

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_start = event.position()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            scene_pos: QPointF = self.mapToScene(event.pos())
            # Alt / Ctrl + 点击 → 在叠压元素间循环选中（纯选择，不拖拽）。
            # 优先于缩放手柄：保证小元素即使被手柄覆盖也能稳定循环。
            alt_or_ctrl: bool = bool(
                event.modifiers()
                & (Qt.KeyboardModifier.AltModifier
                   | Qt.KeyboardModifier.ControlModifier)
            )
            if alt_or_ctrl:
                stacked = self._elements_at(scene_pos)
                if stacked:
                    self._cycle_select(stacked)
                else:
                    self._select_element(None)
                event.accept()
                return
            # 先检查是否点击了缩放手柄
            handle_idx = self._handle_at(scene_pos)
            if handle_idx is not None and self._selected_element_id is not None:
                self._resizing_handle_index = handle_idx
                self._resize_start_pos = scene_pos
                elem = self._find_element(self._selected_element_id)
                if elem is not None:
                    self._resize_start_rect = (
                        elem.x_mm, elem.y_mm, elem.width_mm, elem.height_mm,
                    )
                event.accept()
                return
            # 已选中元素：其整个选择盒（含外扩边距）均可拖拽移动。
            # 小元素（如文本）内容盒过小，若只按内容命中，按到附近空白会
            # 选到下方的大框；这里让选中元素的整盒+边距都能抓住拖动。
            # 若该点被位于其上方的其他元素内容命中，则仍交常规命中处理。
            # 抓取边距按设备像素换算，保证任意缩放级别手感一致。
            # Shift+点已选中的地图框除外——那是地图内容平移，交下方分支。
            if self._selected_element_id is not None:
                sel = self._find_element(self._selected_element_id)
                if sel is not None:
                    cached = self._element_items.get(sel.element_id)
                    if cached is not None:
                        _sel_items, sel_bounds = cached
                        scale = abs(self.viewportTransform().m11())
                        gm = (
                            self._GRAB_MARGIN / scale
                            if scale > 0 else self._GRAB_MARGIN
                        )
                        grab_rect: QRectF = sel_bounds.rect().adjusted(
                            -gm, -gm, gm, gm,
                        )
                        shift_held = bool(
                            event.modifiers()
                            & Qt.KeyboardModifier.ShiftModifier
                        )
                        if (
                            not (shift_held and isinstance(sel, MapFrameElement))
                            and grab_rect.contains(scene_pos)
                            and not self._element_above_content(
                                scene_pos, sel.element_id
                            )
                        ):
                            self._dragging_element_id = sel.element_id
                            self._drag_start_pos = scene_pos
                            self._drag_start_mm = (sel.x_mm, sel.y_mm)
                            self.viewport().setCursor(
                                Qt.CursorShape.ClosedHandCursor
                            )
                            event.accept()
                            return
            elem_id: str | None = self._element_at(scene_pos)
            if elem_id is not None:
                elem = self._find_element(elem_id)
                # Shift + 点击已选中的地图框 → 进入地图内容平移模式
                shift_held = bool(
                    event.modifiers() & Qt.KeyboardModifier.ShiftModifier
                )
                if (shift_held
                        and isinstance(elem, MapFrameElement)
                        and elem_id == self._selected_element_id):
                    self._map_panning = True
                    self._drag_start_pos = scene_pos
                    self._map_pan_center = (elem.map_center_x, elem.map_center_y)
                    self.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
                    event.accept()
                    return
                # 点击元素：选中并准备拖拽移动
                self._select_element(elem_id)
                self._dragging_element_id = elem_id
                self._drag_start_pos = scene_pos
                if elem is not None:
                    self._drag_start_mm = (elem.x_mm, elem.y_mm)
                self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return
            # 点击空白区：取消选择
            self._select_element(None)
        # 非元素左键 / 中键 / 右键 → 交由 ScrollHandDrag 处理
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        # Shift+拖拽 → 平移地图框内容
        if self._map_panning and self._drag_start_pos is not None:
            scene_pos = self.mapToScene(event.pos())
            elem = self._find_element(self._selected_element_id)
            if isinstance(elem, MapFrameElement):
                dx_scene = scene_pos.x() - self._drag_start_pos.x()
                dy_scene = scene_pos.y() - self._drag_start_pos.y()
                mupp = elem.map_units_per_pixel
                # 场景 X 与地图 X 同向，场景 Y 与地图 Y 反向
                elem.map_center_x = self._map_pan_center[0] - dx_scene * mupp
                elem.map_center_y = self._map_pan_center[1] + dy_scene * mupp
                self._render_element(elem)
                if self._selected_element_id == elem.element_id:
                    self._select_element(elem.element_id)
            event.accept()
            return

        if self._panning and self._pan_start is not None:
            delta = event.position() - self._pan_start
            self._pan_start = event.position()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - round(delta.x()))
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - round(delta.y()))
            event.accept()
            return
        if self._resizing_handle_index is not None:
            self._do_resize(event)
            event.accept()
            return
        if self._dragging_element_id is not None:
            scene_pos = self.mapToScene(event.pos())
            if self._drag_start_pos is None:
                return
            elem = self._find_element(self._dragging_element_id)
            if elem is None or self._document is None:
                return
            dpi: float = self._view_dpi
            dx_mm: float = (scene_pos.x() - self._drag_start_pos.x()) / dpi * 25.4
            dy_mm: float = (scene_pos.y() - self._drag_start_pos.y()) / dpi * 25.4
            elem.x_mm = self._drag_start_mm[0] + dx_mm
            elem.y_mm = self._drag_start_mm[1] + dy_mm
            self._update_element_position(elem)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._map_panning:
            self._map_panning = False
            old_cx, old_cy = self._map_pan_center
            elem = self._find_element(self._selected_element_id)
            if isinstance(elem, MapFrameElement):
                new_cx, new_cy = elem.map_center_x, elem.map_center_y
                if (old_cx, old_cy) != (new_cx, new_cy):
                    self._push_undo(
                        "平移地图",
                        undo_action=partial(
                            self._restore_map_center, elem, old_cx, old_cy
                        ),
                        redo_action=partial(
                            self._restore_map_center, elem, new_cx, new_cy
                        ),
                    )
            self._drag_start_pos = None
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            self._pan_start = None
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        if self._resizing_handle_index is not None:
            self._finish_resize()
            event.accept()
            return
        if self._dragging_element_id is not None:
            eid = self._dragging_element_id
            old_x, old_y = self._drag_start_mm
            elem = self._find_element(eid)
            new_x = elem.x_mm if elem else old_x
            new_y = elem.y_mm if elem else old_y
            if (old_x, old_y) != (new_x, new_y) and elem is not None:
                self._push_undo(
                    "移动元素",
                    undo_action=partial(self._move_element_to, elem, old_x, old_y),
                    redo_action=partial(self._move_element_to, elem, new_x, new_y),
                )
            self._dragging_element_id = None
            self._drag_start_pos = None
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        """右键菜单：删除选中元素。"""
        scene_pos: QPointF = self.mapToScene(event.pos())
        elem_id: str | None = self._element_at(scene_pos)
        if elem_id is not None:
            self._select_element(elem_id)
        if self._selected_element_id is None:
            return
        menu: QMenu = QMenu(self)
        delete_action: QAction = menu.addAction("删除")
        delete_action.triggered.connect(self._delete_selected)
        menu.exec(event.globalPos())

    def mouseDoubleClickEvent(self, event) -> None:
        """双击元素打开属性编辑对话框。"""
        if event.button() == Qt.MouseButton.LeftButton:
            scene_pos: QPointF = self.mapToScene(event.pos())
            elem_id: str | None = self._element_at(scene_pos)
            if elem_id is not None:
                self._select_element(elem_id)
                self._open_properties_dialog(elem_id)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def _open_properties_dialog(self, element_id: str) -> None:
        """打开元素属性编辑对话框。"""
        element = self._find_element(element_id)
        if element is None:
            return
        from app.presentation.widgets.element_properties_dialog import (
            ElementPropertiesDialog,
        )

        dialog = ElementPropertiesDialog(self)
        dialog.set_element(element)
        if dialog.exec() != ElementPropertiesDialog.DialogCode.Accepted:
            return
        changes = dialog.changes()
        if changes:
            self.apply_element_changes(element_id, changes)

    def wheelEvent(self, event) -> None:
        # Ctrl+滚轮 → 始终缩放整个布局视图
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor: float = 1.05 if event.angleDelta().y() > 0 else 1.0 / 1.05
            self._apply_scale(factor)
            event.accept()
            return

        # 选中地图框时，滚轮缩放地图内容（map_units_per_pixel）
        if self._selected_element_id is not None:
            elem = self._find_element(self._selected_element_id)
            if isinstance(elem, MapFrameElement):
                scene_pos = self.mapToScene(event.position().toPoint())
                old = self._element_items.get(elem.element_id)
                if old is not None and old[1].contains(scene_pos):
                    zoom_in = event.angleDelta().y() > 0
                    factor = 0.8 if zoom_in else 1.25  # mupp 越小越放大
                    elem.map_units_per_pixel *= factor
                    self._render_element(elem)
                    if self._selected_element_id == elem.element_id:
                        self._select_element(elem.element_id)
                    event.accept()
                    return

        # 默认：缩放整个布局视图
        factor = 1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15
        self._apply_scale(factor)
        event.accept()

    def _apply_scale(self, factor: float) -> None:
        view_pos = self.mapFromGlobal(self.cursor().pos())
        if not self.viewport().rect().contains(view_pos):
            view_pos = self.viewport().rect().center()
        scene_pos: QPointF = self.mapToScene(view_pos)

        self.scale(factor, factor)

        new_scene_pos: QPointF = self.mapToScene(view_pos)
        delta: QPointF = scene_pos - new_scene_pos
        self.translate(delta.x(), delta.y())

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 元素操作：选择、删除、撤销
    # ------------------------------------------------------------------

    def _select_element(self, element_id: str | None) -> None:
        """选中或取消选中元素，更新视觉高亮和缩放手柄。"""
        self._remove_resize_handles()
        # 取消旧选择的高亮
        if self._selected_element_id is not None:
            old = self._element_items.get(self._selected_element_id)
            if old is not None:
                _items, bounds_rect = old
                bounds_rect.setPen(QPen(Qt.PenStyle.NoPen))
        self._selected_element_id = element_id
        # 应用新选择高亮
        if element_id is not None:
            new = self._element_items.get(element_id)
            if new is not None:
                _items, bounds_rect = new
                highlight_pen: QPen = QPen(_SELECTION_COLOR, 2.0)
                highlight_pen.setCosmetic(True)
                bounds_rect.setPen(highlight_pen)
                self._create_resize_handles(bounds_rect.rect())
        self.element_selected.emit(element_id)

    def remove_element(self, element_id: str) -> None:
        """从文档和场景中移除指定元素。

        若移除的是当前选中元素，会一并清除选中高亮和缩放手柄，
        并立即刷新视口，避免矩形滞留到下次点击。
        """
        if self._document is None:
            return
        elem = self._find_element(element_id)
        if elem is None:
            return
        # 从文档中移除
        current = list(self._document.elements)
        current = [e for e in current if e.element_id != element_id]
        object.__setattr__(self._document, "elements", tuple(current))
        # 从场景中移除
        old = self._element_items.pop(element_id, None)
        if old is not None:
            items_list, bounds_rect = old
            for item in items_list:
                self._scene.removeItem(item)
            self._scene.removeItem(bounds_rect)
        # 移除选中状态与缩放手柄，避免残留矩形
        if self._selected_element_id == element_id:
            self._selected_element_id = None
            self._remove_resize_handles()
        self.viewport().update()
        self.elements_changed.emit()

    def _delete_selected(self) -> None:
        """删除当前选中的元素。"""
        if self._selected_element_id is None:
            return
        self._delete_element_with_undo(self._selected_element_id)

    def _delete_element_with_undo(self, element_id: str) -> None:
        """删除指定元素并推入撤销栈（保留快照供撤销恢复）。"""
        elem = self._find_element(element_id)
        if elem is None:
            return
        # 快照元素状态用于撤销
        elem_snapshot = _snapshot_element(elem, element_id)
        elem_type_name: str = type(elem).__name__
        self.remove_element(element_id)
        self._push_undo(
            f"删除{elem_type_name}",
            undo_action=partial(self._add_element, elem_snapshot),
            redo_action=partial(self.remove_element, element_id),
        )

    def _toggle_existing(self, existing: LayoutElement) -> str | None:
        """处理已存在元素的点击切换：选中保持高亮或删除。

        参数:
            existing: 已存在的同类型元素。

        返回:
            已选中的元素 ID；删除时返回 None。
        """
        if self._selected_element_id == existing.element_id:
            # 已选中 → 再次点击删除
            self._delete_element_with_undo(existing.element_id)
            return None
        # 已存在但未选中 → 选中并保持高亮
        self._select_element(existing.element_id)
        return existing.element_id

    def clear_all_elements(self) -> None:
        """清空图幅中的全部元素（可撤销）。

        撤销可恢复全部元素；此方法仅由工具栏"清空"按钮触发。
        """
        if self._document is None:
            return
        elements = list(self._document.elements)
        if not elements:
            return
        snapshots = [_snapshot_element(e, e.element_id) for e in elements]
        self._clear_all_elements_now()
        self._push_undo(
            "清空图幅",
            undo_action=partial(self._restore_elements, snapshots),
            redo_action=self._clear_all_elements_now,
        )

    def _clear_all_elements_now(self) -> None:
        """不推入撤销栈地清空全部元素（用于清空的重做）。"""
        if self._document is None:
            return
        for element in list(self._document.elements):
            self.remove_element(element.element_id)

    def _restore_elements(self, snapshots: list[LayoutElement]) -> None:
        """恢复此前清空的全部元素（用于清空的撤销）。"""
        for element in snapshots:
            self._add_element(element)

    def has_elements(self) -> bool:
        """图幅中是否包含制图元素。"""
        return bool(self._document is not None and self._document.elements)

    def _move_element_to(self, element, x_mm: float, y_mm: float) -> None:
        """将元素移动到指定毫米位置（用于撤销/重做）。"""
        element.x_mm = x_mm
        element.y_mm = y_mm
        self._update_element_position(element)

    # ------------------------------------------------------------------
    # 撤销 / 重做
    # ------------------------------------------------------------------

    def _push_undo(
        self,
        description: str,
        undo_action: Callable[[], object],
        redo_action: Callable[[], object],
    ) -> None:
        """将可逆操作压入撤销栈。"""
        self._undo_stack.append((description, undo_action, redo_action))
        self._redo_stack.clear()
        # 限制栈深度
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)
        self.undo_state_changed.emit(self.can_undo(), self.can_redo())

    def _undo(self) -> None:
        """撤销最近一步操作。"""
        if not self._undo_stack:
            return
        _desc, undo_fn, redo_fn = self._undo_stack.pop()
        undo_fn()
        self._redo_stack.append((_desc, undo_fn, redo_fn))
        self.undo_state_changed.emit(self.can_undo(), self.can_redo())

    def _redo(self) -> None:
        """重做最近一次撤销。"""
        if not self._redo_stack:
            return
        _desc, undo_fn, redo_fn = self._redo_stack.pop()
        redo_fn()
        self._undo_stack.append((_desc, undo_fn, redo_fn))
        self.undo_state_changed.emit(self.can_undo(), self.can_redo())

    def can_undo(self) -> bool:
        """撤销栈非空时返回 True。"""
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        """重做栈非空时返回 True。"""
        return bool(self._redo_stack)

    @property
    def selected_element_id(self) -> str | None:
        """返回当前选中元素的 ID。"""
        return self._selected_element_id

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _find_existing(self, element_type: type) -> LayoutElement | None:
        """查找文档中第一个指定类型的元素，不存在返回 None。"""
        if self._document is None:
            return None
        for e in self._document.elements:
            if isinstance(e, element_type):
                return e
        return None

    def _find_element(self, element_id: str | None) -> LayoutElement | None:
        """按 ID 查找布局元素；空 ID 时返回 None。"""
        if self._document is None or element_id is None:
            return None
        for elem in self._document.elements:
            if elem.element_id == element_id:
                return elem
        return None

    def _update_element_position(self, element) -> None:
        """更新元素在场景中的位置（用于拖拽移动）。
        注意：非地图框元素拖拽后需要重新渲染以更新内部布局。
        """
        if self._document is None:
            return
        dpi: float = self._view_dpi

        # 对于非地图框的装饰元素，重新渲染以更新内部坐标
        if not isinstance(element, MapFrameElement):
            self._render_element(element)
            # 更新缩放手柄到新位置
            if (self._selected_element_id == element.element_id
                    and self._selected_element_id in self._element_items):
                _items, br = self._element_items[self._selected_element_id]
                self._create_resize_handles(br.rect())
            self.viewport().update()
            return

        old = self._element_items.get(element.element_id)
        if old is None:
            return
        items_list, bounds_rect = old
        px: float = _mm_to_px(element.x_mm, dpi)
        py: float = _mm_to_px(element.y_mm, dpi)
        pw: float = _mm_to_px(element.width_mm, dpi)
        ph: float = _mm_to_px(element.height_mm, dpi)

        # 像素图层
        if items_list:
            items_list[0].setPos(px, py)
        # 边框
        if len(items_list) > 1:
            border_item = items_list[1]
            if isinstance(border_item, QGraphicsRectItem):
                border_item.setRect(QRectF(px, py, pw, ph))
        # 边界矩形
        bounds_rect.setRect(QRectF(px, py, pw, ph))

        # 同步更新缩放手柄位置
        if self._selected_element_id == element.element_id:
            self._create_resize_handles(bounds_rect.rect())

        # 强制刷新避免拖拽残影
        self.viewport().update()

    # ------------------------------------------------------------------
    # 缩放手柄
    # ------------------------------------------------------------------

    _HANDLE_SIZE: float = 8.0
    _HANDLE_MARGIN: float = 2.0  # 缩放手柄命中区外扩（设备像素）
    _GRAB_MARGIN: float = 10.0  # 已选中元素可拖动区域外扩（设备像素）

    def _create_resize_handles(self, rect: QRectF) -> None:
        """在元素边界矩形的 8 个位置创建缩放手柄。"""
        self._remove_resize_handles()
        hs = self._HANDLE_SIZE
        positions = [
            (rect.left(), rect.top()),
            (rect.center().x(), rect.top()),
            (rect.right(), rect.top()),
            (rect.left(), rect.center().y()),
            (rect.right(), rect.center().y()),
            (rect.left(), rect.bottom()),
            (rect.center().x(), rect.bottom()),
            (rect.right(), rect.bottom()),
        ]
        for sx, sy in positions:
            handle = QGraphicsRectItem(QRectF(-hs / 2, -hs / 2, hs, hs))
            handle.setPos(sx, sy)
            handle.setBrush(QBrush(QColor("#ffffff")))
            handle.setPen(QPen(_SELECTION_COLOR, 1.0))
            handle.setZValue(100)
            handle.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
            handle.setFlag(
                QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True,
            )
            self._scene.addItem(handle)
            self._resize_handles.append(handle)

    def _remove_resize_handles(self) -> None:
        """移除所有缩放手柄。"""
        for handle in self._resize_handles:
            self._scene.removeItem(handle)
        self._resize_handles.clear()

    def _handle_at(self, scene_pos: QPointF) -> int | None:
        """检查场景坐标是否命中某个缩放手柄。

        手柄是 ItemIgnoresTransformations 图元，视觉尺寸恒定（设备像素），
        sceneBoundingRect 是场景像素、不随缩放变化，直接用它加场景边距会让
        命中区大小随缩放漂移。这里改为以手柄中心为锚点、按设备像素半径
        换算场景半径，保证任意缩放级别下手感一致（视觉手柄 + 少量余量）。
        命中半径内的多个手柄取最近者：小元素缩到很小时相邻手柄会重叠，
        取最近保证点右下角手柄不会因为列表顺序先命中右中手柄。
        """
        scale = abs(self.viewportTransform().m11())
        radius = (
            (self._HANDLE_SIZE / 2.0 + self._HANDLE_MARGIN) / scale
            if scale > 0 else self._HANDLE_SIZE
        )
        radius2 = radius * radius
        best_idx: int | None = None
        best_d2: float = float("inf")
        for i, handle in enumerate(self._resize_handles):
            center = handle.scenePos()
            dx = scene_pos.x() - center.x()
            dy = scene_pos.y() - center.y()
            d2 = dx * dx + dy * dy
            if d2 <= radius2 and d2 < best_d2:
                best_d2 = d2
                best_idx = i
        return best_idx

    def _do_resize(self, event) -> None:
        """根据手柄索引和鼠标位移更新元素尺寸。"""
        if (
            self._selected_element_id is None
            or self._resizing_handle_index is None
            or self._resize_start_pos is None
            or self._document is None
        ):
            return
        elem = self._find_element(self._selected_element_id)
        if elem is None:
            return
        dpi: float = self._view_dpi
        scene_pos = self.mapToScene(event.pos())
        dx_mm = (scene_pos.x() - self._resize_start_pos.x()) / dpi * 25.4
        dy_mm = (scene_pos.y() - self._resize_start_pos.y()) / dpi * 25.4

        ox, oy, ow, oh = self._resize_start_rect
        idx = self._resizing_handle_index
        new_x, new_y, new_w, new_h = ox, oy, ow, oh
        min_size = 5.0

        # 水平方向
        if idx in (0, 3, 5):  # 左侧
            new_x = ox + dx_mm
            new_w = ow - dx_mm
        elif idx in (2, 4, 7):  # 右侧
            new_w = ow + dx_mm
        # 垂直方向
        if idx in (0, 1, 2):  # 顶部
            new_y = oy + dy_mm
            new_h = oh - dy_mm
        elif idx in (5, 6, 7):  # 底部
            new_h = oh + dy_mm

        # 最小尺寸约束
        if new_w < min_size:
            if idx in (0, 3, 5):
                new_x = ox + ow - min_size
            new_w = min_size
        if new_h < min_size:
            if idx in (0, 1, 2):
                new_y = oy + oh - min_size
            new_h = min_size

        elem.x_mm = new_x
        elem.y_mm = new_y
        elem.width_mm = new_w
        elem.height_mm = new_h
        self._render_element(elem)
        # 更新选中高亮和手柄位置
        if self._selected_element_id is not None:
            old = self._element_items.get(self._selected_element_id)
            if old is not None:
                _items, bounds_rect = old
                highlight_pen = QPen(_SELECTION_COLOR, 2.0)
                highlight_pen.setCosmetic(True)
                bounds_rect.setPen(highlight_pen)
                self._create_resize_handles(bounds_rect.rect())

    def _finish_resize(self) -> None:
        """完成缩放操作，推入撤销栈。"""
        if (
            self._selected_element_id is None
            or self._resizing_handle_index is None
        ):
            self._resizing_handle_index = None
            self._resize_start_pos = None
            return
        elem = self._find_element(self._selected_element_id)
        if elem is not None:
            old_rect = self._resize_start_rect
            new_rect = (elem.x_mm, elem.y_mm, elem.width_mm, elem.height_mm)
            if old_rect != new_rect:
                self._push_undo(
                    "调整元素大小",
                    undo_action=partial(self._set_rect, elem, old_rect),
                    redo_action=partial(self._set_rect, elem, new_rect),
                )
        self._resizing_handle_index = None
        self._resize_start_pos = None

    def _set_rect(
        self,
        element: LayoutElement,
        rect: tuple[float, float, float, float],
    ) -> None:
        """设置元素的位置和尺寸（用于撤销/重做）。"""
        element.x_mm, element.y_mm, element.width_mm, element.height_mm = rect
        self._render_element(element)
        if self._selected_element_id == element.element_id:
            old = self._element_items.get(element.element_id)
            if old is not None:
                _items, bounds_rect = old
                highlight_pen = QPen(_SELECTION_COLOR, 2.0)
                highlight_pen.setCosmetic(True)
                bounds_rect.setPen(highlight_pen)
                self._create_resize_handles(bounds_rect.rect())
