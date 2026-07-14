"""GIS 应用功能统一入口。"""

from pathlib import Path

from shapely.geometry import Point, Polygon

from app.application.errors import LayerNotFound
from app.application.ports import VectorReader
from app.application.results import (
    LayerSnapshot,
    OpenVectorResult,
    SelectedFeature,
    SelectionResult,
    WorkspaceSnapshot,
)
from app.domain.feature import Feature, FeatureId
from app.domain.map_document import MapDocument
from app.domain.vector_layer import VectorLayer


class GisApplication:
    """通过较小公开接口统一编排图层管理和空间查询流程。"""

    # 矢量读取端口：由启动组装模块注入真实或测试适配器。
    vector_reader: VectorReader

    def __init__(
        self,
        vector_reader: VectorReader,
        document: MapDocument | None = None,
    ) -> None:
        """使用矢量读取端口和可选地图文档初始化应用入口。"""
        self.vector_reader = vector_reader

        # 地图文档：作为图层、显隐、活动状态和选择集的唯一事实来源。
        self._document: MapDocument = document or MapDocument()

    def open_vector(self, path: Path) -> OpenVectorResult:
        """读取矢量文件，并在成功后原子地加入地图文档。"""
        layer: VectorLayer = self.vector_reader.read(path, self._document.display_crs)
        self._document.add_layer(layer)
        warning: str | None = "数据未声明坐标参考系统。" if layer.crs is None else None
        return OpenVectorResult(
            layer_id=layer.layer_id,
            snapshot=self.snapshot(),
            warning=warning,
        )

    def remove_layer(self, layer_id: str) -> WorkspaceSnapshot:
        """移除指定图层并返回最新工作区快照。"""
        try:
            self._document.remove_layer(layer_id)
        except KeyError as error:
            raise LayerNotFound(f"图层不存在：{layer_id}") from error
        return self.snapshot()

    def move_layer(self, layer_id: str, target_index: int) -> WorkspaceSnapshot:
        """调整指定图层的显示顺序并返回最新快照。"""
        try:
            self._document.move_layer(layer_id, target_index)
        except KeyError as error:
            raise LayerNotFound(f"图层不存在：{layer_id}") from error
        return self.snapshot()

    def set_layer_visibility(self, layer_id: str, visible: bool) -> WorkspaceSnapshot:
        """设置指定图层显隐状态并返回最新快照。"""
        try:
            self._document.set_layer_visibility(layer_id, visible)
        except KeyError as error:
            raise LayerNotFound(f"图层不存在：{layer_id}") from error
        return self.snapshot()

    def set_active_layer(self, layer_id: str) -> WorkspaceSnapshot:
        """设置活动图层并返回最新工作区快照。"""
        try:
            self._document.set_active_layer(layer_id)
        except KeyError as error:
            raise LayerNotFound(f"图层不存在：{layer_id}") from error
        return self.snapshot()

    def select_point(self, point: Point, tolerance: float) -> SelectionResult:
        """选择可见图层中容差范围内优先级最高的最近要素。"""
        if tolerance < 0:
            raise ValueError("点选容差不能小于零。")
        self._document.clear_selection()
        ordered_layers: tuple[VectorLayer, ...] = self._point_query_order()
        layer: VectorLayer
        for layer in ordered_layers:
            if not self._document.is_visible(layer.layer_id):
                continue
            nearest_feature: Feature | None = None
            nearest_distance: float = float("inf")
            feature: Feature
            for feature in layer.features:
                if feature.geometry.is_empty:
                    continue
                distance: float = float(feature.geometry.distance(point))
                if distance <= tolerance and distance < nearest_distance:
                    nearest_feature = feature
                    nearest_distance = distance
            if nearest_feature is not None:
                self._document.set_selection(layer.layer_id, (nearest_feature.fid,))
                selected_feature: SelectedFeature = SelectedFeature(
                    layer_id=layer.layer_id,
                    layer_name=layer.name,
                    feature=nearest_feature,
                )
                return SelectionResult(features=(selected_feature,), snapshot=self.snapshot())
        return SelectionResult(features=(), snapshot=self.snapshot())

    def select_rectangle(self, rectangle: Polygon) -> SelectionResult:
        """选择全部可见图层中与给定矩形相交的有效要素。"""
        self._document.clear_selection()
        selected_features: list[SelectedFeature] = []
        layer: VectorLayer
        for layer in self._document.layers:
            if not self._document.is_visible(layer.layer_id):
                continue
            feature_ids: list[FeatureId] = []
            feature: Feature
            for feature in layer.features:
                if not feature.geometry.is_empty and feature.geometry.intersects(rectangle):
                    feature_ids.append(feature.fid)
                    selected_features.append(
                        SelectedFeature(
                            layer_id=layer.layer_id,
                            layer_name=layer.name,
                            feature=feature,
                        )
                    )
            self._document.set_selection(layer.layer_id, tuple(feature_ids))
        return SelectionResult(features=tuple(selected_features), snapshot=self.snapshot())

    def clear_selection(self) -> SelectionResult:
        """清除全部图层选择并返回空选择结果。"""
        self._document.clear_selection()
        return SelectionResult(features=(), snapshot=self.snapshot())

    def snapshot(self) -> WorkspaceSnapshot:
        """构造供界面一次性刷新的不可变工作区快照。"""
        layer_snapshots: tuple[LayerSnapshot, ...] = tuple(
            LayerSnapshot(
                layer=layer,
                visible=self._document.is_visible(layer.layer_id),
                selected_feature_ids=self._document.selected_feature_ids(layer.layer_id),
            )
            for layer in self._document.layers
        )
        return WorkspaceSnapshot(
            layers=layer_snapshots,
            active_layer_id=self._document.active_layer_id,
            display_crs=self._document.display_crs,
        )

    def _point_query_order(self) -> tuple[VectorLayer, ...]:
        """返回活动图层优先、其余图层自顶向下的点选顺序。"""
        active_layer_id: str | None = self._document.active_layer_id
        active_layers: tuple[VectorLayer, ...] = tuple(
            layer for layer in self._document.layers if layer.layer_id == active_layer_id
        )
        remaining_layers: tuple[VectorLayer, ...] = tuple(
            layer for layer in reversed(self._document.layers) if layer.layer_id != active_layer_id
        )
        return active_layers + remaining_layers
