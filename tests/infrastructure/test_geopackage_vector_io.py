"""GeoPackage 矢量多图层读写测试。"""

from pathlib import Path

import pytest
from pyproj import CRS
from shapely.geometry import Point

from app.application.errors import DataWriteFailed
from app.domain.feature import Feature
from app.domain.vector_layer import VectorLayer
from app.infrastructure.file_io.geopandas_vector_reader import GeoPandasVectorReader
from app.infrastructure.file_io.geopandas_vector_writer import GeoPandasVectorWriter


def make_layer(name: str) -> VectorLayer:
    """创建用于 GeoPackage 多图层测试的点图层。"""
    return VectorLayer.create(
        name=name,
        features=(
            Feature(fid=1, geometry=Point(0, 0), attributes={"名称": name}),
        ),
        crs=CRS.from_epsg(4326),
    )


def test_geopackage_writer_appends_named_layers_and_reader_restores_name(
    tmp_path: Path,
) -> None:
    """同一 GeoPackage 应能追加多个结果图层，并按名称读取指定图层。"""
    path: Path = tmp_path / "results.gpkg"
    writer: GeoPandasVectorWriter = GeoPandasVectorWriter()

    writer.write(make_layer("第一结果"), path, layer_name="buffer_001")
    writer.write(make_layer("第二结果"), path, layer_name="buffer_002")

    loaded: VectorLayer = GeoPandasVectorReader().read(path, layer_name="buffer_002")

    assert loaded.name == "buffer_002"
    assert loaded.source_layer_name == "buffer_002"
    assert loaded.features[0].attributes["名称"] == "第二结果"


def test_geopackage_writer_rejects_duplicate_layer_instead_of_overwriting(
    tmp_path: Path,
) -> None:
    """分析结果写入不能覆盖同名旧结果。"""
    path: Path = tmp_path / "results.gpkg"
    writer: GeoPandasVectorWriter = GeoPandasVectorWriter()
    writer.write(make_layer("旧结果"), path, layer_name="buffer_001")

    with pytest.raises(DataWriteFailed, match="不能覆盖旧结果"):
        writer.write(make_layer("新结果"), path, layer_name="buffer_001")
