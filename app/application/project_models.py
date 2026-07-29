"""工程快照和分析历史的应用层数据模型。"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.domain.feature import FeatureId


@dataclass(frozen=True, slots=True)
class MapViewState:
    """保存与 Qt 无关的地图视图状态。"""

    center_x: float
    center_y: float
    zoom_percent: float

    def __post_init__(self) -> None:
        """拒绝无法恢复的非正缩放比例。"""
        if self.zoom_percent <= 0:
            raise ValueError("地图视图缩放比例必须大于零。")


@dataclass(frozen=True, slots=True)
class SourceFingerprint:
    """记录外部数据源的轻量文件指纹。"""

    size: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class LayerReference:
    """保存工程恢复一个图层所需的外部引用和工作区状态。"""

    layer_id: str
    name: str
    source_path: str
    source_layer_name: str | None
    layer_kind: str
    visible: bool
    selected_feature_ids: tuple[FeatureId, ...]
    fingerprint: SourceFingerprint | None
    symbology: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class AnalysisOutputReference:
    """指向一个已经持久化的分析结果图层。"""

    layer_id: str
    source_path: str
    source_layer_name: str | None


@dataclass(frozen=True, slots=True)
class AnalysisRun:
    """记录一次分析的输入、参数、输出和依赖关系。"""

    run_id: str
    algorithm_id: str
    input_layer_ids: tuple[str, ...]
    parameters: Mapping[str, object]
    output_layer_ids: tuple[str, ...]
    outputs: tuple[AnalysisOutputReference, ...]
    parent_run_ids: tuple[str, ...]
    status: str
    created_at: str
    supersedes_run_id: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        """复制参数映射，避免调用方在保存前修改分析记录。"""
        readonly_parameters: Mapping[str, object] = MappingProxyType(dict(self.parameters))
        object.__setattr__(self, "parameters", readonly_parameters)


@dataclass(frozen=True, slots=True)
class ProjectManifest:
    """表示一个可序列化的工程工作状态快照。"""

    schema_version: int
    name: str
    created_at: str
    modified_at: str
    display_crs: str | None
    active_layer_id: str | None
    layers: tuple[LayerReference, ...]
    view_state: MapViewState | None
    analysis_runs: tuple[AnalysisRun, ...]
