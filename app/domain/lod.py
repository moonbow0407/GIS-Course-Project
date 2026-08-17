"""矢量图层的多级细节层次（LOD）领域模型。"""

from dataclasses import dataclass

from app.domain.feature import Feature


@dataclass(frozen=True, slots=True)
class LodLevel:
    """表示单个简化级别：简化容差与该容差下的要素集合。

    要素几何经过拓扑保持简化，编号与属性保持不变，相邻要素在共享
    边界处仍紧密贴合，不会因逐要素简化产生缝隙或重叠。
    """

    # 简化容差：该级别生成时使用的容差，单位与图层源坐标系一致。
    tolerance: float

    # 简化后的要素：编号与原图层逐一对齐，几何为贴合简化结果。
    features: tuple[Feature, ...]

    def __post_init__(self) -> None:
        """校验简化容差非负。"""
        if self.tolerance < 0.0:
            raise ValueError("简化容差不能为负数。")


@dataclass(frozen=True, slots=True)
class LodPyramid:
    """表示一个矢量图层的多级 LOD 金字塔。

    级别按容差严格升序排列，首级通常为原始几何（容差为零）。渲染时
    根据当前视图的每像素地图单位选择容差不超过该值的最粗级别，实现
    “缩小取简化、放大取细节”的自适应绘制。
    """

    # 简化级别：按容差升序排列，至少包含一个级别。
    levels: tuple[LodLevel, ...]

    def __post_init__(self) -> None:
        """校验级别非空且容差严格升序。"""
        if not self.levels:
            raise ValueError("LOD 金字塔必须至少包含一个级别。")
        previous: LodLevel | None = None
        for level in self.levels:
            if previous is not None and level.tolerance <= previous.tolerance:
                raise ValueError("LOD 级别容差必须严格递增。")
            previous = level

    def select(self, map_units_per_pixel: float) -> tuple[Feature, ...]:
        """按当前每像素地图单位选择最合适的简化级别。

        参数:
            map_units_per_pixel: 当前视图一个屏幕像素对应的地图单位。

        返回:
            容差不超过该值的最粗级别要素；放大到任何级别都无法匹配时
            回退到最细级别。
        """
        for level in reversed(self.levels):
            if level.tolerance <= map_units_per_pixel:
                return level.features
        return self.levels[0].features

    def select_fade(
        self,
        map_units_per_pixel: float,
    ) -> tuple[tuple[Feature, ...], tuple[Feature, ...], float]:
        """返回用于交叉淡化的相邻两个级别与向粗级别的插值因子。

        每像素地图单位连续变化，落在相邻两级容差之间时按线性插值混合
        两级几何，避免硬切换造成的形状跳变。返回 ``(细级别要素, 粗级别
        要素, t)``，``t`` 为 0 时仅显示细级别、为 1 时仅显示粗级别；
        位于首级之前或末级之后时两级相同，分别返回最细/最粗级别。

        参数:
            map_units_per_pixel: 当前视图一个屏幕像素对应的地图单位。
        """
        levels: tuple[LodLevel, ...] = self.levels
        first: LodLevel = levels[0]
        last: LodLevel = levels[-1]
        if map_units_per_pixel <= first.tolerance:
            return first.features, first.features, 0.0
        if map_units_per_pixel >= last.tolerance:
            return last.features, last.features, 1.0
        fine: LodLevel
        coarse: LodLevel
        for fine, coarse in zip(levels, levels[1:]):
            if fine.tolerance <= map_units_per_pixel <= coarse.tolerance:
                span: float = coarse.tolerance - fine.tolerance
                t: float = (
                    0.0
                    if span <= 0.0
                    else (map_units_per_pixel - fine.tolerance) / span
                )
                return fine.features, coarse.features, t
        # 理论上不可达（首末级已在上面处理）；兜底返回最粗级别。
        return last.features, last.features, 1.0
