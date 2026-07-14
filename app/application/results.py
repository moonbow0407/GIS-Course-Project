"""应用层命令返回的不可变结果对象。"""

from dataclasses import dataclass
from typing import TypeAlias

from pyproj import CRS

from app.domain.feature import Feature, FeatureId
from app.domain.layer_style import GeometryFamily
from app.domain.raster_layer import RasterLayer
from app.domain.spatial_layer import SpatialLayer
from app.domain.vector_layer import Bounds, VectorLayer


@dataclass(frozen=True, slots=True)
class LayerSnapshot:
    """表示某一时刻供界面读取的单个图层状态。"""

    # 领域图层：保存只读要素、坐标系、范围和样式。
    layer: SpatialLayer

    # 显示状态：表示图层当前是否参与地图绘制和空间查询。
    visible: bool

    # 已选要素编号：保存该图层当前选择集的稳定编号。
    selected_feature_ids: tuple[FeatureId, ...]

    @property
    def layer_id(self) -> str:
        """返回图层稳定编号。"""
        return self.layer.layer_id

    @property
    def name(self) -> str:
        """返回图层显示名称。"""
        return self.layer.name

    @property
    def feature_count(self) -> int:
        """返回图层包含的要素数量。"""
        return len(self.layer.features) if isinstance(self.layer, VectorLayer) else 0

    @property
    def geometry_family(self) -> GeometryFamily | None:
        """返回矢量图层几何类别；栅格图层返回空值。"""
        return self.layer.geometry_family if isinstance(self.layer, VectorLayer) else None

    @property
    def is_raster(self) -> bool:
        """返回当前快照是否属于栅格图层。"""
        return isinstance(self.layer, RasterLayer)

    @property
    def bounds(self) -> Bounds:
        """返回图层空间范围。"""
        return self.layer.bounds


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    """表示界面刷新所需的完整地图工作区状态。"""

    # 图层快照：按照地图显示顺序保存全部图层状态。
    layers: tuple[LayerSnapshot, ...]

    # 活动图层编号：为空表示地图文档当前没有图层。
    active_layer_id: str | None

    # 显示坐标系：为空表示地图文档尚未建立已知坐标系。
    display_crs: CRS | None

    @property
    def selection_count(self) -> int:
        """返回全部图层当前选中要素的总数量。"""
        return sum(len(layer.selected_feature_ids) for layer in self.layers)


@dataclass(frozen=True, slots=True)
class SelectedFeature:
    """表示带有所属图层信息的选择结果要素。"""

    # 所属图层编号：用于定位渲染项和更新图层选择状态。
    layer_id: str

    # 所属图层名称：用于属性面板和用户提示显示。
    layer_name: str

    # 领域要素：包含被选中要素的几何和只读属性。
    feature: Feature


@dataclass(frozen=True, slots=True)
class SelectionResult:
    """表示空间选择命令返回的要素和最新工作区状态。"""

    # 选择要素：按照查询优先级保存全部命中要素。
    features: tuple[SelectedFeature, ...]

    # 工作区快照：包含选择命令执行后的完整图层状态。
    snapshot: WorkspaceSnapshot

    @property
    def count(self) -> int:
        """返回本次选择结果中的要素数量。"""
        return len(self.features)


@dataclass(frozen=True, slots=True)
class OpenDataResult:
    """表示打开矢量或栅格图层命令的结构化结果。"""

    # 新增图层编号：用于界面定位刚刚加载的图层。
    layer_id: str

    # 工作区快照：包含新增图层后的完整地图状态。
    snapshot: WorkspaceSnapshot

    # 用户警告：为空表示加载过程不需要额外提醒。
    warning: str | None = None


# 旧矢量结果名称：仅供既有调用代码兼容，新代码应使用 OpenDataResult。
OpenVectorResult: TypeAlias = OpenDataResult
