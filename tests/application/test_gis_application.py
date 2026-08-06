"""GIS 应用层公开接口测试。"""

from pathlib import Path

import pytest
from pyproj import CRS
from shapely.geometry import Point, box

from app.application.errors import NoActiveLayer, VectorReadFailed
from app.application.gis_application import GisApplication
from app.application.ports import DataWriter, VectorReader
from app.application.results import OpenVectorResult, SelectionResult, WorkspaceSnapshot
from app.domain.feature import Feature
from app.domain.spatial_layer import SpatialLayer
from app.domain.vector_layer import VectorLayer


class InMemoryVectorReader(VectorReader):
    """返回预设图层或异常的内存矢量读取器。"""

    # 预设图层：读取成功时返回的领域图层。
    layer: VectorLayer

    # 预设异常：不为空时在读取操作中抛出。
    error: Exception | None

    # 最近目标坐标系：记录应用层传入的显示坐标系。
    last_target_crs: CRS | None

    def __init__(self, layer: VectorLayer, error: Exception | None = None) -> None:
        """使用预设图层和可选异常初始化读取器。"""
        self.layer = layer
        self.error = error
        self.last_target_crs = None

    def read(self, path: Path, target_crs: CRS | None = None) -> VectorLayer:
        """返回预设图层，并记录调用方提供的目标坐标系。"""
        del path
        self.last_target_crs = target_crs
        if self.error is not None:
            raise self.error
        return self.layer


class RecordingDataWriter(DataWriter):
    """记录应用层传入的导出图层、路径和选择集。"""

    def __init__(self) -> None:
        """创建尚未执行写入的记录器。"""
        self.layer: SpatialLayer | None = None
        self.path: Path | None = None
        self.selected_feature_ids: tuple[str | int, ...] = ()

    def write(
        self,
        layer: SpatialLayer,
        path: Path,
        selected_feature_ids: tuple[str | int, ...] = (),
    ) -> None:
        """记录单次写入请求。"""
        self.layer = layer
        self.path = path
        self.selected_feature_ids = selected_feature_ids


def make_layer(layer_id: str, point_x: float = 0.0, point_y: float = 0.0) -> VectorLayer:
    """创建包含两个测试点的 WGS84 矢量图层。"""
    features: tuple[Feature, ...] = (
        Feature(fid=1, geometry=Point(point_x, point_y), attributes={"名称": "甲"}),
        Feature(fid=2, geometry=Point(point_x + 10, point_y + 10), attributes={"名称": "乙"}),
    )
    return VectorLayer.create(
        layer_id=layer_id,
        name=layer_id,
        features=features,
        crs=CRS.from_epsg(4326),
    )


def test_open_vector_returns_workspace_snapshot() -> None:
    """打开矢量文件后应返回包含真实图层状态的不可变快照。"""
    reader: InMemoryVectorReader = InMemoryVectorReader(make_layer("roads"))
    application: GisApplication = GisApplication(data_reader=reader)

    result: OpenVectorResult = application.open_vector(Path("roads.geojson"))

    assert result.layer_id == "roads"
    assert result.snapshot.active_layer_id == "roads"
    assert result.snapshot.layers[0].feature_count == 2
    assert result.snapshot.layers[0].visible is True
    assert reader.last_target_crs is None


def test_reader_failure_keeps_document_unchanged() -> None:
    """文件读取失败时不能向地图文档写入部分状态。"""
    error: VectorReadFailed = VectorReadFailed("文件损坏")
    reader: InMemoryVectorReader = InMemoryVectorReader(make_layer("roads"), error=error)
    application: GisApplication = GisApplication(data_reader=reader)

    with pytest.raises(VectorReadFailed, match="文件损坏"):
        application.open_vector(Path("broken.geojson"))

    assert application.snapshot().layers == ()


def test_layer_commands_return_synchronized_snapshot() -> None:
    """图层显隐、激活、排序和删除命令应返回同步快照。"""
    first_reader: InMemoryVectorReader = InMemoryVectorReader(make_layer("roads"))
    application: GisApplication = GisApplication(data_reader=first_reader)
    application.open_vector(Path("roads.geojson"))
    application.data_reader = InMemoryVectorReader(make_layer("rivers"))
    application.open_vector(Path("rivers.geojson"))

    application.set_active_layer("roads")
    application.set_layer_visibility("rivers", False)
    moved_snapshot: WorkspaceSnapshot = application.move_layer("roads", 1)
    removed_snapshot: WorkspaceSnapshot = application.remove_layer("roads")

    assert [layer.layer_id for layer in moved_snapshot.layers] == ["rivers", "roads"]
    assert moved_snapshot.layers[0].visible is False
    assert removed_snapshot.active_layer_id == "rivers"
    assert [layer.layer_id for layer in removed_snapshot.layers] == ["rivers"]


def test_point_selection_returns_nearest_feature_within_tolerance() -> None:
    """点选应返回容差内最近的要素并更新选择数量。"""
    reader: InMemoryVectorReader = InMemoryVectorReader(make_layer("points"))
    application: GisApplication = GisApplication(data_reader=reader)
    application.open_vector(Path("points.geojson"))

    selection: SelectionResult = application.select_point(Point(0.2, 0.1), tolerance=1.0)

    assert selection.count == 1
    assert selection.features[0].feature.fid == 1
    assert selection.features[0].feature.attributes["名称"] == "甲"


def test_rectangle_selection_returns_all_visible_intersections() -> None:
    """框选应返回全部可见图层中与矩形相交的要素。"""
    first_reader: InMemoryVectorReader = InMemoryVectorReader(make_layer("first"))
    application: GisApplication = GisApplication(data_reader=first_reader)
    application.open_vector(Path("first.geojson"))
    application.data_reader = InMemoryVectorReader(make_layer("second", 0.5, 0.5))
    application.open_vector(Path("second.geojson"))

    selection: SelectionResult = application.select_rectangle(box(-1, -1, 1, 1))

    assert selection.count == 2
    assert {feature.layer_id for feature in selection.features} == {"first", "second"}


def test_clear_selection_returns_empty_result() -> None:
    """清除选择后应返回空选择结果和零计数快照。"""
    reader: InMemoryVectorReader = InMemoryVectorReader(make_layer("points"))
    application: GisApplication = GisApplication(data_reader=reader)
    application.open_vector(Path("points.geojson"))
    application.select_point(Point(0, 0), tolerance=1.0)

    selection: SelectionResult = application.clear_selection()

    assert selection.features == ()
    assert selection.count == 0
    assert selection.snapshot.selection_count == 0


def test_export_active_vector_uses_selection_when_present(tmp_path: Path) -> None:
    """活动矢量图层存在选择集时应只把选中要素交给写入端口。"""
    reader: InMemoryVectorReader = InMemoryVectorReader(make_layer("points"))
    writer: RecordingDataWriter = RecordingDataWriter()
    application: GisApplication = GisApplication(data_reader=reader, data_writer=writer)
    application.open_vector(Path("points.geojson"))
    application.select_point(Point(0, 0), tolerance=1.0)
    output_path: Path = tmp_path / "selected.geojson"

    result = application.export_active_layer(output_path)

    assert writer.layer is not None
    assert writer.layer.layer_id == "points"
    assert writer.path == output_path
    assert writer.selected_feature_ids == (1,)
    assert result.path == output_path.resolve()
    assert result.exported_feature_count == 1


def test_export_active_vector_without_selection_uses_all_features(tmp_path: Path) -> None:
    """活动矢量图层没有选择集时应导出全部要素。"""
    reader: InMemoryVectorReader = InMemoryVectorReader(make_layer("points"))
    writer: RecordingDataWriter = RecordingDataWriter()
    application: GisApplication = GisApplication(data_reader=reader, data_writer=writer)
    application.open_vector(Path("points.geojson"))

    result = application.export_active_layer(tmp_path / "all.geojson")

    assert writer.selected_feature_ids == ()
    assert result.exported_feature_count == 2


def test_export_without_active_layer_is_rejected(tmp_path: Path) -> None:
    """空工作区不能执行导出。"""
    writer: RecordingDataWriter = RecordingDataWriter()
    application: GisApplication = GisApplication(
        data_reader=InMemoryVectorReader(make_layer("unused")),
        data_writer=writer,
    )

    with pytest.raises(NoActiveLayer, match="活动图层"):
        application.export_active_layer(tmp_path / "empty.geojson")
