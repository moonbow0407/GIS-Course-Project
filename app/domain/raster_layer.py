"""栅格图层领域模型。"""

from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import numpy as np
from affine import Affine
from numpy.typing import NDArray
from pyproj import CRS

from app.domain.vector_layer import Bounds


@dataclass(frozen=True, slots=True)
class RasterLayer:
    """表示同时包含分析像元和显示缓存的内存栅格图层。"""

    # 图层编号：应用内部生成或调用方提供的稳定唯一标识。
    layer_id: str

    # 图层名称：用于图层面板和状态栏显示。
    name: str

    # 分析像元：按“波段×高度×宽度”保存真实数值，导出和空间处理使用此数组。
    raster_data: NDArray[np.generic]

    # RGBA 像素：按行列保存八位四通道显示数据。
    image_data: NDArray[np.uint8]

    # 有效像元掩膜：真值表示该行列可参与分析和导出。
    valid_mask: NDArray[np.bool_]

    # 仿射变换：将像素列行坐标转换为图层坐标系中的地图坐标。
    transform: Affine

    # 坐标参考系统：为空表示源栅格没有声明坐标系。
    crs: CRS | None

    # 图层范围：使用图层坐标系表示的最小包围矩形。
    bounds: Bounds

    # 无数据值：为空表示数据集没有声明统一 NoData。
    nodata: float | int | None = None

    # 数据源路径：记录栅格来源，内存构造时可以为空。
    source_path: Path | None = None

    # 波段数量：根据真实分析像元派生，不等同于显示通道数量。
    band_count: int = field(init=False)

    def __post_init__(self) -> None:
        """校验分析数组、显示缓存、掩膜和空间范围。"""
        if self.raster_data.ndim != 3:
            raise ValueError("栅格分析数据必须是波段×高度×宽度数组。")
        if self.raster_data.shape[0] == 0:
            raise ValueError("栅格波段数量必须大于零。")
        if self.image_data.ndim != 3 or self.image_data.shape[2] != 4:
            raise ValueError("栅格显示数据必须是高度×宽度×4的 RGBA 数组。")
        if self.image_data.dtype != np.uint8:
            raise ValueError("栅格显示数据必须使用 uint8 类型。")
        if self.image_data.shape[0] == 0 or self.image_data.shape[1] == 0:
            raise ValueError("栅格显示数据不能为空。")
        raster_shape: tuple[int, int] = (
            self.raster_data.shape[1],
            self.raster_data.shape[2],
        )
        if self.image_data.shape[:2] != raster_shape:
            raise ValueError("栅格分析数据与显示缓存的行列尺寸必须一致。")
        if self.valid_mask.dtype != np.bool_ or self.valid_mask.shape != raster_shape:
            raise ValueError("栅格有效掩膜必须是与像元行列一致的布尔数组。")
        if self.bounds[0] >= self.bounds[2] or self.bounds[1] >= self.bounds[3]:
            raise ValueError("栅格图层范围无效。")
        object.__setattr__(self, "band_count", self.raster_data.shape[0])

    @classmethod
    def create(
        cls,
        name: str,
        raster_data: NDArray[np.generic],
        image_data: NDArray[np.uint8],
        valid_mask: NDArray[np.bool_],
        transform: Affine,
        crs: CRS | None,
        bounds: Bounds,
        nodata: float | int | None = None,
        source_path: Path | None = None,
        layer_id: str | None = None,
    ) -> "RasterLayer":
        """创建栅格图层，并在未提供编号时生成唯一编号。"""
        return cls(
            layer_id=layer_id or uuid4().hex,
            name=name,
            raster_data=raster_data,
            image_data=image_data,
            valid_mask=valid_mask,
            transform=transform,
            crs=crs,
            bounds=bounds,
            nodata=nodata,
            source_path=source_path,
        )
