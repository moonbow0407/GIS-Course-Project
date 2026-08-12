"""重投影服务分流与流式路径测试。"""

from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from pyproj import CRS

from app.application.display_projection_service import DisplayProjectionService
from app.application.errors import WorkspaceOperationCancelled
from app.application.gis_application import GisApplication
from app.application.reprojection_service import ReprojectionService
from app.domain.raster_layer import RasterLayer
from app.infrastructure.file_io.auto_reader import AutoDataReader
from app.infrastructure.file_io.auto_writer import AutoDataWriter
from app.infrastructure.file_io.rasterio_raster_reader import RasterioRasterReader
from app.infrastructure.projection.pyproj_coordinate_transformer import (
    PyprojCoordinateTransformer,
)
from app.infrastructure.projection.rasterio_raster_projector import (
    RasterioRasterProjector,
)
from app.infrastructure.projection.windowed_raster_projector import (
    WindowedRasterProjector,
)

_SOURCE_TRANSFORM = Affine.translation(0, 0) * Affine.scale(1000, -1000)
# 源栅格行列：64×128 int16 的分析数组为 16 KiB，超过读取器延迟阈值
# （测试中 monkeypatch 为 1024 字节），保证 open_data 产物是延迟栅格。
_HEIGHT = 64
_WIDTH = 128


def _write_source(
    path: Path,
    values: np.ndarray,
    crs: str = "EPSG:3857",
    nodata: int = -9999,
) -> None:
    """写入测试源 GeoTIFF。"""
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=values.shape[1],
        height=values.shape[0],
        count=1,
        dtype=values.dtype,
        crs=crs,
        transform=_SOURCE_TRANSFORM,
        nodata=nodata,
    ) as dataset:
        dataset.write(values[np.newaxis])


def _write_dem_source(tmp_path: Path) -> Path:
    """写一个 64×128 的 int16 源栅格并返回路径。"""
    values = np.arange(_HEIGHT * _WIDTH, dtype=np.int16).reshape(_HEIGHT, _WIDTH)
    values[2:4, 3:5] = -9999
    path = tmp_path / "dem.tif"
    _write_source(path, values)
    return path


def make_application(monkeypatch: pytest.MonkeyPatch) -> GisApplication:
    """创建启用流式重投影端口的应用服务；小文件按延迟栅格处理。"""
    monkeypatch.setattr(RasterioRasterReader, "MAX_EAGER_ANALYSIS_BYTES", 1024)
    return GisApplication(
        AutoDataReader(),
        data_writer=AutoDataWriter(),
        display_projection_service=DisplayProjectionService(
            coordinate_transformer=PyprojCoordinateTransformer(),
            raster_projector=RasterioRasterProjector(),
        ),
        windowed_raster_projector=WindowedRasterProjector(),
    )


def test_lazy_raster_reprojection_streams_to_file_and_stays_lazy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """延迟大栅格重投影应流式写出文件，结果图层保持延迟加载。"""
    source_path = _write_dem_source(tmp_path)
    application = make_application(monkeypatch)
    opened = application.open_data(source_path)
    source_layer = application.snapshot().layers[0].layer
    assert isinstance(source_layer, RasterLayer)
    assert source_layer.analysis_data_loaded is False

    output = tmp_path / "projected.tif"
    preparation = application.prepare_reprojection(
        opened.layer_id, CRS.from_epsg(4326), output
    )
    result = application.commit_reprojection(preparation)

    assert preparation.owns_output_file is True
    assert preparation.output_path == output.resolve()
    assert result.layer_id != opened.layer_id
    projected = application.snapshot().layers[1].layer
    assert isinstance(projected, RasterLayer)
    assert projected.crs.equals(CRS.from_epsg(4326))
    assert projected.source_path == output.resolve()
    # 结果图层与读取器产物同构：延迟加载、预览有界。
    assert projected.analysis_data_loaded is False
    assert max(projected.image_data.shape[:2]) <= 2048
    # 源图层未被修改。
    assert application.snapshot().layers[0].layer is source_layer
    assert source_layer.analysis_data_loaded is False
    # 元数据记录输出网格。
    metadata = result.reprojection_metadata
    assert metadata is not None
    assert metadata.output_shape is not None
    assert metadata.target_crs == CRS.from_epsg(4326).to_string()
    # 延迟加载仍能从成品文件按需读取完整像元。
    assert projected.raster_data.shape[0] == 1
    assert projected.raster_data.shape[1:] == projected.raster_shape
    assert projected.analysis_data_loaded is True


def test_previously_loaded_large_raster_still_streams(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """曾被加载过的大栅格不能因 analysis_data_loaded 而退回内存路径。"""
    source_path = _write_dem_source(tmp_path)
    application = make_application(monkeypatch)
    opened = application.open_data(source_path)
    layer = application.snapshot().layers[0].layer
    assert isinstance(layer, RasterLayer)
    # 先触发全量加载，模拟访问过分析数据的场景。
    _ = layer.raster_data
    assert layer.analysis_data_loaded is True
    # 把内存快路径阈值压到 1 字节，使已加载图层仍被判定为流式。
    monkeypatch.setattr(
        "app.application.reprojection_service.MAX_EAGER_REPROJECTION_BYTES", 1
    )

    output = tmp_path / "projected.tif"
    preparation = application.prepare_reprojection(
        opened.layer_id, CRS.from_epsg(4326), output
    )

    projected = preparation.projected_layer
    assert isinstance(projected, RasterLayer)
    assert preparation.owns_output_file is True
    # 流式路径产出延迟图层，而不是全尺寸内存图层。
    assert projected.analysis_data_loaded is False
    assert projected.source_path == output.resolve()


def test_small_loaded_raster_keeps_memory_path_without_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已加载小栅格且无输出路径时保持内存快路径，不产生物化文件。"""
    source_path = _write_dem_source(tmp_path)
    application = make_application(monkeypatch)
    opened = application.open_data(source_path)
    layer = application.snapshot().layers[0].layer
    assert isinstance(layer, RasterLayer)
    _ = layer.raster_data  # 模拟已经加载过的小栅格。
    # 保持默认 64 MiB 内存阈值：已加载小数组应走内存路径。
    monkeypatch.setattr(
        "app.application.reprojection_service.MAX_EAGER_REPROJECTION_BYTES",
        64 * 1024 * 1024,
    )

    preparation = application.prepare_reprojection(
        opened.layer_id, CRS.from_epsg(4326), None
    )

    assert preparation.owns_output_file is False
    assert preparation.output_path is None
    assert preparation.projected_layer.source_path is None


def test_cancel_is_passthrough_and_cleans_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取消应原样抛出 WorkspaceOperationCancelled，且不留下输出文件。"""
    source_path = _write_dem_source(tmp_path)
    application = make_application(monkeypatch)
    opened = application.open_data(source_path)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    output = output_dir / "projected.tif"

    with pytest.raises(WorkspaceOperationCancelled, match="已取消重投影"):
        application.prepare_reprojection(
            opened.layer_id,
            CRS.from_epsg(4326),
            output,
            progress_callback=lambda _done, _total: False,
        )

    assert not output.exists()
    assert not list(output_dir.iterdir())


def test_crs_override_is_honored_in_streaming(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """工程内覆盖的源 CRS 应传给流式投影器，而不是用文件错误声明。"""
    values = np.arange(_HEIGHT * _WIDTH, dtype=np.int16).reshape(_HEIGHT, _WIDTH)
    path = tmp_path / "misdeclared.tif"
    # 文件错误声明为 4326，但坐标实际是 3857 米制网格。
    _write_source(path, values, crs="EPSG:4326")
    application = make_application(monkeypatch)
    # 读取后用工程覆盖修正源 CRS。
    opened = application.open_data(path)
    application.define_layer_crs(opened.layer_id, CRS.from_epsg(3857))
    layer = application.snapshot().layers[0].layer
    assert isinstance(layer, RasterLayer)
    assert layer.crs_override is True
    assert layer.crs.equals(CRS.from_epsg(3857))

    output = tmp_path / "out" / "projected.tif"
    output.parent.mkdir()
    preparation = application.prepare_reprojection(
        opened.layer_id, CRS.from_epsg(4326), output
    )
    application.commit_reprojection(preparation)

    with rasterio.open(output) as dataset:
        bounds = dataset.bounds
    # 3857 米制 0..128000 范围应投影到约 0..1.15 度；若按文件声明的
    # 4326 解释则会得到 0..128000 度的荒谬范围。
    assert abs(bounds.left) < 0.5
    assert abs(bounds.right - 1.15) < 0.05
    assert abs(bounds.top) < 0.5
    assert abs(bounds.bottom + 0.55) < 0.05


def test_direct_service_cancel_wraps_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """直接调用服务的取消同样透传，不包装成 LayerReprojectionFailed。"""
    source_path = _write_dem_source(tmp_path)
    monkeypatch.setattr(RasterioRasterReader, "MAX_EAGER_ANALYSIS_BYTES", 1024)
    layer = RasterioRasterReader().read(source_path)
    service = ReprojectionService(
        DisplayProjectionService(
            coordinate_transformer=PyprojCoordinateTransformer(),
            raster_projector=RasterioRasterProjector(),
        ),
        data_reader=AutoDataReader(),
        windowed_projector=WindowedRasterProjector(),
    )
    output = tmp_path / "out" / "projected.tif"
    output.parent.mkdir()

    with pytest.raises(WorkspaceOperationCancelled, match="已取消重投影"):
        service.execute(
            layer,
            CRS.from_epsg(4326),
            output_path=output,
            progress_callback=lambda _done, _total: False,
        )

    assert not output.exists()
    assert not list(output.parent.iterdir())
    assert service.output_file_written is False
