"""动态标注应用服务与工程持久化测试。"""

from pathlib import Path

from pyproj import CRS
from shapely.geometry import Point

from app.application.gis_application import GisApplication
from app.domain.feature import Feature
from app.domain.labeling import LabelClass, LabelingConfig, LabelPlacement
from app.domain.vector_layer import VectorLayer
from app.infrastructure.file_io.auto_reader import AutoDataReader
from app.infrastructure.file_io.auto_writer import AutoDataWriter
from app.infrastructure.file_io.geopandas_vector_writer import GeoPandasVectorWriter
from app.infrastructure.project.json_project_store import JsonProjectStore


def _make_layer() -> VectorLayer:
    """创建用于标注持久化的点图层。"""
    return VectorLayer.create(
        name="城市",
        features=(
            Feature(
                fid=1,
                geometry=Point(117.2, 31.8),
                attributes={"name": "合肥", "kind": "capital"},
            ),
        ),
        crs=CRS.from_epsg(4326),
    )


def _make_application() -> GisApplication:
    """创建带本地读写与工程存储适配器的应用服务。"""
    return GisApplication(
        AutoDataReader(),
        AutoDataWriter(),
        project_store=JsonProjectStore(),
    )


def test_labeling_is_applied_and_restored_with_project(tmp_path: Path) -> None:
    """标注配置应进入工作区快照，并在工程重开后恢复。"""
    source_path: Path = tmp_path / "cities.geojson"
    GeoPandasVectorWriter().write(_make_layer(), source_path)
    project_path: Path = tmp_path / "cities.gisproj"
    application = _make_application()
    opened = application.open_data(source_path)
    config = LabelingConfig(
        enabled=True,
        classes=(
            LabelClass(
                name="省会",
                field_name="name",
                filter_field="kind",
                filter_value="capital",
                placement=LabelPlacement.ABOVE,
                font_size=16.0,
                halo_enabled=True,
            ),
        ),
    )

    snapshot = application.set_layer_labeling(opened.layer_id, config)
    application.save_project(project_path)
    restored = _make_application().open_project(project_path)

    assert snapshot.layers[0].layer.labeling == config
    assert restored.snapshot.layers[0].layer.labeling == config
