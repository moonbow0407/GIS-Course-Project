"""栅格重投影端口。

栅格显示投影和重投影工具依赖该端口把像元、有效掩膜和仿射变换
转换到目标 CRS；具体实现由基础设施层的 Rasterio 适配器提供，
应用层不直接依赖重投影库。
"""

from dataclasses import dataclass
from typing import Protocol

from affine import Affine
from numpy.typing import NDArray
from pyproj import CRS


@dataclass(frozen=True, slots=True)
class RasterProjectionResult:
    """表示一次栅格重投影的不可变结果载荷。"""

    # 重投影后的像元：保持波段×高度×宽度的数组布局。
    data: NDArray

    # 重投影后的仿射变换：把目标像元列行坐标转换为目标 CRS 坐标。
    transform: Affine

    # 重投影后的有效像元掩膜：与 data 的高度、宽度一致。
    valid_mask: NDArray


class RasterProjector(Protocol):
    """定义把栅格像元与有效掩膜重投影到目标坐标系的能力。"""

    def project(
        self,
        data: NDArray,
        valid_mask: NDArray,
        transform: Affine,
        source_crs: CRS,
        target_crs: CRS,
        nodata: float | int | None = None,
        resampling: str = "bilinear",
        resolution: float | None = None,
        target_transform: Affine | None = None,
        target_shape: tuple[int, int] | None = None,
    ) -> RasterProjectionResult:
        """重投影栅格像元，并同步重采样有效掩膜。

        参数:
            data: 源栅格像元数组（波段×高度×宽度）。
            valid_mask: 与 data 行列一致的布尔有效掩膜。
            transform: 源栅格的仿射变换。
            source_crs: 源栅格所在坐标系。
            target_crs: 重投影目标坐标系。
            nodata: 源栅格的无数据值；为空时重投影填充值使用零。
            resampling: 重采样方法名称（nearest/bilinear/cubic 等）。
            resolution: 目标坐标系下的输出像元尺寸；为空时使用默认推导。
            target_transform: 显式参考栅格的目标仿射变换。
            target_shape: 显式参考栅格的目标行列数（高、宽）。

        返回:
            包含重投影像元、目标仿射变换和有效掩膜的结果载荷。

        异常:
            ValueError: 输入数组布局无效或重采样方法不支持时抛出。
        """
        ...
