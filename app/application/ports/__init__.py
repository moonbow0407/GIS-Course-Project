"""应用层访问外部数据源所需的端口接口。"""

from pathlib import Path
from typing import Protocol

from pyproj import CRS

from app.application.database_models import (
    DatabaseLayerInfo,
    DatabaseServerInfo,
)
from app.application.project_models import ProjectManifest
from app.domain.feature import FeatureId
from app.domain.spatial_layer import SpatialLayer
from app.domain.vector_layer import VectorLayer


class VectorReader(Protocol):
    """定义将外部矢量数据读取为统一领域图层的能力。"""

    def read(
        self,
        path: Path,
        target_crs: CRS | None = None,
        layer_name: str | None = None,
        source_crs_override: CRS | None = None,
    ) -> VectorLayer:
        """读取指定文件，可定义源 CRS，并可选转换到目标 CRS。"""
        ...


class DataReader(Protocol):
    """定义自动读取外部矢量或栅格数据的能力。"""

    def read(
        self,
        path: Path,
        target_crs: CRS | None = None,
        layer_name: str | None = None,
        source_crs_override: CRS | None = None,
    ) -> SpatialLayer:
        """读取指定空间数据，可定义源 CRS，并可选转换到目标 CRS。"""
        ...


class DataWriter(Protocol):
    """定义将统一空间图层写入本地文件的能力。"""

    def write(
        self,
        layer: SpatialLayer,
        path: Path,
        selected_feature_ids: tuple[FeatureId, ...] = (),
        layer_name: str | None = None,
    ) -> None:
        """写出图层；矢量选择集非空时仅写出选中要素。"""
        ...


class ProjectStore(Protocol):
    """定义工程清单的持久化能力。"""

    def load(self, path: Path) -> ProjectManifest:
        """读取并校验一个工程清单。"""
        ...

    def save(self, path: Path, manifest: ProjectManifest) -> None:
        """将工程清单原子写入指定路径。"""
        ...


class DatabaseGateway(Protocol):
    """定义应用层访问 PostgreSQL/PostGIS 所需的数据库端口。"""

    def test_connection(self) -> DatabaseServerInfo:
        """测试连接并确认服务端启用了 PostGIS。"""
        ...

    def ensure_schema(self) -> None:
        """幂等创建数据库模块所需的扩展、表和索引。"""
        ...

    def list_layers(self) -> tuple[DatabaseLayerInfo, ...]:
        """读取数据库中的矢量图层目录。"""
        ...

    def import_layer(self, layer: VectorLayer) -> DatabaseLayerInfo:
        """以事务方式导入一个内存矢量图层。"""
        ...

    def load_layer(
        self,
        layer_id: int,
        target_crs: CRS | None = None,
    ) -> VectorLayer:
        """读取数据库图层，并按需转换到目标 CRS。"""
        ...

    def close(self) -> None:
        """释放数据库连接池资源。"""
        ...
