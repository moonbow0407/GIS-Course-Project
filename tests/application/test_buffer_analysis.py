"""缓冲区分析应用用例测试。"""

from pathlib import Path
from typing import cast

import geopandas as gpd
import pytest
from pyproj import CRS
from shapely.geometry import Point

from app.application.buffer_analysis import BufferRequest
from app.application.errors import DataWriteFailed, InvalidBufferParameters
from app.application.gis_application import GisApplication
from app.application.ports import DataReader, DataWriter
from app.domain.feature import Feature
from app.domain.spatial_layer import SpatialLayer
from app.domain.vector_layer import VectorLayer
from app.infrastructure.file_io.geopandas_vector_writer import GeoPandasVectorWriter


class InMemoryDataReader(DataReader):
    """返回预设矢量图层的测试读取器。"""

    def __init__(self, layer: VectorLayer) -> None:
        """保存测试用图层。"""
        self.layer = layer

    def read(
        self,
        path: Path,
        target_crs: CRS | None = None,
        layer_name: str | None = None,
    ) -> SpatialLayer:
        """忽略路径并返回预设图层。"""
        del path, target_crs, layer_name
        return self.layer


class RecordingDataWriter(DataWriter):
    """记录缓冲区结果写出请求。"""

    def __init__(self) -> None:
        """创建空记录器。"""
        self.layer: SpatialLayer | None = None
        self.path: Path | None = None
        self.layer_name: str | None = None

    def write(
        self,
        layer: SpatialLayer,
        path: Path,
        selected_feature_ids: tuple[str | int, ...] = (),
        layer_name: str | None = None,
    ) -> None:
        """记录一次写出请求。"""
        del selected_feature_ids
        self.layer = layer
        self.path = path
        self.layer_name = layer_name


class FailingDataWriter(RecordingDataWriter):
    """在写出时报告应用层错误的测试写入器。"""

    def write(
        self,
        layer: SpatialLayer,
        path: Path,
        selected_feature_ids: tuple[str | int, ...] = (),
        layer_name: str | None = None,
    ) -> None:
        """拒绝写出结果。"""
        del layer, path, selected_feature_ids, layer_name
        raise DataWriteFailed("测试写出失败")


def make_layer() -> VectorLayer:
    """创建包含两个相邻点的米制测试图层。"""
    return VectorLayer.create(
        layer_id="points",
        name="测试点",
        features=(
            Feature(fid=1, geometry=Point(0, 0), attributes={"名称": "甲"}),
            Feature(fid=2, geometry=Point(10, 0), attributes={"名称": "乙"}),
        ),
        crs=CRS.from_epsg(3857),
    )


def make_request(path: Path, *, dissolve: bool = False) -> BufferRequest:
    """创建一份使用米制坐标的缓冲区请求。"""
    return BufferRequest(
        input_layer_id="points",
        output_path=path,
        output_layer_name="点缓冲区",
        distance=10.0,
        segments=8,
        dissolve=dissolve,
    )


def test_buffer_analysis_writes_and_activates_new_result_layer(tmp_path: Path) -> None:
    """缓冲区结果应写出、保留属性，并作为新的活动图层加入工作区。"""
    writer: RecordingDataWriter = RecordingDataWriter()
    application: GisApplication = GisApplication(
        InMemoryDataReader(make_layer()),
        data_writer=writer,
    )
    application.open_data(Path("points.geojson"))

    result = application.buffer_analysis(make_request(tmp_path / "buffers.geojson"))

    assert result.input_layer_id == "points"
    assert result.output_layer_name == "点缓冲区"
    assert result.output_path == (tmp_path / "buffers.geojson").resolve()
    assert result.feature_count == 2
    assert result.snapshot.active_layer_id == result.output_layer_id
    assert [layer.name for layer in result.snapshot.layers] == ["测试点", "点缓冲区"]
    assert writer.path == result.output_path
    assert writer.layer_name == "点缓冲区"
    output_layer = cast(VectorLayer, writer.layer)
    assert output_layer.features[0].attributes["名称"] == "甲"
    assert output_layer.features[0].geometry.area == pytest.approx(312.1445, rel=1e-3)


def test_buffer_analysis_dissolves_overlapping_features(tmp_path: Path) -> None:
    """启用融合时相互重叠的输入缓冲区应合并为一个结果要素。"""
    writer: RecordingDataWriter = RecordingDataWriter()
    application: GisApplication = GisApplication(
        InMemoryDataReader(make_layer()),
        data_writer=writer,
    )
    application.open_data(Path("points.geojson"))

    result = application.buffer_analysis(
        make_request(tmp_path / "dissolved.geojson", dissolve=True)
    )

    assert result.feature_count == 1
    output_layer = cast(VectorLayer, writer.layer)
    assert output_layer.features[0].attributes == {"source_count": 2}
    assert output_layer.features[0].geometry.geom_type == "Polygon"


def test_buffer_analysis_writes_readable_geojson(tmp_path: Path) -> None:
    """缓冲区应用用例应能通过真实写入器生成可重新读取的结果文件。"""
    application: GisApplication = GisApplication(
        InMemoryDataReader(make_layer()),
        data_writer=GeoPandasVectorWriter(),
    )
    application.open_data(Path("points.geojson"))
    output_path: Path = tmp_path / "written_buffers.geojson"

    application.buffer_analysis(make_request(output_path))

    dataframe: gpd.GeoDataFrame = gpd.read_file(output_path)
    assert len(dataframe) == 2
    assert dataframe.crs == CRS.from_epsg(3857)
    assert dataframe.geometry.iloc[0].geom_type == "Polygon"


def test_buffer_analysis_write_failure_does_not_add_partial_result(tmp_path: Path) -> None:
    """结果写出失败时，地图文档不能留下未持久化的半成品图层。"""
    application: GisApplication = GisApplication(
        InMemoryDataReader(make_layer()),
        data_writer=FailingDataWriter(),
    )
    application.open_data(Path("points.geojson"))

    with pytest.raises(DataWriteFailed, match="测试写出失败"):
        application.buffer_analysis(make_request(tmp_path / "failed.geojson"))

    assert len(application.snapshot().layers) == 1
    assert application.snapshot().active_layer_id == "points"


def test_buffer_request_rejects_non_positive_distance(tmp_path: Path) -> None:
    """缓冲距离为零或负数时应在执行前被拒绝。"""
    with pytest.raises(InvalidBufferParameters, match="大于零"):
        BufferRequest(
            input_layer_id="points",
            output_path=tmp_path / "buffers.geojson",
            output_layer_name="结果",
            distance=0,
        )
