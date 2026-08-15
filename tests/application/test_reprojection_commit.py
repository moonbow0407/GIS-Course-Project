"""重投影 prepare/commit 拆分与提交校验测试。"""

from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from pyproj import CRS

from app.application.display_projection_service import DisplayProjectionService
from app.application.errors import LayerReprojectionFailed
from app.application.gis_application import GisApplication
from app.domain.raster_layer import RasterLayer
from app.infrastructure.file_io.auto_reader import AutoDataReader
from app.infrastructure.file_io.auto_writer import AutoDataWriter
from app.infrastructure.file_io.rasterio_raster_reader import RasterioRasterReader
from app.infrastructure.project.json_project_store import JsonProjectStore
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


def make_application(monkeypatch: pytest.MonkeyPatch) -> GisApplication:
    """创建启用流式重投影和工程存储的应用服务。"""
    monkeypatch.setattr(RasterioRasterReader, "MAX_EAGER_ANALYSIS_BYTES", 1024)
    return GisApplication(
        AutoDataReader(),
        data_writer=AutoDataWriter(),
        project_store=JsonProjectStore(),
        display_projection_service=DisplayProjectionService(
            coordinate_transformer=PyprojCoordinateTransformer(),
            raster_projector=RasterioRasterProjector(),
        ),
        windowed_raster_projector=WindowedRasterProjector(),
    )


def _open_lazy_source(
    application: GisApplication,
    tmp_path: Path,
) -> tuple[str, Path]:
    """打开一个延迟源栅格，返回图层编号和输出路径。"""
    values = np.arange(64 * 128, dtype=np.int16).reshape(64, 128)
    values[2:4, 3:5] = -9999
    source = tmp_path / "dem.tif"
    with rasterio.open(
        source,
        "w",
        driver="GTiff",
        width=128,
        height=64,
        count=1,
        dtype=np.int16,
        crs="EPSG:3857",
        transform=_SOURCE_TRANSFORM,
        nodata=-9999,
    ) as dataset:
        dataset.write(values[np.newaxis])
    opened = application.open_data(source)
    output = tmp_path / "projected.tif"
    return opened.layer_id, output


def test_commit_adds_layer_and_records_analysis_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """提交应新增结果图层、记录分析历史并保留输出文件。"""
    application = make_application(monkeypatch)
    layer_id, output = _open_lazy_source(application, tmp_path)

    preparation = application.prepare_reprojection(
        layer_id, CRS.from_epsg(4326), output
    )
    result = application.commit_reprojection(preparation)

    assert result.layer_id == preparation.projected_layer.layer_id
    assert output.is_file()
    assert len(application.snapshot().layers) == 2
    assert application.analysis_runs[-1].algorithm_id == "reproject"
    assert application.analysis_runs[-1].parameters["output_shape"] is not None
    assert result.reprojection_metadata is not None


def test_commit_rejects_removed_source_and_cleans_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """提交前源图层被移除应报错，并删除本次创建的输出文件。"""
    application = make_application(monkeypatch)
    layer_id, output = _open_lazy_source(application, tmp_path)
    preparation = application.prepare_reprojection(
        layer_id, CRS.from_epsg(4326), output
    )
    application.remove_layer(layer_id)

    with pytest.raises(ValueError, match="已从工作区移除"):
        application.commit_reprojection(preparation)

    assert not output.exists()
    assert not Path(str(output) + ".msk").exists()


def test_commit_rejects_revision_change_and_cleans_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """提交前源图层内容变化应报错，并删除本次创建的输出文件。"""
    application = make_application(monkeypatch)
    layer_id, output = _open_lazy_source(application, tmp_path)
    preparation = application.prepare_reprojection(
        layer_id, CRS.from_epsg(4326), output
    )
    # 重建符号系统会替换图层并使修订号递增。
    layer = application.snapshot().layers[0].layer
    assert isinstance(layer, RasterLayer)
    assert layer.symbology is not None
    application.apply_raster_symbology(layer_id, layer.symbology)

    with pytest.raises(ValueError, match="源图层内容发生变化"):
        application.commit_reprojection(preparation)

    assert not output.exists()


def test_commit_rejects_missing_output_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """提交前输出文件被删除应报错，且不留下新文件。"""
    application = make_application(monkeypatch)
    layer_id, output = _open_lazy_source(application, tmp_path)
    preparation = application.prepare_reprojection(
        layer_id, CRS.from_epsg(4326), output
    )
    output.unlink()

    with pytest.raises(ValueError, match="已被删除"):
        application.commit_reprojection(preparation)

    assert not output.exists()


def test_commit_rejects_modified_output_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """提交前输出文件被外部修改应报错。"""
    application = make_application(monkeypatch)
    layer_id, output = _open_lazy_source(application, tmp_path)
    preparation = application.prepare_reprojection(
        layer_id, CRS.from_epsg(4326), output
    )
    output.write_bytes(b"tampered")

    with pytest.raises(ValueError, match="已被修改"):
        application.commit_reprojection(preparation)


def test_commit_failure_cleans_output_when_owned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """注册图层失败时应清理本次创建的输出文件。"""
    application = make_application(monkeypatch)
    layer_id, output = _open_lazy_source(application, tmp_path)
    preparation = application.prepare_reprojection(
        layer_id, CRS.from_epsg(4326), output
    )
    monkeypatch.setattr(
        application,
        "add_layer",
        lambda _layer: (_ for _ in ()).throw(RuntimeError("注册失败")),
    )

    with pytest.raises(RuntimeError, match="注册失败"):
        application.commit_reprojection(preparation)

    assert not output.exists()


def test_temp_mode_resolves_project_dir_file_and_commits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「否」模式（无输出路径）应为流式栅格在工程目录物化文件。"""
    application = make_application(monkeypatch)
    application.save_project(tmp_path / "proj.gisproj")
    layer_id, _output = _open_lazy_source(application, tmp_path)

    preparation = application.prepare_reprojection(
        layer_id, CRS.from_epsg(4326), None
    )

    assert preparation.owns_output_file is True
    assert preparation.output_path is not None
    assert preparation.output_path.parent == tmp_path.resolve()
    assert preparation.output_path.name.startswith("dem_reprojected_")
    assert preparation.output_path.suffix == ".tif"
    result = application.commit_reprojection(preparation)
    assert result.layer_id == preparation.projected_layer.layer_id
    assert preparation.output_path.is_file()
    names = [layer.name for layer in result.snapshot.layers]
    assert names[0] == "dem"
    assert names[1] == "dem_reprojected"


def test_temp_mode_requires_saved_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """工程未保存时「否」模式应给出明确错误。"""
    application = make_application(monkeypatch)
    layer_id, _output = _open_lazy_source(application, tmp_path)

    with pytest.raises(LayerReprojectionFailed, match="工程尚未保存"):
        application.prepare_reprojection(layer_id, CRS.from_epsg(4326), None)
