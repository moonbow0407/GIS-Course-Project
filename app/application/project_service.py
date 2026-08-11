"""工程打开、恢复和快照构建服务。"""

import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from pyproj import CRS

from app.application.database_service import DatabaseService
from app.application.errors import (
    ProjectReadFailed,
    ProjectSourceMissing,
    ProjectWriteFailed,
)
from app.application.ports import DataReader, DataWriter, ProjectStore
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

    def __init__(
        self,
        data_reader: DataReader,
        project_store: ProjectStore,
        data_writer: DataWriter | None = None,
        database_service: DatabaseService | None = None,
    ) -> None:
        """注入数据读取端口和工程清单存储端口。"""
        self._data_reader: DataReader = data_reader
        self._project_store: ProjectStore = project_store
        self._data_writer: DataWriter | None = data_writer
        self._database_service: DatabaseService | None = database_service

    def load(self, path: Path) -> LoadedProject:
        """原子读取工程中的全部图层，成功后返回临时地图文档。"""
        resolved_path: Path = path.expanduser().resolve()
        manifest: ProjectManifest = self._project_store.load(resolved_path)
        document: MapDocument = MapDocument()
        warnings: list[str] = []
        stale_layer_ids: set[str] = set()

        display_crs: CRS | None = self._parse_crs(manifest.display_crs)

        layer_reference: LayerReference
        for layer_reference in manifest.layers:
            try:
                source_path: Path | None
                if layer_reference.source_kind == "database":
                    loaded_layer, source_path = self._load_database_reference(
                        layer_reference, warnings
                    )
                    if loaded_layer is None:
                        continue
                else:
                    if layer_reference.source_path is None:
                        warnings.append(
                            f"临时图层“{layer_reference.name}”未持久化，重新打开工程时已跳过。"
                        )
                        continue
                    source_path = self._resolve_source_path(
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
                        warnings.append(
                            f"数据源已变化，相关分析结果可能过期：{source_path.name}"
                        )
                    loaded_layer = self._data_reader.read(
                        source_path,
                        None,
                        layer_reference.source_layer_name,
                        self._parse_crs(layer_reference.crs_override),
                    )
                restored_layer: SpatialLayer = self._restore_layer_identity(
                    loaded_layer,
                    layer_reference,
                    source_path,
                )
                document.add_layer(restored_layer)
                if (
                    layer_reference.display_resampling is not None
                    and isinstance(restored_layer, RasterLayer)
                ):
                    document.set_raster_display_resampling(
                        restored_layer.layer_id,
                        layer_reference.display_resampling,
                    )
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

        if display_crs is not None and document.layers:
            document.set_display_crs(display_crs)

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
        persist_temporary: bool = False,
    ) -> ProjectManifest:
        """从当前地图文档构建待保存的工程快照。"""
        resolved_project_path: Path = project_path.expanduser().resolve()
        layer_references: list[LayerReference] = []
        for layer in document.layers:
            layer_to_save: SpatialLayer = layer
            is_database_layer: bool = (
                isinstance(layer, VectorLayer) and layer.database_layer_id is not None
            )
            database_layer_id: int | None = (
                layer.database_layer_id if isinstance(layer, VectorLayer) else None
            )
            if layer.source_path is None and not is_database_layer:
                if not persist_temporary:
                    # 临时结果可以继续留在当前会话，但没有稳定引用时不能写入
                    # 工程。调用方会在保存结果中提示用户这些图层未被持久化。
                    continue
                if self._data_writer is None:
                    raise ProjectWriteFailed("持久化临时图层需要配置空间数据写出服务。")
                data_directory: Path = resolved_project_path.parent / "project_data"
                data_directory.mkdir(parents=True, exist_ok=True)
                temporary_path: Path = data_directory / (
                    f"temporary_{layer.layer_id}"
                    + (".gpkg" if isinstance(layer, VectorLayer) else ".tif")
                )
                temporary_layer_name: str | None = (
                    f"temporary_{layer.layer_id}"
                    if isinstance(layer, VectorLayer)
                    else None
                )
                if isinstance(layer, VectorLayer):
                    layer_to_save = VectorLayer.create(
                        layer_id=layer.layer_id,
                        name=layer.name,
                        features=layer.features,
                        crs=layer.crs,
                        source_path=temporary_path,
                        source_layer_name=temporary_layer_name,
                        symbology=layer.symbology,
                        labeling=layer.labeling,
                        crs_override=layer.crs_override,
                    )
                elif isinstance(layer, RasterLayer):
                    layer_to_save = layer.with_identity(
                        layer_id=layer.layer_id,
                        name=layer.name,
                        source_path=temporary_path,
                        symbology=layer.symbology,
                    )
                self._data_writer.write(
                    layer_to_save,
                    temporary_path,
                    (),
                    temporary_layer_name,
                )
            if layer_to_save.source_path is None and not is_database_layer:
                raise ProjectWriteFailed(
                    f"图层“{layer.name}”没有持久化数据源，请先导出该图层。"
                )
            source_path: Path | None = (
                layer_to_save.source_path.expanduser().resolve()
                if layer_to_save.source_path is not None
                else None
            )
            if source_path is not None and not source_path.is_file():
                raise ProjectWriteFailed(
                    f"图层“{layer.name}”的数据源不存在：{source_path}"
                )
            source_layer_name: str | None = (
                layer_to_save.source_layer_name
                if isinstance(layer_to_save, VectorLayer)
                else None
            )
            layer_kind: str = "vector" if isinstance(layer, VectorLayer) else "raster"
            layer_references.append(
                LayerReference(
                    layer_id=layer.layer_id,
                    name=layer.name,
                    source_path=(
                        self._relative_path(source_path, resolved_project_path.parent)
                        if source_path is not None
                        else None
                    ),
                    source_layer_name=source_layer_name,
                    layer_kind=layer_kind,
                    visible=document.is_visible(layer.layer_id),
                    selected_feature_ids=document.selected_feature_ids(layer.layer_id),
                    fingerprint=(
                        self._fingerprint(source_path) if source_path is not None else None
                    ),
                    crs_override=(
                        layer_to_save.crs.to_string()
                        if layer_to_save.crs_override and layer_to_save.crs is not None
                        else None
                    ),
                    display_resampling=(
                        document.raster_display_resampling(layer.layer_id)
                        if isinstance(layer, RasterLayer)
                        else None
                    ),
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
                    source_kind="database" if is_database_layer else "file",
                    database_layer_id=database_layer_id if is_database_layer else None,
                    database_connection_identity=(
                        self._database_service.connection_identity
                        if is_database_layer and self._database_service is not None
                        else None
                    ),
                )
            )

        display_crs: str | None = (
            document.display_crs.to_string() if document.display_crs is not None else None
        )
        return ProjectManifest(
            schema_version=3,
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

    def _load_database_reference(
        self,
        reference: LayerReference,
        warnings: list[str],
    ) -> tuple[SpatialLayer | None, Path | None]:
        """按工程中的数据库图层 ID 恢复内存图层，不写入数据库连接密码。"""
        if reference.database_layer_id is None:
            warnings.append(f"数据库图层“{reference.name}”缺少图层 ID，已跳过。")
            return None, None
        service: DatabaseService | None = self._database_service
        if service is None or not service.is_connected:
            warnings.append(
                f"数据库图层“{reference.name}”未恢复：请先连接工程记录的数据库后重新打开。"
            )
            return None, None
        if (
            reference.database_connection_identity is not None
            and service.connection_identity != reference.database_connection_identity
        ):
            warnings.append(
                f"数据库图层“{reference.name}”未恢复：当前连接与工程记录不匹配。"
            )
            return None, None
        try:
            loaded_layer: VectorLayer = service.load_layer(reference.database_layer_id, None)
        except Exception as error:
            warnings.append(f"数据库图层“{reference.name}”加载失败，已跳过：{error}")
            return None, None
        override_crs: CRS | None = self._parse_crs(reference.crs_override)
        if override_crs is not None:
            loaded_layer = VectorLayer.create(
                layer_id=loaded_layer.layer_id,
                name=loaded_layer.name,
                features=loaded_layer.features,
                crs=override_crs,
                source_layer_name=loaded_layer.source_layer_name,
                database_layer_id=reference.database_layer_id,
                symbology=loaded_layer.symbology,
                labeling=loaded_layer.labeling,
                geometry_family=loaded_layer.geometry_family,
                crs_override=True,
            )
        return loaded_layer, None

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
        source_path: Path | None,
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
                database_layer_id=reference.database_layer_id,
                symbology=symbology,
                labeling=labeling,
                crs_override=reference.crs_override is not None,
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
                crs_override=reference.crs_override is not None,
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
