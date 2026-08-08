"""栅格图层领域模型。"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from uuid import uuid4

import numpy as np
from affine import Affine
from numpy.typing import NDArray
from pyproj import CRS

from app.domain.symbology import RasterRendererType, RasterSymbology
from app.domain.vector_layer import Bounds

RasterDataLoader = Callable[
    [], tuple[NDArray[np.generic], NDArray[np.bool_]]
]


@dataclass(frozen=True, slots=True)
class RasterLayer:
    """表示带有显示预览、空间定位和按需分析数据的栅格图层。

    大栅格首读时只需要显示预览；完整分析像元和有效掩膜由读取器注册为延迟
    加载器，栅格计算器或导出器首次访问 ``raster_data`` 时才真正读入内存。
    """

    # 图层编号：应用内部生成或调用方提供的稳定唯一标识。
    layer_id: str

    # 图层名称：用于图层面板和状态栏显示。
    name: str

    # 完整分析像元缓存；None 表示尚未触发按需加载。
    _raster_data: NDArray[np.generic] | None

    # RGBA 像素：按显示预览的行列保存八位四通道数据。
    image_data: NDArray[np.uint8]

    # 完整有效像元掩膜缓存；None 表示尚未触发按需加载。
    _valid_mask: NDArray[np.bool_] | None

    # 仿射变换：将完整分析像元列行坐标转换为图层坐标系中的地图坐标。
    transform: Affine

    # 坐标参考系统：为空表示源栅格没有声明坐标系。
    crs: CRS | None

    # 图层范围：使用图层坐标系表示的最小包围矩形。
    bounds: Bounds

    # 显示预览使用的仿射变换；预览降采样时与分析变换的像元大小不同。
    display_transform: Affine | None = None

    # 无数据值：为空表示数据集没有声明统一 NoData。
    nodata: float | int | None = None

    # 数据源路径：记录栅格来源，内存构造时可以为空。
    source_path: Path | None = None

    # 符号系统：为空时根据波段数量生成默认 RGB 或灰度拉伸配置。
    symbology: RasterSymbology | None = None

    # 延迟读取完整分析数组的外部适配器回调；领域层不依赖 Rasterio。
    _analysis_loader: RasterDataLoader | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    # 延迟状态下用于校验和公开元数据的完整数组形状、波段数提示。
    _raster_shape_hint: tuple[int, int] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    _band_count_hint: int | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    # 延迟加载可能由界面线程和分析任务线程先后触发，避免重复打开大文件。
    _analysis_data_lock: RLock = field(
        default_factory=RLock,
        init=False,
        repr=False,
        compare=False,
    )

    # 波段数量：根据完整分析像元或读取器元数据派生，不等同于显示通道数量。
    band_count: int = field(init=False)

    # 完整分析数组的高度、宽度；可以在不加载像元时读取。
    raster_shape: tuple[int, int] = field(init=False)

    def __post_init__(self) -> None:
        """校验分析元数据、显示缓存、掩膜和空间范围。"""
        if self._raster_data is not None:
            if self._raster_data.ndim != 3:
                raise ValueError("栅格分析数据必须是波段×高度×宽度数组。")
            if self._raster_data.shape[0] == 0:
                raise ValueError("栅格波段数量必须大于零。")
            raster_shape: tuple[int, int] = (
                self._raster_data.shape[1],
                self._raster_data.shape[2],
            )
            band_count: int = self._raster_data.shape[0]
            if self._raster_shape_hint is not None and self._raster_shape_hint != raster_shape:
                raise ValueError("栅格分析数组形状与元数据不一致。")
            if self._band_count_hint is not None and self._band_count_hint != band_count:
                raise ValueError("栅格分析波段数与元数据不一致。")
        else:
            if self._analysis_loader is None:
                raise ValueError("栅格未提供分析数据或延迟加载器。")
            if self._raster_shape_hint is None:
                raise ValueError("延迟栅格必须提供完整分析数组形状。")
            if self._band_count_hint is None or self._band_count_hint <= 0:
                raise ValueError("延迟栅格必须提供有效波段数。")
            raster_shape = self._raster_shape_hint
            band_count = self._band_count_hint

        if self.image_data.ndim != 3 or self.image_data.shape[2] != 4:
            raise ValueError("栅格显示数据必须是高度×宽度×4的 RGBA 数组。")
        if self.image_data.dtype != np.uint8:
            raise ValueError("栅格显示数据必须使用 uint8 类型。")
        if self.image_data.shape[0] == 0 or self.image_data.shape[1] == 0:
            raise ValueError("栅格显示数据不能为空。")
        if self._raster_data is not None and self.image_data.shape[:2] != raster_shape:
            raise ValueError("已加载栅格的分析数据与显示缓存行列尺寸必须一致。")
        if self._valid_mask is not None and (
            self._valid_mask.dtype != np.bool_ or self._valid_mask.shape != raster_shape
        ):
            raise ValueError("栅格有效像元掩膜必须是与完整像元行列一致的布尔数组。")
        if self._raster_data is not None and self._valid_mask is None:
            raise ValueError("已加载分析数组的栅格必须同时提供有效像元掩膜。")
        if self.bounds[0] >= self.bounds[2] or self.bounds[1] >= self.bounds[3]:
            raise ValueError("栅格图层范围无效。")
        if self.display_transform is None:
            object.__setattr__(self, "display_transform", self.transform)
        object.__setattr__(self, "raster_shape", raster_shape)
        object.__setattr__(self, "band_count", band_count)
        if self.symbology is None:
            renderer_type = (
                RasterRendererType.RGB
                if band_count >= 3
                else RasterRendererType.STRETCH
            )
            object.__setattr__(
                self,
                "symbology",
                RasterSymbology(renderer_type=renderer_type),
            )

    @property
    def raster_data(self) -> NDArray[np.generic]:
        """返回完整分析像元；延迟栅格在首次访问时从源文件加载。"""
        self._ensure_analysis_data()
        assert self._raster_data is not None
        return self._raster_data

    @property
    def valid_mask(self) -> NDArray[np.bool_]:
        """返回完整有效像元掩膜；延迟栅格在首次访问时与像元一起加载。"""
        self._ensure_analysis_data()
        assert self._valid_mask is not None
        return self._valid_mask

    @property
    def analysis_data_loaded(self) -> bool:
        """返回完整分析像元和掩膜是否已经进入内存。"""
        return self._raster_data is not None and self._valid_mask is not None

    def _ensure_analysis_data(self) -> None:
        """按需加载并校验完整分析数组。"""
        if self.analysis_data_loaded:
            return
        with self._analysis_data_lock:
            if self.analysis_data_loaded:
                return
            if self._analysis_loader is None:
                raise RuntimeError("栅格分析数据加载器不可用。")
            raster_data, valid_mask = self._analysis_loader()
            if raster_data.ndim != 3 or raster_data.shape[0] != self.band_count:
                raise ValueError("延迟加载的栅格分析数组波段数无效。")
            if raster_data.shape[1:] != self.raster_shape:
                raise ValueError("延迟加载的栅格分析数组形状无效。")
            if valid_mask.dtype != np.bool_ or valid_mask.shape != self.raster_shape:
                raise ValueError("延迟加载的栅格有效像元掩膜形状无效。")
            object.__setattr__(self, "_raster_data", raster_data)
            object.__setattr__(self, "_valid_mask", valid_mask)

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
        symbology: RasterSymbology | None = None,
        display_transform: Affine | None = None,
    ) -> "RasterLayer":
        """创建已经加载完整分析像元的栅格图层。"""
        return cls(
            layer_id=layer_id or uuid4().hex,
            name=name,
            _raster_data=raster_data,
            image_data=image_data,
            _valid_mask=valid_mask,
            transform=transform,
            display_transform=display_transform,
            crs=crs,
            bounds=bounds,
            nodata=nodata,
            source_path=source_path,
            symbology=symbology,
        )

    @classmethod
    def create_lazy(
        cls,
        name: str,
        image_data: NDArray[np.uint8],
        transform: Affine,
        display_transform: Affine,
        crs: CRS | None,
        bounds: Bounds,
        raster_shape: tuple[int, int],
        band_count: int,
        analysis_loader: RasterDataLoader,
        nodata: float | int | None = None,
        source_path: Path | None = None,
        layer_id: str | None = None,
        symbology: RasterSymbology | None = None,
    ) -> "RasterLayer":
        """创建只含显示预览的栅格图层，并注册完整像元延迟加载器。"""
        return cls(
            layer_id=layer_id or uuid4().hex,
            name=name,
            _raster_data=None,
            image_data=image_data,
            _valid_mask=None,
            transform=transform,
            display_transform=display_transform,
            crs=crs,
            bounds=bounds,
            nodata=nodata,
            source_path=source_path,
            symbology=symbology,
            _analysis_loader=analysis_loader,
            _raster_shape_hint=raster_shape,
            _band_count_hint=band_count,
        )

    def with_identity(
        self,
        *,
        layer_id: str,
        name: str,
        source_path: Path | None,
        symbology: RasterSymbology | None,
    ) -> "RasterLayer":
        """复制图层身份而不触发延迟加载。"""
        return type(self)(
            layer_id=layer_id,
            name=name,
            _raster_data=self._raster_data,
            image_data=self.image_data,
            _valid_mask=self._valid_mask,
            transform=self.transform,
            display_transform=self.display_transform,
            crs=self.crs,
            bounds=self.bounds,
            nodata=self.nodata,
            source_path=source_path,
            symbology=symbology,
            _analysis_loader=self._analysis_loader,
            _raster_shape_hint=self.raster_shape,
            _band_count_hint=self.band_count,
        )
