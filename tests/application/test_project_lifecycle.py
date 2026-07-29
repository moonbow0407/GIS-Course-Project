"""工程保存、恢复和分析结果历史测试。"""

from pathlib import Path

import fiona
import pytest
from pyproj import CRS
from shapely.geometry import Point

from app.application.buffer_analysis import BufferRequest
from app.application.errors import InvalidBufferParameters
from app.application.gis_application import GisApplication
from app.application.project_models import MapViewState
from app.domain.feature import Feature
from app.domain.vector_layer import VectorLayer
from app.infrastructure.file_io.auto_reader import AutoDataReader
from app.infrastructure.file_io.auto_writer import AutoDataWriter
from app.infrastructure.file_io.geopandas_vector_writer import GeoPandasVectorWriter
from app.infrastructure.project.json_project_store import JsonProjectStore


def make_layer(name: str = "道路") -> VectorLayer:
    """创建包含两个点要素的 WGS84 测试图层。"""
    return VectorLayer.create(
        name=name,
        features=(
            Feature(fid=1, geometry=Point(118.0, 31.0), attributes={"名称": "甲"}),
            Feature(fid=2, geometry=Point(118.1, 31.1), attributes={"名称": "乙"}),
        ),
        crs=CRS.from_epsg(4326),
    )


def make_application() -> GisApplication:
    """创建带真实工程和本地文件适配器的应用服务。"""
    return GisApplication(
        AutoDataReader(),
        AutoDataWriter(),
        project_store=JsonProjectStore(),
    )


def test_project_round_trip_restores_workspace_and_relative_source_path(
    tmp_path: Path,
) -> None:
    """保存并重新打开工程后应恢复图层状态、稳定编号和地图视图。"""
    source_path: Path = tmp_path / "data" / "roads.geojson"
    source_path.parent.mkdir()
    GeoPandasVectorWriter().write(make_layer(), source_path)
    project_path: Path = tmp_path / "project" / "roads.gisproj"

    application: GisApplication = make_application()
    opened = application.open_data(source_path)
    layer_id: str = opened.layer_id
    application.apply_unique_value_symbology(layer_id, "名称", "soft")
    application.set_layer_visibility(layer_id, False)
    application.save_project(project_path, MapViewState(118.05, 31.05, 150.0))

    restored_application: GisApplication = make_application()
    restored = restored_application.open_project(project_path)

    restored_layer = restored.snapshot.layers[0]
    assert restored_layer.layer_id == layer_id
    assert restored_layer.visible is False
    assert restored_layer.layer.source_path == source_path.resolve()
    assert restored_layer.layer.symbology.color_scheme == "soft"
    assert len(restored_layer.layer.symbology.unique_classes) == 2
    assert restored.view_state == MapViewState(118.05, 31.05, 150.0)
    assert restored_application.is_modified is False

    manifest_text: str = project_path.read_text(encoding="utf-8")
    assert "../data/roads.geojson" in manifest_text


def test_project_restores_multiple_non_overwritten_results_and_history(
    tmp_path: Path,
) -> None:
    """同一工程的多次分析应生成不同 GeoPackage 图层并保留两条历史。"""
    source_path: Path = tmp_path / "roads.geojson"
    GeoPandasVectorWriter().write(make_layer(), source_path)
    project_path: Path = tmp_path / "roads.gisproj"

    application: GisApplication = make_application()
    source_result = application.open_data(source_path)
    application.save_project(project_path)
    source_layer = application.snapshot().layers[0].layer

    first = application.persist_vector_analysis_result(
        source_layer,
        "buffer",
        (source_result.layer_id,),
        {"distance": 500},
    )
    second = application.persist_vector_analysis_result(
        source_layer,
        "buffer",
        (source_result.layer_id,),
        {"distance": 800},
    )
    application.save_project()

    result_path: Path = project_path.parent / "project_data" / "results.gpkg"
    result_layer_names: list[str] = list(fiona.listlayers(result_path))
    assert len(result_layer_names) == 2
    assert result_layer_names[0] != result_layer_names[1]
    assert len(application.analysis_runs) == 2
    assert first.run.outputs[0].source_layer_name != second.run.outputs[0].source_layer_name

    restored_application: GisApplication = make_application()
    restored = restored_application.open_project(project_path)

    assert len(restored.snapshot.layers) == 3
    assert len(restored.analysis_runs) == 2
    assert all(run.status == "completed" for run in restored.analysis_runs)
    assert {layer.layer_id for layer in restored.snapshot.layers} >= {
        first.run.output_layer_ids[0],
        second.run.output_layer_ids[0],
    }


def test_changed_input_marks_dependent_analysis_history_stale(tmp_path: Path) -> None:
    """输入文件指纹变化后，打开工程应标记相关分析记录为过期。"""
    source_path: Path = tmp_path / "roads.geojson"
    GeoPandasVectorWriter().write(make_layer(), source_path)
    project_path: Path = tmp_path / "roads.gisproj"

    application: GisApplication = make_application()
    source_result = application.open_data(source_path)
    application.save_project(project_path)
    source_layer = application.snapshot().layers[0].layer
    application.persist_vector_analysis_result(
        source_layer,
        "buffer",
        (source_result.layer_id,),
        {"distance": 500},
    )
    application.save_project()

    changed_layer: VectorLayer = VectorLayer.create(
        name="道路",
        features=make_layer().features
        + (Feature(fid=3, geometry=Point(118.2, 31.2), attributes={"名称": "丙"}),),
        crs=CRS.from_epsg(4326),
    )
    GeoPandasVectorWriter().write(changed_layer, source_path)

    restored_application: GisApplication = make_application()
    restored = restored_application.open_project(project_path)

    assert restored.analysis_runs[0].status == "stale"
    assert any("数据源已变化" in warning for warning in restored.warnings)


def test_failed_analysis_history_remains_failed_when_input_changes(tmp_path: Path) -> None:
    """失败记录是执行事实，输入文件变化后不能被错误标记为过期。"""
    source_path: Path = tmp_path / "roads.geojson"
    GeoPandasVectorWriter().write(make_layer(), source_path)
    project_path: Path = tmp_path / "roads.gisproj"

    application: GisApplication = make_application()
    source_result = application.open_data(source_path)
    application.save_project(project_path)
    with pytest.raises(InvalidBufferParameters):
        application.buffer_analysis(
            BufferRequest(
                input_layer_id=source_result.layer_id,
                output_path=source_path,
                output_layer_name="失败结果",
                distance=500,
            )
        )
    application.save_project()

    changed_layer: VectorLayer = VectorLayer.create(
        name="道路",
        features=make_layer().features
        + (Feature(fid=3, geometry=Point(118.2, 31.2), attributes={"名称": "丙"}),),
        crs=CRS.from_epsg(4326),
    )
    GeoPandasVectorWriter().write(changed_layer, source_path)

    restored_application: GisApplication = make_application()
    restored = restored_application.open_project(project_path)

    assert restored.analysis_runs[0].status == "failed"
    assert restored.analysis_runs[0].message is not None
