"""根据文件类型自动选择空间数据读取适配器。"""

from pathlib import Path

from pyproj import CRS

from app.application.errors import UnsupportedVectorFormat
from app.domain.spatial_layer import SpatialLayer
from app.infrastructure.file_io.geopandas_vector_reader import GeoPandasVectorReader
from app.infrastructure.file_io.rasterio_raster_reader import RasterioRasterReader


class AutoDataReader:
    """统一判断矢量或栅格文件，并转发到对应读取器。"""

    # 矢量扩展名：交给 GeoPandas 读取。
    VECTOR_SUFFIXES: frozenset[str] = GeoPandasVectorReader.SUPPORTED_SUFFIXES

    # 栅格扩展名：交给 Rasterio 读取。
    RASTER_SUFFIXES: frozenset[str] = RasterioRasterReader.SUPPORTED_SUFFIXES

    def __init__(self) -> None:
        """创建自动分派器及矢量、栅格读取适配器。"""
        self._vector_reader: GeoPandasVectorReader = GeoPandasVectorReader()
        self._raster_reader: RasterioRasterReader = RasterioRasterReader()

    def read(self, path: Path, target_crs: CRS | None = None) -> SpatialLayer:
        """根据扩展名自动识别并读取矢量或栅格数据。

        参数:
            path: 待读取的空间数据文件路径。
            target_crs: 地图显示坐标系；为空时保留源数据坐标系。

        返回:
            由具体读取器构造的矢量或栅格领域图层。

        异常:
            UnsupportedVectorFormat: 扩展名无法识别时抛出。
            ApplicationError: 具体读取器无法正常读取数据时抛出其对应错误。
        """
        suffix: str = path.suffix.lower()
        if suffix in self.VECTOR_SUFFIXES:
            return self._vector_reader.read(path, target_crs)
        if suffix in self.RASTER_SUFFIXES:
            return self._raster_reader.read(path, target_crs)
        raise UnsupportedVectorFormat(f"无法识别空间数据类型：{suffix or '无扩展名'}")
