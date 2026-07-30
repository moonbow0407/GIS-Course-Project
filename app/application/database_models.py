"""数据库用例使用的不可变数据对象。"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class DatabaseConnectionConfig:
    """描述一次 PostgreSQL 数据库连接所需的参数。

    密码只在当前进程内使用，``repr`` 不显示密码，调用方也不应将整个对象
    写入工程文件或日志。
    """

    host: str
    port: int
    database: str
    username: str
    password: str = field(repr=False)

    def __post_init__(self) -> None:
        """校验连接参数，尽早阻止生成明显无效的连接请求。"""
        if not self.host.strip():
            raise ValueError("数据库主机不能为空。")
        if not 1 <= self.port <= 65535:
            raise ValueError("数据库端口必须在 1 到 65535 之间。")
        if not self.database.strip():
            raise ValueError("数据库名不能为空。")
        if not self.username.strip():
            raise ValueError("数据库用户名不能为空。")


@dataclass(frozen=True, slots=True)
class DatabaseServerInfo:
    """表示数据库连接测试成功后返回的服务端信息。"""

    database: str
    username: str
    postgres_version: str
    postgis_version: str


@dataclass(frozen=True, slots=True)
class DatabaseLayerInfo:
    """表示数据库中的一个矢量图层目录项。"""

    layer_id: int
    name: str
    geometry_type: str
    crs: str | None
    srid: int
    feature_count: int
    created_at: datetime | None
