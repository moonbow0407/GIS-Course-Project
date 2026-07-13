"""空间要素模型。"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Feature:
    """单个空间要素，geometry 暂以 Any 占位，后续可接入 Shapely 几何对象。"""

    fid: int
    geometry: Any
    attributes: dict[str, Any] = field(default_factory=dict)
