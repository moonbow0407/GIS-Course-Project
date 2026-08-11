"""基于 Rasterio 的栅格重投影适配器。

使用 Rasterio 的默认变换推导与重投影能力把像元转换到目标坐标系，
同时用最近邻重采样同步转换有效掩膜，保证无效像元在结果中仍然无效。
"""

import numpy as np
from affine import Affine
from numpy.typing import NDArray
from pyproj import CRS
from rasterio.transform import array_bounds
from rasterio.warp import Resampling, calculate_default_transform, reproject

from app.application.crs_utils import crs_equivalent
from app.application.ports.raster_projector import RasterProjectionResult


class RasterioRasterProjector:
    """使用 Rasterio 将栅格像元与有效掩膜重投影到目标坐标系。"""

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
        """重投影栅格像元，并同步重采样有效掩膜。"""
        if data.ndim != 3:
            raise ValueError("栅格重投影输入必须是波段×高度×宽度数组。")
        if data.shape[0] == 0:
            raise ValueError("栅格重投影输入必须至少包含一个波段。")
        if valid_mask.shape != data.shape[1:]:
            raise ValueError("栅格有效掩膜必须与像元行列尺寸一致。")
        try:
            method: Resampling = Resampling[resampling]
        except KeyError as error:
            raise ValueError(f"不支持的重采样方法：{resampling}") from error
        if (target_transform is None) != (target_shape is None):
            raise ValueError("显式参考栅格必须同时提供仿射变换和行列尺寸。")
        if (
            crs_equivalent(source_crs, target_crs)
            and resolution is None
            and target_transform is None
        ):
            return RasterProjectionResult(
                data=data,
                transform=transform,
                valid_mask=valid_mask,
            )

        height: int = data.shape[1]
        width: int = data.shape[2]
        if target_transform is not None and target_shape is not None:
            dst_transform = target_transform
            dst_height, dst_width = target_shape
        else:
            dst_transform, dst_width, dst_height = calculate_default_transform(
                source_crs,
                target_crs,
                width,
                height,
                *array_bounds(height, width, transform),
                resolution=resolution,
            )
        fill_value: float | int = 0 if nodata is None else nodata
        projected_data: NDArray = np.full(
            (data.shape[0], dst_height, dst_width),
            fill_value=fill_value,
            dtype=data.dtype,
        )
        band_index: int
        for band_index in range(data.shape[0]):
            reproject(
                data[band_index],
                projected_data[band_index],
                src_transform=transform,
                src_crs=source_crs,
                src_nodata=nodata,
                dst_transform=dst_transform,
                dst_crs=target_crs,
                dst_nodata=fill_value,
                resampling=method,
            )
        # 掩膜使用最近邻重采样：无效像元在目标网格中保持无效，
        # 插值产生的中间值可能把边缘有效像元误判为无效。
        projected_mask: NDArray = np.zeros((dst_height, dst_width), dtype=np.uint8)
        reproject(
            valid_mask.astype(np.uint8),
            projected_mask,
            src_transform=transform,
            src_crs=source_crs,
            dst_transform=dst_transform,
            dst_crs=target_crs,
            resampling=Resampling.nearest,
        )
        return RasterProjectionResult(
            data=projected_data,
            transform=dst_transform,
            valid_mask=projected_mask > 0,
        )
