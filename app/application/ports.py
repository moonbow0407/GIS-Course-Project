"""应用层访问外部数据源所需的端口接口。"""

from pathlib import Path
from typing import Protocol

from pyproj import CRS

from app.domain.feature import FeatureId
from app.domain.spatial_layer import SpatialLayer
from app.domain.vector_layer import VectorLayer


class VectorReader(Protocol):
    """定义将外部矢量数据读取为统一领域图层的能力。"""

    def read(self, path: Path, target_crs: CRS | None = None) -> VectorLayer:
        """读取指定文件，并在需要时转换到目标坐标参考系统。"""
        ...


class DataReader(Protocol):
    """定义自动读取外部矢量或栅格数据的能力。"""

    def read(self, path: Path, target_crs: CRS | None = None) -> SpatialLayer:
        """读取指定空间数据，并在需要时转换到目标坐标参考系统。"""
        ...


class DataWriter(Protocol):
    """定义将统一空间图层写入本地文件的能力。"""

    def write(
        self,
        layer: SpatialLayer,
        path: Path,
        selected_feature_ids: tuple[FeatureId, ...] = (),
    ) -> None:
        """写出图层；矢量选择集非空时仅写出选中要素。"""
        ...
