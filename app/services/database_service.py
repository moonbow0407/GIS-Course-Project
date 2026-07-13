"""数据库服务占位实现。"""


class DatabaseService:
    """后续接入 PostgreSQL/PostGIS 的数据库服务接口。"""

    def __init__(self) -> None:
        self.connected = False

    def connect(self, connection_url: str) -> None:
        raise NotImplementedError(f"数据库连接功能正在开发中：{connection_url}")

    def disconnect(self) -> None:
        self.connected = False

    def load_layers(self) -> list[str]:
        raise NotImplementedError("从数据库加载图层功能正在开发中。")

    def import_current_layer(self, layer_name: str) -> None:
        raise NotImplementedError(f"导入图层到数据库功能正在开发中：{layer_name}")
