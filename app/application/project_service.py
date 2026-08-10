"""工程打开、恢复和快照构建服务。"""

import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from pyproj import CRS

from app.application.errors import (
    ProjectReadFailed,
    ProjectSourceMissing,
    ProjectWriteFailed,
)
from app.application.ports import DataReader, ProjectStore
from app.application.project_models import (
    AnalysisRun,
    LayerReference,
    MapViewState,
    ProjectManifest,
    SourceFingerprint,
)
from app.application.symbology_service import apply_raster_symbology
from app.domain.labeling import labeling_from_dict, labeling_to_dict
from app.domain.layout import LayoutDocument, layout_to_dict
from app.domain.map_document import MapDocument
from app.domain.raster_layer import RasterLayer
from app.domain.spatial_layer import SpatialLayer
from app.domain.symbology import (
    raster_symbology_from_dict,
    symbology_to_dict,
    vector_symbology_from_dict,
)
from app.domain.vector_layer import VectorLayer


@dataclass(frozen=True, slots=True)
class LoadedProject:
    """表示已经从工程文件恢复完成、可替换当前工作区的结果。"""

    path: Path
    manifest: ProjectManifest
    document: MapDocument
    warnings: tuple[str, ...]


class ProjectService:
    """将工程清单与运行时地图文档之间进行双向转换。"""

    def __init__(self, data_reader: DataReader, project_store: ProjectStore) -> None:
        """注入数据读取端口和工程清单存储端口。"""
        self._data_reader: DataReader = data_reader
        self._project_store: ProjectStore = project_store

    def load(self, path: Path) -> LoadedProject:
        """原子读取工程中的全部图层，成功后返回临时地图文档。"""
        resolved_path: Path = path.expanduser().resolve()
        manifest: ProjectManifest = self._project_store.load(resolved_path)
        document: MapDocument = MapDocument()
        warnings: list[str] = []
        stale_layer_ids: set[str] = set()

        display_crs: CRS | None = self._parse_crs(manifest.display_crs)
        if display_crs is not None:
            document.set_display_crs(display_crs)

        layer_reference: LayerReference
        for layer_reference in manifest.layers:
            source_path: Path = self._resolve_source_path(
                resolved_path.parent,
                layer_reference.source_path,
            )
            if not source_path.is_file():
                raise ProjectSourceMissing(
                    f"工程图层“{layer_reference.name}”的数据源不存在：{source_path}"
                )
            current_fingerprint: SourceFingerprint = self._fingerprint(source_path)
            if (
                layer_reference.fingerprint is not None
                and layer_reference.fingerprint != current_fingerprint
            ):
                stale_layer_ids.add(layer_reference.layer_id)
                warnings.append(f"数据源已变化，相关分析结果可能过期：{source_path.name}")

            try:
                loaded_layer: SpatialLayer = self._data_reader.read(
                    source_path,
                    display_crs,
                    layer_reference.source_layer_name,
                )
                restored_layer: SpatialLayer = self._restore_layer_identity(
                    loaded_layer,
                    layer_reference,
                    source_path,
                )
                document.add_layer(restored_layer)
                document.set_layer_visibility(
                    restored_layer.layer_id,
                    layer_reference.visible,
                )
                document.set_layer_opacity(
                    restored_layer.layer_id,
                    layer_reference.opacity,
                )
                document.set_layer_blend_mode(
                    restored_layer.layer_id,
                    layer_reference.blend_mode,
                )
                document.set_layer_scale_range(
                    restored_layer.layer_id,
                    layer_reference.min_scale_percent,
                    layer_reference.max_scale_percent,
                )
                if isinstance(restored_layer, VectorLayer):
                    try:
                        document.set_selection(
                            restored_layer.layer_id,
                            layer_reference.selected_feature_ids,
                        )
                    except ValueError:
                        warnings.append(
                            f"图层“{layer_reference.name}”的选择集已失效，已清除。"
                        )
            except ProjectSourceMissing:
                raise
            except Exception as error:
                raise ProjectReadFailed(
                    f"工程图层“{layer_reference.name}”加载失败。"
                ) from error

        if manifest.active_layer_id is not None:
            try:
                document.set_active_layer(manifest.active_layer_id)
            except KeyError:
                warnings.append("工程记录的活动图层不存在，已使用默认活动图层。")

        stale_run_ids: set[str] = set()
        changed: bool = True
        while changed:
            changed = False
            for run in manifest.analysis_runs:
                # 只有成功生成结果的分析才需要沿依赖关系检查是否过期；失败记录
                # 代表一次执行事实，不应因为输入文件后来变化而覆盖成 stale。
                if run.status != "completed":
                    continue
                if run.run_id in stale_run_ids:
                    continue
                input_is_stale: bool = any(
                    layer_id in stale_layer_ids for layer_id in run.input_layer_ids
                )
                parent_is_stale: bool = any(
                    parent_id in stale_run_ids for parent_id in run.parent_run_ids
                )
                if input_is_stale or parent_is_stale:
                    stale_run_ids.add(run.run_id)
                    stale_layer_ids.update(run.output_layer_ids)
                    changed = True

        updated_runs: tuple[AnalysisRun, ...] = tuple(
            replace(run, status="stale") if run.run_id in stale_run_ids else run
            for run in manifest.analysis_runs
        )
        if stale_run_ids:
            warnings.append(f"检测到 {len(stale_run_ids)} 条分析结果可能过期。")
        updated_manifest: ProjectManifest = replace(manifest, analysis_runs=updated_runs)
        return LoadedProject(
            path=resolved_path,
            manifest=updated_manifest,
            document=document,
            warnings=tuple(warnings),
        )

    def build_manifest(
        self,
        document: MapDocument,
        project_path: Path,
        project_name: str,
        created_at: str,
        analysis_runs: tuple[AnalysisRun, ...],
        view_state: MapViewState | None,
        layout_document: LayoutDocument | None = None,
    ) -> ProjectManifest:
        """从当前地图文档构建待保存的工程快照。"""
        resolved_project_path: Path = project_path.expanduser().resolve()
        layer_references: list[LayerReference] = []
        for layer in document.layers:
            if layer.source_path is None:
                raise ProjectWriteFailed(
                    f"图层“{layer.name}”没有持久化数据源，请先导出该图层。"
                )
            source_path: Path = layer.source_path.expanduser().resolve()
            if not source_path.is_file():
                raise ProjectWriteFailed(
                    f"图层“{layer.name}”的数据源不存在：{source_path}"
                )
            source_layer_name: str | None = (
                layer.source_layer_name if isinstance(layer, VectorLayer) else None
            )
            layer_kind: str = "vector" if isinstance(layer, VectorLayer) else "raster"
            layer_references.append(
                LayerReference(
                    layer_id=layer.layer_id,
                    name=layer.name,
                    source_path=self._relative_path(
                        source_path,
                        resolved_project_path.parent,
                    ),
                    source_layer_name=source_layer_name,
                    layer_kind=layer_kind,
                    visible=document.is_visible(layer.layer_id),
                    selected_feature_ids=document.selected_feature_ids(layer.layer_id),
                    fingerprint=self._fingerprint(source_path),
                    symbology=(
                        symbology_to_dict(layer.symbology)
                        if layer.symbology is not None
                        else None
                    ),
                    labeling=(
                        labeling_to_dict(layer.labeling)
                        if isinstance(layer, VectorLayer)
                        else None
                    ),
                    opacity=document.layer_opacity(layer.layer_id),
                    blend_mode=document.layer_blend_mode(layer.layer_id),
                    min_scale_percent=document.layer_scale_range(layer.layer_id)[0],
                    max_scale_percent=document.layer_scale_range(layer.layer_id)[1],
                )
            )

        display_crs: str | None = (
            document.display_crs.to_string() if document.display_crs is not None else None
        )
        return ProjectManifest(
            schema_version=1,
            name=project_name,
            created_at=created_at,
            modified_at=self._now(),
            display_crs=display_crs,
            active_layer_id=document.active_layer_id,
            layers=tuple(layer_references),
            view_state=view_state,
            analysis_runs=analysis_runs,
            layout_state=(
                layout_to_dict(layout_document)
                if layout_document is not None
                else None
            ),
        )

    @staticmethod
    def _parse_crs(value: str | None) -> CRS | None:
        """把工程中保存的 CRS 文本转换为 PyProj 对象。"""
        if value is None:
            return None
        try:
            return CRS.from_user_input(value)
        except Exception as error:
            raise ProjectReadFailed(f"工程显示坐标系无法识别：{value}") from error

    @staticmethod
    def _resolve_source_path(project_directory: Path, source_path: str) -> Path:
        """按工程目录解析相对路径，同时兼容旧工程中的绝对路径。"""
        candidate: Path = Path(source_path)
        if not candidate.is_absolute():
            candidate = project_directory / candidate
        return candidate.expanduser().resolve()

    @staticmethod
    def _relative_path(source_path: Path, project_directory: Path) -> str:
        """将外部路径转换为工程文件中的跨平台相对路径。"""
        try:
            relative_path: str = os.path.relpath(source_path, project_directory)
        except ValueError:
            # Windows 不同盘符无法生成相对路径，保留绝对路径并明确记录。
            relative_path = str(source_path)
        return relative_path.replace("\\", "/")

    @staticmethod
    def _fingerprint(path: Path) -> SourceFingerprint:
        """读取轻量文件指纹；调用方已保证路径存在。"""
        try:
            stat_result = path.stat()
        except OSError as error:
            raise ProjectWriteFailed(f"无法读取数据源文件信息：{path}") from error
        return SourceFingerprint(size=stat_result.st_size, mtime_ns=stat_result.st_mtime_ns)

    @staticmethod
    def _restore_layer_identity(
        layer: SpatialLayer,
        reference: LayerReference,
        source_path: Path,
    ) -> SpatialLayer:
        """将读取器生成的临时编号替换为工程中稳定的图层身份。"""
        if reference.layer_kind == "vector" and isinstance(layer, VectorLayer):
            symbology = (
                vector_symbology_from_dict(dict(reference.symbology))
                if reference.symbology is not None
                else None
            )
            labeling = (
                labeling_from_dict(dict(reference.labeling))
                if reference.labeling is not None
                else None
            )
            return VectorLayer.create(
                layer_id=reference.layer_id,
                name=reference.name,
                features=layer.features,
                crs=layer.crs,
                source_path=source_path,
                source_layer_name=reference.source_layer_name,
                symbology=symbology,
                labeling=labeling,
            )
        if reference.layer_kind == "raster" and isinstance(layer, RasterLayer):
            restored_raster_symbology = (
                raster_symbology_from_dict(dict(reference.symbology))
                if reference.symbology is not None
                else None
            )
            restored = layer.with_identity(
                layer_id=reference.layer_id,
                name=reference.name,
                source_path=source_path,
                symbology=restored_raster_symbology,
            )
            if restored_raster_symbology is None:
                return restored
            # 分类/拉伸符号必须同步作用到地图预览；符号服务会优先使用
            # 读取器保留的低分辨率原始值，不会因工程重开而加载整幅大栅格。
            return apply_raster_symbology(restored, restored_raster_symbology)
        raise ProjectReadFailed(
            f"工程图层“{reference.name}”的数据类型与记录不一致。"
        )

    @staticmethod
    def _now() -> str:
        """返回 UTC ISO 时间，避免工程在不同时区保存出歧义。"""
        return datetime.now(timezone.utc).isoformat()
