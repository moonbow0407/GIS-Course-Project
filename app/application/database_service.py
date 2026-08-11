"""数据库用例服务。"""

from collections.abc import Callable

from pyproj import CRS

from app.application.database_models import (
    DatabaseConnectionConfig,
    DatabaseLayerInfo,
    DatabaseServerInfo,
)
from app.application.errors import (
    ApplicationError,
    DatabaseConnectionFailed,
    DatabaseNotConnected,
)
from app.application.ports import DatabaseGateway
from app.domain.vector_layer import VectorLayer

DatabaseGatewayFactory = Callable[[DatabaseConnectionConfig], DatabaseGateway]


class DatabaseService:
    """管理当前数据库连接，并编排数据库图层导入和加载用例。"""

    def __init__(self, gateway_factory: DatabaseGatewayFactory) -> None:
        """注入数据库网关工厂，保持应用层不依赖具体驱动实现。"""
        self._gateway_factory: DatabaseGatewayFactory = gateway_factory
        self._gateway: DatabaseGateway | None = None
        self._connection_identity: str | None = None

    @property
    def is_connected(self) -> bool:
        """返回当前是否存在已经通过连接测试的数据库网关。"""
        return self._gateway is not None

    @property
    def connection_identity(self) -> str | None:
        """返回不含密码的连接身份，用于工程匹配数据库图层引用。"""
        return self._connection_identity

    def connect(self, config: DatabaseConnectionConfig) -> DatabaseServerInfo:
        """创建并测试一个新的数据库连接。

        新连接测试成功后才替换旧连接，避免错误配置覆盖当前可用连接。
        """
        candidate: DatabaseGateway | None = None
        try:
            candidate = self._gateway_factory(config)
            server_info: DatabaseServerInfo = candidate.test_connection()
        except ApplicationError:
            if candidate is not None:
                candidate.close()
            raise
        except Exception as error:
            if candidate is not None:
                candidate.close()
            raise DatabaseConnectionFailed("数据库连接失败，请检查连接参数和服务状态。") from error

        previous: DatabaseGateway | None = self._gateway
        self._gateway = candidate
        self._connection_identity = self._identity(config)
        if previous is not None:
            previous.close()
        return server_info

    def disconnect(self) -> None:
        """断开当前数据库连接；重复断开视为幂等操作。"""
        gateway: DatabaseGateway | None = self._gateway
        self._gateway = None
        self._connection_identity = None
        if gateway is not None:
            gateway.close()

    def ensure_schema(self) -> None:
        """初始化数据库模式。"""
        self._require_gateway().ensure_schema()

    def list_layers(self) -> tuple[DatabaseLayerInfo, ...]:
        """返回当前数据库中的矢量图层目录。"""
        return self._require_gateway().list_layers()

    def import_layer(self, layer: VectorLayer) -> DatabaseLayerInfo:
        """将内存矢量图层导入当前数据库。"""
        return self._require_gateway().import_layer(layer)

    def load_layer(self, layer_id: int, target_crs: CRS | None = None) -> VectorLayer:
        """从当前数据库加载图层，并按需统一到目标 CRS。"""
        return self._require_gateway().load_layer(layer_id, target_crs)

    def _require_gateway(self) -> DatabaseGateway:
        """返回当前网关；未连接时统一抛出应用层异常。"""
        if self._gateway is None:
            raise DatabaseNotConnected("请先连接 PostgreSQL/PostGIS 数据库。")
        return self._gateway

    @staticmethod
    def _identity(config: DatabaseConnectionConfig) -> str:
        """构造可写入工程的连接身份，不包含密码。"""
        return f"{config.host}:{config.port}/{config.database}?user={config.username}"
