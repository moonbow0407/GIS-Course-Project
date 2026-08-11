"""矢量坐标转换端口。

显示投影服务和重投影工具依赖该端口把几何与范围在图层 CRS 与
目标 CRS 之间转换；具体转换由基础设施层的 PyProj 适配器实现，
应用层不直接依赖坐标转换库。
"""

from typing import Protocol

from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from app.domain.feature import Feature
from app.domain.vector_layer import Bounds


class CoordinateTransformer(Protocol):
    """定义把矢量几何在坐标系之间转换的能力。"""

    def transform_features(
        self,
        features: tuple[Feature, ...],
        source_crs: CRS,
        target_crs: CRS,
    ) -> tuple[Feature, ...]:
        """转换要素几何，要素编号和属性保持不变。

        参数:
            features: 待转换的要素集合。
            source_crs: 要素当前所在坐标系。
            target_crs: 转换目标坐标系。

        返回:
            几何已转换到目标坐标系的要素集合。

        异常:
            ValueError: 坐标系无法转换时抛出。
        """
        ...

    def transform_geometry(
        self,
        geometry: BaseGeometry,
        source_crs: CRS,
        target_crs: CRS,
    ) -> BaseGeometry:
        """转换单个查询或编辑几何到目标坐标系。"""
        ...

    def transform_bounds(
        self,
        bounds: Bounds,
        source_crs: CRS,
        target_crs: CRS,
    ) -> Bounds:
        """把空间范围转换到目标坐标系后的最小包围矩形。

        范围转换通过对四个角点逐点转换后取最小/最大值近似完成。

        参数:
            bounds: 源坐标系下的空间范围 (min_x, min_y, max_x, max_y)。
            source_crs: 范围当前所在坐标系。
            target_crs: 转换目标坐标系。

        返回:
            覆盖全部转换后角点的最小包围矩形。

        异常:
            ValueError: 坐标系无法转换时抛出。
        """
        ...

    def describe_operation(self, source_crs: CRS, target_crs: CRS) -> str:
        """返回本次转换由投影库自动选择的大地基准转换操作摘要。"""
        ...
