"""根据领域图层类型自动分派空间数据写入器。"""

from pathlib import Path

from app.domain.feature import FeatureId
from app.domain.raster_layer import RasterLayer
from app.domain.spatial_layer import SpatialLayer
from app.domain.vector_layer import VectorLayer
from app.infrastructure.file_io.geopandas_vector_writer import GeoPandasVectorWriter
from app.infrastructure.file_io.rasterio_raster_writer import RasterioRasterWriter


class AutoDataWriter:
    """把矢量和栅格图层转发到对应基础设施写入器。"""

    def __init__(self) -> None:
        """创建默认矢量和栅格写入适配器。"""
        self._vector_writer: GeoPandasVectorWriter = GeoPandasVectorWriter()
        self._raster_writer: RasterioRasterWriter = RasterioRasterWriter()

    def write(
        self,
        layer: SpatialLayer,
        path: Path,
        selected_feature_ids: tuple[FeatureId, ...] = (),
    ) -> None:
        """根据图层类型调用对应写入器。"""
        if isinstance(layer, VectorLayer):
            self._vector_writer.write(layer, path, selected_feature_ids)
            return
        if isinstance(layer, RasterLayer):
            self._raster_writer.write(layer, path)
            return
        raise TypeError(f"不支持的空间图层类型：{type(layer).__name__}")
