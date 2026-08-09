"""布局渲染器测试 —— render_text 和 render_full_page。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QGraphicsScene, QGraphicsSimpleTextItem

from app.domain.layout import (
    LayoutDocument,
    LayoutPage,
    NorthArrowElement,
    TextElement,
)
from app.presentation.renderers.layout_renderer import (
    render_full_page,
    render_text,
)


def test_render_text_creates_simple_text_item() -> None:
    """render_text 应创建 QGraphicsSimpleTextItem 并设置正确文本。"""
    QApplication.instance() or QApplication([])
    scene = QGraphicsScene()
    element = TextElement(text="测试文字", x_mm=10, y_mm=10)

    items = render_text(element, scene, dpi=96.0)

    assert len(items) >= 1
    text_items = [i for i in items if isinstance(i, QGraphicsSimpleTextItem)]
    assert len(text_items) == 1
    assert text_items[0].text() == "测试文字"


def test_render_full_page_returns_correct_size() -> None:
    """render_full_page 应返回与页面 DPI 匹配尺寸的像素图。"""
    QApplication.instance() or QApplication([])
    page = LayoutPage(width_mm=25.4, height_mm=25.4, dpi=96.0)
    document = LayoutDocument(page=page, elements=())

    pixmap = render_full_page(document)

    assert pixmap.width() == 96
    assert pixmap.height() == 96


def test_render_full_page_with_text_element() -> None:
    """render_full_page 含文本元素时应正常渲染不返回空图。"""
    QApplication.instance() or QApplication([])
    page = LayoutPage(width_mm=50.0, height_mm=50.0, dpi=96.0)
    text = TextElement(text="Hello", x_mm=5, y_mm=5, width_mm=30, height_mm=8)
    document = LayoutDocument(page=page, elements=(text,))

    pixmap = render_full_page(document)

    assert pixmap.width() > 0
    assert pixmap.height() > 0
    assert not pixmap.isNull()


def test_render_full_page_with_rotation() -> None:
    """render_full_page 含旋转元素时不应崩溃。"""
    QApplication.instance() or QApplication([])
    page = LayoutPage(width_mm=50.0, height_mm=50.0, dpi=96.0)
    arrow = NorthArrowElement(
        x_mm=10, y_mm=10, width_mm=15, height_mm=20, rotation=45.0,
    )
    document = LayoutDocument(page=page, elements=(arrow,))

    pixmap = render_full_page(document)

    assert not pixmap.isNull()
