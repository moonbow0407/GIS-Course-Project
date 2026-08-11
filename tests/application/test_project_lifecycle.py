"""工程保存、恢复和分析结果历史测试。"""

from pathlib import Path

import fiona
import numpy as np
import pytest
import rasterio
from affine import Affine
from pyproj import CRS
from shapely.geometry import Point

from app.application.buffer_analysis import BufferRequest
from app.application.database_models import DatabaseConnectionConfig, DatabaseServerInfo
from app.application.database_service import DatabaseService
from app.application.errors import InvalidBufferParameters
from app.application.gis_application import GisApplication
from app.application.project_models import LayerReference, MapViewState
from app.application.project_service import ProjectService
from app.application.symbology_service import create_raster_classified_symbology
from app.domain.feature import Feature
from app.domain.raster_layer import RasterLayer
from app.domain.symbology import RasterRendererType, RasterSymbology, symbology_to_dict
from app.domain.vector_layer import VectorLayer
from app.infrastructure.file_io.auto_reader import AutoDataReader
from app.infrastructure.file_io.auto_writer import AutoDataWriter
from app.infrastructure.file_io.geopandas_vector_writer import GeoPandasVectorWriter
from app.infrastructure.project.json_project_store import JsonProjectStore


class FakeDatabaseGateway:
    """为工程数据库图层恢复测试提供最小 PostGIS 网关。"""

    def __init__(self) -> None:
        self.layer = VectorLayer.create(
            name="数据库道路",
            features=(Feature(fid=11, geometry=Point(118.0, 31.0), attributes={}),),
            crs=CRS.from_epsg(4326),
            database_layer_id=17,
        )

    def test_connection(self) -> DatabaseServerInfo:
        """返回模拟连接信息。"""
        return DatabaseServerInfo("gis", "tester", "PostgreSQL", "PostGIS")

    def ensure_schema(self) -> None:
        """测试网关不需要建表。"""

    def list_layers(self) -> tuple[object, ...]:
        """返回空目录。"""
        return ()

    def import_layer(self, layer: VectorLayer) -> object:
        """测试网关不执行导入。"""
        return layer

    def load_layer(self, layer_id: int, target_crs: CRS | None = None) -> VectorLayer:
        """按固定数据库 ID 返回图层。"""
        assert layer_id == 17
        assert target_crs is None
        return self.layer

    def close(self) -> None:
        """释放模拟连接。"""


def make_database_service() -> DatabaseService:
    """创建已经连接到固定测试身份的数据库服务。"""
    service = DatabaseService(lambda _config: FakeDatabaseGateway())
    service.connect(
        DatabaseConnectionConfig(
            host="localhost",
            port=5432,
            database="gis",
            username="tester",
            password="secret",
        )
    )
    return service


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


def test_restoring_lazy_raster_symbology_does_not_load_full_analysis_data() -> None:
    """打开工程恢复栅格符号时不应因重建显示图像而读取完整像元。"""

    def fail_if_loaded() -> tuple[np.ndarray, np.ndarray]:
        raise AssertionError("恢复工程不应触发完整栅格分析数据加载")

    symbology = RasterSymbology(
        renderer_type=RasterRendererType.STRETCH,
        color_scheme="terrain",
    )
    layer = RasterLayer.create_lazy(
        name="dem",
        image_data=np.zeros((2, 2, 4), dtype=np.uint8),
        transform=Affine.identity(),
        display_transform=Affine.identity(),
        crs=CRS.from_epsg(4549),
        bounds=(0, 0, 2, 2),
        raster_shape=(10000, 10000),
        band_count=1,
        analysis_loader=fail_if_loaded,
    )
    reference = LayerReference(
        layer_id="stable-dem",
        name="工程中的 DEM",
        source_path="dem.tif",
        source_layer_name=None,
        layer_kind="raster",
        visible=True,
        selected_feature_ids=(),
        fingerprint=None,
        symbology=symbology_to_dict(symbology),
    )

    restored = ProjectService._restore_layer_identity(layer, reference, Path("dem.tif"))

    assert restored.layer_id == "stable-dem"
    assert restored.name == "工程中的 DEM"
    assert restored.symbology == symbology
    assert restored.analysis_data_loaded is False


def test_restoring_lazy_classified_raster_uses_preview_values_without_full_load() -> None:
    """大栅格恢复分类符号时应使用预览原始值而不是读取完整分析数组。"""

    def fail_if_loaded() -> tuple[np.ndarray, np.ndarray]:
        raise AssertionError("分类预览不应触发完整栅格分析数据加载")

    symbology = create_raster_classified_symbology((1.0, 2.0))
    layer = RasterLayer.create_lazy(
        name="reclass",
        image_data=np.zeros((2, 2, 4), dtype=np.uint8),
        display_values=np.asarray([[[1.0, 2.0], [1.0, 9.0]]], dtype=np.float32),
        display_valid_mask=np.ones((2, 2), dtype=np.bool_),
        display_band_indexes=(0,),
        transform=Affine.identity(),
        display_transform=Affine.identity(),
        crs=CRS.from_epsg(4326),
        bounds=(0, 0, 2, 2),
        raster_shape=(10000, 10000),
        band_count=1,
        analysis_loader=fail_if_loaded,
    )
    reference = LayerReference(
        layer_id="stable-reclass",
        name="工程中的重分类",
        source_path="reclass.tif",
        source_layer_name=None,
        layer_kind="raster",
        visible=True,
        selected_feature_ids=(),
        fingerprint=None,
        symbology=symbology_to_dict(symbology),
    )

    restored = ProjectService._restore_layer_identity(layer, reference, Path("reclass.tif"))

    assert restored.analysis_data_loaded is False
    assert restored.image_data[0, 0, :3].tolist() == [78, 121, 167]
    assert restored.image_data[0, 1, :3].tolist() == [242, 142, 43]


def test_project_round_trip_rebuilds_raster_classified_preview(tmp_path: Path) -> None:
    """工程重开后，分类图例颜色应与地图栅格预览保持一致。"""
    source_path: Path = tmp_path / "classified.tif"
    with rasterio.open(
        source_path,
        "w",
        driver="GTiff",
        height=2,
        width=2,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=Affine.translation(0, 2) * Affine.scale(1, -1),
        nodata=-9999.0,
    ) as dataset:
        dataset.write(np.asarray([[1.0, 2.0], [1.0, -9999.0]], dtype=np.float32), 1)
    project_path: Path = tmp_path / "classified.gisproj"

    application = make_application()
    opened = application.open_data(source_path)
    # 直接从工程可持久化的符号配置构造两类，避免测试依赖界面操作。
    symbology = create_raster_classified_symbology((1.0, 2.0))
    application.apply_raster_symbology(opened.layer_id, symbology)
    application.save_project(project_path)

    restored = make_application().open_project(project_path)
    restored_layer = restored.snapshot.layers[0].layer

    assert restored_layer.symbology.renderer_type is RasterRendererType.CLASSIFIED
    assert restored_layer.image_data[0, 0, :3].tolist() == [78, 121, 167]


def test_project_round_trip_restores_raster_display_resampling(tmp_path: Path) -> None:
    """工程应保存栅格显示重采样设置，但不改变分析栅格。"""
    source_path = tmp_path / "display.tif"
    with rasterio.open(
        source_path,
        "w",
        driver="GTiff",
        width=2,
        height=2,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=Affine.translation(0, 2) * Affine.scale(1, -1),
    ) as dataset:
        dataset.write(np.ones((1, 2, 2), dtype=np.float32))
    project_path = tmp_path / "display.gisproj"

    application = make_application()
    opened = application.open_data(source_path)
    application.set_raster_display_resampling(opened.layer_id, "nearest")
    application.save_project(project_path)

    restored = make_application().open_project(project_path)

    assert restored.snapshot.layers[0].raster_display_resampling == "nearest"
    assert restored.snapshot.layers[0].layer.raster_data.shape == (1, 2, 2)


def test_project_save_can_skip_or_persist_temporary_layers(tmp_path: Path) -> None:
    """保存工程时跳过临时层会告警，确认持久化后应能重新打开该图层。"""
    application = make_application()
    temporary = VectorLayer.create(
        name="临时结果",
        features=make_layer().features,
        crs=CRS.from_epsg(4326),
    )
    application.add_layer(temporary)
    project_path = tmp_path / "temporary.gisproj"

    skipped = application.save_project(project_path, persist_temporary=False)
    assert skipped.layer_count == 0
    assert skipped.warnings
    assert len(make_application().open_project(project_path).snapshot.layers) == 0

    persisted = application.save_project(project_path, persist_temporary=True)
    assert persisted.layer_count == 1
    reopened = make_application().open_project(project_path)
    assert len(reopened.snapshot.layers) == 1
    assert reopened.snapshot.layers[0].layer.source_path is not None


def test_project_round_trip_persists_database_layer_reference_without_password(
    tmp_path: Path,
) -> None:
    """工程应保存数据库图层 ID 和连接身份，绝不能保存数据库密码。"""
    project_path = tmp_path / "database.gisproj"
    database_service = make_database_service()
    application = GisApplication(
        AutoDataReader(),
        project_store=JsonProjectStore(),
        database_service=database_service,
    )
    opened = application.load_database_layer(17)
    application.save_project(project_path)

    manifest_text = project_path.read_text(encoding="utf-8")
    assert '"source_kind": "database"' in manifest_text
    assert "secret" not in manifest_text

    restored = GisApplication(
        AutoDataReader(),
        project_store=JsonProjectStore(),
        database_service=make_database_service(),
    ).open_project(project_path)

    assert restored.snapshot.layers[0].layer_id == opened.layer_id
    assert restored.snapshot.layers[0].layer.database_layer_id == 17


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
    assert application.is_modified is False

    application.set_layer_opacity(layer_id, 0.4)
    application.set_layer_scale_range(layer_id, 50.0, 200.0)
    application.save_project(project_path, MapViewState(118.05, 31.05, 150.0))

    restored_application: GisApplication = make_application()
    restored = restored_application.open_project(project_path)

    restored_layer = restored.snapshot.layers[0]
    assert restored_layer.layer_id == layer_id
    assert restored_layer.visible is False
    assert restored_layer.opacity == pytest.approx(0.4)
    assert restored_layer.min_scale_percent == pytest.approx(50.0)
    assert restored_layer.max_scale_percent == pytest.approx(200.0)
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
