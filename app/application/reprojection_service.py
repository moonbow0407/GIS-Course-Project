"""独立的矢量和栅格重投影图层工具。"""

import re
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import numpy as np
from pyproj import CRS
from rasterio.transform import array_bounds

from app.application.display_projection_service import DisplayProjectionService
from app.application.errors import LayerReprojectionFailed, WorkspaceOperationCancelled
from app.application.ports import DataReader, DataWriter
from app.application.ports.raster_projector import RasterProjectionResult
from app.application.ports.windowed_raster_projector import WindowedRasterProjector
from app.application.results import ReprojectionMetadata
from app.application.symbology_service import apply_raster_symbology
from app.domain.raster_layer import RasterLayer
from app.domain.spatial_layer import SpatialLayer
from app.domain.vector_layer import VectorLayer

# 内存快路径上限：完整分析数组超过该字节数时改用分块流式路径。
MAX_EAGER_REPROJECTION_BYTES: int = 64 * 1024 * 1024

# 进度回调：接收（已完成窗口数, 总窗口数），返回 False 表示请求取消。
ProgressCallback = Callable[[int, int], bool]

# 工程目录临时重投影文件：{源名}_reprojected_{32位hex}.tif
_AUTO_REPROJECTED_STEM = re.compile(
    r"^(?P<base>.+)_reprojected_[0-9a-f]{32}$",
    re.IGNORECASE,
)


def resolve_reprojected_layer_name(
    source_name: str,
    output_path: Path | None,
) -> str:
    """根据输出文件确定重投影图层显示名，避免与源图层重名。

    有输出文件时默认使用文件名（不含扩展名），与打开数据的命名规则一致。
    工程自动生成的 ``*_reprojected_<uuid>`` 文件去掉 UUID，只保留
    ``源名_reprojected``。无输出路径的内存图层同样追加 ``_reprojected``。
    """
    if output_path is not None:
        stem = output_path.stem.strip()
        if stem:
            matched = _AUTO_REPROJECTED_STEM.fullmatch(stem)
            if matched is not None:
                return f"{matched.group('base')}_reprojected"
            return stem
    cleaned = source_name.strip() or "layer"
    if cleaned.endswith("_reprojected"):
        return cleaned
    return f"{cleaned}_reprojected"


def should_stream_raster(
    layer: RasterLayer,
    windowed_projector: WindowedRasterProjector | None,
) -> bool:
    """判断栅格是否应走分块流式路径。

    只要有可读取的源文件，且完整数组预计超过内存快路径阈值（未加载时
    按延迟大栅格处理，避免为了分流而触发全量加载），就应流式重投影。
    ``analysis_data_loaded`` 只作为免 I/O 的大小提示，曾被加载过的大
    栅格仍必须能回到流式路径。
    """
    if windowed_projector is None or layer.source_path is None:
        return False
    if not layer.source_path.is_file():
        return False
    if layer.analysis_data_loaded:
        return layer.raster_data.nbytes > MAX_EAGER_REPROJECTION_BYTES
    return True


class ReprojectionService:
    """创建独立的新图层，绝不替换输入领域图层。"""

    def __init__(
        self,
        display_projection_service: DisplayProjectionService,
        data_writer: DataWriter | None = None,
        data_reader: DataReader | None = None,
        windowed_projector: WindowedRasterProjector | None = None,
    ) -> None:
        """注入投影服务、可选输出写入器和分块流式投影端口。"""
        self._projection = display_projection_service
        self._writer = data_writer
        self._reader = data_reader
        self._windowed_projector = windowed_projector
        self._metadata: ReprojectionMetadata | None = None
        self._output_file_written: bool = False

    @property
    def metadata(self) -> ReprojectionMetadata | None:
        """返回最近一次重投影的操作和输出网格摘要。"""
        return self._metadata

    @property
    def output_file_written(self) -> bool:
        """返回流式路径是否已经把结果写入输出文件。"""
        return self._output_file_written

    def execute(
        self,
        layer: SpatialLayer,
        target_crs: CRS,
        output_path: Path | None = None,
        raster_resampling: str | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> SpatialLayer:
        """重投影输入图层，输出路径为空时返回临时内存图层。

        参数:
            layer: 待重投影的矢量或栅格图层。
            target_crs: 目标坐标系。
            output_path: 输出文件路径；栅格大文件必须提供，否则回退内存路径。
            raster_resampling: 栅格重采样方法；为空时默认双线性。
            progress_callback: 流式路径按窗口回报进度；返回 False 时取消。

        返回:
            重投影后的新图层（与输入图层对象相互独立）。
        """
        if layer.crs is None:
            raise LayerReprojectionFailed(f"图层“{layer.name}”没有 CRS。")
        resolved_path: Path | None = (
            output_path.expanduser().resolve() if output_path is not None else None
        )
        try:
            if isinstance(layer, VectorLayer):
                return self._execute_vector(layer, target_crs, resolved_path)
            if isinstance(layer, RasterLayer):
                if self._can_stream(layer, resolved_path):
                    assert resolved_path is not None
                    return self._execute_streaming(
                        layer, target_crs, resolved_path, raster_resampling,
                        progress_callback,
                    )
                return self._execute_in_memory(
                    layer, target_crs, resolved_path, raster_resampling
                )
        except WorkspaceOperationCancelled:
            # 取消必须原样透传，不能包装成普通失败，否则界面无法识别。
            raise
        except LayerReprojectionFailed:
            raise
        except Exception as error:
            raise LayerReprojectionFailed(
                f"图层“{layer.name}”无法转换到目标 CRS。"
            ) from error
        raise LayerReprojectionFailed(f"图层“{layer.name}”类型不支持重投影。")

    def _can_stream(
        self,
        layer: RasterLayer,
        output_path: Path | None,
    ) -> bool:
        """判断当前调用是否具备流式条件（大文件、端口齐备且提供输出路径）。"""
        return (
            output_path is not None
            and self._reader is not None
            and should_stream_raster(layer, self._windowed_projector)
        )

    def _execute_vector(
        self,
        layer: VectorLayer,
        target_crs: CRS,
        output_path: Path | None,
    ) -> VectorLayer:
        """内存中转换矢量要素并创建新图层。"""
        source_crs: CRS | None = layer.crs
        assert source_crs is not None
        features = self._projection.transform_features(
            layer.features, source_crs, target_crs
        )
        projected_vector_layer = VectorLayer.create(
            name=resolve_reprojected_layer_name(layer.name, output_path),
            features=features,
            crs=target_crs,
            source_path=output_path,
            source_layer_name=layer.source_layer_name,
            symbology=layer.symbology,
            labeling=layer.labeling,
            crs_override=False,
        )
        self._metadata = ReprojectionMetadata(
            source_crs=source_crs.to_string(),
            target_crs=target_crs.to_string(),
            operation=self._projection.describe_operation(source_crs, target_crs),
            resampling=None,
            output_shape=None,
            output_transform=None,
            output_bounds=projected_vector_layer.bounds,
            feature_count=len(projected_vector_layer.features),
        )
        return projected_vector_layer

    def _execute_in_memory(
        self,
        layer: RasterLayer,
        target_crs: CRS,
        output_path: Path | None,
        raster_resampling: str | None,
    ) -> RasterLayer:
        """已加载小栅格的内存快路径：整幅投影并全尺寸渲染。"""
        source_crs: CRS | None = layer.crs
        assert source_crs is not None
        projected: RasterProjectionResult = self._projection.project_raster_data(
            layer.raster_data,
            layer.valid_mask,
            layer.transform,
            source_crs,
            target_crs,
            nodata=layer.nodata,
            resampling=raster_resampling or "bilinear",
        )
        height, width = projected.data.shape[1:]
        bounds = array_bounds(height, width, projected.transform)
        resolved_resampling = raster_resampling or "bilinear"
        placeholder = RasterLayer.create(
            name=resolve_reprojected_layer_name(layer.name, output_path),
            raster_data=projected.data,
            image_data=np.zeros((height, width, 4), dtype=np.uint8),
            valid_mask=projected.valid_mask,
            transform=projected.transform,
            crs=target_crs,
            bounds=(
                min(bounds[0], bounds[2]),
                min(bounds[1], bounds[3]),
                max(bounds[0], bounds[2]),
                max(bounds[1], bounds[3]),
            ),
            nodata=layer.nodata,
            source_path=output_path,
            symbology=layer.symbology,
        )
        projected_raster_layer = (
            apply_raster_symbology(placeholder, layer.symbology)
            if layer.symbology
            else placeholder
        )
        self._metadata = ReprojectionMetadata(
            source_crs=source_crs.to_string(),
            target_crs=target_crs.to_string(),
            operation=self._projection.describe_operation(source_crs, target_crs),
            resampling=resolved_resampling,
            output_shape=(height, width),
            output_transform=(
                float(projected.transform.a),
                float(projected.transform.b),
                float(projected.transform.c),
                float(projected.transform.d),
                float(projected.transform.e),
                float(projected.transform.f),
            ),
            output_bounds=projected_raster_layer.bounds,
        )
        return projected_raster_layer

    def _execute_streaming(
        self,
        layer: RasterLayer,
        target_crs: CRS,
        output_path: Path,
        raster_resampling: str | None,
        progress_callback: ProgressCallback | None,
    ) -> RasterLayer:
        """大栅格流式路径：窗口写文件后按延迟栅格模式重读结果。

        源图层通过工程内修正覆盖 CRS 时（``crs_override=True``），必须把
        覆盖值传给投影端口，否则会按源文件声明的错误坐标系解释坐标。
        """
        source_crs: CRS | None = layer.crs
        source_path: Path | None = layer.source_path
        projector: WindowedRasterProjector | None = self._windowed_projector
        reader: DataReader | None = self._reader
        assert source_crs is not None
        assert source_path is not None
        assert projector is not None
        assert reader is not None
        resolved_resampling: str = raster_resampling or "bilinear"
        source_crs_override: CRS | None = source_crs if layer.crs_override else None
        grid = projector.project_to_file(
            source_path,
            target_crs,
            output_path,
            source_crs_override=source_crs_override,
            resampling=resolved_resampling,
            progress_callback=progress_callback,
        )
        self._output_file_written = True
        # 成品文件由既有读取器重读：预览、display_values、延迟加载器与
        # 普通栅格图层完全同构，后续栅格分析可经 source_path 窗口化读取。
        reloaded: SpatialLayer = reader.read(output_path)
        if not isinstance(reloaded, RasterLayer):
            raise LayerReprojectionFailed(
                f"重投影输出读取失败：{output_path.name} 不是栅格图层。"
            )
        projected_raster_layer = reloaded.with_identity(
            layer_id=uuid4().hex,
            name=resolve_reprojected_layer_name(layer.name, output_path),
            source_path=output_path,
            symbology=layer.symbology,
            crs_override=False,
        )
        if layer.symbology is not None:
            projected_raster_layer = apply_raster_symbology(
                projected_raster_layer, layer.symbology
            )
        self._metadata = ReprojectionMetadata(
            source_crs=source_crs.to_string(),
            target_crs=target_crs.to_string(),
            operation=self._projection.describe_operation(source_crs, target_crs),
            resampling=resolved_resampling,
            output_shape=(grid.height, grid.width),
            output_transform=(
                float(grid.transform.a),
                float(grid.transform.b),
                float(grid.transform.c),
                float(grid.transform.d),
                float(grid.transform.e),
                float(grid.transform.f),
            ),
            output_bounds=grid.bounds,
        )
        return projected_raster_layer

    def persist(
        self,
        layer: SpatialLayer,
        output_path: Path,
        layer_name: str | None = None,
    ) -> None:
        """将已重投影图层写入用户指定的新数据源（内存路径的栅格与矢量）。"""
        if self._writer is None:
            raise LayerReprojectionFailed("空间数据写出服务尚未配置。")
        self._writer.write(layer, output_path.expanduser().resolve(), (), layer_name)
