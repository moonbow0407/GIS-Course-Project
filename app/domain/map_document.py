"""地图文档聚合模型。"""

from pyproj import CRS

from app.domain.feature import FeatureId
from app.domain.raster_layer import RasterLayer
from app.domain.spatial_layer import SpatialLayer


class MapDocument:
    """统一管理地图中的图层顺序、活动状态、显隐状态和选择集。"""

    def __init__(self) -> None:
        # 图层列表：按照从底层到顶层的顺序保存地图中的图层。
        self._layers: list[SpatialLayer] = []

        # 图层显隐状态：按图层编号保存当前是否参与显示和查询。
        self._visibility: dict[str, bool] = {}

        # 活动图层编号：表示当前接收编辑和优先查询操作的图层。
        self._active_layer_id: str | None = None

        # 选择集合：按图层编号保存当前选中的要素编号。
        self._selection: dict[str, tuple[FeatureId, ...]] = {}

        # 显示坐标系：由首个已知坐标系图层建立，全部已知图层必须一致。
        self._display_crs: CRS | None = None

    @property
    def layers(self) -> tuple[SpatialLayer, ...]:
        """返回按显示顺序排列的只读图层元组。"""
        return tuple(self._layers)

    @property
    def active_layer_id(self) -> str | None:
        """返回当前活动图层编号。"""
        return self._active_layer_id

    @property
    def display_crs(self) -> CRS | None:
        """返回地图文档当前采用的显示坐标参考系统。"""
        return self._display_crs

    def add_layer(self, layer: SpatialLayer) -> None:
        """校验坐标系后将图层添加到地图文档顶层。"""
        if any(existing.layer_id == layer.layer_id for existing in self._layers):
            raise ValueError(f"图层编号已存在：{layer.layer_id}")
        self._validate_coordinate_reference_system(layer)

        self._layers.append(layer)
        self._visibility[layer.layer_id] = True
        self._selection[layer.layer_id] = ()
        if self._active_layer_id is None:
            self._active_layer_id = layer.layer_id
        if len(self._layers) == 1:
            self._display_crs = layer.crs

    def remove_layer(self, layer_id: str) -> SpatialLayer:
        """移除指定图层，并修复活动图层和选择状态。"""
        current_index: int = self._layer_index(layer_id)
        removed_layer: SpatialLayer = self._layers.pop(current_index)
        self._visibility.pop(layer_id, None)
        self._selection.pop(layer_id, None)

        if not self._layers:
            self._active_layer_id = None
            self._display_crs = None
        elif self._active_layer_id == layer_id:
            next_index: int = min(current_index, len(self._layers) - 1)
            self._active_layer_id = self._layers[next_index].layer_id
        return removed_layer

    def move_layer(self, layer_id: str, target_index: int) -> None:
        """将指定图层移动到有效的目标位置。"""
        if not 0 <= target_index < len(self._layers):
            raise IndexError(f"图层目标位置超出范围：{target_index}")
        current_index: int = self._layer_index(layer_id)
        layer: SpatialLayer = self._layers.pop(current_index)
        self._layers.insert(target_index, layer)

    def set_active_layer(self, layer_id: str) -> None:
        """将已存在的图层设置为活动图层。"""
        self._require_layer(layer_id)
        self._active_layer_id = layer_id

    def set_layer_visibility(self, layer_id: str, visible: bool) -> None:
        """设置图层显隐状态，并在隐藏时清除该图层选择。"""
        self._require_layer(layer_id)
        self._visibility[layer_id] = visible
        if not visible:
            self._selection[layer_id] = ()

    def is_visible(self, layer_id: str) -> bool:
        """返回指定图层当前是否可见。"""
        self._require_layer(layer_id)
        return self._visibility[layer_id]

    def set_selection(self, layer_id: str, feature_ids: tuple[FeatureId, ...]) -> None:
        """替换指定图层的要素选择集合。"""
        layer: SpatialLayer = self._require_layer(layer_id)
        if isinstance(layer, RasterLayer) and feature_ids:
            raise ValueError("栅格图层不包含可选择的矢量要素。")
        if isinstance(layer, RasterLayer):
            self._selection[layer_id] = ()
            return
        valid_feature_ids: set[FeatureId] = {feature.fid for feature in layer.features}
        if any(feature_id not in valid_feature_ids for feature_id in feature_ids):
            raise ValueError("选择集合包含不属于该图层的要素编号。")
        self._selection[layer_id] = tuple(dict.fromkeys(feature_ids))

    def selected_feature_ids(self, layer_id: str) -> tuple[FeatureId, ...]:
        """返回指定图层的已选要素编号；已删除图层返回空元组。"""
        return self._selection.get(layer_id, ())

    def clear_selection(self) -> None:
        """清除地图文档中全部图层的选择集合。"""
        layer_id: str
        for layer_id in self._selection:
            self._selection[layer_id] = ()

    def _validate_coordinate_reference_system(self, layer: SpatialLayer) -> None:
        """保证新增图层不会与现有地图文档坐标系静默冲突。"""
        if not self._layers:
            return
        if self._display_crs is None:
            if layer.crs is not None:
                raise ValueError("已存在未知坐标系图层，无法与未知坐标系安全叠加。")
            return
        if layer.crs is None or layer.crs != self._display_crs:
            raise ValueError("图层坐标参考系统不一致。")

    def _layer_index(self, layer_id: str) -> int:
        """返回指定图层的位置，不存在时抛出明确异常。"""
        index: int
        layer: SpatialLayer
        for index, layer in enumerate(self._layers):
            if layer.layer_id == layer_id:
                return index
        raise KeyError(f"图层不存在：{layer_id}")

    def _require_layer(self, layer_id: str) -> SpatialLayer:
        """返回指定图层，不存在时抛出明确异常。"""
        layer: SpatialLayer
        for layer in self._layers:
            if layer.layer_id == layer_id:
                return layer
        raise KeyError(f"图层不存在：{layer_id}")
