"""KML 矢量读取适配器测试。"""

from pathlib import Path

import pytest
from pyproj import CRS
from pyproj.transformer import Transformer
from shapely.geometry import GeometryCollection

from app.application.errors import (
    EmptyVectorDataset,
    IncompatibleCoordinateReferenceSystem,
    NoUsableGeometry,
    UnsupportedVectorFormat,
    VectorFileNotFound,
    VectorReadFailed,
)
from app.domain.vector_layer import VectorLayer
from app.infrastructure.file_io.auto_reader import AutoDataReader
from app.infrastructure.file_io.kml_vector_reader import KmlVectorReader


def write_kml(path: Path, content: str) -> None:
    """将测试用 KML 内容写入临时文件。"""
    path.write_text(content, encoding="utf-8")


def test_read_point_preserves_longitude_latitude_and_attributes(
    tmp_path: Path,
) -> None:
    """读取点 KML 时应保持经度在前的坐标顺序和常用属性。"""
    path: Path = tmp_path / "sites.kml"
    write_kml(
        path,
        """<?xml version="1.0" encoding="UTF-8"?>
<kml>
  <Document>
    <Placemark>
      <name>监测点1</name>
      <description>第一个监测点</description>
      <Point>
        <coordinates>116.4,39.9,0</coordinates>
      </Point>
    </Placemark>
  </Document>
</kml>
""",
    )
    reader: KmlVectorReader = KmlVectorReader()

    layer: VectorLayer = reader.read(path)

    assert layer.name == "sites"
    assert layer.crs == CRS.from_epsg(4326)
    assert layer.features[0].geometry.x == pytest.approx(116.4)
    assert layer.features[0].geometry.y == pytest.approx(39.9)
    assert layer.features[0].geometry.has_z is False
    assert layer.features[0].attributes == {
        "name": "监测点1",
        "description": "第一个监测点",
    }


def test_read_kml_22_namespace_line_and_polygon(tmp_path: Path) -> None:
    """带 KML 2.2 命名空间时线、面应正确解析，环自动闭合。"""
    path: Path = tmp_path / "features.kml"
    write_kml(
        path,
        """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>路线</name>
      <LineString>
        <coordinates>116.3,39.8 116.5,39.9 116.7,39.85</coordinates>
      </LineString>
    </Placemark>
    <Placemark>
      <name>区域</name>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>116.0,39.0 116.1,39.0 116.1,39.1</coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>
  </Document>
</kml>
""",
    )

    layer: VectorLayer = KmlVectorReader().read(path)

    assert len(layer.features) == 2
    line = layer.features[0].geometry
    assert line.geom_type == "LineString"
    assert len(line.coords) == 3
    polygon = layer.features[1].geometry
    assert polygon.geom_type == "Polygon"
    # 外环只有 3 个不同顶点，读取器应自动补上闭合点。
    assert len(polygon.exterior.coords) == 4
    assert polygon.exterior.coords[0] == polygon.exterior.coords[-1]


def test_read_polygon_inner_ring_and_extended_data(tmp_path: Path) -> None:
    """面要素内环和 ExtendedData 属性应被保留。"""
    path: Path = tmp_path / "zone.kml"
    write_kml(
        path,
        """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>生态区</name>
      <ExtendedData>
        <Data name="code"><value>A01</value></Data>
        <Data name="level"><value>低</value></Data>
      </ExtendedData>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>0,0 0,4 4,4 4,0 0,0</coordinates>
          </LinearRing>
        </outerBoundaryIs>
        <innerBoundaryIs>
          <LinearRing>
            <coordinates>1,1 3,1 3,3 1,3 1,1</coordinates>
          </LinearRing>
        </innerBoundaryIs>
      </Polygon>
    </Placemark>
  </Document>
</kml>
""",
    )

    layer: VectorLayer = KmlVectorReader().read(path)

    polygon = layer.features[0].geometry
    assert polygon.geom_type == "Polygon"
    assert len(polygon.interiors) == 1
    assert layer.features[0].attributes == {
        "name": "生态区",
        "code": "A01",
        "level": "低",
    }


def test_read_multi_geometry_combines_members(tmp_path: Path) -> None:
    """MultiGeometry 的混合成员应合并为几何集合。"""
    path: Path = tmp_path / "mixed.kml"
    write_kml(
        path,
        """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <MultiGeometry>
        <Point>
          <coordinates>116.4,39.9</coordinates>
        </Point>
        <LineString>
          <coordinates>116.4,39.9 116.5,40.0</coordinates>
        </LineString>
      </MultiGeometry>
    </Placemark>
  </Document>
</kml>
""",
    )

    layer: VectorLayer = KmlVectorReader().read(path)

    geometry = layer.features[0].geometry
    assert isinstance(geometry, GeometryCollection)
    assert len(geometry.geoms) == 2


def test_read_reprojects_to_target_crs(tmp_path: Path) -> None:
    """提供目标坐标系时应把经纬度转换到目标投影。"""
    path: Path = tmp_path / "site.kml"
    write_kml(
        path,
        """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <Point>
        <coordinates>116.4,39.9</coordinates>
      </Point>
    </Placemark>
  </Document>
</kml>
""",
    )
    target: CRS = CRS.from_epsg(3857)

    layer: VectorLayer = KmlVectorReader().read(path, target_crs=target)

    transformer: Transformer = Transformer.from_crs(
        CRS.from_epsg(4326),
        target,
        always_xy=True,
    )
    expected_x, expected_y = transformer.transform(116.4, 39.9)
    assert layer.crs == target
    assert layer.features[0].geometry.x == pytest.approx(expected_x)
    assert layer.features[0].geometry.y == pytest.approx(expected_y)


def test_read_empty_document_raises(tmp_path: Path) -> None:
    """没有 Placemark 的 KML 文档应报告空数据集。"""
    path: Path = tmp_path / "empty.kml"
    write_kml(path, '<kml xmlns="http://www.opengis.net/kml/2.2"/>')

    with pytest.raises(EmptyVectorDataset):
        KmlVectorReader().read(path)


def test_read_placemark_without_geometry_raises(tmp_path: Path) -> None:
    """Placemark 全部缺少几何时应报告没有可用几何。"""
    path: Path = tmp_path / "names.kml"
    write_kml(
        path,
        """<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>只有名称</name>
    </Placemark>
  </Document>
</kml>
""",
    )

    with pytest.raises(NoUsableGeometry):
        KmlVectorReader().read(path)


def test_read_invalid_xml_raises(tmp_path: Path) -> None:
    """损坏的 XML 应报告读取失败而不是崩溃。"""
    path: Path = tmp_path / "broken.kml"
    write_kml(path, "<kml><Placemark>")

    with pytest.raises(VectorReadFailed):
        KmlVectorReader().read(path)


def test_read_missing_file_raises(tmp_path: Path) -> None:
    """不存在的文件应报告文件不存在。"""
    with pytest.raises(VectorFileNotFound):
        KmlVectorReader().read(tmp_path / "missing.kml")


def test_read_unsupported_suffix_raises(tmp_path: Path) -> None:
    """非 KML 扩展名应报告格式不支持。"""
    path: Path = tmp_path / "data.txt"
    path.write_text("内容", encoding="utf-8")

    with pytest.raises(UnsupportedVectorFormat):
        KmlVectorReader().read(path)


def test_read_coordinates_out_of_range_raises(tmp_path: Path) -> None:
    """超出经纬度范围的坐标应报告坐标系异常。"""
    path: Path = tmp_path / "bad.kml"
    write_kml(
        path,
        """<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <Point>
        <coordinates>200,39.9</coordinates>
      </Point>
    </Placemark>
  </Document>
</kml>
""",
    )

    with pytest.raises(IncompatibleCoordinateReferenceSystem):
        KmlVectorReader().read(path)


def test_auto_reader_dispatches_kml_file(tmp_path: Path) -> None:
    """自动读取器应把 .kml 分派给 KML 解析器。"""
    path: Path = tmp_path / "routes.kml"
    write_kml(
        path,
        """<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <LineString>
        <coordinates>116.3,39.8 116.5,39.9</coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>
""",
    )

    layer: VectorLayer = AutoDataReader().read(path)

    assert layer.name == "routes"
    assert layer.crs == CRS.from_epsg(4326)
    assert len(layer.features) == 1
