"""栅格分析算法内核：重分类、DEM 地形和掩膜裁剪。

本模块只包含与 Qt/Rasterio 无关的纯 NumPy 算法内核和不可变请求对象。
窗口读取、重投影和 GeoTIFF 写入由基础设施层和服务层负责。
"""

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
from numpy.typing import NDArray

# ---------------------------------------------------------------------------
# 重分类
# ---------------------------------------------------------------------------

UnmatchedPolicy = Literal["nodata", "keep", "constant"]
DemMode = Literal["slope", "aspect", "hillshade"]
ResamplingMethod = Literal["nearest", "bilinear", "cubic"]


@dataclass(frozen=True, slots=True)
class ReclassRule:
    """一条重分类区间规则。

    区间为半开区间，默认下包含、上不包含：[lower, upper)。
    lower/upper 为 None 表示该侧无界。
    """

    lower: float | None
    """区间下限；None 表示负无穷。"""

    upper: float | None
    """区间上限；None 表示正无穷。"""

    output_value: float
    """命中该区间的像元输出值。"""

    include_lower: bool = True
    """是否包含下限；True 为 [lower，False 为 (lower。"""

    include_upper: bool = False
    """是否包含上限；True 为 upper]，False 为 upper)。"""

    def __post_init__(self) -> None:
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError(
                f"重分类规则下限 {self.lower} 大于上限 {self.upper}。"
            )

    def matches(self, value: float) -> bool:
        """判断单个标量值是否落入本规则区间。"""
        if self.lower is not None:
            if self.include_lower and value < self.lower:
                return False
            if not self.include_lower and value <= self.lower:
                return False
        if self.upper is not None:
            if self.include_upper and value > self.upper:
                return False
            if not self.include_upper and value >= self.upper:
                return False
        return True


def build_unique_value_rules(
    data: NDArray[np.generic],
    valid_mask: NDArray[np.bool_],
    max_rules: int = 500,
) -> tuple[ReclassRule, ...]:
    """根据有效像元的唯一值生成重分类规则。

    新值从 1 开始连续编号，便于将土地覆盖等离散值转换为适合制图的类别码；
    用户仍可在规则表中逐项修改新值。
    """
    values = data.astype(np.float64)[valid_mask]
    values = values[np.isfinite(values)]
    unique_values = np.unique(values)
    if unique_values.size == 0:
        raise ValueError("输入波段没有可用于生成规则的有效像元。")
    if unique_values.size > max_rules:
        raise ValueError(
            f"唯一值数量为 {unique_values.size}，超过 {max_rules} 行的安全上限；"
            "请改用按范围分类或缩小数据范围。"
        )
    return tuple(
        ReclassRule(
            lower=float(value),
            upper=float(value),
            output_value=float(index),
            include_lower=True,
            include_upper=True,
        )
        for index, value in enumerate(unique_values, start=1)
    )


def build_equal_interval_rules(
    data: NDArray[np.generic],
    valid_mask: NDArray[np.bool_],
    class_count: int,
) -> tuple[ReclassRule, ...]:
    """按有效像元最小值、最大值生成等距分级规则。"""
    if class_count < 2:
        raise ValueError("分类数必须至少为 2。")
    values = data.astype(np.float64)[valid_mask]
    values = values[np.isfinite(values)]
    if values.size == 0:
        raise ValueError("输入波段没有可用于生成规则的有效像元。")
    lower = float(np.min(values))
    upper = float(np.max(values))
    if math.isclose(lower, upper):
        return (
            ReclassRule(
                lower=lower,
                upper=upper,
                output_value=1.0,
                include_lower=True,
                include_upper=True,
            ),
        )

    edges = np.linspace(lower, upper, class_count + 1)
    return tuple(
        ReclassRule(
            lower=float(edges[index]),
            upper=float(edges[index + 1]),
            output_value=float(index + 1),
            include_lower=True,
            include_upper=index == class_count - 1,
        )
        for index in range(class_count)
    )


def build_quantile_rules(
    data: NDArray[np.generic],
    valid_mask: NDArray[np.bool_],
    class_count: int,
) -> tuple[ReclassRule, ...]:
    """按分位数生成重分类规则；重复分位点会自动合并。"""
    if class_count < 2:
        raise ValueError("分类数必须至少为 2。")
    values = data.astype(np.float64)[valid_mask]
    values = values[np.isfinite(values)]
    if values.size == 0:
        raise ValueError("输入波段没有可用于生成规则的有效像元。")
    if np.all(values == values[0]):
        value = float(values[0])
        return (
            ReclassRule(
                lower=value,
                upper=value,
                output_value=1.0,
                include_lower=True,
                include_upper=True,
            ),
        )

    quantiles = np.quantile(values, np.linspace(0.0, 1.0, class_count + 1))
    edges = np.unique(quantiles)
    return tuple(
        ReclassRule(
            lower=float(edges[index]),
            upper=float(edges[index + 1]),
            output_value=float(index + 1),
            include_lower=True,
            include_upper=index == len(edges) - 2,
        )
        for index in range(len(edges) - 1)
    )


@dataclass(frozen=True, slots=True)
class RasterReclassifyRequest:
    """栅格重分类请求。"""

    input_layer_id: str
    band_index: int
    """1-based 波段编号。"""

    rules: tuple[ReclassRule, ...]
    unmatched_policy: UnmatchedPolicy
    unmatched_constant: float | None = None
    """unmatched_policy 为 constant 时使用的统一值。"""

    output_dtype: str = "float32"
    output_nodata: float | None = -9999.0
    output_layer_name: str = ""
    output_path: Path = Path()

    def __post_init__(self) -> None:
        if not self.rules:
            raise ValueError("重分类规则至少需要一条。")
        if self.band_index < 1:
            raise ValueError("波段编号必须 ≥ 1。")
        if self.unmatched_policy == "constant" and self.unmatched_constant is None:
            raise ValueError("未匹配策略为 constant 时必须指定常量值。")
        if self.output_nodata is not None:
            _validate_nodata_representable(self.output_dtype, self.output_nodata)
        _validate_no_rule_overlap(self.rules)


@dataclass(frozen=True, slots=True)
class DemAnalysisRequest:
    """DEM 地形分析请求。"""

    input_layer_id: str
    band_index: int = 1
    mode: DemMode = "slope"
    elevation_unit: Literal["meter", "foot"] = "meter"
    z_factor: float | None = None
    """可选 Z 因子；为空时根据水平和高程单位自动换算。"""

    azimuth: float = 315.0
    """山体阴影太阳方位角（度），默认 315。"""

    altitude: float = 45.0
    """山体阴影太阳高度角（度），默认 45。"""

    output_layer_name: str = ""
    output_path: Path = Path()
    output_nodata: float | None = -9999.0

    def __post_init__(self) -> None:
        if self.band_index < 1:
            raise ValueError("波段编号必须 ≥ 1。")
        if self.elevation_unit not in ("meter", "foot"):
            raise ValueError(f"不支持的高程单位：{self.elevation_unit}。")
        if not 0.0 <= self.azimuth < 360.0:
            raise ValueError("太阳方位角必须在 [0, 360) 范围内。")
        if not 0.0 < self.altitude < 90.0:
            raise ValueError("太阳高度角必须在 (0, 90) 范围内。")


@dataclass(frozen=True, slots=True)
class RasterClipRequest:
    """按矢量掩膜裁剪栅格的请求。"""

    raster_layer_id: str
    mask_layer_id: str
    crop: bool = True
    """是否缩小输出范围至掩膜外接范围与输入栅格范围的交集。"""

    all_touched: bool = False
    """是否将所有被几何边界触碰的像元视为有效。"""

    invert: bool = False
    """是否反转掩膜（保留矢量范围外区域）。"""

    output_layer_name: str = ""
    output_path: Path = Path()


# ---------------------------------------------------------------------------
# 公共辅助
# ---------------------------------------------------------------------------

_DTYPE_NODATA_LIMITS: dict[str, tuple[float, float]] = {
    "int8": (-128.0, 127.0),
    "int16": (-32768.0, 32767.0),
    "int32": (-2147483648.0, 2147483647.0),
    "uint8": (0.0, 255.0),
    "uint16": (0.0, 65535.0),
    "uint32": (0.0, 4294967295.0),
    "float16": (-65504.0, 65504.0),
    "float32": (-3.4e38, 3.4e38),
    "float64": (-1.8e308, 1.8e308),
}


def _validate_nodata_representable(dtype: str, nodata: float) -> None:
    """校验 NoData 值能被目标数据类型表示。"""
    limits = _DTYPE_NODATA_LIMITS.get(dtype.lower())
    if limits is None:
        return
    lo, hi = limits
    if not (lo <= nodata <= hi) or math.isnan(nodata):
        raise ValueError(
            f"NoData 值 {nodata} 无法用数据类型 {dtype} 表示。"
        )


def _validate_no_rule_overlap(rules: tuple[ReclassRule, ...]) -> None:
    """检查重分类规则区间是否存在重叠。"""
    # 将每条规则转换为闭区间端点用于比较，None 用极值替代。
    for left_index, left in enumerate(rules):
        for right in rules[left_index + 1:]:
            left_lower = left.lower if left.lower is not None else -math.inf
            left_upper = left.upper if left.upper is not None else math.inf
            right_lower = right.lower if right.lower is not None else -math.inf
            right_upper = right.upper if right.upper is not None else math.inf
            overlap_lower = max(left_lower, right_lower)
            overlap_upper = min(left_upper, right_upper)
            if overlap_lower < overlap_upper:
                raise ValueError(
                    "重分类规则区间存在重叠，请在提交前调整。"
                )
            if overlap_lower == overlap_upper:
                left_contains = left.matches(overlap_lower)
                right_contains = right.matches(overlap_lower)
                if left_contains and right_contains:
                    raise ValueError(
                        "重分类规则区间存在重叠，请在提交前调整。"
                    )


# ---------------------------------------------------------------------------
# 历史参数序列化
# ---------------------------------------------------------------------------


def reclassify_history_parameters(request: RasterReclassifyRequest) -> dict[str, object]:
    """将重分类请求转换为可持久化的历史参数。"""
    return {
        "band_index": request.band_index,
        "rule_count": len(request.rules),
        "unmatched_policy": request.unmatched_policy,
        "output_dtype": request.output_dtype,
        "output_nodata": request.output_nodata,
        "output_path": str(request.output_path.expanduser().resolve()),
        "output_layer_name": request.output_layer_name,
    }


def dem_history_parameters(request: DemAnalysisRequest) -> dict[str, object]:
    """将 DEM 分析请求转换为可持久化的历史参数。"""
    return {
        "mode": request.mode,
        "band_index": request.band_index,
        "elevation_unit": request.elevation_unit,
        "z_factor": request.z_factor,
        "azimuth": request.azimuth,
        "altitude": request.altitude,
        "output_path": str(request.output_path.expanduser().resolve()),
        "output_layer_name": request.output_layer_name,
    }


def clip_history_parameters(request: RasterClipRequest) -> dict[str, object]:
    """将掩膜裁剪请求转换为可持久化的历史参数。"""
    return {
        "crop": request.crop,
        "all_touched": request.all_touched,
        "invert": request.invert,
        "output_path": str(request.output_path.expanduser().resolve()),
        "output_layer_name": request.output_layer_name,
    }


# ---------------------------------------------------------------------------
# 算法内核：重分类
# ---------------------------------------------------------------------------


def reclassify_array(
    data: NDArray[np.generic],
    valid_mask: NDArray[np.bool_],
    rules: tuple[ReclassRule, ...],
    unmatched_policy: UnmatchedPolicy,
    unmatched_constant: float | None,
    output_dtype: str,
    output_nodata: float | None = None,
) -> tuple[NDArray[np.generic], NDArray[np.bool_]]:
    """对单波段数组执行重分类。

    参数:
        data: 输入 2D 像元数组。
        valid_mask: 输入有效像元掩膜；无效像元不参与重分类。
        rules: 重分类规则元组。
        unmatched_policy: 未匹配像元策略。
        unmatched_constant: constant 策略的常量值。
        output_dtype: 输出 numpy dtype 字符串。

    返回:
        (重分类结果数组, 输出有效掩膜)。
    """
    result = np.full(data.shape, np.nan, dtype=np.float64)
    matched = np.zeros(data.shape, dtype=bool)
    remaining = valid_mask.copy()
    # 统一转为浮点数组参与区间比较，避免泛型 dtype 比较产生类型歧义。
    values: NDArray[np.float64] = data.astype(np.float64)

    rule: ReclassRule
    for rule in rules:
        candidate = remaining.copy()
        # 逐规则生成布尔命中掩膜。
        if rule.lower is not None:
            if rule.include_lower:
                candidate &= values >= rule.lower
            else:
                candidate &= values > rule.lower
        if rule.upper is not None:
            if rule.include_upper:
                candidate &= values <= rule.upper
            else:
                candidate &= values < rule.upper
        result[candidate] = rule.output_value
        matched |= candidate
        remaining &= ~candidate

    # 处理未匹配的有效像元。
    unmatched = valid_mask & ~matched
    if unmatched_policy == "nodata":
        output_valid = matched.copy()
    elif unmatched_policy == "keep":
        result[unmatched] = values[unmatched]
        output_valid = valid_mask.copy()
    else:  # constant
        result[unmatched] = unmatched_constant if unmatched_constant is not None else np.nan
        output_valid = valid_mask.copy()

    return _cast_output(result, output_dtype, output_nodata), output_valid


def _cast_output(
    data: NDArray[np.generic], dtype: str, output_nodata: float | None = None
) -> NDArray[np.generic]:
    """将浮点中间结果转换为目标 dtype，NaN 转换由调用方处理。"""
    target_dtype = np.dtype(dtype)
    if np.issubdtype(target_dtype, np.integer) and np.isnan(data).any():
        if output_nodata is None:
            raise ValueError("整数输出必须指定可表示的 NoData 值")
        data = data.copy()
        data[np.isnan(data)] = output_nodata
    return data.astype(target_dtype)


# ---------------------------------------------------------------------------
# 算法内核：DEM 地形分析
# ---------------------------------------------------------------------------


def resolve_z_factor(elevation_unit: str, z_factor: float | None) -> float:
    """根据高程单位计算 Z 因子。

    高程为英尺时需要乘以 0.3048 转换为米制；用户显式指定时优先使用。
    """
    if z_factor is not None:
        return z_factor
    if elevation_unit == "foot":
        return 0.3048
    return 1.0


def compute_slope(
    dem: NDArray[np.generic],
    valid_mask: NDArray[np.bool_],
    pixel_width: float,
    pixel_height: float,
    z_factor: float,
) -> tuple[NDArray[np.generic], NDArray[np.bool_]]:
    """以 3×3 中心差分计算坡度（度）。

    邻域中包含 NoData 的像元输出 NoData，避免边缘差分产生伪造坡度。
    """
    z = dem.astype(np.float64) * z_factor
    output = np.full(dem.shape, np.nan, dtype=np.float64)
    output_valid = np.zeros(dem.shape, dtype=bool)

    # 3×3 邻域有效性：窗口内全部有效才输出。
    kernel_valid = _all_neighbours_valid(valid_mask, radius=1)
    dx = (z[:, 2:] - z[:, :-2]) / (2.0 * pixel_width)
    dy = (z[2:, :] - z[:-2, :]) / (2.0 * pixel_height)
    # 中心区域为 [1:-1, 1:-1]。
    dz_dx = np.zeros(dem.shape, dtype=np.float64)
    dz_dy = np.zeros(dem.shape, dtype=np.float64)
    dz_dx[1:-1, 1:-1] = dx[1:-1, :]
    dz_dy[1:-1, 1:-1] = dy[:, 1:-1]
    slope_rad = np.arctan(np.sqrt(dz_dx**2 + dz_dy**2))
    slope_deg = np.degrees(slope_rad)
    output_valid = kernel_valid
    output = slope_deg
    output[~output_valid] = np.nan
    return output.astype(np.float32), output_valid


def compute_aspect(
    dem: NDArray[np.generic],
    valid_mask: NDArray[np.bool_],
    pixel_width: float,
    pixel_height: float,
    z_factor: float,
) -> tuple[NDArray[np.generic], NDArray[np.bool_]]:
    """以 3×3 中心差分计算坡向（度，北为 0，顺时针）。

    平坦区域没有稳定方向，输出 NoData。
    """
    z = dem.astype(np.float64) * z_factor
    kernel_valid = _all_neighbours_valid(valid_mask, radius=1)
    dx = (z[:, 2:] - z[:, :-2]) / (2.0 * pixel_width)
    dy = (z[2:, :] - z[:-2, :]) / (2.0 * pixel_height)
    dz_dx = np.zeros(dem.shape, dtype=np.float64)
    dz_dy = np.zeros(dem.shape, dtype=np.float64)
    dz_dx[1:-1, 1:-1] = dx[1:-1, :]
    dz_dy[1:-1, 1:-1] = dy[:, 1:-1]
    # 坡向：下坡度方向在北为 0°、顺时针增加的罗盘角约定下为
    # atan2(-dz/dx, dz/drow)，其中 dz/drow 是沿行号增大（向南）的变化率。
    aspect_rad = np.arctan2(-dz_dx, dz_dy)
    aspect_deg = np.degrees(aspect_rad)
    aspect_deg = np.where(aspect_deg < 0, aspect_deg + 360.0, aspect_deg)
    # 平坦（dx 和 dy 都接近零）时输出 NoData；单方向倾斜仍有效。
    flat = (np.abs(dz_dx) < 1e-12) & (np.abs(dz_dy) < 1e-12)
    output_valid = kernel_valid & ~flat
    aspect_deg[~output_valid] = np.nan
    return aspect_deg.astype(np.float32), output_valid


def compute_hillshade(
    dem: NDArray[np.generic],
    valid_mask: NDArray[np.bool_],
    pixel_width: float,
    pixel_height: float,
    z_factor: float,
    azimuth: float,
    altitude: float,
) -> tuple[NDArray[np.generic], NDArray[np.bool_]]:
    """计算山体阴影，输出 0–255。"""
    z = dem.astype(np.float64) * z_factor
    kernel_valid = _all_neighbours_valid(valid_mask, radius=1)
    dx = (z[:, 2:] - z[:, :-2]) / (2.0 * pixel_width)
    dy = (z[2:, :] - z[:-2, :]) / (2.0 * pixel_height)
    dz_dx = np.zeros(dem.shape, dtype=np.float64)
    dz_dy = np.zeros(dem.shape, dtype=np.float64)
    dz_dx[1:-1, 1:-1] = dx[1:-1, :]
    dz_dy[1:-1, 1:-1] = dy[:, 1:-1]

    slope_rad = np.arctan(np.sqrt(dz_dx**2 + dz_dy**2))
    aspect_rad = np.arctan2(dz_dy, -dz_dx)

    zenith_rad = np.radians(90.0 - altitude)
    azimuth_rad = np.radians(360.0 - azimuth + 90.0)

    hillshade = (
        np.cos(zenith_rad) * np.cos(slope_rad)
        + np.sin(zenith_rad) * np.sin(slope_rad)
        * np.cos(azimuth_rad - aspect_rad)
    )
    hillshade = np.clip(hillshade * 255.0, 0, 255)
    output_valid = kernel_valid
    hillshade[~output_valid] = 0
    return hillshade.astype(np.uint8), output_valid


def _all_neighbours_valid(
    valid_mask: NDArray[np.bool_], radius: int
) -> NDArray[np.bool_]:
    """返回每个像元的指定半径方形邻域是否全部有效。"""
    from scipy.ndimage import minimum_filter

    neighbor: NDArray[np.bool_] = minimum_filter(
        valid_mask, size=2 * radius + 1, mode="constant", cval=False
    )
    return neighbor


# ---------------------------------------------------------------------------
# 算法内核：掩膜裁剪
# ---------------------------------------------------------------------------


def apply_geometry_mask(
    data: NDArray[np.generic],
    input_valid: NDArray[np.bool_],
    geometry_mask: NDArray[np.bool_],
    invert: bool,
) -> tuple[NDArray[np.generic], NDArray[np.bool_]]:
    """将几何掩膜应用到单波段栅格数据。

    参数:
        data: 输入 2D 像元数组。
        input_valid: 输入有效掩膜。
        geometry_mask: 几何掩膜，True 表示在矢量范围内。
        invert: 为 True 时保留矢量范围外像元。

    返回:
        (保留原始值的像元数组, 输出有效掩膜)。
    """
    keep = ~geometry_mask if invert else geometry_mask
    output_valid = input_valid & keep
    return data, output_valid
