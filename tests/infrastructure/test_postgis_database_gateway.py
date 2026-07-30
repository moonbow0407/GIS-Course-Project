"""PostGIS 数据库网关的无服务单元测试。"""

from collections.abc import Mapping
from typing import Any, cast

import pytest
from pyproj import CRS
from shapely.geometry import Point
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.application.database_models import DatabaseConnectionConfig
from app.application.errors import DatabaseImportFailed
from app.domain.feature import Feature
from app.domain.vector_layer import VectorLayer
from app.infrastructure.database.postgis_database_gateway import (
    PostgisDatabaseGateway,
)


class FakeResult:
    """模拟 SQLAlchemy Result 的最小读取接口。"""

    def __init__(self, rows: list[Mapping[str, Any]] | None = None, scalar: object = None):
        self._rows: list[Mapping[str, Any]] = rows or []
        self._scalar: object = scalar

    def mappings(self) -> "FakeResult":
        """返回自身以模拟 mappings()。"""
        return self

    def one(self) -> Mapping[str, Any]:
        """返回唯一行。"""
        return self._rows[0]

    def one_or_none(self) -> Mapping[str, Any] | None:
        """返回唯一行或空值。"""
        return self._rows[0] if self._rows else None

    def all(self) -> list[Mapping[str, Any]]:
        """返回全部行。"""
        return self._rows

    def scalar_one_or_none(self) -> object:
        """返回可空标量。"""
        return self._scalar


class FakeConnection:
    """按 SQL 片段返回预设结果的连接。"""

    def __init__(self, mode: str, fail_on_feature_insert: bool = False) -> None:
        self.mode: str = mode
        self.fail_on_feature_insert: bool = fail_on_feature_insert
        self.executed_sql: list[str] = []

    def __enter__(self) -> "FakeConnection":
        """支持 with engine.connect()。"""
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        """支持连接上下文退出。"""

    def execute(self, statement: object, params: object = None) -> FakeResult:
        """根据 SQL 语句返回测试结果。"""
        sql: str = str(statement)
        self.executed_sql.append(sql)
        if self.fail_on_feature_insert and "INSERT INTO public.gis_features" in sql:
            raise SQLAlchemyError("simulated feature insert failure")
        if "to_regclass" in sql:
            return FakeResult(scalar=None)
        if "current_database()" in sql:
            return FakeResult(
                rows=[
                    {
                        "database_name": "gis",
                        "username": "tester",
                        "postgres_version": "PostgreSQL",
                        "postgis_version": "3.4",
                    }
                ]
            )
        if "SELECT id, name, crs, srid" in sql:
            return FakeResult(
                rows=[{"id": 7, "name": "数据库点图层", "crs": "EPSG:4326", "srid": 4326}]
            )
        if "ST_AsText(geom)" in sql:
            return FakeResult(
                rows=[
                    {
                        "id": 11,
                        "wkt": "POINT (0 0)",
                        "attrs_json": {"名称": "原点", "标签": ["A", "B"]},
                    }
                ]
            )
        if "RETURNING id, created_at" in sql:
            return FakeResult(rows=[{"id": 9, "created_at": None}])
        return FakeResult()


class FakeTransaction(FakeConnection):
    """记录事务是否提交或回滚。"""

    def __init__(self, fail_on_feature_insert: bool = False) -> None:
        super().__init__("transaction", fail_on_feature_insert)
        self.committed: bool = False
        self.rolled_back: bool = False

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        """根据异常记录事务结果。"""
        if exc_type is None:
            self.committed = True
        else:
            self.rolled_back = True


class FakeEngine:
    """模拟 SQLAlchemy Engine。"""

    def __init__(self, fail_on_feature_insert: bool = False) -> None:
        self.connection: FakeConnection = FakeConnection("connection")
        self.transactions: list[FakeTransaction] = []
        self.disposed: bool = False

    def connect(self) -> FakeConnection:
        """返回普通查询连接。"""
        return self.connection

    def begin(self) -> FakeTransaction:
        """返回事务连接。"""
        transaction: FakeTransaction = FakeTransaction(
            fail_on_feature_insert=len(self.transactions) == 1
        )
        self.transactions.append(transaction)
        return transaction

    def dispose(self) -> None:
        """记录资源释放。"""
        self.disposed = True


def make_config() -> DatabaseConnectionConfig:
    """创建测试连接配置。"""
    return DatabaseConnectionConfig("localhost", 5432, "gis", "tester", "p@ss word")


def make_layer() -> VectorLayer:
    """创建点图层测试数据。"""
    return VectorLayer.create(
        name="测试图层",
        features=(Feature(101, Point(0, 0), {"名称": "原点"}),),
        crs=CRS.from_epsg(4326),
    )


def make_gateway(engine: FakeEngine) -> PostgisDatabaseGateway:
    """使用假引擎创建网关。"""
    return PostgisDatabaseGateway(make_config(), lambda url: cast(Engine, engine))


def test_build_url_escapes_special_password_and_hides_it_when_rendered() -> None:
    """SQLAlchemy URL 应安全保存特殊字符密码且支持脱敏渲染。"""
    url = PostgisDatabaseGateway.build_url(make_config())

    assert url.drivername == "postgresql+psycopg"
    assert url.password == "p@ss word"
    assert "p@ss word" not in url.render_as_string(hide_password=True)
    assert "tester:***@localhost:5432/gis" in url.render_as_string(hide_password=True)


def test_load_layer_reprojects_geometry_and_preserves_attributes() -> None:
    """数据库图层加载应按目标 CRS 转换几何并保留属性。"""
    engine: FakeEngine = FakeEngine()
    layer = make_gateway(engine).load_layer(7, CRS.from_epsg(3857))

    assert layer.layer_id == "db-layer-7"
    assert layer.crs == CRS.from_epsg(3857)
    assert layer.features[0].fid == 11
    assert layer.features[0].geometry.x == pytest.approx(0.0)
    assert layer.features[0].attributes["名称"] == "原点"
    assert layer.features[0].attributes["标签"] == '["A", "B"]'


def test_list_layers_returns_empty_before_schema_initialization() -> None:
    """数据库模式不存在时，图层目录应为空而不是伪造数据。"""
    engine: FakeEngine = FakeEngine()

    assert make_gateway(engine).list_layers() == ()


def test_import_failure_rolls_back_whole_transaction() -> None:
    """任一要素写入失败时，图层元数据和已写要素都必须回滚。"""
    engine: FakeEngine = FakeEngine(fail_on_feature_insert=True)

    with pytest.raises(DatabaseImportFailed, match="事务已回滚"):
        make_gateway(engine).import_layer(make_layer())

    import_transaction: FakeTransaction = engine.transactions[-1]
    assert import_transaction.rolled_back
    assert not import_transaction.committed
    assert any(
        "CREATE TABLE IF NOT EXISTS public.gis_layers" in sql
        for sql in engine.transactions[0].executed_sql
    )
