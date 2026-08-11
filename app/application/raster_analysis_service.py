"""栅格分析编排服务：对齐、分块执行、结果构建。

服务职责：
1. 校验输入图层和请求参数。
2. 解析目标 RasterGrid（主栅格网格策略）。
3. 按窗口读取输入栅格；CRS 不同时整体重投影，范围裁剪时换算源窗口。
4. 调用算法内核（重分类/DEM/掩膜裁剪）。
5. 通过分块写入器写出 GeoTIFF。
6. 使用现有栅格读取器重新加载结果图层（带预览和延迟分析）。

服务不依赖 Qt，但通过 RasterWindowReader/RasterBlockWriter 使用 Rasterio。
"""

import math
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import numpy as np
from affine import Affine
from numpy.typing import NDArray
from pyproj import CRS
from rasterio.enums import Resampling
from rasterio.windows import Window, from_bounds
from rasterio.windows import bounds as window_bounds

from app.application.crs_utils import crs_equivalent
from app.application.errors import (
    EmptyRasterResult,
    InvalidRasterAnalysisParameters,
    RasterAnalysisFailed,
    UnsupportedRasterAnalysisInput,
)
from app.application.raster_analysis import (
    DemAnalysisRequest,
    RasterClipRequest,
    RasterReclassifyRequest,
    apply_geometry_mask,
    compute_aspect,
    compute_hillshade,
    compute_slope,
    reclassify_array,
    resolve_z_factor,
)
from app.application.raster_calculator import (
    RasterCalculatorRequest,
    compute_raster_expression,
)
from app.application.symbology_service import (
    create_raster_classified_symbology,
    render_raster_classified,
)
from app.domain.raster_grid import RasterGrid, grid_from_layer
from app.domain.raster_layer import RasterLayer
from app.domain.spatial_layer import SpatialLayer
from app.domain.vector_layer import VectorLayer
from app.infrastructure.file_io.raster_window_io import (
    DEFAULT_BLOCK_SIZE,
    RasterBlockWriter,
    RasterWindowReader,
    build_geometry_mask,
    iter_windows,
)
from app.infrastructure.file_io.rasterio_raster_reader import RasterioRasterReader

# 算法内核签名：输入波段字典、有效掩膜字典和当前窗口的仿射变换。
AnalysisKernel = Callable[
    [dict[str, NDArray[np.generic]], dict[str, NDArray[np.bool_]], Affine],
    tuple[NDArray[np.generic], NDArray[np.bool_]],
]

# 回调类型：进度回调 (已处理窗口数, 总窗口数) -> 是否继续。
ProgressCallback = Callable[[int, int], bool]
"""进度回调；返回 False 表示用户请求取消。"""


class RasterLayerReader(Protocol):
    """分析结果加载器的最小接口。"""

    def read(self, path: Path) -> SpatialLayer:
        """从 GeoTIFF 路径加载空间图层。"""

# DEM 邻域算法需要的 halo 像元数（3×3 中心差分）。
_DEM_HALO: int = 1


class RasterAnalysisService:
    """栅格分析编排服务。

    所有分析方法均将结果写出为 GeoTIFF，再通过现有栅格读取器
    重新加载为 RasterLayer，确保结果图层具备预览和延迟分析能力。
    """

    def __init__(
        self,
        data_reader: RasterLayerReader | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        """初始化服务，可选注入进度回调。

        参数:
            data_reader: 用于重新加载结果图层的读取器；为空时临时创建。
            progress_callback: 进度回调；为空时不报告进度。
        """
        self._reader: RasterLayerReader = data_reader or RasterioRasterReader()
        self._progress: ProgressCallback | None = progress_callback

    # ── 栅格计算器 ─────────────────────────────────────────

    def execute_calculator(
        self,
        request: RasterCalculatorRequest,
        layers: dict[str, SpatialLayer],
    ) -> RasterLayer:
        """分块执行栅格计算器表达式并返回结果图层。"""
        inputs: list[tuple[str, RasterLayer, int]] = []
        for mapping in request.band_mappings:
            layer = self._require_raster(layers, mapping.layer_id)
            if mapping.band_index > layer.band_count:
                raise InvalidRasterAnalysisParameters(
                    f"图层「{layer.name}」没有波段 {mapping.band_index}。"
                )
            inputs.append((mapping.alias, layer, mapping.band_index))

        reference_layer: RasterLayer = inputs[0][1]
        if request.reference_layer_id is not None:
            reference_candidate = self._require_raster(layers, request.reference_layer_id)
            reference_layer = reference_candidate
        target_grid = grid_from_layer(reference_layer)
        for _alias, layer, _band_index in inputs:
            if not crs_equivalent(layer.crs, target_grid.crs):
                raise InvalidRasterAnalysisParameters(
                    "输入栅格 CRS 不一致；请先手动重投影到统一 CRS。"
                )
            if request.reference_layer_id is None and not grid_from_layer(layer).matches(
                target_grid
            ):
                raise InvalidRasterAnalysisParameters(
                    "输入栅格网格不一致；请指定显式参考栅格后再临时对齐。"
                )

        def kernel(
            arrays: dict[str, NDArray[np.generic]],
            masks: dict[str, NDArray[np.bool_]],
            _transform: Affine,
        ) -> tuple[NDArray[np.generic], NDArray[np.bool_]]:
            result = compute_raster_expression(arrays, request.expression)
            valid = np.logical_and.reduce(tuple(masks.values()))
            valid &= np.isfinite(result)
            if request.nodata is not None:
                valid &= ~np.isclose(result, request.nodata)
            return result.astype(np.float32, copy=False), valid

        return self._run_analysis(
            inputs=tuple(inputs),
            target_grid=target_grid,
            # 计算器不假设输入是连续变量，默认使用不会创造新值的最近邻。
            resampling=Resampling.nearest,
            kernel=kernel,
            output_layer_name=request.output_layer_name,
            output_path=request.output_path,
            output_dtype="float32",
            output_nodata=request.nodata,
            halo=0,
        )

    def sample_band_values(
        self,
        layer: RasterLayer,
        band_index: int,
        max_dimension: int = 512,
    ) -> tuple[NDArray[np.generic], NDArray[np.bool_], bool]:
        """读取重分类规则生成所需的有限大小波段样本。

        已在内存中的小栅格直接使用完整数据；延迟加载或超大栅格按最长边
        降采样到 ``max_dimension``，避免点击“生成规则”再次把主界面拖入长时间读盘。

        返回:
            (波段数组, 有效掩膜, 是否为降采样结果)。
        """
        if band_index < 1 or band_index > layer.band_count:
            raise InvalidRasterAnalysisParameters(
                f"图层「{layer.name}」没有波段 {band_index}。"
            )
        if max_dimension < 1:
            raise InvalidRasterAnalysisParameters("规则生成采样尺寸必须大于 0。")
        if layer.analysis_data_loaded:
            return layer.raster_data[band_index - 1], layer.valid_mask, False
        if layer.source_path is None:
            raise UnsupportedRasterAnalysisInput(
                f"栅格图层「{layer.name}」没有源文件，无法生成自动规则。"
            )

        with RasterWindowReader(layer.source_path) as reader:
            width = reader.width
            height = reader.height
            scale = max(width / max_dimension, height / max_dimension, 1.0)
            out_width = max(1, math.ceil(width / scale))
            out_height = max(1, math.ceil(height / scale))
            data, valid = reader.read_band_window(
                band_index,
                Window(0, 0, width, height),
                resampling=Resampling.nearest,
                out_shape=(out_height, out_width),
            )
            return data, valid, scale > 1.0

    # ── 重分类 ───────────────────────────────────────────────

    def execute_reclassify(
        self,
        request: RasterReclassifyRequest,
        layers: dict[str, SpatialLayer],
    ) -> RasterLayer:
        """执行栅格重分类并返回结果图层。"""
        layer = self._require_raster(layers, request.input_layer_id)
        if request.band_index > layer.band_count:
            raise InvalidRasterAnalysisParameters(
                f"图层「{layer.name}」没有波段 {request.band_index}"
                f"（共 {layer.band_count} 个波段）。"
            )
        target_grid = grid_from_layer(layer)

        def kernel(
            arrays: dict[str, NDArray[np.generic]],
            masks: dict[str, NDArray[np.bool_]],
            _transform: Affine,
        ) -> tuple[NDArray[np.generic], NDArray[np.bool_]]:
            return reclassify_array(
                arrays["input"],
                masks["input"],
                request.rules,
                request.unmatched_policy,
                request.unmatched_constant,
                request.output_dtype,
                request.output_nodata,
            )

        return self._run_analysis(
            inputs=(("input", layer, request.band_index),),
            target_grid=target_grid,
            # 重分类默认最近邻（分类数据不应插值）。
            resampling=Resampling.nearest,
            kernel=kernel,
            output_layer_name=request.output_layer_name,
            output_path=request.output_path,
            output_dtype=request.output_dtype,
            output_nodata=request.output_nodata,
            halo=0,
            classified_values=tuple(
                sorted(
                    {
                        float(rule.output_value)
                        for rule in request.rules
                    }
                    | (
                        {float(request.unmatched_constant)}
                        if request.unmatched_policy == "constant"
                        and request.unmatched_constant is not None
                        else set()
                    )
                )
            ),
            classified_labels=self._reclass_labels(request),
        )

    # ── DEM 地形分析 ─────────────────────────────────────────

    def execute_dem_analysis(
        self,
        request: DemAnalysisRequest,
        layers: dict[str, SpatialLayer],
    ) -> RasterLayer:
        """执行 DEM 坡度/坡向/山体阴影计算并返回结果图层。"""
        layer = self._require_raster(layers, request.input_layer_id)
        if request.band_index > layer.band_count:
            raise InvalidRasterAnalysisParameters(
                f"图层「{layer.name}」没有波段 {request.band_index}。"
            )
        target_grid = grid_from_layer(layer)
        if not _is_projected_crs(target_grid.crs):
            raise InvalidRasterAnalysisParameters(
                "DEM 地形分析要求投影坐标系（米制），"
                "请先将 DEM 重投影到米制 CRS。"
            )
        z_factor = resolve_z_factor(request.elevation_unit, request.z_factor)
        pixel_width = target_grid.pixel_width
        pixel_height = target_grid.pixel_height

        def kernel(
            arrays: dict[str, NDArray[np.generic]],
            masks: dict[str, NDArray[np.bool_]],
            _transform: Affine,
        ) -> tuple[NDArray[np.generic], NDArray[np.bool_]]:
            dem = arrays["input"]
            valid = masks["input"]
            if request.mode == "slope":
                return compute_slope(dem, valid, pixel_width, pixel_height, z_factor)
            if request.mode == "aspect":
                return compute_aspect(dem, valid, pixel_width, pixel_height, z_factor)
            return compute_hillshade(
                dem, valid, pixel_width, pixel_height, z_factor,
                request.azimuth, request.altitude,
            )

        output_dtype = "uint8" if request.mode == "hillshade" else "float32"
        # 山体阴影输出 uint8，无法表示常规 NoData；无效区域由掩膜表达。
        output_nodata: float | None = (
            None if request.mode == "hillshade" else request.output_nodata
        )
        return self._run_analysis(
            inputs=(("input", layer, request.band_index),),
            target_grid=target_grid,
            resampling=Resampling.bilinear,
            kernel=kernel,
            output_layer_name=request.output_layer_name,
            output_path=request.output_path,
            output_dtype=output_dtype,
            output_nodata=output_nodata,
            halo=_DEM_HALO,
        )

    # ── 矢量掩膜裁剪 ─────────────────────────────────────────

    def execute_clip(
        self,
        request: RasterClipRequest,
        layers: dict[str, SpatialLayer],
    ) -> RasterLayer:
        """执行矢量掩膜裁剪栅格并返回结果图层。"""
        raster_layer = self._require_raster(layers, request.raster_layer_id)
        mask_layer = layers.get(request.mask_layer_id)
        if mask_layer is None:
            raise UnsupportedRasterAnalysisInput("掩膜图层不存在。")
        if not isinstance(mask_layer, VectorLayer):
            raise UnsupportedRasterAnalysisInput("掩膜图层必须是矢量面图层。")
        if (
            mask_layer.geometry_family is not None
            and mask_layer.geometry_family.value != "polygon"
        ):
            raise UnsupportedRasterAnalysisInput("掩膜裁剪只支持面图层。")
        if mask_layer.crs is None:
            raise UnsupportedRasterAnalysisInput(
                f"掩膜图层「{mask_layer.name}」没有坐标参考系统。"
            )
        if raster_layer.crs is None or not crs_equivalent(mask_layer.crs, raster_layer.crs):
            raise UnsupportedRasterAnalysisInput(
                "栅格与掩膜 CRS 不一致；请先手动重投影后再裁剪。"
            )

        from shapely.geometry.base import BaseGeometry

        shapes: list[BaseGeometry] = [
            feature.geometry
            for feature in mask_layer.features
            if not feature.geometry.is_empty
        ]
        if not shapes:
            raise EmptyRasterResult("掩膜图层不包含有效面几何。")

        target_grid = grid_from_layer(raster_layer)
        transformed_shapes = _transform_geometries(
            shapes, mask_layer.crs, target_grid.crs
        )

        from shapely.ops import unary_union

        union_geom = unary_union(transformed_shapes)
        if not _bounds_intersect(union_geom.bounds, target_grid.bounds):
            raise EmptyRasterResult("掩膜范围与栅格范围无交集，无法裁剪。")

        output_grid = (
            _crop_grid(target_grid, union_geom.bounds) if request.crop else target_grid
        )

        band_roles = tuple(
            (f"input_{band_index}", band_index)
            for band_index in range(1, raster_layer.band_count + 1)
        )

        def kernel(
            arrays: dict[str, NDArray[np.generic]],
            masks: dict[str, NDArray[np.bool_]],
            transform: Affine,
        ) -> tuple[NDArray[np.generic], NDArray[np.bool_]]:
            first_data = arrays[band_roles[0][0]]
            geom_mask = build_geometry_mask(
                transformed_shapes,
                transform,
                first_data.shape[0],
                first_data.shape[1],
                request.all_touched,
                invert=False,
            )
            output_bands: list[NDArray[np.generic]] = []
            valid_bands: list[NDArray[np.bool_]] = []
            for role, _band_index in band_roles:
                clipped, valid = apply_geometry_mask(
                    arrays[role], masks[role], geom_mask, request.invert
                )
                output_bands.append(clipped)
                valid_bands.append(valid)
            return np.stack(output_bands, axis=0), np.logical_and.reduce(valid_bands)

        output_dtype = "float32"
        if raster_layer.analysis_data_loaded:
            output_dtype = str(raster_layer.raster_data.dtype)
        return self._run_analysis(
            inputs=tuple(
                (role, raster_layer, band_index)
                for role, band_index in band_roles
            ),
            target_grid=output_grid,
            resampling=Resampling.nearest,
            kernel=kernel,
            output_layer_name=request.output_layer_name,
            output_path=request.output_path,
            output_dtype=output_dtype,
            output_nodata=raster_layer.nodata,
            halo=0,
            output_band_count=raster_layer.band_count,
        )

    # ── 统一执行引擎 ─────────────────────────────────────────

    def _run_analysis(
        self,
        inputs: tuple[tuple[str, RasterLayer, int], ...],
        target_grid: RasterGrid,
        resampling: Resampling,
        kernel: AnalysisKernel,
        output_layer_name: str,
        output_path: Path,
        output_dtype: str,
        output_nodata: float | None,
        halo: int,
        output_band_count: int = 1,
        classified_values: tuple[float, ...] | None = None,
        classified_labels: Mapping[float, str] | None = None,
    ) -> RasterLayer:
        """按目标网格分块执行分析并写出结果。"""
        for _role, layer, band_index in inputs:
            if layer.source_path is None:
                raise UnsupportedRasterAnalysisInput(
                    f"栅格图层「{layer.name}」没有源文件，无法按窗口读取。"
                )
            if band_index > layer.band_count:
                raise InvalidRasterAnalysisParameters(
                    f"图层「{layer.name}」没有波段 {band_index}。"
                )

        resolved_output_path = output_path.expanduser().resolve()
        self._validate_output_path(resolved_output_path)
        temporary_path = resolved_output_path.with_name(
            f".{resolved_output_path.stem}.{uuid4().hex}{resolved_output_path.suffix}"
        )
        windows = iter_windows(
            target_grid.width, target_grid.height, DEFAULT_BLOCK_SIZE, halo
        )
        total = len(windows)
        self._report_progress(0, total)

        try:
            writer = RasterBlockWriter(
                temporary_path,
                target_grid.width,
                target_grid.height,
                band_count=output_band_count,
                dtype=output_dtype,
                crs=target_grid.crs,
                transform=target_grid.transform,
                nodata=output_nodata,
            )
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        readers: dict[str, RasterWindowReader] = {}
        try:
            for role, layer, _band in inputs:
                assert layer.source_path is not None
                readers[role] = RasterWindowReader(layer.source_path)
            prepared: dict[str, _InputSource] = {
                role: _InputSource(
                    readers[role], band, target_grid, resampling
                )
                for role, _layer, band in inputs
            }

            for index, (read_win, write_win) in enumerate(windows):
                arrays: dict[str, NDArray[np.generic]] = {}
                masks: dict[str, NDArray[np.bool_]] = {}
                for role, _layer, _band in inputs:
                    data, valid = prepared[role].read_window(read_win)
                    arrays[role] = data
                    masks[role] = valid
                win_transform: Affine = target_grid.transform * Affine.translation(
                    read_win.col_off, read_win.row_off
                )
                result, result_valid = kernel(arrays, masks, win_transform)
                _write_window_center(
                    writer, result, result_valid, read_win, write_win, halo
                )
                if not self._report_progress(index + 1, total):
                    raise RasterAnalysisFailed("已取消栅格分析。")
        except RasterAnalysisFailed:
            writer._abort()
            raise
        except Exception as error:
            writer._abort()
            raise RasterAnalysisFailed(f"栅格分析执行失败：{error}") from error
        finally:
            for reader in readers.values():
                reader.close()
            writer.close()

        try:
            temporary_path.replace(resolved_output_path)
        except Exception as error:
            temporary_path.unlink(missing_ok=True)
            raise RasterAnalysisFailed(
                f"raster analysis result commit failed: {error}"
            ) from error

        try:
            return self._load_result(
                resolved_output_path,
                output_layer_name,
                classified_values=classified_values,
                classified_labels=classified_labels,
            )
        except Exception:
            # 输出是本次调用新建的；若结果层无法加载，不留下不可识别的文件。
            resolved_output_path.unlink(missing_ok=True)
            raise

    # ── 辅助 ─────────────────────────────────────────────────

    @staticmethod
    def _require_raster(
        layers: dict[str, SpatialLayer], layer_id: str
    ) -> RasterLayer:
        """查找并校验输入为带坐标系的栅格图层。"""
        layer = layers.get(layer_id)
        if layer is None:
            raise UnsupportedRasterAnalysisInput(f"图层不存在：{layer_id}。")
        if not isinstance(layer, RasterLayer):
            raise UnsupportedRasterAnalysisInput(
                f"图层「{layer.name}」不是栅格图层。"
            )
        if layer.crs is None:
            raise UnsupportedRasterAnalysisInput(
                f"栅格图层「{layer.name}」没有坐标参考系统。"
            )
        return layer

    @staticmethod
    def _validate_output_path(output_path: Path) -> None:
        """校验输出路径合法且目录存在。"""
        resolved = output_path.expanduser().resolve()
        suffix = resolved.suffix.lower()
        if suffix not in (".tif", ".tiff"):
            raise InvalidRasterAnalysisParameters(
                "栅格分析输出必须是 GeoTIFF (.tif/.tiff) 文件。"
            )
        if not resolved.parent.is_dir():
            raise InvalidRasterAnalysisParameters(
                f"输出目录不存在：{resolved.parent}"
            )
        if resolved.exists():
            raise InvalidRasterAnalysisParameters(
                f"输出文件已存在：{resolved.name}，请使用新的结果文件。"
            )

    def _load_result(
        self,
        path: Path,
        layer_name: str,
        classified_values: tuple[float, ...] | None = None,
        classified_labels: Mapping[float, str] | None = None,
    ) -> RasterLayer:
        """重新加载结果，并按需要为分类栅格生成离散显示预览。"""
        result_layer = self._reader.read(path)
        if not isinstance(result_layer, RasterLayer):
            raise RasterAnalysisFailed("分析结果未读取为栅格图层")
        if classified_values is not None:
            symbology = create_raster_classified_symbology(
                classified_values,
                labels=classified_labels,
            )
            preview_shape: tuple[int, int] = (
                int(result_layer.image_data.shape[0]),
                int(result_layer.image_data.shape[1]),
            )
            with RasterWindowReader(path) as reader:
                preview_values, preview_valid = reader.read_band_window(
                    1,
                    Window(0, 0, reader.width, reader.height),
                    resampling=Resampling.nearest,
                    out_shape=preview_shape,
                )
            result_layer = replace(
                result_layer,
                image_data=render_raster_classified(
                    preview_values,
                    preview_valid,
                    symbology,
                ),
                symbology=symbology,
            )
        return result_layer.with_identity(
            layer_id=result_layer.layer_id,
            name=layer_name or result_layer.name,
            source_path=result_layer.source_path,
            symbology=result_layer.symbology,
        )

    @staticmethod
    def _reclass_labels(request: RasterReclassifyRequest) -> dict[float, str]:
        """为重分类输出值保留其输入区间，供图层图例直接说明。"""
        labels: dict[float, list[str]] = {}
        for rule in request.rules:
            lower = "-∞" if rule.lower is None else f"{rule.lower:g}"
            upper = "+∞" if rule.upper is None else f"{rule.upper:g}"
            left = "[" if rule.include_lower else "("
            right = "]" if rule.include_upper else ")"
            labels.setdefault(float(rule.output_value), []).append(
                f"{left}{lower}, {upper}{right}"
            )
        if request.unmatched_policy == "constant" and request.unmatched_constant is not None:
            labels.setdefault(float(request.unmatched_constant), []).append("未匹配值")
        return {
            value: " / ".join(dict.fromkeys(parts))
            for value, parts in labels.items()
        }

    def _report_progress(self, done: int, total: int) -> bool:
        """报告进度；返回是否继续。"""
        if self._progress is None:
            return True
        return self._progress(done, total)


class _InputSource:
    """单个输入栅格的窗口读取策略。

    两种读取模式：
    - 源网格与目标网格一致：按目标窗口直接读取；
    - CRS 相同但范围不同（参考栅格临时对齐）：按窗口换算源坐标读取；
    """

    def __init__(
        self,
        reader: RasterWindowReader,
        band_index: int,
        target_grid: RasterGrid,
        resampling: Resampling,
    ) -> None:
        """根据源网格与目标网格关系选择读取模式。"""
        self._reader: RasterWindowReader = reader
        self._band_index: int = band_index
        self._target_grid: RasterGrid = target_grid

        source_crs = reader.crs
        if source_crs is None:
            raise UnsupportedRasterAnalysisInput(
                "多源分析中缺少坐标参考系统的输入无法对齐到目标网格。"
            )
        source_grid = RasterGrid(
            crs=source_crs,
            transform=reader.transform,
            width=reader.width,
            height=reader.height,
        )
        if source_grid.matches(target_grid):
            self._mode = "window"
        elif not crs_equivalent(source_crs, target_grid.crs):
            raise UnsupportedRasterAnalysisInput(
                "多源栅格 CRS 不一致；请先手动重投影到统一 CRS。"
            )
        else:
            self._mode = "translate"
            if source_crs is None:
                raise UnsupportedRasterAnalysisInput(
                    "多源分析中缺少坐标参考系统的输入无法对齐到目标网格。"
                )
        self._resampling: Resampling = resampling

    def read_window(
        self, read_win: Window
    ) -> tuple[NDArray[np.generic], NDArray[np.bool_]]:
        """读取目标窗口对应的输入像元和有效掩膜。"""
        if self._mode == "window":
            return self._reader.read_band_window(self._band_index, read_win)
        # translate：同 CRS 不同范围，将目标窗口换算为源窗口读取。
        win_bounds = window_bounds(read_win, self._target_grid.transform)
        source_win = (
            from_bounds(*win_bounds, transform=self._reader.transform)
            .round_offsets()
            .round_lengths()
        )
        return self._reader.read_band_window(
            self._band_index,
            source_win,
            resampling=self._resampling,
            out_shape=(int(read_win.height), int(read_win.width)),
            boundless=True,
        )


def _write_window_center(
    writer: RasterBlockWriter,
    result: NDArray[np.generic],
    result_valid: NDArray[np.bool_],
    read_win: Window,
    write_win: Window,
    halo: int,
) -> None:
    """将带 halo 的计算结果中心区域写入输出窗口。"""
    if halo <= 0:
        writer.write_window(result, result_valid, write_win)
        return
    col_offset = int(write_win.col_off - read_win.col_off)
    row_offset = int(write_win.row_off - read_win.row_off)
    row_slice = slice(row_offset, row_offset + write_win.height)
    col_slice = slice(col_offset, col_offset + write_win.width)
    if result.ndim == 3:
        center = result[:, row_slice, col_slice]
    else:
        center = result[row_slice, col_slice]
    if result_valid.ndim == 3:
        center_valid = result_valid[:, row_slice, col_slice]
    else:
        center_valid = result_valid[row_slice, col_slice]
    writer.write_window(center, center_valid, write_win)


def _is_projected_crs(crs: object) -> bool:
    """判断 CRS 是否为投影坐标系。"""
    from pyproj import CRS

    if not isinstance(crs, CRS):
        return False
    try:
        return crs.is_projected
    except Exception:
        return False


def _transform_geometries(
    shapes: list[object], source_crs: CRS, target_crs: CRS
) -> list[object]:
    """将几何列表从源 CRS 临时转换到目标 CRS。"""
    from pyproj import Transformer
    from shapely.geometry.base import BaseGeometry
    from shapely.ops import transform as shp_transform

    if crs_equivalent(source_crs, target_crs):
        return list(shapes)
    transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)
    result: list[BaseGeometry] = []
    for geom in shapes:
        if isinstance(geom, BaseGeometry):
            result.append(shp_transform(transformer.transform, geom))
    return result


def _bounds_intersect(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> bool:
    """判断两个空间范围是否相交。"""
    return not (
        a[0] > b[2] or a[2] < b[0] or a[1] > b[3] or a[3] < b[1]
    )


def _crop_grid(
    source: RasterGrid, mask_bounds: tuple[float, float, float, float]
) -> RasterGrid:
    """根据掩膜范围裁剪目标网格，保持像元对齐。"""
    grid_bounds = source.bounds
    inter_min_x = max(grid_bounds[0], mask_bounds[0])
    inter_min_y = max(grid_bounds[1], mask_bounds[1])
    inter_max_x = min(grid_bounds[2], mask_bounds[2])
    inter_max_y = min(grid_bounds[3], mask_bounds[3])
    if inter_min_x >= inter_max_x or inter_min_y >= inter_max_y:
        raise EmptyRasterResult("掩膜范围与栅格范围无交集，无法裁剪。")

    inv = ~source.transform
    col_start, row_start = inv * (inter_min_x, inter_max_y)
    col_end, row_end = inv * (inter_max_x, inter_min_y)
    col_off = int(max(0, math.floor(min(col_start, col_end))))
    row_off = int(max(0, math.floor(min(row_start, row_end))))
    width = int(max(1, math.ceil(max(col_start, col_end)))) - col_off
    height = int(max(1, math.ceil(max(row_start, row_end)))) - row_off
    width = min(width, source.width - col_off)
    height = min(height, source.height - row_off)

    new_transform = source.transform * Affine.translation(col_off, row_off)
    return RasterGrid(
        crs=source.crs,
        transform=new_transform,
        width=width,
        height=height,
    )
