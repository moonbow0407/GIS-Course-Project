"""数据库应用服务测试。"""

from dataclasses import dataclass

import pytest
from pyproj import CRS
from shapely.geometry import Point

from app.application.database_models import (
    DatabaseConnectionConfig,
    DatabaseLayerInfo,
    DatabaseServerInfo,
)
from app.application.database_service import DatabaseService
from app.application.errors import DatabaseConnectionFailed, DatabaseNotConnected
from app.domain.feature import Feature
from app.domain.vector_layer import VectorLayer


@dataclass
class FakeDatabaseGateway:
    """用于验证应用服务连接生命周期的内存网关。"""

    should_fail: bool = False
    closed: bool = False

    def test_connection(self) -> DatabaseServerInfo:
        """返回固定连接信息，或模拟连接失败。"""
        if self.should_fail:
            raise DatabaseConnectionFailed("测试连接失败")
        return DatabaseServerInfo("gis", "tester", "PostgreSQL", "3.4")

    def ensure_schema(self) -> None:
        """满足数据库端口。"""

    def list_layers(self) -> tuple[DatabaseLayerInfo, ...]:
        """满足数据库端口。"""
        return ()

    def import_layer(self, layer: VectorLayer) -> DatabaseLayerInfo:
        """满足数据库端口。"""
        return DatabaseLayerInfo(1, layer.name, "point", "EPSG:4326", 4326, 1, None)

    def load_layer(self, layer_id: int, target_crs: CRS | None = None) -> VectorLayer:
        """满足数据库端口。"""
        return VectorLayer.create(
            layer_id=f"db-layer-{layer_id}",
            name="测试图层",
            features=(Feature(1, Point(0, 0), {}),),
            crs=target_crs or CRS.from_epsg(4326),
        )

    def close(self) -> None:
        """记录关闭操作。"""
        self.closed = True


def make_config() -> DatabaseConnectionConfig:
    """创建测试连接配置。"""
    return DatabaseConnectionConfig(
        host="localhost",
        port=5432,
        database="gis",
        username="tester",
        password="secret",
    )


def test_connection_config_validates_port_and_does_not_expose_password() -> None:
    """连接配置应拒绝非法端口，repr 不应泄露密码。"""
    config: DatabaseConnectionConfig = make_config()

    assert "secret" not in repr(config)
    with pytest.raises(ValueError, match="端口"):
        DatabaseConnectionConfig("localhost", 0, "gis", "tester", "secret")


def test_database_service_requires_connection_for_database_operations() -> None:
    """未连接时列表、模式初始化、导入和加载都应返回统一异常。"""
    service: DatabaseService = DatabaseService(lambda config: FakeDatabaseGateway())
    layer: VectorLayer = VectorLayer.create(
        name="点图层",
        features=(Feature(1, Point(0, 0), {}),),
        crs=CRS.from_epsg(4326),
    )

    with pytest.raises(DatabaseNotConnected):
        service.ensure_schema()
    with pytest.raises(DatabaseNotConnected):
        service.list_layers()
    with pytest.raises(DatabaseNotConnected):
        service.import_layer(layer)
    with pytest.raises(DatabaseNotConnected):
        service.load_layer(1)


def test_successful_connection_replaces_previous_gateway_only_after_testing() -> None:
    """新连接测试成功后才替换旧连接，避免错误配置破坏可用连接。"""
    gateways: list[FakeDatabaseGateway] = []

    def factory(config: DatabaseConnectionConfig) -> FakeDatabaseGateway:
        gateway: FakeDatabaseGateway = FakeDatabaseGateway(
            should_fail=config.host == "bad-host"
        )
        gateways.append(gateway)
        return gateway

    service: DatabaseService = DatabaseService(factory)
    server_info: DatabaseServerInfo = service.connect(make_config())
    assert server_info.postgis_version == "3.4"
    assert service.is_connected

    with pytest.raises(DatabaseConnectionFailed):
        service.connect(
            DatabaseConnectionConfig("bad-host", 5432, "gis", "tester", "secret")
        )

    assert not gateways[0].closed
    assert gateways[1].closed

    service.connect(make_config())
    assert gateways[0].closed
    assert not gateways[2].closed
