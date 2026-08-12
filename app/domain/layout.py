"""布局视图领域模型 —— 纸张页面、制图元素与排版文档。

在布局视图中，所有元素位置使用 **毫米** 表示，原点位于页面左上角。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, cast
from uuid import uuid4

# ---------------------------------------------------------------------------
# 纸张规格
# ---------------------------------------------------------------------------

_PAPER_SIZES_MM: dict[str, tuple[float, float]] = {
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
    "A5": (148.0, 210.0),
    "Letter": (215.9, 279.4),
    "Legal": (215.9, 355.6),
}


class PageOrientation(str, Enum):
    """纸张方向。"""

    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"


@dataclass(frozen=True, slots=True)
class LayoutPage:
    """虚拟纸张规格。

    属性:
        name: 纸张名称（如 "A4"），也支持自定义尺寸。
        width_mm: 页面宽度（毫米）。
        height_mm: 页面高度（毫米）。
        dpi: 渲染分辨率（默认 300）。
        orientation: 纸张方向。
        margin_mm: 四边页边距（默认 10 mm）。
    """

    name: str = "A4"
    width_mm: float = 210.0
    height_mm: float = 297.0
    dpi: float = 300.0
    orientation: PageOrientation = PageOrientation.PORTRAIT
    margin_mm: float = 10.0

    @classmethod
    def from_preset(
        cls,
        name: str = "A4",
        orientation: PageOrientation = PageOrientation.PORTRAIT,
        dpi: float = 300.0,
        margin_mm: float = 10.0,
    ) -> "LayoutPage":
        """根据预设名称创建纸张。"""
        w, h = _PAPER_SIZES_MM.get(name, (210.0, 297.0))
        if orientation is PageOrientation.LANDSCAPE:
            w, h = h, w
        return cls(
            name=name,
            width_mm=w,
            height_mm=h,
            dpi=dpi,
            orientation=orientation,
            margin_mm=margin_mm,
        )

    @property
    def printable_width_mm(self) -> float:
        """扣除页边距后的可打印宽度。"""
        return max(1.0, self.width_mm - 2 * self.margin_mm)

    @property
    def printable_height_mm(self) -> float:
        """扣除页边距后的可打印高度。"""
        return max(1.0, self.height_mm - 2 * self.margin_mm)

    @property
    def width_px(self) -> int:
        """纸张像素宽度（基于 DPI）。"""
        return max(1, round(self.width_mm / 25.4 * self.dpi))

    @property
    def height_px(self) -> int:
        """纸张像素高度（基于 DPI）。"""
        return max(1, round(self.height_mm / 25.4 * self.dpi))


# ---------------------------------------------------------------------------
# 布局元素
# ---------------------------------------------------------------------------


def _new_element_id() -> str:
    """生成短 UUID 作为元素稳定标识。"""
    return uuid4().hex[:12]


@dataclass(frozen=False, slots=True)
class LayoutElement:
    """布局元素基类 —— 页面上的一个可放置、可移动的制图组件。

    属性:
        element_id: 稳定唯一标识。
        x_mm: 距页面左边缘的距离（毫米）。
        y_mm: 距页面上边缘的距离（毫米）。
        width_mm: 元素宽度（毫米）。
        height_mm: 元素高度（毫米）。
        rotation: 旋转角度（度数，顺时针）。
    """

    element_id: str = field(default_factory=_new_element_id)
    x_mm: float = 10.0
    y_mm: float = 10.0
    width_mm: float = 80.0
    height_mm: float = 60.0
    rotation: float = 0.0

    @property
    def element_type(self) -> str:
        """返回元素类型标识，供渲染调度使用。"""
        return self.__class__.__name__


# ---------------------------------------------------------------------------
# 具体布局元素
# ---------------------------------------------------------------------------


@dataclass(frozen=False, slots=True)
class MapFrameElement(LayoutElement):
    """地图框 —— 在纸张上显示指定范围和比例尺的地图数据。

    属性:
        map_center_x: 地图框中心点的地图 X 坐标。
        map_center_y: 地图框中心点的地图 Y 坐标。
        map_units_per_pixel: 当前屏幕分辨率（一个像素对应的地图单位）。
        border_color: 边框颜色。
        border_width_mm: 边框线宽（毫米）。
        background_color: 地图框背景颜色。
    """

    map_center_x: float = 0.0
    map_center_y: float = 0.0
    map_units_per_pixel: float = 1.0
    border_color: str = "#333333"
    border_width_mm: float = 0.5
    background_color: str = "#ffffff"

    @property
    def map_scale_denom(self) -> int:
        """根据 map_units_per_pixel 和典型 DPI 估算比例尺分母。

        假设地图单位为米，则 1 像素 = map_units_per_pixel 米。
        在 96 DPI 屏幕（或约 3.78 点/mm）上反算比例尺。
        注：此值为近似值，精确比例尺需结合具体 CRS 和纬度计算。
        """
        if self.map_units_per_pixel <= 0:
            return 0
        # 1 inch = 25.4 mm = 96 pixels → 1 pixel = 25.4/96 mm on screen
        # scale = map_units_per_pixel (m) / (0.0254/96) (m) = map_units_per_pixel * 96 / 0.0254
        return max(1, round(self.map_units_per_pixel * 96.0 / 0.0254))


@dataclass(frozen=False, slots=True)
class ScaleBarElement(LayoutElement):
    """比例尺 —— 根据关联地图框自动计算地面距离。

    属性:
        linked_frame_id: 关联的地图框元素 ID，用于获取比例。
        style: 样式 "alternating"（黑白交替）。
        unit: 显示单位 "km" / "m"。
        num_segments: 分段数量（默认 4）。
        label_font_size_mm: 标签字号（毫米）。
        color: 线条/文字颜色。
    """

    linked_frame_id: str = ""
    style: str = "alternating"
    unit: str = "km"
    num_segments: int = 4
    label_font_size_mm: float = 2.5
    color: str = "#000000"


@dataclass(frozen=False, slots=True)
class LegendElement(LayoutElement):
    """图例 —— 显示可见图层的符号和名称。

    属性:
        linked_frame_id: 关联的地图框元素 ID。
        title: 图例标题。
        title_font_size_mm: 标题字号（毫米）。
        item_font_size_mm: 条目字号（毫米）。
        column_count: 列数。
    """

    linked_frame_id: str = ""
    title: str = "图例"
    title_font_size_mm: float = 3.0
    item_font_size_mm: float = 2.5
    column_count: int = 1


@dataclass(frozen=False, slots=True)
class NorthArrowElement(LayoutElement):
    """指北针 —— 指示地图北方向。

    属性:
        style: "simple" / "compass" / "arrow"。
        color: 填充颜色。
    """

    style: str = "compass"
    color: str = "#333333"


@dataclass(frozen=False, slots=True)
class TextElement(LayoutElement):
    """文本元素 —— 在纸张上放置自由文本标注。

    属性:
        text: 文本内容。
        font_size_mm: 字号（毫米）。
        color: 文字颜色。
        bold: 是否粗体。
        italic: 是否斜体。
        alignment: 对齐方式 "left" / "center" / "right"。
    """

    text: str = "文本"
    font_size_mm: float = 5.0
    color: str = "#000000"
    bold: bool = False
    italic: bool = False
    alignment: str = "left"


# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LayoutDocument:
    """包含一个页面和若干制图元素的排版文档。

    属性:
        page: 纸张规格。
        elements: 布局元素集合（按 z-order 排列，后绘制的在上层）。
    """

    page: LayoutPage = field(default_factory=LayoutPage)
    elements: tuple[LayoutElement, ...] = ()

    @classmethod
    def create_default(cls, page_name: str = "A4") -> "LayoutDocument":
        """创建一个空 A4 纵向布局文档。"""
        return cls(page=LayoutPage.from_preset(page_name))


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------


def layout_to_dict(document: LayoutDocument) -> dict[str, object]:
    """把布局文档转换为可写入工程 JSON 的字典。"""
    page = document.page
    return {
        "page": {
            "name": page.name,
            "width_mm": page.width_mm,
            "height_mm": page.height_mm,
            "dpi": page.dpi,
            "orientation": page.orientation.value,
            "margin_mm": page.margin_mm,
        },
        "elements": [_element_to_dict(e) for e in document.elements],
    }


def layout_from_dict(payload: dict[str, object]) -> LayoutDocument:
    """从工程字典恢复布局文档。"""
    # 工程 JSON 载荷经过运行时校验；对象和数组先做类型收窄再读取。
    page_data = payload.get("page", {})
    if not isinstance(page_data, dict):
        page_data = {}
    orientation_str = page_data.get("orientation", "portrait")
    try:
        orientation = PageOrientation(orientation_str)
    except ValueError:
        orientation = PageOrientation.PORTRAIT
    page = LayoutPage(
        name=str(page_data.get("name", "A4")),
        width_mm=float(page_data.get("width_mm", 210.0)),
        height_mm=float(page_data.get("height_mm", 297.0)),
        dpi=float(page_data.get("dpi", 300.0)),
        orientation=orientation,
        margin_mm=float(page_data.get("margin_mm", 10.0)),
    )
    raw_elements = payload.get("elements", [])
    if not isinstance(raw_elements, list):
        raw_elements = []
    elements: list[LayoutElement] = []
    for item in raw_elements:
        if isinstance(item, dict):
            elem = _element_from_dict(item)
            if elem is not None:
                elements.append(elem)
    return LayoutDocument(page=page, elements=tuple(elements))


def _element_to_dict(element: LayoutElement) -> dict[str, object]:
    """把单个布局元素转换为字典。"""
    base: dict[str, object] = {
        "type": element.element_type,
        "element_id": element.element_id,
        "x_mm": element.x_mm,
        "y_mm": element.y_mm,
        "width_mm": element.width_mm,
        "height_mm": element.height_mm,
        "rotation": element.rotation,
    }
    if isinstance(element, MapFrameElement):
        base.update({
            "map_center_x": element.map_center_x,
            "map_center_y": element.map_center_y,
            "map_units_per_pixel": element.map_units_per_pixel,
            "border_color": element.border_color,
            "border_width_mm": element.border_width_mm,
            "background_color": element.background_color,
        })
    elif isinstance(element, ScaleBarElement):
        base.update({
            "linked_frame_id": element.linked_frame_id,
            "style": element.style,
            "unit": element.unit,
            "num_segments": element.num_segments,
            "label_font_size_mm": element.label_font_size_mm,
            "color": element.color,
        })
    elif isinstance(element, LegendElement):
        base.update({
            "linked_frame_id": element.linked_frame_id,
            "title": element.title,
            "title_font_size_mm": element.title_font_size_mm,
            "item_font_size_mm": element.item_font_size_mm,
            "column_count": element.column_count,
        })
    elif isinstance(element, NorthArrowElement):
        base.update({
            "style": element.style,
            "color": element.color,
        })
    elif isinstance(element, TextElement):
        base.update({
            "text": element.text,
            "font_size_mm": element.font_size_mm,
            "color": element.color,
            "bold": element.bold,
            "italic": element.italic,
            "alignment": element.alignment,
        })
    return base


def _element_from_dict(data: dict[str, object]) -> LayoutElement | None:
    """从字典恢复单个布局元素。"""
    type_name = data.get("type", "")
    # 载荷来自工程 JSON，数值字段先收窄为可转换类型再交给构造器；
    # 各元素构造器字段为 str/float 混合，公共字段用 Any 展开。
    common: dict[str, Any] = {
        "element_id": str(data.get("element_id", _new_element_id())),
        "x_mm": float(cast(str | int | float, data.get("x_mm", 10.0))),
        "y_mm": float(cast(str | int | float, data.get("y_mm", 10.0))),
        "width_mm": float(cast(str | int | float, data.get("width_mm", 80.0))),
        "height_mm": float(cast(str | int | float, data.get("height_mm", 60.0))),
        "rotation": float(cast(str | int | float, data.get("rotation", 0.0))),
    }
    if type_name == "MapFrameElement":
        return MapFrameElement(
            **common,
            map_center_x=float(cast(str | int | float, data.get("map_center_x", 0.0))),
            map_center_y=float(cast(str | int | float, data.get("map_center_y", 0.0))),
            map_units_per_pixel=float(
                cast(str | int | float, data.get("map_units_per_pixel", 1.0))
            ),
            border_color=str(data.get("border_color", "#333333")),
            border_width_mm=float(
                cast(str | int | float, data.get("border_width_mm", 0.5))
            ),
            background_color=str(data.get("background_color", "#ffffff")),
        )
    if type_name == "ScaleBarElement":
        return ScaleBarElement(
            **common,
            linked_frame_id=str(data.get("linked_frame_id", "")),
            style=str(data.get("style", "alternating")),
            unit=str(data.get("unit", "km")),
            num_segments=int(cast(str | int | float, data.get("num_segments", 4))),
            label_font_size_mm=float(
                cast(str | int | float, data.get("label_font_size_mm", 2.5))
            ),
            color=str(data.get("color", "#000000")),
        )
    if type_name == "LegendElement":
        return LegendElement(
            **common,
            linked_frame_id=str(data.get("linked_frame_id", "")),
            title=str(data.get("title", "图例")),
            title_font_size_mm=float(
                cast(str | int | float, data.get("title_font_size_mm", 3.0))
            ),
            item_font_size_mm=float(
                cast(str | int | float, data.get("item_font_size_mm", 2.5))
            ),
            column_count=int(cast(str | int | float, data.get("column_count", 1))),
        )
    if type_name == "NorthArrowElement":
        return NorthArrowElement(
            **common,
            style=str(data.get("style", "compass")),
            color=str(data.get("color", "#333333")),
        )
    if type_name == "TextElement":
        return TextElement(
            **common,
            text=str(data.get("text", "文本")),
            font_size_mm=float(cast(str | int | float, data.get("font_size_mm", 3.0))),
            color=str(data.get("color", "#000000")),
            bold=bool(data.get("bold", False)),
            italic=bool(data.get("italic", False)),
            alignment=str(data.get("alignment", "left")),
        )
    return None
