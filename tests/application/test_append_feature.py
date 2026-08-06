"""向活动矢量图层追加要素的应用层测试。"""

from pathlib import Path

import numpy as np
import pytest
from affine import Affine
from pyproj import CRS
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry

from app.application.errors import (
    ApplicationError,
    DataWriteFailed,
    LayerNotFound,
)
from app.application.gis_application import GisApplication
from app.application.ports import DataWriter, VectorReader
from app.application.results import WorkspaceSnapshot
from app.domain.feature import Feature
from app.domain.map_document import MapDocument
from app.domain.raster_layer import RasterLayer
from app.domain.vector_layer import VectorLayer
from app.infrastructure.file_io.auto_reader import AutoDataReader
from app.infrastructure.file_io.auto_writer import AutoDataWriter
from app.infrastructure.file_io.geopandas_vector_writer import GeoPandasVectorWriter

CRS_4326: CRS = CRS.from_epsg(4326)


class InMemoryVectorReader(VectorReader):
    """返回预设图层或异常的内存矢量读取器。"""

    def __init__(self, layer: VectorLayer) -> None:
        self._layer: VectorLayer = layer
        self.last_target_crs: CRS | None = None

    def read(
        self, path: Path, target_crs: CRS | None = None, layer_name: str | None = None
    ) -> VectorLayer:
        self.last_target_crs = target_crs
        return self._layer


class RecordingDataWriter(DataWriter):
    """记录最近一次写出参数的内存写出器。"""

    def __init__(self) -> None:
        self.layer: VectorLayer | None = None
        self.path: Path | None = None
        self.selected_feature_ids: tuple[object, ...] = ()

    def write(
        self,
        layer: VectorLayer,
        path: Path,
        selected_feature_ids: tuple[object, ...] = (),
        layer_name: str | None = None,
    ) -> None:
        self.layer = layer
        self.path = path
        self.selected_feature_ids = selected_feature_ids


def _make_layer(
    layer_id: str,
    source_path: Path | None,
    geometry: BaseGeometry | None = None,
) -> VectorLayer:
    """构造带单个要素和可选数据文件路径的测试矢量图层。"""
    return VectorLayer.create(
        layer_id=layer_id,
        name="测试图层",
        features=(
            Feature(
                fid=1,
                geometry=geometry or Point(0, 0),
                attributes={"名称": "甲"},
            ),
        ),
        crs=CRS_4326,
        source_path=source_path,
    )


def _make_application(
    layer: VectorLayer,
    writer: RecordingDataWriter | None = None,
) -> GisApplication:
    """构造直接持有指定图层和写出端口的应用服务。"""
    document: MapDocument = MapDocument()
    document.add_layer(layer)
    return GisApplication(
        data_reader=InMemoryVectorReader(layer),
        data_writer=writer,
        document=document,
    )


def test_append_feature_adds_typed_attributes_and_increments_fid(
    tmp_path: Path,
) -> None:
    """追加要素应生成自增编号、保留属性并写回源文件。"""
    source: Path = tmp_path / "points.geojson"
    layer: VectorLayer = _make_layer("l1", source)
    writer: RecordingDataWriter = RecordingDataWriter()
    application: GisApplication = _make_application(layer, writer)

    snapshot: WorkspaceSnapshot = application.append_feature(
        "l1",
        Point(10, 10),
        {"名称": "乙", "等级": 2},
    )

    features = snapshot.layers[0].layer.features
    assert len(features) == 2
    assert features[1].fid == 2
    assert features[1].attributes == {"名称": "乙", "等级": 2}
    assert features[1].geometry.x == 10.0
    assert writer.layer is not None
    assert writer.layer.layer_id == "l1"
    assert writer.path == source
    assert writer.selected_feature_ids == ()


def test_append_feature_to_non_vector_layer_raises() -> None:
    """向栅格图层追加要素应报告应用错误。"""
    document: MapDocument = MapDocument()
    document.add_layer(
        RasterLayer.create(
            layer_id="raster",
            name="栅格",
            raster_data=np.ones((1, 2, 2), dtype=np.uint8),
            image_data=np.full((2, 2, 4), 255, dtype=np.uint8),
            valid_mask=np.ones((2, 2), dtype=np.bool_),
            transform=Affine(1, 0, 0, 0, -1, 2),
            crs=CRS_4326,
            bounds=(0, 0, 2, 2),
        )
    )
    application: GisApplication = GisApplication(
        data_reader=AutoDataReader(),
        data_writer=None,
        document=document,
    )

    with pytest.raises(ApplicationError, match="只能向矢量图层"):
        application.append_feature("raster", Point(0, 0), {})


def test_append_feature_with_mismatched_geometry_family_raises(tmp_path: Path) -> None:
    """几何类型与图层类别不符时应拒绝追加。"""
    source: Path = tmp_path / "routes.geojson"
    layer: VectorLayer = _make_layer(
        "l1", source, LineString([(0, 0), (1, 1)])
    )
    application: GisApplication = _make_application(
        layer, RecordingDataWriter()
    )

    with pytest.raises(ApplicationError, match="几何类型与图层"):
        application.append_feature("l1", Point(0, 0), {})


def test_append_feature_to_database_layer_raises() -> None:
    """没有本地数据文件的数据库图层应拒绝追加。"""
    layer: VectorLayer = _make_layer("l1", None)
    application: GisApplication = _make_application(
        layer, RecordingDataWriter()
    )

    with pytest.raises(ApplicationError, match="没有本地数据文件"):
        application.append_feature("l1", Point(0, 0), {})


def test_append_feature_to_unsupported_format_raises(tmp_path: Path) -> None:
    """源文件为 GeoPackage 等不支持写回的格式时应拒绝追加。"""
    source: Path = tmp_path / "zones.gpkg"
    layer: VectorLayer = _make_layer("l1", source)
    application: GisApplication = _make_application(
        layer, RecordingDataWriter()
    )

    with pytest.raises(ApplicationError, match="暂不支持追加要素"):
        application.append_feature("l1", Point(0, 0), {})


def test_append_feature_to_missing_layer_raises(tmp_path: Path) -> None:
    """目标图层不存在时应报告图层不存在。"""
    source: Path = tmp_path / "points.geojson"
    layer: VectorLayer = _make_layer("l1", source)
    application: GisApplication = _make_application(
        layer, RecordingDataWriter()
    )

    with pytest.raises(LayerNotFound):
        application.append_feature("missing", Point(0, 0), {})


def test_append_feature_without_writer_raises(tmp_path: Path) -> None:
    """未配置写出端口时应报告数据写出失败。"""
    source: Path = tmp_path / "points.geojson"
    layer: VectorLayer = _make_layer("l1", source)
    application: GisApplication = _make_application(layer, None)

    with pytest.raises(DataWriteFailed, match="写出服务"):
        application.append_feature("l1", Point(0, 0), {})


def test_append_feature_writes_to_real_geojson_file(tmp_path: Path) -> None:
    """真实 GeoJSON 图层追加要素后文件应多出一个要素。"""
    source: Path = tmp_path / "points.geojson"
    GeoPandasVectorWriter().write(_make_layer("l1", source), source)
    application: GisApplication = GisApplication(
        data_reader=AutoDataReader(),
        data_writer=AutoDataWriter(),
        document=MapDocument(),
    )
    application.open_data(source)

    application.append_feature(
        application.snapshot().active_layer_id or "",
        Point(10, 10),
        {"名称": "乙"},
    )

    reopened = AutoDataReader().read(source)
    assert len(reopened.features) == 2
    assert reopened.features[1].attributes == {"名称": "乙"}
