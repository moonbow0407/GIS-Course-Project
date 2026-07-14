"""应用层访问外部数据源所需的端口接口。"""

from pathlib import Path
from typing import Protocol

from pyproj import CRS

from app.domain.vector_layer import VectorLayer


class VectorReader(Protocol):
    """定义将外部矢量数据读取为统一领域图层的能力。"""

    def read(self, path: Path, target_crs: CRS | None = None) -> VectorLayer:
        """读取指定文件，并在需要时转换到目标坐标参考系统。"""
        ...
