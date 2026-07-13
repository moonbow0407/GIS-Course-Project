"""矢量图层模型。"""

from dataclasses import dataclass, field

from app.models.feature import Feature


@dataclass(slots=True)
class Layer:
    """内存矢量图层。"""

    name: str
    crs: str | None = None
    geometry_type: str | None = None
    features: list[Feature] = field(default_factory=list)
    srid: int | None = None
    visible: bool = True
    style: dict[str, str] = field(default_factory=dict)
