"""GeoPackage 矢量多图层读写与同名图层原子替换测试。"""

import sqlite3
from pathlib import Path

import geopandas as gpd
import pytest
from pyproj import CRS
from shapely.geometry import Point

import app.infrastructure.file_io.geopandas_vector_writer as writer_module
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


def make_multi_feature_layer(name: str, extra_field: bool = False) -> VectorLayer:
    """创建含多个要素的点图层，可选携带新增字段验证 schema 变化。"""
    features: tuple[Feature, ...] = tuple(
        Feature(
            fid=index,
            geometry=Point(index, index),
            attributes=(
                {"名称": f"{name}-{index}", "备注": f"备注{index}"}
                if extra_field
                else {"名称": f"{name}-{index}"}
            ),
        )
        for index in range(1, 3)
    )
    return VectorLayer.create(name=name, features=features, crs=CRS.from_epsg(4326))


def assert_sqlite_healthy(path: Path) -> None:
    """断言 GeoPackage 通过 SQLite 完整性和外键检查。"""
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


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


def test_geopackage_writer_replaces_same_name_layer_atomically(
    tmp_path: Path,
) -> None:
    """同名图层应整层原子替换：新数据生效、其他图层不变、文件完整。

    覆盖连续两次替换和带新增字段的 schema 变化，并确认不会残留临时图层。
    """
    import fiona

    path: Path = tmp_path / "results.gpkg"
    writer: GeoPandasVectorWriter = GeoPandasVectorWriter()
    reader: GeoPandasVectorReader = GeoPandasVectorReader()
    writer.write(make_layer("旧结果"), path, layer_name="L1")
    writer.write(make_layer("保留结果"), path, layer_name="L2")

    # 第一次替换：新数据生效，L2 保持原数据。
    writer.write(make_multi_feature_layer("新结果"), path, layer_name="L1")
    replaced: VectorLayer = reader.read(path, layer_name="L1")
    assert [f.attributes["名称"] for f in replaced.features] == ["新结果-1", "新结果-2"]
    untouched: VectorLayer = reader.read(path, layer_name="L2")
    assert untouched.features[0].attributes["名称"] == "保留结果"

    # 连续第二次替换：带新增字段的 schema。
    writer.write(make_multi_feature_layer("再次替换", extra_field=True), path, layer_name="L1")
    replaced_again: VectorLayer = reader.read(path, layer_name="L1")
    assert [f.attributes["备注"] for f in replaced_again.features] == ["备注1", "备注2"]
    assert reader.read(path, layer_name="L2").features[0].attributes["名称"] == "保留结果"

    # 图层名集合不变（顺序允许变化），没有临时层残留。
    assert set(fiona.listlayers(path)) == {"L1", "L2"}
    assert_sqlite_healthy(path)


def test_geopackage_replace_temp_write_failure_keeps_original_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """临时副本写入失败时原文件字节不得改变，且不残留临时文件。"""
    path: Path = tmp_path / "results.gpkg"
    writer: GeoPandasVectorWriter = GeoPandasVectorWriter()
    writer.write(make_layer("旧结果"), path, layer_name="L1")
    writer.write(make_layer("保留结果"), path, layer_name="L2")
    original_bytes: bytes = path.read_bytes()

    def _fail_to_file(*args: object, **kwargs: object) -> None:
        raise RuntimeError("模拟临时副本写入失败")

    monkeypatch.setattr(gpd.GeoDataFrame, "to_file", _fail_to_file)

    with pytest.raises(DataWriteFailed):
        writer.write(make_multi_feature_layer("新结果"), path, layer_name="L1")

    assert path.read_bytes() == original_bytes
    reader: GeoPandasVectorReader = GeoPandasVectorReader()
    assert reader.read(path, layer_name="L1").features[0].attributes["名称"] == "旧结果"
    assert reader.read(path, layer_name="L2").features[0].attributes["名称"] == "保留结果"
    # 同目录只剩目标文件，本次生成的临时文件已被清理。
    assert set(tmp_path.iterdir()) == {path}


def test_geopackage_replace_os_replace_failure_keeps_original_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """最终原子替换失败（如文件被外部程序锁定）时原文件不得改变。"""
    path: Path = tmp_path / "results.gpkg"
    writer: GeoPandasVectorWriter = GeoPandasVectorWriter()
    writer.write(make_layer("旧结果"), path, layer_name="L1")
    writer.write(make_layer("保留结果"), path, layer_name="L2")
    original_bytes: bytes = path.read_bytes()

    def _deny_replace(source: object, destination: object) -> None:
        raise PermissionError("目标文件被占用")

    monkeypatch.setattr(writer_module.os, "replace", _deny_replace)

    with pytest.raises(DataWriteFailed, match="可能被其他程序占用"):
        writer.write(make_multi_feature_layer("新结果"), path, layer_name="L1")

    assert path.read_bytes() == original_bytes
    reader: GeoPandasVectorReader = GeoPandasVectorReader()
    assert reader.read(path, layer_name="L1").features[0].attributes["名称"] == "旧结果"
    assert reader.read(path, layer_name="L2").features[0].attributes["名称"] == "保留结果"
    assert set(tmp_path.iterdir()) == {path}
