"""地图显示 CRS 与分析 CRS 工作流测试。"""

import json
from pathlib import Path

import pytest
from pyproj import CRS

from app.application.gis_application import GisApplication
from app.infrastructure.file_io.geopandas_vector_reader import GeoPandasVectorReader


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


def test_set_display_crs_reprojects_loaded_layer_without_changing_layer_id(
    tmp_path: Path,
) -> None:
    """设置地图 CRS 后应从源文件重建图层，并保留图层编号。"""
    path: Path = tmp_path / "point.geojson"
    write_point_geojson(path)
    application: GisApplication = GisApplication(GeoPandasVectorReader())
    application.open_data(path)
    original_layer = application.snapshot().layers[0].layer

    application.set_display_crs(CRS.from_epsg(3857))

    snapshot = application.snapshot()
    assert snapshot.display_crs == CRS.from_epsg(3857)
    assert snapshot.layers[0].layer_id == original_layer.layer_id
    assert snapshot.layers[0].layer.crs == CRS.from_epsg(3857)
    assert snapshot.layers[0].layer.features[0].geometry.x == pytest.approx(
        111319.49,
        rel=1e-5,
    )


def test_analysis_layers_use_target_crs_without_changing_display_layer(tmp_path: Path) -> None:
    """分析输入应按目标 CRS 创建临时副本，工作区显示图层保持不变。"""
    path: Path = tmp_path / "point.geojson"
    write_point_geojson(path)
    application: GisApplication = GisApplication(GeoPandasVectorReader())
    application.open_data(path)
    application.set_display_crs(CRS.from_epsg(3857))
    layer_id: str = application.snapshot().layers[0].layer_id

    environment = application.create_analysis_environment(CRS.from_epsg(4326))
    prepared_layers = application.prepare_analysis_layers((layer_id,), environment)

    assert prepared_layers[0].crs == CRS.from_epsg(4326)
    assert prepared_layers[0] is not application.snapshot().layers[0].layer
    assert application.snapshot().display_crs == CRS.from_epsg(3857)
    assert application.snapshot().layers[0].layer.crs == CRS.from_epsg(3857)


def test_display_crs_can_be_set_before_loading_data(tmp_path: Path) -> None:
    """空地图预先指定 CRS 后，后续加载图层应直接转换到该 CRS。"""
    path: Path = tmp_path / "point.geojson"
    write_point_geojson(path)
    application: GisApplication = GisApplication(GeoPandasVectorReader())

    application.set_display_crs(CRS.from_epsg(3857))
    application.open_data(path)

    assert application.snapshot().display_crs == CRS.from_epsg(3857)
    assert application.snapshot().layers[0].layer.crs == CRS.from_epsg(3857)
