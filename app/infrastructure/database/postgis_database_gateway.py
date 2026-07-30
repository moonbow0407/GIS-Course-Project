"""基于 SQLAlchemy Core 和 psycopg 的 PostGIS 数据库网关。"""

import json
import math
from collections.abc import Callable, Mapping
from datetime import date, datetime
from typing import Any, cast

from pyproj import CRS, Transformer
from shapely import wkt
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError

from app.application.database_models import (
    DatabaseConnectionConfig,
    DatabaseLayerInfo,
    DatabaseServerInfo,
)
from app.application.errors import (
    DatabaseConnectionFailed,
    DatabaseImportFailed,
    DatabaseLayerNotFound,
    DatabaseListFailed,
    DatabaseLoadFailed,
    DatabaseSchemaFailed,
)
from app.domain.feature import AttributeValue, Feature
from app.domain.vector_layer import VectorLayer

EngineFactory = Callable[[URL], Engine]


class PostgisDatabaseGateway:
    """将统一矢量图层映射到 PostgreSQL/PostGIS 的基础设施适配器。"""

    _SCHEMA_STATEMENTS: tuple[str, ...] = (
        "CREATE EXTENSION IF NOT EXISTS postgis",
        """
        CREATE TABLE IF NOT EXISTS public.gis_layers (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            geometry_type TEXT NOT NULL,
            crs TEXT,
            srid INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS public.gis_features (
            id BIGSERIAL PRIMARY KEY,
            layer_id BIGINT NOT NULL
                REFERENCES public.gis_layers(id) ON DELETE CASCADE,
            geom geometry(Geometry) NOT NULL,
            attrs_json JSONB NOT NULL DEFAULT '{}'::jsonb
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_gis_features_layer_id
            ON public.gis_features(layer_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_gis_features_geom
            ON public.gis_features USING GIST (geom)
        """,
    )

    _CONNECTION_TEST_SQL: str = """
        SELECT
            current_database() AS database_name,
            current_user AS username,
            version() AS postgres_version,
            PostGIS_Version() AS postgis_version
    """

    _LIST_LAYERS_SQL: str = """
        SELECT
            layers.id,
            layers.name,
            layers.geometry_type,
            layers.crs,
            layers.srid,
            layers.created_at,
            COUNT(features.id) AS feature_count
        FROM public.gis_layers AS layers
        LEFT JOIN public.gis_features AS features
            ON features.layer_id = layers.id
        GROUP BY
            layers.id,
            layers.name,
            layers.geometry_type,
            layers.crs,
            layers.srid,
            layers.created_at
        ORDER BY layers.created_at DESC, layers.id DESC
    """

    def __init__(
        self,
        config: DatabaseConnectionConfig,
        engine_factory: EngineFactory = create_engine,
    ) -> None:
        """根据连接配置创建惰性 SQLAlchemy 引擎。"""
        self.config: DatabaseConnectionConfig = config
        try:
            self._engine: Engine = engine_factory(self.build_url(config))
        except SQLAlchemyError as error:
            raise DatabaseConnectionFailed("数据库连接配置无效。") from error

    @staticmethod
    def build_url(config: DatabaseConnectionConfig) -> URL:
        """构造安全的 SQLAlchemy URL，不手工拼接密码或特殊字符。"""
        return URL.create(
            drivername="postgresql+psycopg",
            username=config.username,
            password=config.password,
            host=config.host,
            port=config.port,
            database=config.database,
        )

    def test_connection(self) -> DatabaseServerInfo:
        """验证 PostgreSQL 连接和 PostGIS 函数是否可用。"""
        try:
            with self._engine.connect() as connection:
                row: Mapping[str, Any] = cast(
                    Mapping[str, Any],
                    connection.execute(text(self._CONNECTION_TEST_SQL)).mappings().one(),
                )
        except SQLAlchemyError as error:
            raise DatabaseConnectionFailed(
                "无法连接 PostgreSQL 或检测到 PostGIS 不可用。"
            ) from error

        return DatabaseServerInfo(
            database=str(row["database_name"]),
            username=str(row["username"]),
            postgres_version=str(row["postgres_version"]),
            postgis_version=str(row["postgis_version"]),
        )

    def ensure_schema(self) -> None:
        """在一个事务中幂等创建 PostGIS 扩展、表和索引。"""
        try:
            with self._engine.begin() as connection:
                for statement in self._SCHEMA_STATEMENTS:
                    connection.execute(text(statement))
        except SQLAlchemyError as error:
            raise DatabaseSchemaFailed(
                "数据库模式初始化失败，请确认账号具有 PostGIS 和建表权限。"
            ) from error

    def list_layers(self) -> tuple[DatabaseLayerInfo, ...]:
        """返回数据库图层目录；模式尚未初始化时返回空元组。"""
        try:
            with self._engine.connect() as connection:
                table_exists: object = connection.execute(
                    text("SELECT to_regclass('public.gis_layers')")
                ).scalar_one_or_none()
                if table_exists is None:
                    return ()
                rows: list[Mapping[str, Any]] = [
                    cast(Mapping[str, Any], row)
                    for row in connection.execute(text(self._LIST_LAYERS_SQL))
                    .mappings()
                    .all()
                ]
        except SQLAlchemyError as error:
            raise DatabaseListFailed("数据库图层目录读取失败。") from error

        return tuple(
            DatabaseLayerInfo(
                layer_id=int(row["id"]),
                name=str(row["name"]),
                geometry_type=str(row["geometry_type"]),
                crs=self._optional_string(row.get("crs")),
                srid=int(row["srid"]),
                feature_count=int(row["feature_count"]),
                created_at=self._optional_datetime(row.get("created_at")),
            )
            for row in rows
        )

    def import_layer(self, layer: VectorLayer) -> DatabaseLayerInfo:
        """将图层及全部要素放入一个原子事务。"""
        if not layer.features:
            raise DatabaseImportFailed("不能导入不包含要素的矢量图层。")

        self.ensure_schema()
        srid: int = self._resolve_srid(layer.crs)
        crs_text: str | None = layer.crs.to_string() if layer.crs is not None else None
        feature_payloads: tuple[tuple[str, str], ...] = tuple(
            (feature.geometry.wkt, self._encode_attributes(feature.attributes))
            for feature in layer.features
        )

        try:
            with self._engine.begin() as connection:
                layer_row: Mapping[str, Any] = cast(
                    Mapping[str, Any],
                    connection.execute(
                        text(
                            """
                            INSERT INTO public.gis_layers
                                (name, geometry_type, crs, srid)
                            VALUES (:name, :geometry_type, :crs, :srid)
                            RETURNING id, created_at
                            """
                        ),
                        {
                            "name": layer.name,
                            "geometry_type": layer.geometry_family.value,
                            "crs": crs_text,
                            "srid": srid,
                        },
                    ).mappings().one(),
                )
                database_layer_id: int = int(layer_row["id"])
                for wkt_text, attributes_json in feature_payloads:
                    connection.execute(
                        text(
                            """
                            INSERT INTO public.gis_features
                                (layer_id, geom, attrs_json)
                            VALUES (
                                :layer_id,
                                ST_GeomFromText(:wkt, :srid),
                                CAST(:attrs_json AS JSONB)
                            )
                            """
                        ),
                        {
                            "layer_id": database_layer_id,
                            "wkt": wkt_text,
                            "srid": srid,
                            "attrs_json": attributes_json,
                        },
                    )
        except DatabaseImportFailed:
            raise
        except SQLAlchemyError as error:
            raise DatabaseImportFailed(
                f"图层“{layer.name}”导入失败，事务已回滚。"
            ) from error

        return DatabaseLayerInfo(
            layer_id=database_layer_id,
            name=layer.name,
            geometry_type=layer.geometry_family.value,
            crs=crs_text,
            srid=srid,
            feature_count=len(layer.features),
            created_at=self._optional_datetime(layer_row.get("created_at")),
        )

    def load_layer(self, layer_id: int, target_crs: CRS | None = None) -> VectorLayer:
        """从数据库读取图层，并在内存中按需重投影。"""
        try:
            with self._engine.connect() as connection:
                layer_row: Mapping[str, Any] | None = cast(
                    Mapping[str, Any] | None,
                    connection.execute(
                        text(
                            """
                            SELECT id, name, crs, srid
                            FROM public.gis_layers
                            WHERE id = :layer_id
                            """
                        ),
                        {"layer_id": layer_id},
                    ).mappings().one_or_none(),
                )
                if layer_row is None:
                    raise DatabaseLayerNotFound(f"数据库图层不存在：{layer_id}")
                feature_rows: list[Mapping[str, Any]] = [
                    cast(Mapping[str, Any], row)
                    for row in connection.execute(
                        text(
                            """
                            SELECT id, ST_AsText(geom) AS wkt, attrs_json
                            FROM public.gis_features
                            WHERE layer_id = :layer_id
                            ORDER BY id
                            """
                        ),
                        {"layer_id": layer_id},
                    )
                    .mappings()
                    .all()
                ]
        except DatabaseLayerNotFound:
            raise
        except SQLAlchemyError as error:
            raise DatabaseLoadFailed(f"数据库图层读取失败：{layer_id}") from error

        try:
            source_crs: CRS | None = self._parse_crs(
                layer_row.get("crs"),
                int(layer_row.get("srid") or 0),
                layer_id,
            )
            features: tuple[Feature, ...] = tuple(
                Feature(
                    fid=int(row["id"]),
                    geometry=self._parse_wkt(row.get("wkt"), layer_id),
                    attributes=self._decode_attributes(row.get("attrs_json"), layer_id),
                )
                for row in feature_rows
            )
        except DatabaseLoadFailed:
            raise
        except (TypeError, ValueError) as error:
            raise DatabaseLoadFailed(f"数据库图层要素字段格式错误：{layer_id}") from error

        if not features:
            raise DatabaseLoadFailed(f"数据库图层没有可加载的要素：{layer_id}")

        resolved_features: tuple[Feature, ...] = features
        resolved_crs: CRS | None = source_crs
        if target_crs is not None and source_crs != target_crs:
            if source_crs is None:
                raise DatabaseLoadFailed(
                    f"数据库图层“{layer_row['name']}”没有 CRS，无法转换到目标坐标系。"
                )
            resolved_features = self._reproject_features(features, source_crs, target_crs)
            resolved_crs = target_crs

        try:
            return VectorLayer.create(
                layer_id=f"db-layer-{layer_id}",
                name=str(layer_row["name"]),
                features=resolved_features,
                crs=resolved_crs,
                source_layer_name=f"gis_layers:{layer_id}",
                database_layer_id=layer_id,
            )
        except ValueError as error:
            raise DatabaseLoadFailed(f"数据库图层无法构造为矢量图层：{layer_id}") from error

    def close(self) -> None:
        """释放 SQLAlchemy 引擎的连接池资源。"""
        self._engine.dispose()

    @staticmethod
    def _encode_attributes(attributes: Mapping[str, AttributeValue]) -> str:
        """将领域属性转换为 JSONB 可接受的 JSON 文本。"""
        try:
            return json.dumps(
                dict(attributes),
                ensure_ascii=False,
                allow_nan=False,
                default=PostgisDatabaseGateway._json_default,
            )
        except (TypeError, ValueError) as error:
            raise DatabaseImportFailed("图层属性包含无法写入 JSONB 的值。") from error

    @staticmethod
    def _json_default(value: object) -> str:
        """把领域支持的日期类型编码为可移植的 ISO 字符串。"""
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        raise TypeError(f"不支持的属性类型：{type(value).__name__}")

    @staticmethod
    def _decode_attributes(value: object, layer_id: int) -> Mapping[str, AttributeValue]:
        """校验 JSONB 结果并转换为领域属性映射。"""
        raw_value: object = value
        if isinstance(raw_value, str):
            try:
                raw_value = json.loads(raw_value)
            except (TypeError, ValueError) as error:
                raise DatabaseLoadFailed(f"数据库图层属性 JSON 无法解析：{layer_id}") from error
        if not isinstance(raw_value, Mapping):
            raise DatabaseLoadFailed(f"数据库图层属性必须是 JSON 对象：{layer_id}")

        attributes: dict[str, AttributeValue] = {}
        for key, item in raw_value.items():
            if not isinstance(key, str):
                raise DatabaseLoadFailed(f"数据库图层属性字段名不是字符串：{layer_id}")
            attributes[key] = PostgisDatabaseGateway._normalize_json_value(item, layer_id)
        return attributes

    @staticmethod
    def _normalize_json_value(value: object, layer_id: int) -> AttributeValue:
        """将 JSON 标量映射到领域属性；复杂值保留为 JSON 文本。"""
        if value is None or isinstance(value, (str, bool, int)):
            return cast(AttributeValue, value)
        if isinstance(value, float):
            if not math.isfinite(value):
                raise DatabaseLoadFailed(f"数据库图层属性包含非有限数值：{layer_id}")
            return value
        if isinstance(value, (list, dict)):
            try:
                return json.dumps(value, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError) as error:
                raise DatabaseLoadFailed(f"数据库图层复杂属性无法转换：{layer_id}") from error
        raise DatabaseLoadFailed(f"数据库图层属性类型不受支持：{layer_id}")

    @staticmethod
    def _parse_wkt(value: object, layer_id: int) -> BaseGeometry:
        """解析数据库返回的 WKT 几何文本。"""
        if not isinstance(value, str) or not value.strip():
            raise DatabaseLoadFailed(f"数据库图层几何为空：{layer_id}")
        try:
            return wkt.loads(value)
        except Exception as error:
            raise DatabaseLoadFailed(f"数据库图层几何 WKT 无法解析：{layer_id}") from error

    @staticmethod
    def _parse_crs(value: object, srid: int, layer_id: int) -> CRS | None:
        """优先解析 CRS 文本；文本缺失时使用数据库 SRID 恢复坐标系。"""
        if value is None:
            if srid <= 0:
                return None
            try:
                return CRS.from_epsg(srid)
            except Exception as error:
                raise DatabaseLoadFailed(f"数据库图层 SRID 无法识别：{layer_id}") from error
        if not isinstance(value, str) or not value.strip():
            raise DatabaseLoadFailed(f"数据库图层 CRS 字段格式错误：{layer_id}")
        try:
            return CRS.from_user_input(value)
        except Exception as error:
            raise DatabaseLoadFailed(f"数据库图层 CRS 无法识别：{layer_id}") from error

    @staticmethod
    def _reproject_features(
        features: tuple[Feature, ...],
        source_crs: CRS,
        target_crs: CRS,
    ) -> tuple[Feature, ...]:
        """在数据库读取边界完成坐标转换，保持属性和数据库 FID 不变。"""
        try:
            transformer: Transformer = Transformer.from_crs(
                source_crs,
                target_crs,
                always_xy=True,
            )
            return tuple(
                Feature(
                    fid=feature.fid,
                    geometry=transform(transformer.transform, feature.geometry),
                    attributes=feature.attributes,
                )
                for feature in features
            )
        except Exception as error:
            raise DatabaseLoadFailed("数据库图层无法转换到目标坐标系。") from error

    @staticmethod
    def _resolve_srid(crs: CRS | None) -> int:
        """返回 PostGIS 可用的 EPSG SRID；无法确定时使用 0。"""
        if crs is None:
            return 0
        try:
            epsg: int | None = crs.to_epsg()
        except Exception:
            epsg = None
        return int(epsg) if epsg is not None else 0

    @staticmethod
    def _optional_string(value: object) -> str | None:
        """将数据库可空文本列转换为应用层值。"""
        return value if isinstance(value, str) else None

    @staticmethod
    def _optional_datetime(value: object) -> datetime | None:
        """将数据库可空时间列转换为应用层值。"""
        return value if isinstance(value, datetime) else None
