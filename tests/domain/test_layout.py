"""布局领域模型测试 —— 纸张预设、序列化往返。"""

from app.domain.layout import (
    LayoutDocument,
    LayoutPage,
    LegendElement,
    MapFrameElement,
    NorthArrowElement,
    PageOrientation,
    ScaleBarElement,
    TextElement,
    align_element_to_page,
    layout_from_dict,
    layout_to_dict,
)


def test_layout_page_preset_a4_portrait() -> None:
    """A4 纵向预设应返回标准尺寸。"""
    page = LayoutPage.from_preset("A4")
    assert page.width_mm == 210.0
    assert page.height_mm == 297.0
    assert page.orientation == PageOrientation.PORTRAIT


def test_layout_page_preset_landscape_swaps_dimensions() -> None:
    """横向预设应交换宽高。"""
    page = LayoutPage.from_preset("A3", orientation=PageOrientation.LANDSCAPE)
    assert page.width_mm == 420.0
    assert page.height_mm == 297.0
    assert page.orientation == PageOrientation.LANDSCAPE


def test_layout_page_pixel_dimensions() -> None:
    """像素尺寸应根据 DPI 正确计算。"""
    page = LayoutPage(width_mm=25.4, height_mm=25.4, dpi=300.0)
    assert page.width_px == 300
    assert page.height_px == 300


def test_align_element_to_page_uses_printable_area() -> None:
    """页面对齐应以可印区为基准，且不改元素尺寸。"""
    page = LayoutPage.from_preset("A4")
    frame = MapFrameElement(x_mm=12.0, y_mm=20.0, width_mm=80.0, height_mm=60.0)
    x_mm, y_mm = align_element_to_page(frame, page, "center", "middle")
    assert x_mm == page.margin_mm + (page.printable_width_mm - 80.0) / 2.0
    assert y_mm == page.margin_mm + (page.printable_height_mm - 60.0) / 2.0
    assert frame.width_mm == 80.0
    assert frame.height_mm == 60.0
    left_x, _ = align_element_to_page(frame, page, "left", None)
    assert left_x == page.margin_mm
    right_x, _ = align_element_to_page(frame, page, "right", None)
    assert right_x == page.width_mm - page.margin_mm - frame.width_mm


def test_layout_page_printable_area() -> None:
    """可打印区域应扣除两侧页边距。"""
    page = LayoutPage(width_mm=210.0, height_mm=297.0, margin_mm=10.0)
    assert page.printable_width_mm == 190.0
    assert page.printable_height_mm == 277.0


def test_text_element_defaults() -> None:
    """TextElement 默认值应符合预期。"""
    elem = TextElement()
    assert elem.text == "文本"
    assert elem.font_size_mm == 5.0
    assert elem.color == "#000000"
    assert elem.bold is False
    assert elem.italic is False
    assert elem.alignment == "left"


def test_layout_to_dict_and_back_roundtrip() -> None:
    """序列化往返应保持页面规格和全部元素类型不变。"""
    page = LayoutPage.from_preset("A4")
    elements = (
        MapFrameElement(
            element_id="mf1",
            x_mm=10, y_mm=10, width_mm=100, height_mm=80,
            map_center_x=500.0, map_center_y=300.0,
            map_units_per_pixel=2.5,
        ),
        ScaleBarElement(
            element_id="sb1",
            x_mm=10, y_mm=280, width_mm=80, height_mm=8,
            linked_frame_id="mf1",
            style="double_alternating",
        ),
        LegendElement(
            element_id="lg1",
            x_mm=120, y_mm=10, width_mm=50, height_mm=40,
            title="测试图例",
        ),
        NorthArrowElement(
            element_id="na1",
            x_mm=180, y_mm=10, width_mm=15, height_mm=20,
        ),
        TextElement(
            element_id="tx1",
            x_mm=50, y_mm=200, width_mm=40, height_mm=8,
            text="标题", bold=True, alignment="center",
        ),
    )
    document = LayoutDocument(page=page, elements=elements)

    payload = layout_to_dict(document)
    restored = layout_from_dict(payload)

    assert restored.page.name == "A4"
    assert restored.page.width_mm == 210.0
    assert len(restored.elements) == 5

    mf = restored.elements[0]
    assert isinstance(mf, MapFrameElement)
    assert mf.element_id == "mf1"
    assert mf.map_center_x == 500.0
    assert mf.map_units_per_pixel == 2.5

    sb = restored.elements[1]
    assert isinstance(sb, ScaleBarElement)
    assert sb.linked_frame_id == "mf1"
    assert sb.style == "double_alternating"

    lg = restored.elements[2]
    assert isinstance(lg, LegendElement)
    assert lg.title == "测试图例"

    na = restored.elements[3]
    assert isinstance(na, NorthArrowElement)

    tx = restored.elements[4]
    assert isinstance(tx, TextElement)
    assert tx.text == "标题"
    assert tx.bold is True
    assert tx.alignment == "center"


def test_layout_from_dict_handles_empty_payload() -> None:
    """空字典应恢复为默认页面和无元素。"""
    restored = layout_from_dict({})
    assert restored.page.name == "A4"
    assert len(restored.elements) == 0


def test_layout_from_dict_ignores_unknown_element_type() -> None:
    """未知元素类型应被跳过，不影响其他元素恢复。"""
    payload = {
        "page": {"name": "A4", "width_mm": 210, "height_mm": 297},
        "elements": [
            {"type": "UnknownWidget", "element_id": "x1"},
            {"type": "TextElement", "element_id": "t1", "text": "保留"},
        ],
    }
    restored = layout_from_dict(payload)
    assert len(restored.elements) == 1
    assert isinstance(restored.elements[0], TextElement)
    assert restored.elements[0].text == "保留"
