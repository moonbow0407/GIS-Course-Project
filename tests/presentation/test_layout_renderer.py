"""布局渲染器测试 —— render_text 和 render_full_page。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication,
    QGraphicsLineItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
)

from app.domain.layout import (
    LayoutDocument,
    LayoutPage,
    MapFrameElement,
    NorthArrowElement,
    ScaleBarElement,
    TextElement,
)
from app.presentation.renderers.layout_renderer import (
    render_full_page,
    render_scale_bar,
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


def test_render_text_centers_inside_frame() -> None:
    """居中应对齐文本框中心，而不是把 x_mm 当文字锚点。"""
    QApplication.instance() or QApplication([])
    scene = QGraphicsScene()
    element = TextElement(
        text="标题",
        x_mm=10.0,
        y_mm=20.0,
        width_mm=80.0,
        height_mm=12.0,
        alignment="center",
        font_size_mm=4.0,
    )
    items = render_text(element, scene, dpi=25.4)
    text_item = items[0]
    assert isinstance(text_item, QGraphicsSimpleTextItem)
    frame_cx = 10.0 + 80.0 / 2.0
    glyph_cx = text_item.pos().x() + text_item.boundingRect().width() / 2.0
    assert abs(glyph_cx - frame_cx) < 0.6


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


def _linked_frame() -> MapFrameElement:
    """测试用地图框：1 像素约 1000 米。"""
    return MapFrameElement(
        element_id="frame",
        x_mm=10,
        y_mm=10,
        width_mm=80,
        height_mm=60,
        map_units_per_pixel=1000.0,
    )


def test_render_scale_bar_double_alternating_draws_two_rows() -> None:
    """双层交替应画出上下两排分段，并带累计距离标签。"""
    QApplication.instance() or QApplication([])
    scene = QGraphicsScene()
    element = ScaleBarElement(
        x_mm=10,
        y_mm=80,
        width_mm=80,
        height_mm=8,
        style="double_alternating",
        num_segments=4,
        unit="km",
    )
    items = render_scale_bar(element, scene, 96.0, _linked_frame())
    rects = [item for item in items if isinstance(item, QGraphicsRectItem)]
    texts = [
        item.text()
        for item in items
        if isinstance(item, QGraphicsSimpleTextItem)
    ]
    assert len(rects) >= 8
    assert texts[0] == "0"
    assert any("km" in text for text in texts)


def test_render_scale_bar_line_uses_ticks() -> None:
    """线状比例尺应使用基线加刻度，而不是填充色块。"""
    QApplication.instance() or QApplication([])
    scene = QGraphicsScene()
    element = ScaleBarElement(
        x_mm=10,
        y_mm=80,
        width_mm=80,
        height_mm=8,
        style="line",
        num_segments=4,
        unit="km",
    )
    items = render_scale_bar(element, scene, 96.0, _linked_frame())
    rects = [item for item in items if isinstance(item, QGraphicsRectItem)]
    lines = [item for item in items if isinstance(item, QGraphicsLineItem)]
    texts = [
        item.text()
        for item in items
        if isinstance(item, QGraphicsSimpleTextItem)
    ]
    assert rects == []
    assert len(lines) >= 5
    assert any("km" in text for text in texts)
