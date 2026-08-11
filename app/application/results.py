"""应用层命令返回的不可变结果对象。"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from pyproj import CRS

from app.application.display_models import DisplayPayload
from app.application.project_models import AnalysisRun, MapViewState
from app.domain.feature import Feature, FeatureId
from app.domain.layer_style import GeometryFamily
from app.domain.raster_layer import RasterLayer
from app.domain.spatial_layer import SpatialLayer
from app.domain.vector_layer import Bounds, VectorLayer


@dataclass(frozen=True, slots=True)
class ReprojectionMetadata:
    """记录一次独立重投影采用的操作和输出网格摘要。"""

    source_crs: str
    target_crs: str
    operation: str
    resampling: str | None
    output_shape: tuple[int, int] | None
    output_transform: tuple[float, float, float, float, float, float] | None
    output_bounds: Bounds
    feature_count: int | None = None


@dataclass(frozen=True, slots=True)
class LayerSnapshot:
    """表示某一时刻供界面读取的单个图层状态。"""

    # 领域图层：保存只读要素、坐标系、范围和样式。
    layer: SpatialLayer

    # 显示状态：表示图层当前是否参与地图绘制和空间查询。
    visible: bool

    # 已选要素编号：保存该图层当前选择集的稳定编号。
    selected_feature_ids: tuple[FeatureId, ...]

    # 显示载荷：几何或像元已经位于地图显示 CRS，只供绘制和显示索引使用。
    # 领域图层仍保留在 layer 字段中，不能用显示载荷替换它。
    display_payload: DisplayPayload | None = None

    # 显示范围：与 display_payload 使用同一显示 CRS；旧调用构造快照时为空，
    # 由 bounds 属性回退到领域图层范围。
    display_bounds: Bounds | None = None

    # 显示透明度：界面用于整体淡化图层，取值范围为零到一。
    opacity: float = 1.0

    # 显示混合模式：控制图层像素与下方图层的合成方式，默认 normal（正常）。
    blend_mode: str = "normal"

    # 显示比例范围：视图比例低于最小值或高于最大值时不绘制该图层。
    min_scale_percent: float | None = None
    max_scale_percent: float | None = None

    # 栅格显示重采样覆盖；为空时按分类/连续数据自动选择。
    raster_display_resampling: str | None = None

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
        """返回显示坐标系下的图层空间范围。"""
        if self.display_bounds is not None:
            return self.display_bounds
        if self.display_payload is not None:
            return self.display_payload.bounds
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
class DisplayCrsPreparation:
    """表示已完成显示缓存准备但尚未提交的显示 CRS 变更。"""

    target_crs: CRS
    source_layer_ids: tuple[str, ...]
    display_payloads: tuple[DisplayPayload, ...] = ()
    source_layer_revisions: tuple[int, ...] = ()

    # 保留字段以兼容尚未迁移的调用方；显示 CRS 提交不再替换领域图层。
    projected_layers: tuple[SpatialLayer, ...] = ()


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

    # 独立重投影工具的自动转换操作和输出网格摘要；普通打开数据为空。
    reprojection_metadata: ReprojectionMetadata | None = None


@dataclass(frozen=True, slots=True)
class ExportDataResult:
    """表示活动图层导出完成后的结构化结果。"""

    # 实际输出路径：使用绝对路径，便于界面向用户准确反馈。
    path: Path

    # 导出图层编号：用于确认本次命令对应的工作区图层。
    layer_id: str

    # 导出要素数量：矢量为实际写出数量，栅格为空值。
    exported_feature_count: int | None


@dataclass(frozen=True, slots=True)
class BufferAnalysisResult:
    """表示缓冲区分析写出并加入工作区后的结构化结果。"""

    # 输入图层编号：用于追踪本次分析所使用的工作区图层。
    input_layer_id: str

    # 输出图层编号：用于界面定位并激活新生成的结果图层。
    output_layer_id: str

    # 输出图层名称：用于状态反馈和结果管理。
    output_layer_name: str

    # 实际写出路径：统一为绝对路径。
    output_path: Path

    # 结果要素数量：融合时为融合后的结果要素数。
    feature_count: int

    # 加入结果图层后的完整工作区快照。
    snapshot: WorkspaceSnapshot


@dataclass(frozen=True, slots=True)
class OverlayAnalysisResult:
    """表示叠加分析写出并加入工作区后的结构化结果。"""

    # 输入图层编号：主输入图层，用于追踪本次分析所使用的工作区图层。
    input_layer_id: str

    # 叠加图层编号：用于叠加或空间连接的第二个图层。
    overlay_layer_id: str

    # 输出图层编号：用于界面定位并激活新生成的结果图层。
    output_layer_id: str

    # 输出图层名称：用于状态反馈和结果管理。
    output_layer_name: str

    # 实际写出路径：统一为绝对路径。
    output_path: Path

    # 结果要素数量。
    feature_count: int

    # 加入结果图层后的完整工作区快照。
    snapshot: WorkspaceSnapshot


@dataclass(frozen=True, slots=True)
class ProjectOpenResult:
    """表示工程打开并恢复完成后的工作区结果。"""

    path: Path
    snapshot: WorkspaceSnapshot
    view_state: MapViewState | None
    analysis_runs: tuple[AnalysisRun, ...]
    warnings: tuple[str, ...]
    layout_state: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class ProjectSaveResult:
    """表示工程快照保存完成后的结果。"""

    path: Path
    layer_count: int
    analysis_run_count: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AnalysisResultPersisted:
    """表示分析结果已写入 GeoPackage 并加入当前工作区。"""

    run: AnalysisRun
    snapshot: WorkspaceSnapshot


@dataclass(frozen=True, slots=True)
class RasterCalculatorResult:
    """表示栅格计算器写出并加入工作区后的结构化结果。"""

    output_layer_id: str
    output_layer_name: str
    output_path: Path
    expression: str
    variable_count: int
    snapshot: WorkspaceSnapshot


@dataclass(frozen=True, slots=True)
class RasterReclassifyResult:
    """表示栅格重分类写出并加入工作区后的结构化结果。"""

    input_layer_id: str
    output_layer_id: str
    output_layer_name: str
    output_path: Path
    rule_count: int
    snapshot: WorkspaceSnapshot


@dataclass(frozen=True, slots=True)
class DemAnalysisResult:
    """表示 DEM 地形分析写出并加入工作区后的结构化结果。"""

    input_layer_id: str
    output_layer_id: str
    output_layer_name: str
    output_path: Path
    mode: str
    snapshot: WorkspaceSnapshot


@dataclass(frozen=True, slots=True)
class RasterClipResult:
    """表示矢量掩膜裁剪写出并加入工作区后的结构化结果。"""

    raster_layer_id: str
    mask_layer_id: str
    output_layer_id: str
    output_layer_name: str
    output_path: Path
    snapshot: WorkspaceSnapshot


# 旧矢量结果名称：仅供既有调用代码兼容，新代码应使用 OpenDataResult。
OpenVectorResult: TypeAlias = OpenDataResult
