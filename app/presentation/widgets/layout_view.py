"""布局视图 — 虚拟纸张上的制图元素排版与预览。

LayoutView 是一个独立的 QGraphicsView，与 MapCanvas 平级。
它通过 QStackedWidget 切换显示，不依赖地图画布的内部状态。
"""

from __future__ import annotations

from collections.abc import Callable

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
        - 左键选择 / 拖拽移动制图元素
        - Ctrl + 滚轮精细缩放

    坐标系:
        场景原点 (0, 0) = 页面左上角，X 向右，Y 向下。
        所有元素位置以毫米为单位存储，渲染时按 DPI 换算为像素。
    """

    # 选中元素变化时发出
    element_selected = Signal(str)  # element_id

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

    def document(self) -> LayoutDocument | None:
        """返回当前排版文档，未设置时返回 None。"""
        return self._document

    def fit_page(self) -> None:
        """将纸张适配到当前视口大小。"""
        self._fit_page()

    def has_content(self) -> bool:
        """是否已设置排版文档。"""
        return self._document is not None

    def add_map_frame(self) -> str | None:
        """在页面中心添加一个默认大小地图框。

        返回:
            新元素的 ID，若文档未设置则返回 None。
        """
        if self._document is None or self._document.page is None:
            return None
        page = self._document.page
        # 默认地图框占据可打印区域的 70%
        pw: float = page.printable_width_mm
        ph: float = page.printable_height_mm
        fw: float = pw * 0.7
        fh: float = ph * 0.7
        fx: float = page.margin_mm + (pw - fw) / 2
        fy: float = page.margin_mm + (ph - fh) / 2

        # 从快照获取初始地图中心和分辨率
        cx, cy = 0.0, 0.0
        mupp = 1.0
        if self._snapshot is not None and self._snapshot.layers:
            first = self._snapshot.layers[0]
            b = first.bounds
            cx = (b[0] + b[2]) / 2.0
            cy = (b[1] + b[3]) / 2.0
            # 估算合适的 mupp 使数据适配地图框
            data_w = max(b[2] - b[0], 1e-9)
            data_h = max(b[3] - b[1], 1e-9)
            px_w = _mm_to_px(fw, page.dpi)
            px_h = _mm_to_px(fh, page.dpi)
            mupp = max(data_w / px_w, data_h / px_h)

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
        self._push_undo(
            "添加地图框",
            undo_action=lambda eid=frame.element_id: self.remove_element(eid),
            redo_action=lambda: self._add_element(frame),
        )
        return frame.element_id

    def add_scale_bar(self) -> str | None:
        """在页面底部添加比例尺。

        返回:
            新元素的 ID，若文档未设置则返回 None。
        """
        if self._document is None:
            return None
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
        self._push_undo(
            "添加比例尺",
            undo_action=lambda eid=element.element_id: self.remove_element(eid),
            redo_action=lambda el=element: self._add_element(el),
        )
        return element.element_id

    def add_legend(self) -> str | None:
        """在页面右侧添加图例。

        返回:
            新元素的 ID，若文档未设置则返回 None。
        """
        if self._document is None:
            return None
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

        element = LegendElement(
            x_mm=pos_x,
            y_mm=pos_y,
            width_mm=50,
            height_mm=5,
            linked_frame_id=linked_id,
        )
        self._add_element(element)
        self._push_undo(
            "添加图例",
            undo_action=lambda eid=element.element_id: self.remove_element(eid),
            redo_action=lambda el=element: self._add_element(el),
        )
        return element.element_id

    def add_north_arrow(self) -> str | None:
        """在页面右上方添加指北针。

        返回:
            新元素的 ID，若文档未设置则返回 None。
        """
        if self._document is None:
            return None
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
        self._push_undo(
            "添加指北针",
            undo_action=lambda eid=element.element_id: self.remove_element(eid),
            redo_action=lambda el=element: self._add_element(el),
        )
        return element.element_id

    def add_text_element(self) -> str | None:
        """弹出输入框让用户输入文本，然后在页面中心添加文本元素。

        返回:
            新元素的 ID，若用户取消或文档未设置则返回 None。
        """
        if self._document is None:
            return None
        text, ok = QInputDialog.getText(
            self, "添加文本", "请输入文本内容:",
            text="文本",
        )
        if not ok or not text:
            return None
        page = self._document.page
        pos_x: float = page.width_mm / 2 - 20
        pos_y: float = page.height_mm / 2

        element = TextElement(
            x_mm=pos_x,
            y_mm=pos_y,
            width_mm=60,
            height_mm=12,
            text=text,
            font_size_mm=5.0,
        )
        self._add_element(element)
        self._push_undo(
            "添加文本",
            undo_action=lambda eid=element.element_id: self.remove_element(eid),
            redo_action=lambda el=element: self._add_element(el),
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
            undo_action=lambda e=elem, ov=old_values: self._restore_props(e, ov),
            redo_action=lambda e=elem, nv=changes: self._restore_props(e, nv),
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

    def _render_element(self, element) -> None:
        """渲染单个布局元素到场景。"""
        if self._document is None:
            return
        page = self._document.page
        dpi = page.dpi

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
            pw = max(1, round(_mm_to_px(frame.width_mm, dpi)))
            ph = max(1, round(_mm_to_px(frame.height_mm, dpi)))
            pixmap = QPixmap(pw, ph)
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

        # 边界矩形
        px: float = _mm_to_px(element.x_mm, dpi)
        py: float = _mm_to_px(element.y_mm, dpi)
        pw: float = _mm_to_px(element.width_mm, dpi) if element.width_mm > 0 else 120
        ph: float = _mm_to_px(element.height_mm, dpi) if element.height_mm > 0 else 8
        bounds_rect: QGraphicsRectItem = QGraphicsRectItem(
            QRectF(px, py, pw, ph),
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

        # 边界矩形 —— 图例高度由渲染函数自动更新
        px: float = _mm_to_px(element.x_mm, dpi)
        py: float = _mm_to_px(element.y_mm, dpi)
        pw: float = _mm_to_px(element.width_mm, dpi) if element.width_mm > 0 else 80
        ph: float = _mm_to_px(element.height_mm, dpi) if element.height_mm > 0 else 20
        bounds_rect: QGraphicsRectItem = QGraphicsRectItem(
            QRectF(px, py, pw, ph),
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

        px: float = _mm_to_px(element.x_mm, dpi)
        py: float = _mm_to_px(element.y_mm, dpi)
        pw: float = _mm_to_px(element.width_mm, dpi) if element.width_mm > 0 else 15
        ph: float = _mm_to_px(element.height_mm, dpi) if element.height_mm > 0 else 20
        bounds_rect: QGraphicsRectItem = QGraphicsRectItem(
            QRectF(px, py, pw, ph),
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
        dpi = page.dpi

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

    def _element_at(self, scene_pos: QPointF) -> str | None:
        """返回场景坐标处最上层的元素 ID，未命中返回 None。

        遍历全部元素并返回最后命中者（后添加的元素在上层），
        避免大尺寸地图框拦截对小元素的点击。
        """
        hit: str | None = None
        for elem_id, (_items, bounds_rect) in self._element_items.items():
            if bounds_rect.contains(scene_pos):
                hit = elem_id
        return hit

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
        if event.button() == Qt.MouseButton.LeftButton:
            scene_pos: QPointF = self.mapToScene(event.pos())
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
            elem_id: str | None = self._element_at(scene_pos)
            if elem_id is not None:
                # 点击元素：选中并准备拖拽移动
                self._select_element(elem_id)
                self._dragging_element_id = elem_id
                self._drag_start_pos = scene_pos
                elem = self._find_element(elem_id)
                if elem is not None:
                    self._drag_start_mm = (elem.x_mm, elem.y_mm)
                self.element_selected.emit(elem_id)
                self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return
            # 点击空白区：取消选择
            self._select_element(None)
        # 非元素左键 / 中键 / 右键 → 交由 ScrollHandDrag 处理
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._resizing_handle_index is not None:
            self._do_resize(event)
            event.accept()
            return
        if self._dragging_element_id is not None:
            scene_pos: QPointF = self.mapToScene(event.pos())
            if self._drag_start_pos is None:
                return
            elem = self._find_element(self._dragging_element_id)
            if elem is None or self._document is None:
                return
            dpi = self._document.page.dpi
            dx_mm: float = (scene_pos.x() - self._drag_start_pos.x()) / dpi * 25.4
            dy_mm: float = (scene_pos.y() - self._drag_start_pos.y()) / dpi * 25.4
            elem.x_mm = self._drag_start_mm[0] + dx_mm
            elem.y_mm = self._drag_start_mm[1] + dy_mm
            self._update_element_position(elem)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
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
                    undo_action=lambda e= elem, ox=old_x, oy=old_y: self._move_element_to(e, ox, oy),
                    redo_action=lambda e= elem, nx=new_x, ny=new_y: self._move_element_to(e, nx, ny),
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
                self.element_selected.emit(elem_id)
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
        factor: float = 1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.05 if event.angleDelta().y() > 0 else 1.0 / 1.05
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

    def remove_element(self, element_id: str) -> None:
        """从文档和场景中移除指定元素。"""
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
        if self._selected_element_id == element_id:
            self._selected_element_id = None

    def _delete_selected(self) -> None:
        """删除当前选中的元素。"""
        if self._selected_element_id is None:
            return
        eid = self._selected_element_id
        elem = self._find_element(eid)
        if elem is None:
            return
        # 快照元素状态用于撤销
        elem_snapshot = _snapshot_element(elem, eid)
        elem_type_name: str = type(elem).__name__
        self.remove_element(eid)
        self._push_undo(
            f"删除{elem_type_name}",
            undo_action=lambda es=elem_snapshot: self._add_element(es),
            redo_action=lambda eid2=eid: self.remove_element(eid2),
        )

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

    def _undo(self) -> None:
        """撤销最近一步操作。"""
        if not self._undo_stack:
            return
        _desc, undo_fn, redo_fn = self._undo_stack.pop()
        undo_fn()
        self._redo_stack.append((_desc, undo_fn, redo_fn))

    def _redo(self) -> None:
        """重做最近一次撤销。"""
        if not self._redo_stack:
            return
        _desc, undo_fn, redo_fn = self._redo_stack.pop()
        redo_fn()
        self._undo_stack.append((_desc, undo_fn, redo_fn))

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

    def _find_element(self, element_id: str):
        """按 ID 查找布局元素。"""
        if self._document is None:
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
        dpi = self._document.page.dpi

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
            items_list[1].setRect(QRectF(px, py, pw, ph))
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
        """检查场景坐标是否命中某个缩放手柄，返回手柄索引或 None。"""
        for i, handle in enumerate(self._resize_handles):
            local_pos = handle.mapFromScene(scene_pos)
            if handle.rect().contains(local_pos):
                return i
        return None

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
        dpi = self._document.page.dpi
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
                    undo_action=lambda e=elem, r=old_rect: self._set_rect(e, r),
                    redo_action=lambda e=elem, r=new_rect: self._set_rect(e, r),
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
