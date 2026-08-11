"""栅格计算器核心模块：逐像素数学表达式求值。

提供：
- BandMapping / RasterCalculatorRequest：不可变请求数据结构
- validate_band_alignment：检查输入波段空间对齐
- compute_raster_expression：受控 eval 执行逐像素 numpy 运算
- generate_display_image：单波段结果 → RGBA 显示图
"""

import ast
import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from affine import Affine
from numpy.typing import NDArray
from pyproj import CRS

from app.application.crs_utils import crs_equivalent

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BandMapping:
    """表达式变量名 → 栅格图层与波段的映射。"""

    alias: str
    """表达式中的变量名，须为纯字母/数字/下划线，不可与保留关键字冲突。"""
    layer_id: str
    """源栅格图层的唯一编号。"""
    band_index: int
    """1-based 波段编号。"""

    _RESERVED: tuple[str, ...] = (
        "where",
        "sin",
        "cos",
        "tan",
        "abs",
        "sqrt",
        "log",
        "log10",
        "exp",
        "clip",
        "maximum",
        "minimum",
        "pi",
        "e",
        "True",
        "False",
        "None",
        "and",
        "or",
        "not",
        "np",
    )

    def __post_init__(self) -> None:
        if not self.alias.strip():
            raise ValueError("波段变量名不能为空。")
        if not re.fullmatch(r"[a-zA-Z_]\w*", self.alias):
            raise ValueError(
                f"波段变量名「{self.alias}」格式无效，"
                "只允许字母、数字和下划线，且必须以字母或下划线开头。"
            )
        if self.alias in self._RESERVED:
            raise ValueError(
                f"「{self.alias}」是保留关键字，不能用作波段变量名。"
            )
        if self.band_index < 1:
            raise ValueError("波段编号必须 ≥ 1。")


@dataclass(frozen=True, slots=True)
class RasterCalculatorRequest:
    """栅格计算器的一次完整请求。"""

    expression: str
    """用户书写的数学表达式，引用 BandMapping.alias。"""
    band_mappings: tuple[BandMapping, ...]
    """至少一个变量 → 波段映射。"""
    output_layer_name: str
    """结果图层在图层列表中的显示名称。"""
    output_path: Path
    """输出 GeoTIFF 文件的完整路径。"""
    nodata: float | None = None
    """可选的统一 NoData 值。"""

    reference_layer_id: str | None = None
    """可选的显式参考栅格；其他同 CRS 网格会临时对齐到它。"""

    def __post_init__(self) -> None:
        if not self.expression.strip():
            raise ValueError("表达式不能为空。")
        if not self.band_mappings:
            raise ValueError("至少需要定义一个波段映射。")
        if not self.output_layer_name.strip():
            raise ValueError("输出图层名不能为空。")
        if not str(self.output_path).strip():
            raise ValueError("输出路径不能为空。")


# ---------------------------------------------------------------------------
# 对齐校验
# ---------------------------------------------------------------------------


def validate_band_alignment(
    transforms: tuple[Affine, ...],
    crss: tuple[CRS | None, ...],
    shapes: tuple[tuple[int, int], ...],
    layer_names: tuple[str, ...],
) -> list[str]:
    """检查多个输入波段是否在空间上对齐，返回人类可读的警告列表。

    参数:
        transforms: 每个波段的 Affine 变换。
        crss: 每个波段的 pyproj CRS（可为 None）。
        shapes: 每个波段的 (height, width)。
        layer_names: 对应图层名称，用于生成描述性警告。

    返回:
        警告信息列表；空列表表示完全对齐。
    """
    warnings: list[str] = []

    if len(crss) >= 2:
        first_crs = crss[0]
        for i in range(1, len(crss)):
            if not crs_equivalent(first_crs, crss[i]):
                warnings.append(
                    f"坐标系不一致：{layer_names[0]} 与 "
                    f"{layer_names[i]} 的 CRS 不同。"
                )

    if len(shapes) >= 2:
        first_shape = shapes[0]
        for i in range(1, len(shapes)):
            if shapes[i] != first_shape:
                warnings.append(
                    f"行列数不一致：{layer_names[0]} 为 {first_shape[1]}×{first_shape[0]}，"
                    f"{layer_names[i]} 为 {shapes[i][1]}×{shapes[i][0]}。"
                )

    if len(transforms) >= 2:
        first_tf = transforms[0]
        for i in range(1, len(transforms)):
            current_tf = transforms[i]
            if any(
                abs(left - right) > 1e-9
                for left, right in zip(first_tf[:], current_tf[:], strict=True)
            ):
                warnings.append(
                    f"像元网格不一致：{layer_names[0]} 为 "
                    f"{first_tf.a:.6f}×{abs(first_tf.e):.6f}，"
                    f"{layer_names[i]} 为 "
                    f"{current_tf.a:.6f}×{abs(current_tf.e):.6f}。"
                )
                break

    return warnings


# ---------------------------------------------------------------------------
# 表达式求值
# ---------------------------------------------------------------------------

# 表达式中支持的数学函数，映射为 numpy 等价函数。
_ALLOWED_FUNCTIONS: dict[str, object] = {
    "where": np.where,
    "sin": np.sin,
    "cos": np.cos,
    "tan": np.tan,
    "arcsin": np.arcsin,
    "arccos": np.arccos,
    "arctan": np.arctan,
    "abs": np.abs,
    "sqrt": np.sqrt,
    "log": np.log,
    "log10": np.log10,
    "exp": np.exp,
    "clip": np.clip,
    "maximum": np.maximum,
    "minimum": np.minimum,
    "pi": math.pi,
    "e": math.e,
}


def compute_raster_expression(
    band_arrays: dict[str, NDArray[np.generic]],
    expression: str,
) -> NDArray[np.generic]:
    """按逐像素表达式对输入波段求值，返回 2D 结果数组。

    使用 AST 白名单限制可执行节点：只允许数字、变量、指定函数、
    算术/比较/布尔运算，禁止属性访问、下标、导入和未注册函数调用。

    参数:
        band_arrays: alias → 2D numpy 数组（height × width）。
        expression: 引用 alias 的数学表达式。

    返回:
        与输入数组同形的 2D numpy 结果数组。

    异常:
        ValueError: 表达式语法错误、引用了未定义的变量或结果不是 2D 数组。
    """
    # ── 构建安全的求值命名空间 ──
    namespace: dict[str, object] = {
        **_ALLOWED_FUNCTIONS,
        "True": True,
        "False": False,
    }
    namespace.update(band_arrays)

    # ── 将表达式中的 "alias" 引用替换为裸变量名 ──
    transformed: str = expression
    quoted_refs: set[str] = set(re.findall(r'"([^"]+)"', transformed))
    unknown: set[str] = quoted_refs - set(band_arrays.keys())
    if unknown:
        raise ValueError(
            f"表达式中引用了未定义的波段变量：{', '.join(sorted(unknown))}。"
            f"可用变量：{', '.join(sorted(band_arrays.keys()))}"
        )
    for alias in quoted_refs & set(band_arrays.keys()):
        transformed = transformed.replace(f'"{alias}"', alias)

    # ── AST 白名单校验 ──
    try:
        tree = ast.parse(transformed, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"表达式语法错误：{exc}") from exc
    _validate_ast(tree)

    # ── 执行求值 ──
    try:
        result = eval(transformed, {"__builtins__": {}}, namespace)  # noqa: S307
    except SyntaxError as exc:
        raise ValueError(f"表达式语法错误：{exc}") from exc
    except Exception as exc:
        raise ValueError(f"表达式求值失败：{exc}") from exc

    # ── 结果校验 ──
    result_array: NDArray[np.generic] = np.asarray(result)

    if result_array.ndim == 0:
        first_shape = next(iter(band_arrays.values())).shape
        result_array = np.full(first_shape, result_array.item(), dtype=np.float32)

    if result_array.ndim != 2:
        raise ValueError(
            f"表达式结果必须是二维数组，实际维度为 {result_array.ndim}。"
        )

    return result_array.astype(np.float32, copy=False)


# AST 白名单允许的节点类型。
_AST_FORBIDDEN_NODES: tuple[type, ...] = (
    ast.Attribute,
    ast.Subscript,
    ast.Import,
    ast.ImportFrom,
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.Starred,
    ast.NamedExpr,
)


def _validate_ast(node: ast.AST) -> None:
    """递归校验 AST 节点只包含白名单允许的类型和已注册函数调用。"""
    for child in ast.walk(node):
        if isinstance(child, _AST_FORBIDDEN_NODES):
            raise ValueError(
                f"表达式包含不允许的语法结构：{type(child).__name__}。"
            )
        if isinstance(child, ast.Call):
            func = child.func
            if not isinstance(func, ast.Name):
                raise ValueError("表达式中的函数调用必须使用已注册的函数名。")
            if func.id not in _ALLOWED_FUNCTIONS:
                raise ValueError(
                    f"表达式引用了未注册的函数：{func.id}。"
                )


# ---------------------------------------------------------------------------
# 显示图生成
# ---------------------------------------------------------------------------


def generate_display_image(
    result_data: NDArray[np.generic],
    valid_mask: NDArray[np.bool_],
) -> NDArray[np.uint8]:
    """将单波段计算结果拉伸为 RGBA 显示图。

    采用 2%–98% 百分位线性拉伸生成灰阶 RGB，无效像素 alpha=0。

    参数:
        result_data: 2D 计算结果数组（height × width）。
        valid_mask: 有效像素掩码，True 表示该像素参与拉伸统计。

    返回:
        (height, width, 4) uint8 RGBA 数组。
    """
    height, width = result_data.shape
    rgba: NDArray[np.uint8] = np.zeros((height, width, 4), dtype=np.uint8)

    valid: NDArray[np.bool_] = valid_mask & np.isfinite(result_data)
    if not valid.any():
        return rgba

    valid_values: NDArray[np.floating] = result_data[valid].astype(np.float64)

    lo: float = float(np.percentile(valid_values, 2.0))
    hi: float = float(np.percentile(valid_values, 98.0))
    data_range: float = hi - lo

    if data_range <= 1e-12:
        # 所有有效值几乎相同，全部显示为中间灰。
        gray = np.where(valid, 128, 0).astype(np.uint8)
    else:
        stretched: NDArray[np.float64] = (result_data.astype(np.float64) - lo) / data_range
        stretched = np.clip(stretched, 0.0, 1.0)
        gray = np.where(valid, (stretched * 255.0).astype(np.uint8), 0)

    rgba[..., 0] = gray
    rgba[..., 1] = gray
    rgba[..., 2] = gray
    rgba[..., 3] = np.where(valid, 255, 0).astype(np.uint8)

    return rgba
