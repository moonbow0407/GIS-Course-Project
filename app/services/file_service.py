"""文件读写服务占位实现。"""

from app.models.layer import Layer
from app.models.raster_layer import RasterLayer


class FileService:
    """后续接入 GeoPandas、Rasterio 的统一文件服务接口。"""

    def read_vector(self, path: str) -> Layer:
        raise NotImplementedError(f"矢量读取功能正在开发中：{path}")

    def read_raster(self, path: str) -> RasterLayer:
        raise NotImplementedError(f"栅格读取功能正在开发中：{path}")

    def save_vector(self, layer: Layer, path: str) -> None:
        raise NotImplementedError(f"矢量保存功能正在开发中：{layer.name} -> {path}")

    def save_raster(self, layer: RasterLayer, path: str) -> None:
        raise NotImplementedError(f"栅格保存功能正在开发中：{layer.name} -> {path}")
