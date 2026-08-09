"""布局视图领域模型 —— 纸张页面、制图元素与排版文档。

在布局视图中，所有元素位置使用 **毫米** 表示，原点位于页面左上角。
"""

from dataclasses import dataclass, field
from enum import Enum
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


# ---------------------------------------------------------------------------
# 布局文档
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
