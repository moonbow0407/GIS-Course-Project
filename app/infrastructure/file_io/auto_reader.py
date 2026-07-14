"""根据文件类型自动选择空间数据读取适配器。"""

from pathlib import Path

from pyproj import CRS

from app.application.errors import UnsupportedVectorFormat
from app.domain.vector_layer import VectorLayer
from app.infrastructure.file_io.geopandas_vector_reader import GeoPandasVectorReader


class AutoDataReader:
    """统一判断矢量或栅格文件，并转发到对应读取器。"""

    # 矢量扩展名：交给 GeoPandas 读取。
    VECTOR_SUFFIXES: frozenset[str] = frozenset({".shp", ".geojson", ".json"})

    # 栅格扩展名：识别后提示功能尚未接入，不伪造读取结果。
    RASTER_SUFFIXES: frozenset[str] = frozenset({".tif", ".tiff", ".img", ".dem"})

    def __init__(self) -> None:
        """创建自动分派器及矢量读取适配器。"""
        self._vector_reader: GeoPandasVectorReader = GeoPandasVectorReader()

    def read(self, path: Path, target_crs: CRS | None = None) -> VectorLayer:
        """根据扩展名自动识别数据类型并读取当前支持的矢量数据。"""
        suffix: str = path.suffix.lower()
        if suffix in self.VECTOR_SUFFIXES:
            return self._vector_reader.read(path, target_crs)
        if suffix in self.RASTER_SUFFIXES:
            raise UnsupportedVectorFormat("已识别为栅格数据，但栅格读取功能尚未接入。")
        raise UnsupportedVectorFormat(f"无法识别空间数据类型：{suffix or '无扩展名'}")
