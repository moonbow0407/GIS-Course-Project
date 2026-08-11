"""地图文档聚合模型。"""

from pyproj import CRS

from app.domain.feature import FeatureId
from app.domain.raster_layer import RasterLayer
from app.domain.spatial_layer import SpatialLayer


class MapDocument:
    """统一管理所有图层顺序、活动状态、显隐状态、显示设置和选择集。"""

    def __init__(self) -> None:
        """创建不包含图层、坐标系和选择状态的空地图文档。"""
        # 图层列表：按照从底层到顶层的顺序保存地图中的图层。
        self._layers: list[SpatialLayer] = []

        # 图层显隐状态：按图层编号保存当前是否参与显示和查询。
        self._visibility: dict[str, bool] = {}

        # 图层次级透明度：按图层编号保存显示透明度，取值范围为零到一。
        self._opacity: dict[str, float] = {}

        # 图层混合模式：按图层编号保存合成模式键，默认值为 normal。
        self._blend_mode: dict[str, str] = {}

        # 图层显示比例尺范围：按图层编号保存 (最小比例, 最大比例)，空值表示不限。
        self._scale_range: dict[str, tuple[float | None, float | None]] = {}

        # 栅格显示重采样覆盖；为空时由显示服务按栅格类型自动选择。
        self._raster_display_resampling: dict[str, str | None] = {}

        # 活动图层编号：表示当前接收编辑和优先查询操作的图层。
        self._active_layer_id: str | None = None

        # 选择集合：按图层编号保存当前选中的要素编号。
        self._selection: dict[str, tuple[FeatureId, ...]] = {}

        # 显示坐标系：由首个已知 CRS 图层建立，也可以在有图层时主动指定。
        self._display_crs: CRS | None = None

        # 图层版本号：按图层编号保存内容版本，几何、样式或显示设置
        # 变更时递增，供显示缓存键判断缓存是否仍然有效。
        self._layer_revisions: dict[str, int] = {}

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

    def set_display_crs(self, crs: CRS) -> None:
        """设置地图显示坐标系；不改变任何领域图层的 CRS 和坐标值。"""
        if not self._layers:
            raise ValueError("空地图没有显示 CRS；请先加入已定义 CRS 的图层。")
        self._display_crs = crs

    def set_raster_display_resampling(
        self, layer_id: str, resampling: str | None
    ) -> None:
        """设置栅格显示重采样覆盖；空值恢复按数据类型自动选择。"""
        layer: SpatialLayer = self._require_layer(layer_id)
        if not isinstance(layer, RasterLayer):
            raise ValueError("显示重采样设置只能应用到栅格图层。")
        if resampling is not None and not resampling.strip():
            raise ValueError("显示重采样方法不能为空。")
        self._raster_display_resampling[layer_id] = resampling
        self._layer_revisions[layer_id] += 1

    def raster_display_resampling(self, layer_id: str) -> str | None:
        """返回栅格显示重采样覆盖；未设置时返回空值。"""
        self._require_layer(layer_id)
        return self._raster_display_resampling.get(layer_id)

    def add_layer(self, layer: SpatialLayer) -> None:
        """将图层添加到地图文档顶层，并按显示 CRS 规则校验未知 CRS。"""
        if any(existing.layer_id == layer.layer_id for existing in self._layers):
            raise ValueError(f"图层编号已存在：{layer.layer_id}")
        self._validate_coordinate_reference_system(layer)

        self._layers.append(layer)
        self._visibility[layer.layer_id] = True
        self._opacity[layer.layer_id] = 1.0
        self._blend_mode[layer.layer_id] = "normal"
        self._scale_range[layer.layer_id] = (None, None)
        self._raster_display_resampling[layer.layer_id] = None
        self._selection[layer.layer_id] = ()
        self._layer_revisions[layer.layer_id] = 1
        if self._active_layer_id is None:
            self._active_layer_id = layer.layer_id
        if self._display_crs is None and layer.crs is not None:
            self._display_crs = layer.crs

    def remove_layer(self, layer_id: str) -> SpatialLayer:
        """移除指定图层，并修复活动图层和选择状态。"""
        current_index: int = self._layer_index(layer_id)
        removed_layer: SpatialLayer = self._layers.pop(current_index)
        self._visibility.pop(layer_id, None)
        self._opacity.pop(layer_id, None)
        self._blend_mode.pop(layer_id, None)
        self._scale_range.pop(layer_id, None)
        self._raster_display_resampling.pop(layer_id, None)
        self._selection.pop(layer_id, None)
        self._layer_revisions.pop(layer_id, None)

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

    def replace_layer(self, layer: SpatialLayer) -> None:
        """以相同稳定编号替换图层内容，并保留顺序和工作区状态。"""
        current_index: int = self._layer_index(layer.layer_id)
        self._validate_replacement_coordinate_reference_system(layer)
        self._layers[current_index] = layer
        # 替换通常伴随几何、样式或 CRS 定义变化，必须使显示缓存失效。
        self._layer_revisions[layer.layer_id] += 1

    def layer_revision(self, layer_id: str) -> int:
        """返回图层当前内容版本号；几何、样式或显示设置变更时递增。"""
        self._require_layer(layer_id)
        return self._layer_revisions[layer_id]

    def set_active_layer(self, layer_id: str) -> None:
        """将已存在的图层设置为活动图层。"""
        self._require_layer(layer_id)
        self._active_layer_id = layer_id

    def clear_active_layer(self) -> None:
        """取消当前活动图层，使工作区无活动图层。"""
        self._active_layer_id = None

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

    def set_layer_opacity(self, layer_id: str, opacity: float) -> None:
        """设置指定图层的显示透明度，取值范围为零到一。"""
        self._require_layer(layer_id)
        if not 0.0 <= opacity <= 1.0:
            raise ValueError("图层透明度必须介于 0 和 1 之间。")
        self._opacity[layer_id] = opacity

    def layer_opacity(self, layer_id: str) -> float:
        """返回指定图层的显示透明度。"""
        self._require_layer(layer_id)
        return self._opacity[layer_id]

    def set_layer_blend_mode(self, layer_id: str, blend_mode: str) -> None:
        """设置指定图层的混合模式，取值必须为已支持的七个模式之一。"""
        self._require_layer(layer_id)
        valid_modes = frozenset({
            "normal", "multiply", "darken",
        })
        if blend_mode not in valid_modes:
            raise ValueError(
                f"不支持的混合模式：{blend_mode}。"
                f"可用模式：{', '.join(sorted(valid_modes))}"
            )
        self._blend_mode[layer_id] = blend_mode

    def layer_blend_mode(self, layer_id: str) -> str:
        """返回指定图层的混合模式。"""
        self._require_layer(layer_id)
        return self._blend_mode[layer_id]

    def set_layer_scale_range(
        self,
        layer_id: str,
        min_scale: float | None,
        max_scale: float | None,
    ) -> None:
        """设置图层的显示比例尺范围；空值表示对应方向不设限制。

        参数:
            min_scale: 显示该图层所需的最小视图比例（百分比），必须大于零。
            max_scale: 显示该图层所需的最大视图比例（百分比），必须大于零。
        """
        self._require_layer(layer_id)
        if min_scale is not None and min_scale <= 0:
            raise ValueError("最小显示比例必须大于零。")
        if max_scale is not None and max_scale <= 0:
            raise ValueError("最大显示比例必须大于零。")
        if (
            min_scale is not None
            and max_scale is not None
            and max_scale < min_scale
        ):
            raise ValueError("最大显示比例不能小于最小显示比例。")
        self._scale_range[layer_id] = (min_scale, max_scale)

    def layer_scale_range(self, layer_id: str) -> tuple[float | None, float | None]:
        """返回指定图层的最小和最大显示比例。"""
        self._require_layer(layer_id)
        return self._scale_range[layer_id]

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
        # 字典键既能去重又保留用户选择要素的原始顺序。
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
        """允许已知 CRS 共存，但拒绝无法安全定位到地图的未知 CRS 图层。"""
        if layer.crs is None:
            raise ValueError("图层未定义 CRS，无法加入地图显示。")

    def _validate_replacement_coordinate_reference_system(self, layer: SpatialLayer) -> None:
        """保证替换图层仍可在当前地图显示；不同已知 CRS 可以共存。"""
        if self._display_crs is not None and layer.crs is None:
            raise ValueError("替换图层未定义 CRS，无法加入地图显示。")

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
