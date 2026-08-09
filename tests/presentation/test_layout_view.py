"""布局视图交互测试 —— 文档设置、元素增删、撤销重做。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.domain.layout import (
    LayoutDocument,
    MapFrameElement,
    TextElement,
)
from app.presentation.widgets.layout_view import LayoutView


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
