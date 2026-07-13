"""查询服务占位实现。"""

from typing import Any

from app.models.layer import Layer


class QueryService:
    """后续接入 Shapely/GeoPandas 的查询服务接口。"""

    def point_query(self, layer: Layer, point: Any, tolerance: float) -> list[Any]:
        raise NotImplementedError("点选查询功能正在开发中。")

    def rectangle_query(self, layer: Layer, rectangle: Any) -> list[Any]:
        raise NotImplementedError("框选查询功能正在开发中。")

    def attribute_query(self, layer: Layer, field: str, operator: str, value: Any) -> list[Any]:
        raise NotImplementedError("属性查询功能正在开发中。")
