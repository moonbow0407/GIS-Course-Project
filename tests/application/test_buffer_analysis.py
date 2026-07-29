"""缓冲区分析应用用例测试。"""

from pathlib import Path
from typing import cast

import geopandas as gpd
import pytest
from pyproj import CRS
from shapely.geometry import LineString, Point, Polygon

from app.application.buffer_analysis import (
    BufferDistanceUnitName,
    BufferRequest,
    BufferSideTypeName,
)
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


def make_line_layer() -> VectorLayer:
    """创建一条有方向的米制测试线，用于验证左右侧缓冲。"""
    return VectorLayer.create(
        layer_id="lines",
        name="测试线",
        features=(
            Feature(fid=1, geometry=LineString([(0, 0), (10, 0)]), attributes={}),
        ),
        crs=CRS.from_epsg(3857),
    )


def make_polygon_layer() -> VectorLayer:
    """创建一个米制测试面，用于验证外侧和负距离缓冲。"""
    return VectorLayer.create(
        layer_id="polygons",
        name="测试面",
        features=(
            Feature(
                fid=1,
                geometry=Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]),
                attributes={},
            ),
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


def make_geometry_request(
    input_layer_id: str,
    path: Path,
    distance: float,
    *,
    side_type: str = "full",
    distance_unit: str = "meter",
) -> BufferRequest:
    """创建点线面专用参数测试请求。"""
    return BufferRequest(
        input_layer_id=input_layer_id,
        output_path=path,
        output_layer_name="几何缓冲区",
        distance=distance,
        distance_unit=cast(BufferDistanceUnitName, distance_unit),
        side_type=cast(BufferSideTypeName, side_type),
        cap_style="flat",
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
    result_layer = result.snapshot.layers[-1].layer
    assert isinstance(result_layer, VectorLayer)
    assert result_layer.source_path == result.output_path
    assert writer.path == result.output_path
    assert writer.layer_name == "点缓冲区"
    output_layer = cast(VectorLayer, writer.layer)
    assert output_layer.features[0].attributes["名称"] == "甲"
    assert output_layer.features[0].geometry.area == pytest.approx(312.1445, rel=1e-3)
    assert len(application.analysis_runs) == 1
    run = application.analysis_runs[0]
    assert run.status == "completed"
    assert run.input_layer_ids == ("points",)
    assert run.output_layer_ids == (result.output_layer_id,)
    assert run.completed_at is not None
    assert run.duration_seconds is not None
    assert run.parameters["distance"] == 10.0
    assert run.parameters["output_path"] == str(result.output_path)


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


def test_buffer_distance_unit_is_converted_before_projected_calculation(tmp_path: Path) -> None:
    """用户输入一千米时，即使内部 CRS 使用米，也应生成一千米半径。"""
    writer: RecordingDataWriter = RecordingDataWriter()
    application: GisApplication = GisApplication(
        InMemoryDataReader(make_layer()),
        data_writer=writer,
    )
    application.open_data(Path("points.geojson"))

    application.buffer_analysis(
        make_geometry_request(
            "points",
            tmp_path / "kilometer.geojson",
            1.0,
            distance_unit="kilometer",
        )
    )

    output_layer = cast(VectorLayer, writer.layer)
    bounds = output_layer.features[0].geometry.bounds
    assert bounds[0] == pytest.approx(-1000.0)
    assert bounds[2] == pytest.approx(1000.0)


def test_buffer_distance_unit_works_for_geographic_input_crs(tmp_path: Path) -> None:
    """经纬度图层输入米制距离时，应自动使用本地米制投影而不是直接按度缓冲。"""
    geographic_layer: VectorLayer = VectorLayer.create(
        layer_id="geographic_points",
        name="经纬度点",
        features=(Feature(fid=1, geometry=Point(0, 0), attributes={}),),
        crs=CRS.from_epsg(4326),
    )
    writer: RecordingDataWriter = RecordingDataWriter()
    application: GisApplication = GisApplication(
        InMemoryDataReader(geographic_layer),
        data_writer=writer,
    )
    application.open_data(Path("geographic_points.geojson"))

    application.buffer_analysis(
        make_geometry_request(
            "geographic_points",
            tmp_path / "geographic_buffer.geojson",
            1.0,
            distance_unit="kilometer",
        )
    )

    output_layer = cast(VectorLayer, writer.layer)
    bounds = output_layer.features[0].geometry.bounds
    assert 0.008 < abs(bounds[0]) < 0.010
    assert 0.008 < abs(bounds[1]) < 0.010
    assert output_layer.crs == CRS.from_epsg(4326)


def test_line_buffer_supports_left_side_only(tmp_path: Path) -> None:
    """线图层选择左侧缓冲时，结果应只位于有方向线的左侧。"""
    writer: RecordingDataWriter = RecordingDataWriter()
    application: GisApplication = GisApplication(
        InMemoryDataReader(make_line_layer()),
        data_writer=writer,
    )
    application.open_data(Path("lines.geojson"))

    application.buffer_analysis(
        make_geometry_request("lines", tmp_path / "left.geojson", 2.0, side_type="left")
    )

    output_layer = cast(VectorLayer, writer.layer)
    bounds = output_layer.features[0].geometry.bounds
    assert bounds[1] == pytest.approx(0.0)
    assert bounds[3] == pytest.approx(2.0)


def test_polygon_buffer_supports_outside_only_and_negative_distance(tmp_path: Path) -> None:
    """面图层应支持仅外侧环带和负距离向内缓冲。"""
    writer: RecordingDataWriter = RecordingDataWriter()
    application: GisApplication = GisApplication(
        InMemoryDataReader(make_polygon_layer()),
        data_writer=writer,
    )
    application.open_data(Path("polygons.geojson"))

    outside_result = application.buffer_analysis(
        make_geometry_request(
            "polygons",
            tmp_path / "outside.geojson",
            2.0,
            side_type="outside",
        )
    )
    outside_layer = cast(VectorLayer, writer.layer)
    assert not outside_layer.features[0].geometry.contains(Point(5, 5))
    assert outside_result.feature_count == 1

    inward_result = application.buffer_analysis(
        make_geometry_request("polygons", tmp_path / "inward.geojson", -2.0)
    )
    inward_layer = cast(VectorLayer, writer.layer)
    assert inward_result.feature_count == 1
    assert inward_layer.features[0].geometry.area < 100.0


def test_mixed_geometry_layer_is_rejected_before_buffering(tmp_path: Path) -> None:
    """混合几何图层不能被错误地套用某一种点线面参数。"""
    mixed_layer: VectorLayer = VectorLayer.create(
        layer_id="mixed",
        name="混合图层",
        features=(
            Feature(fid=1, geometry=Point(0, 0), attributes={}),
            Feature(fid=2, geometry=LineString([(0, 0), (1, 0)]), attributes={}),
        ),
        crs=CRS.from_epsg(3857),
    )
    application: GisApplication = GisApplication(
        InMemoryDataReader(mixed_layer),
        data_writer=RecordingDataWriter(),
    )
    application.open_data(Path("mixed.geojson"))

    with pytest.raises(InvalidBufferParameters, match="混合几何"):
        application.buffer_analysis(
            make_geometry_request("mixed", tmp_path / "mixed_buffer.geojson", 1.0)
        )


def test_buffer_analysis_writes_readable_geojson(tmp_path: Path) -> None:
    """缓冲区应用用例应生成采用 WGS84 坐标且可重新读取的 GeoJSON。"""
    application: GisApplication = GisApplication(
        InMemoryDataReader(make_layer()),
        data_writer=GeoPandasVectorWriter(),
    )
    application.open_data(Path("points.geojson"))
    output_path: Path = tmp_path / "written_buffers.geojson"

    application.buffer_analysis(make_request(output_path))

    dataframe: gpd.GeoDataFrame = gpd.read_file(output_path)
    assert len(dataframe) == 2
    assert dataframe.crs == CRS.from_epsg(4326)
    assert dataframe.geometry.is_valid.all()
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
    assert len(application.analysis_runs) == 1
    failed_run = application.analysis_runs[0]
    assert failed_run.status == "failed"
    assert failed_run.message == "测试写出失败"
    assert failed_run.input_layer_ids == ("points",)
    assert failed_run.output_layer_ids == ()


def test_clear_analysis_history_keeps_result_layer(tmp_path: Path) -> None:
    """清除分析历史时应保留已加入地图的结果图层。"""
    application: GisApplication = GisApplication(
        InMemoryDataReader(make_layer()),
        data_writer=RecordingDataWriter(),
    )
    application.open_data(Path("points.geojson"))
    application.buffer_analysis(make_request(tmp_path / "buffers.geojson"))

    application.clear_analysis_history()

    assert application.analysis_runs == ()
    assert [layer.name for layer in application.snapshot().layers] == ["测试点", "点缓冲区"]
    assert application.is_modified is True


def test_buffer_request_rejects_non_positive_distance(tmp_path: Path) -> None:
    """缓冲距离为零时应在执行前被拒绝；面图层的负距离由几何类型校验。"""
    with pytest.raises(InvalidBufferParameters, match="大于零"):
        BufferRequest(
            input_layer_id="points",
            output_path=tmp_path / "buffers.geojson",
            output_layer_name="结果",
            distance=0,
        )
