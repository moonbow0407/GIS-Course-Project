"""栅格图层模型。"""

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class RasterLayer:
    """内存栅格图层，bands 暂以 Any 占位，后续可接入 NumPy 数组。"""

    name: str
    crs: str | None = None
    transform: Any | None = None
    bands: Any | None = None
    width: int = 0
    height: int = 0
    visible: bool = True
