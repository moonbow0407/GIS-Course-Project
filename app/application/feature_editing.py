"""要素编辑原子补丁和值对象。"""

from dataclasses import dataclass

from shapely.geometry.base import BaseGeometry

from app.application.results import WorkspaceSnapshot
from app.domain.feature import Feature, FeatureId


@dataclass(frozen=True, slots=True)
class FeatureGeometryReplacement:
    """描述一个已有要素的几何替换。"""

    fid: FeatureId
    geometry: BaseGeometry


@dataclass(frozen=True, slots=True)
class FeatureEditPatch:
    """描述同一图层一次提交中的全部原子变更。"""

    replacements: tuple[FeatureGeometryReplacement, ...] = ()
    deletions: tuple[FeatureId, ...] = ()
    additions: tuple[Feature, ...] = ()

    @property
    def is_empty(self) -> bool:
        """返回补丁是否不包含任何变更。"""
        return not (self.replacements or self.deletions or self.additions)


@dataclass(frozen=True, slots=True)
class FeatureEditResult:
    """保存一次原子编辑提交前后的完整要素集合。"""

    layer_id: str
    before_features: tuple[Feature, ...]
    after_features: tuple[Feature, ...]
    snapshot: WorkspaceSnapshot
