"""GIS 应用入口与数据库服务联调测试。"""

from dataclasses import dataclass

import pytest
from pyproj import CRS
from shapely.geometry import Point

from app.application.database_models import (
    DatabaseConnectionConfig,
    DatabaseLayerInfo,
    DatabaseServerInfo,
)
from app.application.errors import DatabaseNotConfigured, DatabaseNotConnected
from app.application.gis_application import GisApplication
from app.application.results import OpenDataResult
from app.domain.feature import Feature
from app.domain.map_document import MapDocument
from app.domain.vector_layer import VectorLayer
from app.infrastructure.file_io.auto_reader import AutoDataReader


@dataclass
class FakeDatabaseService:
    """验证 GIS 应用入口数据库编排的内存服务。"""

    is_connected: bool = True
    imported_layer: VectorLayer | None = None
    loaded_target_crs: CRS | None = None

    def connect(self, config: DatabaseConnectionConfig) -> DatabaseServerInfo:
        """模拟连接成功。"""
        self.is_connected = True
        return DatabaseServerInfo("gis", config.username, "PostgreSQL", "3.4")

    def disconnect(self) -> None:
        """模拟断开连接。"""
        self.is_connected = False

    def list_layers(self) -> tuple[DatabaseLayerInfo, ...]:
        """模拟数据库目录。"""
        if not self.is_connected:
            raise DatabaseNotConnected("未连接")
        return (DatabaseLayerInfo(8, "数据库点图层", "point", "EPSG:4326", 4326, 1, None),)

    def import_layer(self, layer: VectorLayer) -> DatabaseLayerInfo:
        """记录导入图层。"""
        if not self.is_connected:
            raise DatabaseNotConnected("未连接")
        self.imported_layer = layer
        return DatabaseLayerInfo(8, layer.name, "point", "EPSG:4326", 4326, 1, None)

    def load_layer(self, layer_id: int, target_crs: CRS | None = None) -> VectorLayer:
        """返回一个数据库图层，并记录应用传入的目标 CRS。"""
        if not self.is_connected:
            raise DatabaseNotConnected("未连接")
        self.loaded_target_crs = target_crs
        return VectorLayer.create(
            layer_id=f"db-layer-{layer_id}",
            name="数据库点图层",
            features=(Feature(101, Point(1, 2), {"来源": "数据库"}),),
            crs=target_crs or CRS.from_epsg(4326),
            database_layer_id=layer_id,
        )


def make_layer() -> VectorLayer:
    """创建用于数据库导入的活动矢量图层。"""
    return VectorLayer.create(
        layer_id="source",
        name="源点图层",
        features=(Feature(1, Point(0, 0), {}),),
        crs=CRS.from_epsg(4326),
    )


def test_database_import_uses_active_vector_layer() -> None:
    """应用层导入用例应读取当前活动矢量图层，而不是让界面直接访问网关。"""
    service: FakeDatabaseService = FakeDatabaseService()
    document: MapDocument = MapDocument()
    document.add_layer(make_layer())
    application: GisApplication = GisApplication(
        AutoDataReader(),
        document=document,
        database_service=service,  # type: ignore[arg-type]
    )

    result: DatabaseLayerInfo = application.import_active_layer_to_database()

    assert result.layer_id == 8
    assert service.imported_layer is not None
    assert service.imported_layer.layer_id == "source"


def test_database_load_uses_current_display_crs_and_adds_layer() -> None:
    """数据库图层加载应请求当前显示 CRS，并通过工作区规则加入地图。"""
    service: FakeDatabaseService = FakeDatabaseService()
    document: MapDocument = MapDocument()
    document.set_display_crs(CRS.from_epsg(3857))
    application: GisApplication = GisApplication(
        AutoDataReader(),
        document=document,
        database_service=service,  # type: ignore[arg-type]
    )

    result: OpenDataResult = application.load_database_layer(8)

    assert result.layer_id == "db-layer-8"
    assert service.loaded_target_crs == CRS.from_epsg(3857)
    assert application.snapshot().layers[0].layer.name == "数据库点图层"
    assert application.snapshot().display_crs == CRS.from_epsg(3857)

    application.set_display_crs(CRS.from_epsg(4326))

    assert service.loaded_target_crs == CRS.from_epsg(4326)
    assert application.snapshot().layers[0].layer.layer_id == "db-layer-8"
    assert application.snapshot().display_crs == CRS.from_epsg(4326)


def test_database_methods_fail_explicitly_when_service_is_not_configured() -> None:
    """未装配数据库服务时，应用入口应给出明确异常而不是静默失败。"""
    application: GisApplication = GisApplication(AutoDataReader())

    with pytest.raises(DatabaseNotConfigured):
        application.list_database_layers()
