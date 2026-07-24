"""GeoPandas 矢量写入适配器测试。"""

from pathlib import Path

import geopandas as gpd
import pytest
from pyproj import CRS
from shapely.geometry import Point

from app.application.errors import UnsupportedExportFormat
from app.domain.feature import Feature
from app.domain.vector_layer import VectorLayer
from app.infrastructure.file_io.geopandas_vector_writer import GeoPandasVectorWriter


def make_layer() -> VectorLayer:
    """创建包含中文属性的测试矢量图层。"""
    return VectorLayer.create(
        name="学校",
        features=(
            Feature(fid=1, geometry=Point(118.78, 32.04), attributes={"名称": "甲"}),
            Feature(fid=2, geometry=Point(118.79, 32.05), attributes={"名称": "乙"}),
        ),
        crs=CRS.from_epsg(4326),
    )


def test_write_geojson_preserves_features_attributes_and_crs(tmp_path: Path) -> None:
    """GeoJSON 写出后应保留几何、属性和当前图层坐标系。"""
    path: Path = tmp_path / "schools.geojson"

    GeoPandasVectorWriter().write(make_layer(), path)

    dataframe: gpd.GeoDataFrame = gpd.read_file(path)
    assert list(dataframe["名称"]) == ["甲", "乙"]
    assert dataframe.crs == CRS.from_epsg(4326)
    assert dataframe.geometry.iloc[0].x == pytest.approx(118.78)


def test_write_only_selected_features(tmp_path: Path) -> None:
    """提供选择集时只写出对应要素。"""
    path: Path = tmp_path / "selected.geojson"

    GeoPandasVectorWriter().write(make_layer(), path, selected_feature_ids=(2,))

    dataframe: gpd.GeoDataFrame = gpd.read_file(path)
    assert list(dataframe["名称"]) == ["乙"]


def test_write_shapefile_creates_readable_dataset(tmp_path: Path) -> None:
    """Shapefile 写出后应生成可重新读取的完整数据集。"""
    path: Path = tmp_path / "schools.shp"

    GeoPandasVectorWriter().write(make_layer(), path)

    dataframe: gpd.GeoDataFrame = gpd.read_file(path)
    assert len(dataframe) == 2
    assert dataframe.crs == CRS.from_epsg(4326)
    assert path.with_suffix(".dbf").is_file()
    assert path.with_suffix(".shx").is_file()


def test_write_rejects_unsupported_vector_suffix(tmp_path: Path) -> None:
    """矢量写入器应拒绝未支持的输出扩展名。"""
    with pytest.raises(UnsupportedExportFormat, match="导出格式"):
        GeoPandasVectorWriter().write(make_layer(), tmp_path / "schools.kml")
