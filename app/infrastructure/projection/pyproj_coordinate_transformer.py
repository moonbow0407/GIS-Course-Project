"""基于 PyProj 的矢量坐标转换适配器。

项目内所有坐标转换统一使用 ``always_xy=True`` 轴顺序：输入输出坐标
始终按 (x, y) 解释，避免地理坐标系声明轴顺序不同导致的坐标交换。
"""

from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as transform_geometry

from app.application.crs_utils import crs_equivalent
from app.domain.feature import Feature
from app.domain.vector_layer import Bounds


class PyprojCoordinateTransformer:
    """使用 PyProj Transformer 转换矢量要素几何与空间范围。"""

    def transform_features(
        self,
        features: tuple[Feature, ...],
        source_crs: CRS,
        target_crs: CRS,
    ) -> tuple[Feature, ...]:
        """转换要素几何，要素编号和属性保持不变。"""
        if crs_equivalent(source_crs, target_crs):
            return features
        transformer: Transformer = Transformer.from_crs(
            source_crs,
            target_crs,
            always_xy=True,
        )
        return tuple(
            Feature(
                fid=feature.fid,
                geometry=transform_geometry(transformer.transform, feature.geometry),
                attributes=feature.attributes,
            )
            for feature in features
        )

    def transform_geometry(
        self,
        geometry: BaseGeometry,
        source_crs: CRS,
        target_crs: CRS,
    ) -> BaseGeometry:
        """转换单个空间查询几何，保持 XY 轴顺序。"""
        if crs_equivalent(source_crs, target_crs):
            return geometry
        transformer: Transformer = Transformer.from_crs(
            source_crs,
            target_crs,
            always_xy=True,
        )
        return transform_geometry(transformer.transform, geometry)

    def transform_bounds(
        self,
        bounds: Bounds,
        source_crs: CRS,
        target_crs: CRS,
    ) -> Bounds:
        """返回目标坐标系下覆盖源范围的最小包围矩形。

        投影边界可能不是直线，不能只转换四个角点；使用 PyProj 的
        ``transform_bounds`` 对边界加密采样，避免非线性投影下显示范围
        被低估。跨反经线范围仍由调用方按项目的 Bounds 约定处理。
        """
        if crs_equivalent(source_crs, target_crs):
            return bounds
        transformer: Transformer = Transformer.from_crs(
            source_crs,
            target_crs,
            always_xy=True,
        )
        min_x, min_y, max_x, max_y = bounds
        transformed_bounds: tuple[float, float, float, float] = (
            transformer.transform_bounds(
                min_x,
                min_y,
                max_x,
                max_y,
                densify_pts=21,
            )
        )
        return transformed_bounds

    def describe_operation(self, source_crs: CRS, target_crs: CRS) -> str:
        """报告 PyProj 自动选择的转换操作及可用精度。"""
        if crs_equivalent(source_crs, target_crs):
            return "CRS 等价，无需坐标转换"
        transformer: Transformer = Transformer.from_crs(
            source_crs,
            target_crs,
            always_xy=True,
        )
        description: str = transformer.description or transformer.name
        accuracy: float = transformer.accuracy
        if accuracy >= 0:
            return f"{description}；预计精度 {accuracy:g} 米"
        return description
