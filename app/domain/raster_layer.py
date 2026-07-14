"""栅格图层领域模型。"""

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import numpy as np
from affine import Affine
from numpy.typing import NDArray
from pyproj import CRS

from app.domain.vector_layer import Bounds


@dataclass(frozen=True, slots=True)
class RasterLayer:
    """表示已经转换为屏幕显示数据的内存栅格图层。"""

    # 图层编号：应用内部生成或调用方提供的稳定唯一标识。
    layer_id: str

    # 图层名称：用于图层面板和状态栏显示。
    name: str

    # RGBA 像素：按行列保存八位四通道显示数据。
    image_data: NDArray[np.uint8]

    # 仿射变换：将像素列行坐标转换为图层坐标系中的地图坐标。
    transform: Affine

    # 坐标参考系统：为空表示源栅格没有声明坐标系。
    crs: CRS | None

    # 图层范围：使用图层坐标系表示的最小包围矩形。
    bounds: Bounds

    # 数据源路径：记录栅格来源，内存构造时可以为空。
    source_path: Path | None = None

    # 原始波段数量：用于界面描述数据，不等同于显示通道数量。
    band_count: int = 1

    def __post_init__(self) -> None:
        """校验显示数组、范围和波段元数据。"""
        if self.image_data.ndim != 3 or self.image_data.shape[2] != 4:
            raise ValueError("栅格显示数据必须是高度×宽度×4的 RGBA 数组。")
        if self.image_data.dtype != np.uint8:
            raise ValueError("栅格显示数据必须使用 uint8 类型。")
        if self.image_data.shape[0] == 0 or self.image_data.shape[1] == 0:
            raise ValueError("栅格显示数据不能为空。")
        if self.bounds[0] >= self.bounds[2] or self.bounds[1] >= self.bounds[3]:
            raise ValueError("栅格图层范围无效。")
        if self.band_count < 1:
            raise ValueError("栅格波段数量必须大于零。")

    @classmethod
    def create(
        cls,
        name: str,
        image_data: NDArray[np.uint8],
        transform: Affine,
        crs: CRS | None,
        bounds: Bounds,
        source_path: Path | None = None,
        band_count: int = 1,
        layer_id: str | None = None,
    ) -> "RasterLayer":
        """创建栅格图层，并在未提供编号时生成唯一编号。"""
        return cls(
            layer_id=layer_id or uuid4().hex,
            name=name,
            image_data=image_data,
            transform=transform,
            crs=crs,
            bounds=bounds,
            source_path=source_path,
            band_count=band_count,
        )
