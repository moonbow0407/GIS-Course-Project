"""GeoPandas 矢量读取适配器测试。"""

import json
from pathlib import Path

import pytest
from pyproj import CRS

from app.application.errors import (
    EmptyVectorDataset,
    NoUsableGeometry,
    UnsupportedVectorFormat,
    VectorFileNotFound,
)
from app.domain.vector_layer import VectorLayer
from app.infrastructure.file_io.geopandas_vector_reader import GeoPandasVectorReader


def write_geojson(path: Path, content: dict[str, object]) -> None:
    """将测试用 GeoJSON 内容写入临时文件。"""
    serialized_content: str = json.dumps(content, ensure_ascii=False)
    path.write_text(serialized_content, encoding="utf-8")


def test_read_point_geojson_preserves_geometry_attributes_and_crs(tmp_path: Path) -> None:
    """读取点 GeoJSON 时应保留几何、属性和坐标参考系统。"""
    path: Path = tmp_path / "schools.geojson"
    content: dict[str, object] = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": 7,
                "properties": {"名称": "第一中学", "等级": 2},
                "geometry": {"type": "Point", "coordinates": [118.78, 32.04]},
            }
        ],
    }
    write_geojson(path, content)
    reader: GeoPandasVectorReader = GeoPandasVectorReader()

    layer: VectorLayer = reader.read(path)

    assert layer.name == "schools"
    assert layer.crs == CRS.from_epsg(4326)
    assert layer.features[0].attributes == {"名称": "第一中学", "等级": 2}
    assert layer.features[0].geometry.x == pytest.approx(118.78)


def test_read_reprojects_known_crs_to_target(tmp_path: Path) -> None:
    """提供目标坐标系时应在构造领域图层前完成重投影。"""
    path: Path = tmp_path / "point.geojson"
    content: dict[str, object] = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {"type": "Point", "coordinates": [1.0, 1.0]},
            }
        ],
    }
    write_geojson(path, content)
    reader: GeoPandasVectorReader = GeoPandasVectorReader()

    layer: VectorLayer = reader.read(path, CRS.from_epsg(3857))

    assert layer.crs == CRS.from_epsg(3857)
    assert layer.features[0].geometry.x == pytest.approx(111319.49, rel=1e-5)


def test_read_rejects_missing_file(tmp_path: Path) -> None:
    """文件不存在时应抛出结构化应用错误。"""
    reader: GeoPandasVectorReader = GeoPandasVectorReader()

    with pytest.raises(VectorFileNotFound, match="文件不存在"):
        reader.read(tmp_path / "missing.geojson")


def test_read_rejects_unsupported_suffix(tmp_path: Path) -> None:
    """不支持的扩展名应在调用 GeoPandas 前被拒绝。"""
    path: Path = tmp_path / "data.txt"
    path.write_text("invalid", encoding="utf-8")
    reader: GeoPandasVectorReader = GeoPandasVectorReader()

    with pytest.raises(UnsupportedVectorFormat, match="不支持"):
        reader.read(path)


def test_read_rejects_empty_dataset(tmp_path: Path) -> None:
    """没有任何记录的矢量数据集应给出明确错误。"""
    path: Path = tmp_path / "empty.geojson"
    write_geojson(path, {"type": "FeatureCollection", "features": []})
    reader: GeoPandasVectorReader = GeoPandasVectorReader()

    with pytest.raises(EmptyVectorDataset, match="不包含任何记录"):
        reader.read(path)


def test_read_rejects_dataset_without_usable_geometry(tmp_path: Path) -> None:
    """只有空几何的矢量数据集应给出明确错误。"""
    path: Path = tmp_path / "null.geojson"
    content: dict[str, object] = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"名称": "空"}, "geometry": None}
        ],
    }
    write_geojson(path, content)
    reader: GeoPandasVectorReader = GeoPandasVectorReader()

    with pytest.raises(NoUsableGeometry, match="可用几何"):
        reader.read(path)
