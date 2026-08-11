"""栅格分析目标网格值对象。

统一描述一次栅格计算所使用的空间网格：CRS、仿射变换、行列数。
所有栅格分析（计算器、重分类、DEM、掩膜裁剪）都基于该值对象
判断空间对齐，避免用 NumPy 数组广播代替地理空间对齐。
"""

from dataclasses import dataclass

from affine import Affine
from pyproj import CRS

from app.domain.vector_layer import Bounds

# 仿射变换分量比较容差：浮点运算产生的微小差异不应判定为未对齐。
_TRANSFORM_TOLERANCE: float = 1e-9


@dataclass(frozen=True, slots=True)
class RasterGrid:
    """描述栅格分析的目标空间网格。"""

    crs: CRS
    """网格所属坐标参考系统。"""

    transform: Affine
    """将像元列行转换为地图坐标的仿射变换。"""

    width: int
    """网格列数。"""

    height: int
    """网格行数。"""

    def __post_init__(self) -> None:
        """校验行列数为正数，且仿射变换不退化。"""
        if self.width <= 0:
            raise ValueError(f"栅格网格列数必须为正数，实际为 {self.width}。")
        if self.height <= 0:
            raise ValueError(f"栅格网格行数必须为正数，实际为 {self.height}。")
        if self.transform.a == 0 or self.transform.e == 0:
            raise ValueError("栅格网格仿射变换的像元尺寸不能为零。")

    @property
    def pixel_width(self) -> float:
        """返回像元宽度（X 方向分辨率，正值）。"""
        return abs(self.transform.a)

    @property
    def pixel_height(self) -> float:
        """返回像元高度（Y 方向分辨率，正值）。"""
        return abs(self.transform.e)

    @property
    def bounds(self) -> Bounds:
        """返回网格在地图坐标系中的空间范围 (min_x, min_y, max_x, max_y)。"""
        from rasterio.transform import array_bounds

        raw: tuple[float, float, float, float] = array_bounds(
            self.height, self.width, self.transform
        )
        return (
            min(raw[0], raw[2]),
            min(raw[1], raw[3]),
            max(raw[0], raw[2]),
            max(raw[1], raw[3]),
        )

    @property
    def has_rotation(self) -> bool:
        """返回仿射变换是否包含旋转项。"""
        return (
            abs(self.transform.b) > _TRANSFORM_TOLERANCE
            or abs(self.transform.d) > _TRANSFORM_TOLERANCE
        )

    def matches(self, other: object) -> bool:
        """判断两个网格在容差内是否完全一致。

        不能只比较行列数和像元大小，还必须比较 CRS、仿射变换的原点、
        旋转项、像元大小和范围。NumPy 数组可以广播，不代表两个栅格在
        地理空间上对齐。
        """
        if not isinstance(other, RasterGrid):
            return NotImplemented
        return (
            self.width == other.width
            and self.height == other.height
            and self.crs.equals(other.crs, ignore_axis_order=True)
            and _affine_close(self.transform, other.transform)
        )


def _affine_close(left: Affine, right: Affine) -> bool:
    """逐分量比较两个仿射变换是否在容差内一致。"""
    return (
        abs(left.a - right.a) <= _TRANSFORM_TOLERANCE
        and abs(left.b - right.b) <= _TRANSFORM_TOLERANCE
        and abs(left.c - right.c) <= _TRANSFORM_TOLERANCE
        and abs(left.d - right.d) <= _TRANSFORM_TOLERANCE
        and abs(left.e - right.e) <= _TRANSFORM_TOLERANCE
        and abs(left.f - right.f) <= _TRANSFORM_TOLERANCE
    )


def grid_from_layer(layer: object) -> RasterGrid:
    """从 RasterLayer 提取目标网格。

    参数:
        layer: 已加载的栅格图层，需包含 crs、transform、raster_shape。

    返回:
        与输入栅格空间网格一致的 RasterGrid。

    异常:
        ValueError: 图层缺少 CRS 或不是栅格图层。
    """
    from app.domain.raster_layer import RasterLayer

    if not isinstance(layer, RasterLayer):
        raise ValueError("目标网格只能从栅格图层提取。")
    if layer.crs is None:
        raise ValueError(f"栅格图层「{layer.name}」没有坐标参考系统，无法作为目标网格。")
    height, width = layer.raster_shape
    return RasterGrid(
        crs=layer.crs,
        transform=layer.transform,
        width=width,
        height=height,
    )
