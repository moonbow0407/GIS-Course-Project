"""布局视图交互测试 —— 文档设置、元素增删、撤销重做。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from app.application.results import LayerSnapshot, WorkspaceSnapshot
from app.domain.layout import (
    LayoutDocument,
    LayoutPage,
    MapFrameElement,
    PageOrientation,
    TextElement,
)
from app.presentation.widgets.layout_view import LayoutView, _mm_to_px


def _make_view() -> LayoutView:
    """创建带默认文档的布局视图。"""
    QApplication.instance() or QApplication([])
    view = LayoutView()
    doc = LayoutDocument.create_default()
    view.set_document(doc)
    return view


def _add_text(view: LayoutView, text: str = "测试") -> str:
    """绕过输入对话框直接添加文本元素（测试专用）。"""
    elem = TextElement(text=text, x_mm=50, y_mm=50, width_mm=40, height_mm=8)
    view._add_element(elem)
    return elem.element_id


def test_set_document_rebuilds_scene() -> None:
    """set_document 应重建场景并显示纸张。"""
    QApplication.instance() or QApplication([])
    view = LayoutView()
    doc = LayoutDocument.create_default()
    view.set_document(doc)
    assert view.has_content()
    assert view.document() is doc
    assert view._paper_item is not None


def test_add_text_element_increases_count() -> None:
    """添加文本元素应增加文档元素数量。"""
    view = _make_view()
    initial_count = len(view.document().elements)
    eid = _add_text(view)
    assert eid is not None
    assert len(view.document().elements) == initial_count + 1


def test_add_map_frame_returns_id() -> None:
    """add_map_frame 应返回新元素 ID。"""
    view = _make_view()
    eid = view.add_map_frame()
    assert eid is not None
    elem = view.find_element(eid)
    assert isinstance(elem, MapFrameElement)


def test_delete_selected_removes_element() -> None:
    """删除选中元素后文档元素减少。"""
    view = _make_view()
    eid = _add_text(view)
    view._select_element(eid)
    count_before = len(view.document().elements)
    view._delete_selected()
    assert len(view.document().elements) == count_before - 1
    assert view.find_element(eid) is None


def test_undo_restores_deleted_element() -> None:
    """撤销删除应恢复元素。"""
    view = _make_view()
    eid = _add_text(view)
    view._select_element(eid)
    view._delete_selected()
    assert view.find_element(eid) is None
    view._undo()
    assert view.find_element(eid) is not None


def test_redo_re_applies_deletion() -> None:
    """重做应再次删除元素。"""
    view = _make_view()
    eid = _add_text(view)
    view._select_element(eid)
    view._delete_selected()
    view._undo()
    assert view.find_element(eid) is not None
    view._redo()
    assert view.find_element(eid) is None


def test_apply_element_changes_updates_text() -> None:
    """apply_element_changes 应更新文本元素内容。"""
    view = _make_view()
    eid = _add_text(view, text="原始")
    view.apply_element_changes(eid, {"text": "新标题", "bold": True})
    elem = view.find_element(eid)
    assert isinstance(elem, TextElement)
    assert elem.text == "新标题"
    assert elem.bold is True


def test_can_undo_and_redo_reflect_stack_state() -> None:
    """撤销/重做可用性应反映栈状态。"""
    view = _make_view()
    assert view.can_undo() is False
    assert view.can_redo() is False
    view.add_map_frame()
    assert view.can_undo() is True
    view._undo()
    assert view.can_redo() is True


def test_resize_handles_created_on_select() -> None:
    """选中元素后应创建缩放手柄。"""
    view = _make_view()
    eid = view.add_map_frame()
    assert eid is not None
    view._select_element(eid)
    assert len(view._resize_handles) == 8


def test_resize_handles_removed_on_deselect() -> None:
    """取消选中后应移除缩放手柄。"""
    view = _make_view()
    eid = view.add_map_frame()
    view._select_element(eid)
    assert len(view._resize_handles) == 8
    view._select_element(None)
    assert len(view._resize_handles) == 0


def test_add_map_frame_toggles_add_select_then_remove() -> None:
    """add_map_frame：添加后再次点击只选中，不再删除。"""
    view = _make_view()
    eid1 = view.add_map_frame()
    assert eid1 is not None
    assert view.selected_element_id == eid1
    view._select_element(None)
    eid2 = view.add_map_frame()
    assert eid2 == eid1
    assert view.selected_element_id == eid1
    frame_count = [
        e for e in view.document().elements if isinstance(e, MapFrameElement)
    ]
    assert len(frame_count) == 1
    result = view.add_map_frame()
    assert result == eid1
    assert view.find_element(eid1) is not None


def test_add_scale_bar_toggles_select_then_remove() -> None:
    """add_scale_bar：添加后再次点击只选中，不再删除。"""
    view = _make_view()
    eid = view.add_scale_bar()
    assert eid is not None
    view._select_element(None)
    eid2 = view.add_scale_bar()
    assert eid2 == eid
    view.add_scale_bar()
    assert view.find_element(eid) is not None


def test_delete_selected_removes_resize_handles_immediately() -> None:
    """删除选中元素后缩放手柄应立刻清除，不留滞。"""
    view = _make_view()
    eid = view.add_map_frame()
    assert eid is not None
    view._select_element(eid)
    assert len(view._resize_handles) == 8
    view._delete_selected()
    assert len(view._resize_handles) == 0
    assert view._selected_element_id is None


def test_clear_all_removes_all_elements() -> None:
    """清空应删除图幅中的全部元素。"""
    view = _make_view()
    view.add_map_frame()
    view.add_scale_bar()
    eid = _add_text(view)
    assert len(view.document().elements) == 3
    view.clear_all_elements()
    assert len(view.document().elements) == 0
    assert view.find_element(eid) is None
    assert view.has_elements() is False


def test_clear_all_undo_restores_elements() -> None:
    """清空的撤销应恢复全部元素。"""
    view = _make_view()
    view.add_map_frame()
    view.add_scale_bar()
    count_before = len(view.document().elements)
    assert view.has_elements() is True
    view.clear_all_elements()
    assert len(view.document().elements) == 0
    view._undo()
    assert len(view.document().elements) == count_before
    assert view.has_elements() is True


def _frame_with_embedded_text(view: LayoutView) -> tuple[str, str]:
    """在地图框中心放置一个文本元素，返回 (frame_id, text_id)。"""
    fid = view.add_map_frame()
    frame = view.find_element(fid)
    assert frame is not None
    cx = frame.x_mm + frame.width_mm / 2
    cy = frame.y_mm + frame.height_mm / 2
    text = TextElement(text="内嵌", x_mm=cx - 5, y_mm=cy - 5, width_mm=40, height_mm=8)
    view._add_element(text)
    return fid, text.element_id


def test_element_at_prefers_topmost_inside_frame() -> None:
    """叠压时命中检测应返回视觉上层的元素。"""
    view = _make_view()
    fid, tid = _frame_with_embedded_text(view)
    frame = view.find_element(fid)
    assert frame is not None
    dpi = view._view_dpi
    cx = frame.x_mm + frame.width_mm / 2
    cy = frame.y_mm + frame.height_mm / 2
    # 点击文本内容位置 → 命中文本（上层）
    assert view._element_at(QPointF(_mm_to_px(cx, dpi), _mm_to_px(cy, dpi))) == tid
    # 点击地图框内远离文本的位置 → 命中地图框
    assert view._element_at(
        QPointF(_mm_to_px(frame.x_mm + 8, dpi), _mm_to_px(frame.y_mm + 8, dpi))
    ) == fid


def test_elements_at_returns_stack_top_to_bottom() -> None:
    """叠压点应返回从顶层到底层的元素列表。"""
    view = _make_view()
    fid, tid = _frame_with_embedded_text(view)
    frame = view.find_element(fid)
    assert frame is not None
    dpi = view._view_dpi
    cx = frame.x_mm + frame.width_mm / 2
    cy = frame.y_mm + frame.height_mm / 2
    pos = QPointF(_mm_to_px(cx, dpi), _mm_to_px(cy, dpi))
    stacked = view._elements_at(pos)
    assert stacked[0] == tid
    assert fid in stacked
    assert stacked.index(tid) < stacked.index(fid)


def test_cycle_select_wraps_through_stack() -> None:
    """循环选中应在叠压元素间依次切换并回绕。"""
    view = _make_view()
    fid, tid = _frame_with_embedded_text(view)
    frame = view.find_element(fid)
    assert frame is not None
    dpi = view._view_dpi
    cx = frame.x_mm + frame.width_mm / 2
    cy = frame.y_mm + frame.height_mm / 2
    pos = QPointF(_mm_to_px(cx, dpi), _mm_to_px(cy, dpi))
    stacked = view._elements_at(pos)
    view._select_element(None)
    # 第一次 → 顶层（文本）
    view._cycle_select(stacked)
    assert view.selected_element_id == tid
    # 第二次 → 下一层（地图框）
    view._cycle_select(stacked)
    assert view.selected_element_id == fid
    # 第三次 → 回绕到顶层
    view._cycle_select(stacked)
    assert view.selected_element_id == tid


# ---------------------------------------------------------------------------
# 地图框交互：Alt 平移地图内容、地图内容自动适配
# ---------------------------------------------------------------------------


class _StubLayer:
    """最小图层桩：仅提供渲染/取范围所需的属性。"""

    layer_id = "l1"
    name = "测试图层"
    bounds = (10.0, 20.0, 90.0, 80.0)  # 数据范围 80×60


class _StubHiddenLayer:
    layer_id = "l2"
    name = "隐藏图层"
    bounds = (-100.0, -100.0, 100.0, 100.0)


def _view_with_snapshot() -> LayoutView:
    """创建带快照（含一个可见图层）的布局视图。"""
    view = _make_view()
    snap = WorkspaceSnapshot(
        layers=(
            LayerSnapshot(
                layer=_StubLayer(), visible=True, selected_feature_ids=(),
            ),
        ),
        active_layer_id="l1",
        display_crs=None,
    )
    view.set_snapshot(snap)
    return view


def _send_press(view: LayoutView, pos_scene: QPointF, mods) -> None:
    """向视图发送左键按下事件（场景坐标）。"""
    pos = view.mapFromScene(pos_scene)
    ev = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(pos.x(), pos.y()),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        mods,
    )
    view.mousePressEvent(ev)


def _send_move(view: LayoutView, pos_scene: QPointF, mods) -> None:
    """向视图发送按住左键的移动事件（场景坐标）。"""
    pos = view.mapFromScene(pos_scene)
    ev = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(pos.x(), pos.y()),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        mods,
    )
    view.mouseMoveEvent(ev)


def _send_release(view: LayoutView, pos_scene: QPointF, mods) -> None:
    """向视图发送左键释放事件（场景坐标）。"""
    pos = view.mapFromScene(pos_scene)
    ev = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(pos.x(), pos.y()),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        mods,
    )
    view.mouseReleaseEvent(ev)


def test_shift_drag_pans_map_content() -> None:
    """Shift+拖拽已选中的地图框应平移地图内容（中心变化）。"""
    view = _make_view()
    fid = view.add_map_frame()
    frame = view.find_element(fid)
    assert isinstance(frame, MapFrameElement)
    view._select_element(fid)
    dpi = view._view_dpi
    px = _mm_to_px(frame.x_mm + frame.width_mm / 2, dpi)
    py = _mm_to_px(frame.y_mm + frame.height_mm / 2, dpi)
    old_cx, old_cy = frame.map_center_x, frame.map_center_y
    shift = Qt.KeyboardModifier.ShiftModifier
    _send_press(view, QPointF(px, py), shift)
    assert view._map_panning is True
    # 向右下方拖拽 20px（场景 Y 与地图 Y 反向）
    end = QPointF(px + 20, py + 20)
    _send_move(view, end, shift)
    _send_release(view, end, shift)
    assert view._map_panning is False
    assert (frame.map_center_x, frame.map_center_y) != (old_cx, old_cy)
    # 场景向右拖 → 地图中心向西移动（X 减）
    assert frame.map_center_x < old_cx
    # 场景向下拖 → 地图中心向北移动（Y 增）
    assert frame.map_center_y > old_cy


def test_overflowing_map_frame_can_be_dragged_back() -> None:
    """地图框超出纸张后，点框内（含纸外部分）仍应拖动元素而不是平移视图。"""
    view = _make_view()
    fid = view.add_map_frame()
    frame = view.find_element(fid)
    assert isinstance(frame, MapFrameElement)
    page = view.document().page
    frame.x_mm = page.margin_mm
    frame.y_mm = page.height_mm * 0.55
    frame.width_mm = 90.0
    frame.height_mm = page.height_mm
    view._render_element(frame)
    view._expand_scene_rect()
    view._select_element(None)
    dpi = view._view_dpi
    # 点在纸张内的框上部
    on_page = QPointF(
        _mm_to_px(frame.x_mm + 30.0, dpi),
        _mm_to_px(frame.y_mm + 15.0, dpi),
    )
    _send_press(view, on_page, Qt.KeyboardModifier.NoModifier)
    assert view._dragging_element_id == fid
    assert view._panning is False
    old_y = frame.y_mm
    end = QPointF(on_page.x(), on_page.y() - _mm_to_px(25.0, dpi))
    _send_move(view, end, Qt.KeyboardModifier.NoModifier)
    _send_release(view, end, Qt.KeyboardModifier.NoModifier)
    assert frame.y_mm < old_y
    # 点在纸张下方、但仍落在地图框内的溢出部分
    view._select_element(None)
    overflow = QPointF(
        _mm_to_px(frame.x_mm + 30.0, dpi),
        _mm_to_px(page.height_mm + 12.0, dpi),
    )
    assert view._hits_element(frame, overflow)
    _send_press(view, overflow, Qt.KeyboardModifier.NoModifier)
    assert view._dragging_element_id == fid
    assert view._panning is False


def test_page_orientation_change_keeps_elements_draggable() -> None:
    """更换纸张方向后，已选中的地图框仍应能拖动。

    换页会重建场景。若未先卸掉缩放手柄，scene.clear() 会留下已删除的
    C++ 图元，下一次点击在 _handle_at 里踩空，表现为完全拖不动。
    """
    view = _make_view()
    fid = view.add_map_frame()
    assert fid is not None
    view._select_element(fid)
    assert len(view._resize_handles) == 8
    document = view.document()
    assert document is not None
    new_page = LayoutPage.from_preset(document.page.name, PageOrientation.LANDSCAPE)
    view.set_document(LayoutDocument(page=new_page, elements=document.elements))
    for handle in view._resize_handles:
        assert handle.scene() is view.scene()
    frame = view.find_element(fid)
    assert isinstance(frame, MapFrameElement)
    dpi = view._view_dpi
    start = QPointF(
        _mm_to_px(frame.x_mm + frame.width_mm / 2, dpi),
        _mm_to_px(frame.y_mm + frame.height_mm / 2, dpi),
    )
    old_x = frame.x_mm
    _send_press(view, start, Qt.KeyboardModifier.NoModifier)
    assert view._dragging_element_id == fid
    assert view._resizing_handle_index is None
    end = QPointF(start.x() + 40, start.y())
    _send_move(view, end, Qt.KeyboardModifier.NoModifier)
    _send_release(view, end, Qt.KeyboardModifier.NoModifier)
    assert frame.x_mm != old_x


def test_align_selection_centers_map_frame_on_printable_area() -> None:
    """页面居中应把地图框放到可印区中心，且可撤销。"""
    view = _make_view()
    fid = view.add_map_frame()
    frame = view.find_element(fid)
    assert isinstance(frame, MapFrameElement)
    page = view.document().page
    frame.x_mm = 12.0
    frame.y_mm = 18.0
    view._render_element(frame)
    view._select_element(fid)
    old_x, old_y = frame.x_mm, frame.y_mm
    view.align_selection_to_page("center", "middle")
    assert frame.x_mm == page.margin_mm + (page.printable_width_mm - frame.width_mm) / 2.0
    assert frame.y_mm == page.margin_mm + (page.printable_height_mm - frame.height_mm) / 2.0
    view._undo()
    assert (frame.x_mm, frame.y_mm) == (old_x, old_y)
    landscape = LayoutPage.from_preset(page.name, PageOrientation.LANDSCAPE)
    view.set_document(LayoutDocument(page=landscape, elements=view.document().elements))
    view._select_element(fid)
    view.align_selection_to_page("center", "middle")
    new_page = view.document().page
    assert frame.x_mm == new_page.margin_mm + (
        new_page.printable_width_mm - frame.width_mm
    ) / 2.0


def test_alt_click_cycles_selected_frame_not_pan() -> None:
    """Alt+点击已选中的地图框应循环选中，而非进入平移（Shift 专属平移）。"""
    view = _make_view()
    fid, tid = _frame_with_embedded_text(view)
    frame = view.find_element(fid)
    assert frame is not None
    dpi = view._view_dpi
    cx = frame.x_mm + frame.width_mm / 2
    cy = frame.y_mm + frame.height_mm / 2
    pos = QPointF(_mm_to_px(cx, dpi), _mm_to_px(cy, dpi))
    # Alt+点击直到地图框被选中（文本 → 地图框）
    view._select_element(None)
    _send_press(view, pos, Qt.KeyboardModifier.AltModifier)
    _send_press(view, pos, Qt.KeyboardModifier.AltModifier)
    assert view.selected_element_id == fid
    assert view._map_panning is False
    # 再 Alt+点击已选中的地图框 → 回绕到顶层文本，仍不进入平移
    _send_press(view, pos, Qt.KeyboardModifier.AltModifier)
    assert view.selected_element_id == tid
    assert view._map_panning is False


def test_data_extent_skips_invisible_layers() -> None:
    """_data_extent 应只统计可见图层的联合范围。"""
    view = _make_view()
    snap = WorkspaceSnapshot(
        layers=(
            LayerSnapshot(
                layer=_StubLayer(), visible=True, selected_feature_ids=(),
            ),
            LayerSnapshot(
                layer=_StubHiddenLayer(), visible=False, selected_feature_ids=(),
            ),
        ),
        active_layer_id="l1",
        display_crs=None,
    )
    view.set_snapshot(snap)
    assert view._data_extent() == (10.0, 20.0, 90.0, 80.0)


def test_fit_map_content_fits_all_visible_layers() -> None:
    """适配地图内容应将全部可见图层数据充满地图框并可撤销。"""
    view = _view_with_snapshot()
    fid = view.add_map_frame()
    frame = view.find_element(fid)
    assert isinstance(frame, MapFrameElement)
    # 打乱中心与比例，确保适配确实重新计算
    frame.map_center_x = 5.0
    frame.map_center_y = 6.0
    frame.map_units_per_pixel = 0.5
    view._render_element(frame)
    view.fit_map_content(frame)
    dpi = view._view_dpi
    fw = _mm_to_px(frame.width_mm, dpi)
    fh = _mm_to_px(frame.height_mm, dpi)
    assert frame.map_center_x == 50.0
    assert frame.map_center_y == 50.0
    assert frame.map_units_per_pixel == max(80.0 / fw, 60.0 / fh)
    # 适配可撤销
    view._undo()
    assert frame.map_center_x == 5.0
    assert frame.map_center_y == 6.0
    assert frame.map_units_per_pixel == 0.5


# ---------------------------------------------------------------------------
# 已选中元素拖动：内容盒过小导致按到附近空白却选到下方大框
# ---------------------------------------------------------------------------


def _content_rect(view: LayoutView, eid: str) -> "QRectF":
    """返回元素渲染内容盒（场景坐标）。"""
    return view._element_items[eid][1].rect()


def test_drag_selected_text_from_margin_moves_text() -> None:
    """已选中文本后，从内容盒外侧（外扩边距内）按下拖动应移动文本而非选大框。

    修复前：文本命中区只贴内容盒，按下盒外空白会命中下方地图框，
    导致"想拖文本却选中大框"。
    """
    view = _make_view()
    fid, tid = _frame_with_embedded_text(view)
    frame = view.find_element(fid)
    text = view.find_element(tid)
    assert isinstance(text, TextElement)
    assert isinstance(frame, MapFrameElement)
    view._select_element(tid)
    b = _content_rect(view, tid)
    # 抓取边距按设备像素计（_GRAB_MARGIN=10px），这里取右侧 8 设备px处：
    # 在抓取边距内、又远离右侧中点手柄（视觉 8px+2px 余量）的间隙。
    scale = abs(view.viewportTransform().m11())
    start = QPointF(b.right() + 8 / scale, b.center().y())
    end = QPointF(b.right() + 8 / scale + 40, b.center().y() + 20)
    start_x, start_y = text.x_mm, text.y_mm
    frame_x, frame_y = frame.x_mm, frame.y_mm
    _send_press(view, start, Qt.KeyboardModifier.NoModifier)
    assert view._dragging_element_id == tid
    _send_move(view, end, Qt.KeyboardModifier.NoModifier)
    _send_release(view, end, Qt.KeyboardModifier.NoModifier)
    assert view.selected_element_id == tid
    assert (text.x_mm, text.y_mm) != (start_x, start_y)
    assert (frame.x_mm, frame.y_mm) == (frame_x, frame_y)


def test_drag_selected_scale_legend_arrow_from_margin() -> None:
    """比例尺/图例/指北针选中后，从内容盒外侧边距按下拖动应移动该元素而非选大框。

    抓取预检对所有已选中元素通用（不限于文本），逐一验证三类元素的
    边距拖拽行为与文本一致。
    """
    add_ops = (
        ("add_scale_bar", "比例尺"),
        ("add_legend", "图例"),
        ("add_north_arrow", "指北针"),
    )
    for method, label in add_ops:
        v = _make_view()
        fid = v.add_map_frame()
        assert fid is not None
        eid = getattr(v, method)()
        assert eid is not None
        frame = v.find_element(fid)
        elem = v.find_element(eid)
        assert frame is not None and elem is not None
        v._select_element(eid)
        b = _content_rect(v, eid)
        # 与文本测试一致：右侧 8 设备px处（抓取边距内、远离手柄）
        scale = abs(v.viewportTransform().m11())
        start = QPointF(b.right() + 8 / scale, b.center().y())
        end = QPointF(b.right() + 8 / scale + 40, b.center().y() + 20)
        start_x, start_y = elem.x_mm, elem.y_mm
        frame_x, frame_y = frame.x_mm, frame.y_mm
        _send_press(v, start, Qt.KeyboardModifier.NoModifier)
        assert v._dragging_element_id == eid, f"{label} 应从边距抓取拖动"
        _send_move(v, end, Qt.KeyboardModifier.NoModifier)
        _send_release(v, end, Qt.KeyboardModifier.NoModifier)
        assert v.selected_element_id == eid, f"{label} 拖动后应保持选中"
        assert (elem.x_mm, elem.y_mm) != (start_x, start_y), f"{label} 应被移动"
        assert (frame.x_mm, frame.y_mm) == (frame_x, frame_y), "地图框不应被移动"


def test_press_far_from_selected_text_selects_frame() -> None:
    """远离文本内容盒的位置按下应选中地图框，而非拖动文本。"""
    view = _make_view()
    fid, tid = _frame_with_embedded_text(view)
    frame = view.find_element(fid)
    assert isinstance(frame, MapFrameElement)
    view._select_element(tid)
    b = _content_rect(view, tid)
    # 25 设备px处：超出抓取边距（10px），应回落常规命中选中地图框。
    scale = abs(view.viewportTransform().m11())
    pos = QPointF(b.right() + 25 / scale, b.center().y())
    _send_press(view, pos, Qt.KeyboardModifier.NoModifier)
    assert view.selected_element_id == fid
    assert view._dragging_element_id == fid


def test_selected_frame_press_over_embedded_text_selects_text() -> None:
    """选中大框后按在其内文本内容上，应选中文本而非拖动大框。"""
    view = _make_view()
    fid, tid = _frame_with_embedded_text(view)
    text = view.find_element(tid)
    assert isinstance(text, TextElement)
    view._select_element(fid)
    b = _content_rect(view, tid)
    pos = b.center()
    _send_press(view, pos, Qt.KeyboardModifier.NoModifier)
    assert view.selected_element_id == tid
    assert view._dragging_element_id == tid


def test_resize_handle_still_resizes_selected_text() -> None:
    """已选中文本后按在缩放手柄上应触发缩放而非移动。"""
    view = _make_view()
    fid, tid = _frame_with_embedded_text(view)
    text = view.find_element(tid)
    assert isinstance(text, TextElement)
    view._select_element(tid)
    b = _content_rect(view, tid)
    # 右下角手柄
    handle_pos = QPointF(b.right(), b.bottom())
    old_w, old_h = text.width_mm, text.height_mm
    _send_press(view, handle_pos, Qt.KeyboardModifier.NoModifier)
    assert view._resizing_handle_index is not None
    end = QPointF(b.right() + 20, b.bottom() + 10)
    _send_move(view, end, Qt.KeyboardModifier.NoModifier)
    _send_release(view, end, Qt.KeyboardModifier.NoModifier)
    assert text.width_mm > old_w
    assert text.height_mm > old_h
    assert view.selected_element_id == tid


def test_multiple_texts_exist_and_render_independently() -> None:
    """多个文本实例：两个文本都在文档与渲染图中，互不影响。"""
    view = _make_view()
    eid1 = _add_text(view, text="一")
    eid2 = _add_text(view, text="二")
    assert eid1 != eid2
    texts = [e for e in view.document().elements if isinstance(e, TextElement)]
    assert len(texts) == 2
    assert {e.element_id for e in texts} == {eid1, eid2}
    assert eid1 in view._element_items and eid2 in view._element_items


def test_multiple_texts_select_and_delete_independently() -> None:
    """多个文本实例：可独立选中、删除一个不影响另一个。"""
    view = _make_view()
    eid1 = _add_text(view, text="一")
    eid2 = _add_text(view, text="二")
    view._select_element(eid1)
    assert view.selected_element_id == eid1
    view._select_element(eid2)
    assert view.selected_element_id == eid2
    view._delete_selected()
    assert view.find_element(eid2) is None
    assert view.find_element(eid1) is not None


def test_undo_redo_multiple_texts_isolated_by_id() -> None:
    """撤销/重做按 element_id 隔离：删除第二个可单独撤销恢复。"""
    view = _make_view()
    eid1 = _add_text(view, text="一")
    eid2 = _add_text(view, text="二")
    view._select_element(eid2)
    view._delete_selected()
    assert view.find_element(eid2) is None
    view._undo()
    assert view.find_element(eid2) is not None
    assert view.find_element(eid1) is not None
    view._redo()
    assert view.find_element(eid2) is None
    assert view.find_element(eid1) is not None


def test_add_text_element_offsets_positions(monkeypatch) -> None:
    """连续添加两个文本：ID 不同、位置向右错开（偏移算法生效）。"""
    from app.presentation.widgets import layout_view as lv

    texts = iter(["第一行", "第二行"])
    monkeypatch.setattr(
        lv.QInputDialog, "getText",
        staticmethod(lambda *a, **k: (next(texts), True)),
    )
    view = _make_view()
    eid1 = view.add_text_element()
    eid2 = view.add_text_element()
    assert eid1 is not None and eid2 is not None
    assert eid1 != eid2
    t1 = view.find_element(eid1)
    t2 = view.find_element(eid2)
    assert isinstance(t1, TextElement) and isinstance(t2, TextElement)
    # 第一个以页面中心为基准（盒中心落在几何中心）
    page = view.document().page
    assert t1.x_mm == page.width_mm / 2 - 30.0
    assert t1.y_mm == page.height_mm / 2 - 6.0
    # 第二个同一行向右错开
    assert t2.y_mm == t1.y_mm
    assert t2.x_mm > t1.x_mm
