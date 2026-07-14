"""空间图层公共类型。"""

from typing import TypeAlias

from app.domain.raster_layer import RasterLayer
from app.domain.vector_layer import VectorLayer

SpatialLayer: TypeAlias = VectorLayer | RasterLayer
