"""地图显示 CRS 与分析 CRS 工作流测试。"""

import json
from pathlib import Path

import pytest
from pyproj import CRS
from shapely.geometry import LineString, Point

from app.application.display_projection_service import DisplayProjectionService
from app.application.errors import (
    CoordinateReferenceSystemRequired,
    WorkspaceOperationCancelled,
)
from app.application.gis_application import GisApplication
from app.domain.feature import Feature
from app.domain.vector_layer import VectorLayer
from app.infrastructure.file_io.geopandas_vector_reader import GeoPandasVectorReader
from app.infrastructure.projection.pyproj_coordinate_transformer import (
    PyprojCoordinateTransformer,
)


def write_point_geojson(path: Path) -> None:
    """写入一个 WGS84 测试点数据源。"""
    content: dict[str, object] = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"名称": "测试点"},
                "geometry": {"type": "Point", "coordinates": [1.0, 1.0]},
            }
        ],
    }
    path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")


def make_application() -> GisApplication:
    """创建带矢量显示投影适配器的应用服务。"""
    return GisApplication(
        GeoPandasVectorReader(),
        display_projection_service=DisplayProjectionService(
            coordinate_transformer=PyprojCoordinateTransformer()
        ),
    )


def test_set_display_crs_rebuilds_display_payload_without_changing_domain_layer(
    tmp_path: Path,
) -> None:
    """设置地图 CRS 后只重建显示载荷，领域图层仍保留源 CRS 和坐标。"""
    path: Path = tmp_path / "point.geojson"
    write_point_geojson(path)
    application: GisApplication = make_application()
    application.open_data(path)
    original_layer = application.snapshot().layers[0].layer
    application.set_layer_opacity(original_layer.layer_id, 0.35)
    application.set_layer_scale_range(original_layer.layer_id, 75.0, 250.0)

    application.set_display_crs(CRS.from_epsg(3857))

    snapshot = application.snapshot()
    assert snapshot.display_crs == CRS.from_epsg(3857)
    assert snapshot.layers[0].layer_id == original_layer.layer_id
    assert snapshot.layers[0].layer.crs == CRS.from_epsg(4326)
    assert snapshot.layers[0].opacity == pytest.approx(0.35)
    assert snapshot.layers[0].min_scale_percent == pytest.approx(75.0)
    assert snapshot.layers[0].max_scale_percent == pytest.approx(250.0)
    assert snapshot.layers[0].layer.features[0].geometry.x == pytest.approx(1.0)
    assert snapshot.layers[0].display_payload.features[0].geometry.x == pytest.approx(
        111319.49,
        rel=1e-5,
    )


def test_analysis_layers_use_target_crs_without_changing_display_layer(tmp_path: Path) -> None:
    """分析输入应按目标 CRS 创建临时副本，工作区显示图层保持不变。"""
    path: Path = tmp_path / "point.geojson"
    write_point_geojson(path)
    application: GisApplication = make_application()
    application.open_data(path)
    application.set_display_crs(CRS.from_epsg(3857))
    layer_id: str = application.snapshot().layers[0].layer_id

    environment = application.create_analysis_environment(CRS.from_epsg(3857))
    prepared_layers = application.prepare_analysis_layers((layer_id,), environment)

    assert prepared_layers[0].crs == CRS.from_epsg(3857)
    assert prepared_layers[0] is not application.snapshot().layers[0].layer
    assert application.snapshot().display_crs == CRS.from_epsg(3857)
    assert application.snapshot().layers[0].layer.crs == CRS.from_epsg(4326)


def test_display_crs_is_established_by_first_known_layer(tmp_path: Path) -> None:
    """空地图没有 CRS，首个已知 CRS 图层建立地图显示 CRS。"""
    path: Path = tmp_path / "point.geojson"
    write_point_geojson(path)
    application: GisApplication = make_application()
    application.open_data(path)

    assert application.snapshot().display_crs == CRS.from_epsg(4326)
    assert application.snapshot().layers[0].layer.crs == CRS.from_epsg(4326)


def test_unknown_crs_requires_definition_before_import(tmp_path: Path) -> None:
    """未知 CRS 数据必须在导入前通过覆盖定义，且定义不改坐标值。"""
    class UnknownReader:
        """返回未知 CRS 或调用方覆盖 CRS 的测试读取器。"""

        def read(
            self,
            path: Path,
            target_crs: CRS | None = None,
            layer_name: str | None = None,
            source_crs_override: CRS | None = None,
        ) -> VectorLayer:
            del path, target_crs, layer_name
            return VectorLayer.create(
                name="unknown",
                features=(Feature(1, Point(1.0, 1.0), {}),),
                crs=source_crs_override,
            )

    path: Path = tmp_path / "unknown.geojson"
    application: GisApplication = GisApplication(
        UnknownReader(),
        display_projection_service=DisplayProjectionService(
            coordinate_transformer=PyprojCoordinateTransformer()
        ),
    )

    with pytest.raises(CoordinateReferenceSystemRequired):
        application.open_data(path)

    application.open_data(path, source_crs_override=CRS.from_epsg(4326))
    layer_snapshot = application.snapshot().layers[0]
    assert layer_snapshot.layer.crs == CRS.from_epsg(4326)
    assert layer_snapshot.layer.features[0].geometry.equals(Point(1.0, 1.0))


def test_reproject_layer_adds_new_layer_and_preserves_source_layer(tmp_path: Path) -> None:
    """应用层重投影工具应新增独立图层，不替换原始领域图层。"""
    path: Path = tmp_path / "point.geojson"
    write_point_geojson(path)
    application: GisApplication = make_application()
    opened = application.open_data(path)
    original = application.snapshot().layers[0].layer

    result = application.reproject_layer(opened.layer_id, CRS.from_epsg(3857))

    assert result.layer_id != opened.layer_id
    assert len(application.snapshot().layers) == 2
    assert application.snapshot().layers[0].layer is original
    projected = application.snapshot().layers[1].layer
    assert projected.crs == CRS.from_epsg(3857)
    assert projected.source_path is None
    assert original.crs == CRS.from_epsg(4326)
    assert original.features[0].geometry.x == pytest.approx(1.0)
    assert result.reprojection_metadata is not None
    assert result.reprojection_metadata.operation
    assert result.reprojection_metadata.source_crs == CRS.from_epsg(4326).to_string()
    assert result.reprojection_metadata.target_crs == CRS.from_epsg(3857).to_string()
    assert application.analysis_runs[-1].algorithm_id == "reproject"
    assert application.analysis_runs[-1].parameters["output_shape"] is None


def test_measurement_uses_geodesic_for_geographic_and_planar_for_projected() -> None:
    """地理 CRS 测量使用椭球距离，投影 CRS 测量使用平面单位。"""
    geographic = make_application()
    geographic.add_layer(
        VectorLayer.create(
            name="geographic",
            features=(Feature(1, Point(0.0, 0.0), {}),),
            crs=CRS.from_epsg(4326),
        )
    )

    geodesic = geographic.measure_length(LineString([(0.0, 0.0), (1.0, 0.0)]))

    projected = make_application()
    projected.add_layer(
        VectorLayer.create(
            name="projected",
            features=(Feature(1, Point(0.0, 0.0), {}),),
            crs=CRS.from_epsg(3857),
        )
    )
    planar = projected.measure_length(LineString([(0.0, 0.0), (1000.0, 0.0)]))

    assert geodesic.method == "ellipsoidal"
    assert geodesic.value == pytest.approx(111319.49, rel=1e-4)
    assert planar.method == "planar"
    assert planar.value == pytest.approx(1000.0)


def test_query_maps_display_point_back_to_source_crs_and_blocks_cross_crs_edit() -> None:
    """点选应回到源 CRS 匹配 fid，活动层 CRS 不等价时禁止编辑。"""
    application = make_application()
    layer = VectorLayer.create(
        name="source",
        features=(Feature(7, Point(1.0, 0.0), {"name": "A"}),),
        crs=CRS.from_epsg(4326),
    )
    application.add_layer(layer)
    application.set_display_crs(CRS.from_epsg(3857))

    result = application.select_point(Point(111319.49, 0.0), tolerance=5.0)

    assert result.features[0].feature.fid == 7
    assert application.can_edit_layer(layer.layer_id) is False


def test_prepare_display_crs_reports_progress_before_commit(tmp_path: Path) -> None:
    """坐标系转换准备阶段应逐图层报告进度，且提交前不改变工作区。"""
    first_path: Path = tmp_path / "first.geojson"
    second_path: Path = tmp_path / "second.geojson"
    write_point_geojson(first_path)
    write_point_geojson(second_path)
    application: GisApplication = make_application()
    application.open_data(first_path)
    application.open_data(second_path)
    original_snapshot = application.snapshot()
    progress: list[tuple[int, int]] = []

    preparation = application.prepare_display_crs(
        CRS.from_epsg(3857),
        lambda done, total: progress.append((done, total)) or True,
    )

    assert progress == [(0, 2), (1, 2), (2, 2)]
    assert preparation.source_layer_ids == tuple(
        layer.layer_id for layer in original_snapshot.layers
    )
    assert application.snapshot() == original_snapshot

    application.commit_display_crs(preparation)
    assert application.snapshot().display_crs == CRS.from_epsg(3857)


def test_prepare_display_crs_can_be_cancelled_without_partial_update(tmp_path: Path) -> None:
    """取消坐标系转换时不应提交已完成的部分图层。"""
    path: Path = tmp_path / "point.geojson"
    write_point_geojson(path)
    application: GisApplication = make_application()
    application.open_data(path)
    original_snapshot = application.snapshot()

    with pytest.raises(WorkspaceOperationCancelled, match="取消"):
        application.prepare_display_crs(
            CRS.from_epsg(3857),
            lambda _done, _total: False,
        )

    assert application.snapshot() == original_snapshot
