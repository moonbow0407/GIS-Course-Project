"""GIS 应用功能统一入口。"""

from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from pyproj import CRS
from shapely.geometry import Point, Polygon

from app.application.analysis_environment import AnalysisEnvironment
from app.application.buffer_analysis import (
    BufferRequest,
    buffer_features,
    distance_to_crs_units,
    distance_to_meters,
    reproject_features,
    reproject_vector_layer,
    resolve_buffer_analysis_crs,
)
from app.application.database_models import (
    DatabaseConnectionConfig,
    DatabaseLayerInfo,
    DatabaseServerInfo,
)
from app.application.database_service import DatabaseService
from app.application.errors import (
    ApplicationError,
    BufferAnalysisFailed,
    DatabaseImportFailed,
    DatabaseNotConfigured,
    DataWriteFailed,
    InvalidBufferParameters,
    LayerNotFound,
    LayerReprojectionFailed,
    NoActiveLayer,
    ProjectNotSaved,
    ProjectStoreNotConfigured,
    UnsupportedBufferInput,
)
from app.application.ports import DataReader, DataWriter, ProjectStore
from app.application.project_models import (
    AnalysisOutputReference,
    AnalysisRun,
    MapViewState,
)
from app.application.project_service import ProjectService
from app.application.results import (
    AnalysisResultPersisted,
    BufferAnalysisResult,
    ExportDataResult,
    LayerSnapshot,
    OpenDataResult,
    OpenVectorResult,
    ProjectOpenResult,
    ProjectSaveResult,
    SelectedFeature,
    SelectionResult,
    WorkspaceSnapshot,
)
from app.application.symbology_service import (
    apply_raster_symbology,
    create_graduated_symbology,
    create_unique_value_symbology,
)
from app.domain.feature import Feature, FeatureId
from app.domain.map_document import MapDocument
from app.domain.raster_layer import RasterLayer
from app.domain.spatial_layer import SpatialLayer
from app.domain.symbology import RasterSymbology, VectorSymbology
from app.domain.vector_layer import VectorLayer


class GisApplication:
    """通过较小公开接口统一编排图层管理和空间查询流程。"""

    # 空间数据读取端口：由启动组装模块注入真实或测试适配器。
    data_reader: DataReader
    # 空间数据写入端口：为空时保留只读应用服务兼容能力。
    data_writer: DataWriter | None

    def __init__(
        self,
        data_reader: DataReader,
        data_writer: DataWriter | None = None,
        document: MapDocument | None = None,
        project_store: ProjectStore | None = None,
        database_service: DatabaseService | None = None,
    ) -> None:
        """使用空间数据读取端口和可选地图文档初始化应用入口。"""
        self.data_reader = data_reader
        self.data_writer = data_writer
        self.project_store = project_store
        self.database_service = database_service

        # 地图文档：作为图层、显隐、活动状态和选择集的唯一事实来源。
        self._document: MapDocument = document or MapDocument()
        # 工程会话信息：与地图文档分离，保存路径为空表示未命名工程。
        self._project_path: Path | None = None
        self._project_name: str = "未命名工程"
        self._project_created_at: str = self._now()
        self._analysis_runs: tuple[AnalysisRun, ...] = ()
        self._modified: bool = False

    @property
    def project_path(self) -> Path | None:
        """返回当前工程文件路径。"""
        return self._project_path

    @property
    def project_name(self) -> str:
        """返回当前工程显示名称。"""
        return self._project_name

    @property
    def is_modified(self) -> bool:
        """返回当前工程是否存在尚未保存的工作区变化。"""
        return self._modified

    @property
    def analysis_runs(self) -> tuple[AnalysisRun, ...]:
        """返回当前工程的只读分析历史。"""
        return self._analysis_runs

    def clear_analysis_history(self) -> None:
        """清除当前工程的分析历史，但保留地图中的结果图层和结果文件。"""
        self._analysis_runs = ()
        self._modified = True

    def open_vector(self, path: Path, layer_name: str | None = None) -> OpenVectorResult:
        """兼容旧调用方式读取矢量文件，并返回打开数据结果。"""
        return self.open_data(path, layer_name)

    def open_data(self, path: Path, layer_name: str | None = None) -> OpenDataResult:
        """读取空间文件并原子加入地图文档。

        参数:
            path: 待读取的矢量或栅格文件路径。

        返回:
            包含新增图层编号、工作区快照和可选警告的结果。

        异常:
            ApplicationError: 文件不存在、格式不支持或数据无法读取时抛出。
            ValueError: 图层坐标系与地图文档无法安全叠加时抛出。
        """
        if layer_name is None:
            layer = self.data_reader.read(path, self._document.display_crs)
        else:
            layer = self.data_reader.read(path, self._document.display_crs, layer_name)
        self._document.add_layer(layer)
        self._modified = True
        warning: str | None = "数据未声明坐标参考系统。" if layer.crs is None else None
        return OpenDataResult(
            layer_id=layer.layer_id,
            snapshot=self.snapshot(),
            warning=warning,
        )

    def add_layer(self, layer: SpatialLayer) -> OpenDataResult:
        """将已经由其他数据源构造好的图层加入当前地图文档。"""
        self._document.add_layer(layer)
        self._modified = True
        warning: str | None = "数据未声明坐标参考系统。" if layer.crs is None else None
        return OpenDataResult(
            layer_id=layer.layer_id,
            snapshot=self.snapshot(),
            warning=warning,
        )

    @property
    def database_is_connected(self) -> bool:
        """返回数据库服务是否已经建立并通过连接测试。"""
        return self.database_service is not None and self.database_service.is_connected

    def connect_database(self, config: DatabaseConnectionConfig) -> DatabaseServerInfo:
        """连接 PostgreSQL/PostGIS，并返回服务端版本信息。"""
        return self._require_database_service().connect(config)

    def disconnect_database(self) -> None:
        """断开当前数据库连接。"""
        self._require_database_service().disconnect()

    def list_database_layers(self) -> tuple[DatabaseLayerInfo, ...]:
        """读取当前数据库中的图层目录。"""
        return self._require_database_service().list_layers()

    def import_active_layer_to_database(self) -> DatabaseLayerInfo:
        """将当前活动矢量图层完整导入数据库。"""
        active_layer_id: str | None = self._document.active_layer_id
        if active_layer_id is None:
            raise NoActiveLayer("请先选择要导入数据库的活动图层。")
        layer: SpatialLayer = self._find_layer(active_layer_id)
        if not isinstance(layer, VectorLayer):
            raise DatabaseImportFailed("数据库模块当前只支持导入矢量图层。")
        return self._require_database_service().import_layer(layer)

    def load_database_layer(self, layer_id: int) -> OpenDataResult:
        """加载数据库图层，并按当前地图 CRS 统一后加入工作区。"""
        target_crs: CRS | None = self._document.display_crs
        layer: VectorLayer = self._require_database_service().load_layer(layer_id, target_crs)
        return self.add_layer(layer)

    def remove_layer(self, layer_id: str) -> WorkspaceSnapshot:
        """移除指定图层并返回最新工作区快照。"""
        try:
            self._document.remove_layer(layer_id)
        except KeyError as error:
            raise LayerNotFound(f"图层不存在：{layer_id}") from error
        self._modified = True
        return self.snapshot()

    def move_layer(self, layer_id: str, target_index: int) -> WorkspaceSnapshot:
        """调整指定图层的显示顺序并返回最新快照。"""
        try:
            self._document.move_layer(layer_id, target_index)
        except KeyError as error:
            raise LayerNotFound(f"图层不存在：{layer_id}") from error
        self._modified = True
        return self.snapshot()

    def set_layer_visibility(self, layer_id: str, visible: bool) -> WorkspaceSnapshot:
        """设置指定图层显隐状态并返回最新快照。"""
        try:
            self._document.set_layer_visibility(layer_id, visible)
        except KeyError as error:
            raise LayerNotFound(f"图层不存在：{layer_id}") from error
        self._modified = True
        return self.snapshot()

    def set_active_layer(self, layer_id: str) -> WorkspaceSnapshot:
        """设置活动图层并返回最新工作区快照。"""
        try:
            self._document.set_active_layer(layer_id)
        except KeyError as error:
            raise LayerNotFound(f"图层不存在：{layer_id}") from error
        self._modified = True
        return self.snapshot()

    def apply_vector_symbology(
        self,
        layer_id: str,
        symbology: VectorSymbology,
    ) -> WorkspaceSnapshot:
        """替换矢量图层符号配置并保留图层身份和工作区状态。"""
        layer: SpatialLayer = self._find_layer(layer_id)
        if not isinstance(layer, VectorLayer):
            raise ValueError("矢量符号只能应用到矢量图层。")
        updated_layer = VectorLayer.create(
            layer_id=layer.layer_id,
            name=layer.name,
            features=layer.features,
            crs=layer.crs,
            source_path=layer.source_path,
            source_layer_name=layer.source_layer_name,
            database_layer_id=layer.database_layer_id,
            symbology=symbology,
        )
        self._document.replace_layer(updated_layer)
        self._modified = True
        return self.snapshot()

    def apply_unique_value_symbology(
        self,
        layer_id: str,
        field_name: str,
        color_scheme: str,
    ) -> WorkspaceSnapshot:
        """为矢量图层生成最多一百类的唯一值符号。"""
        layer: SpatialLayer = self._find_layer(layer_id)
        if not isinstance(layer, VectorLayer):
            raise ValueError("唯一值符号只能应用到矢量图层。")
        return self.apply_vector_symbology(
            layer_id,
            create_unique_value_symbology(layer, field_name, color_scheme),
        )

    def apply_graduated_symbology(
        self,
        layer_id: str,
        field_name: str,
        color_scheme: str,
        classification_method: str,
        class_count: int,
    ) -> WorkspaceSnapshot:
        """为矢量数值字段生成等间隔或分位数颜色。"""
        layer: SpatialLayer = self._find_layer(layer_id)
        if not isinstance(layer, VectorLayer):
            raise ValueError("分级颜色只能应用到矢量图层。")
        return self.apply_vector_symbology(
            layer_id,
            create_graduated_symbology(
                layer,
                field_name,
                color_scheme,
                classification_method,
                class_count,
            ),
        )

    def apply_raster_symbology(
        self,
        layer_id: str,
        symbology: RasterSymbology,
    ) -> WorkspaceSnapshot:
        """按 RGB 或单波段拉伸配置重建栅格显示缓存。"""
        layer: SpatialLayer = self._find_layer(layer_id)
        if not isinstance(layer, RasterLayer):
            raise ValueError("栅格符号只能应用到栅格图层。")
        self._document.replace_layer(apply_raster_symbology(layer, symbology))
        self._modified = True
        return self.snapshot()

    def select_point(self, point: Point, tolerance: float) -> SelectionResult:
        """选择可见图层中容差范围内优先级最高的最近要素。"""
        if tolerance < 0:
            raise ValueError("点选容差不能小于零。")
        self._document.clear_selection()
        self._modified = True
        # 点选先查活动图层，再按视觉上的顶层到下层查找。
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
        spatial_layer: SpatialLayer
        for spatial_layer in self._document.layers:
            # 栅格没有独立矢量要素，不参与几何相交查询。
            if isinstance(spatial_layer, RasterLayer):
                continue
            layer: VectorLayer = spatial_layer
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
        self._modified = True
        return SelectionResult(features=tuple(selected_features), snapshot=self.snapshot())

    def clear_selection(self) -> SelectionResult:
        """清除全部图层选择并返回空选择结果。"""
        self._document.clear_selection()
        self._modified = True
        return SelectionResult(features=(), snapshot=self.snapshot())

    def new_project(self) -> WorkspaceSnapshot:
        """创建空白工程会话，不加载演示数据。"""
        self._document = MapDocument()
        self._project_path = None
        self._project_name = "未命名工程"
        self._project_created_at = self._now()
        self._analysis_runs = ()
        self._modified = False
        return self.snapshot()

    def open_project(self, path: Path) -> ProjectOpenResult:
        """从工程文件原子恢复工作区、结果图层和分析历史。"""
        project_service: ProjectService = ProjectService(
            self.data_reader,
            self._require_project_store(),
        )
        loaded = project_service.load(path)
        self._document = loaded.document
        self._project_path = loaded.path
        self._project_name = loaded.manifest.name
        self._project_created_at = loaded.manifest.created_at
        self._analysis_runs = loaded.manifest.analysis_runs
        self._modified = False
        return ProjectOpenResult(
            path=loaded.path,
            snapshot=self.snapshot(),
            view_state=loaded.manifest.view_state,
            analysis_runs=self._analysis_runs,
            warnings=loaded.warnings,
        )

    def save_project(
        self,
        path: Path | None = None,
        view_state: MapViewState | None = None,
    ) -> ProjectSaveResult:
        """保存当前工程快照；未传路径时使用当前工程路径。"""
        target_path: Path | None = path or self._project_path
        if target_path is None:
            raise ProjectNotSaved("当前工程尚未命名，请先选择工程保存位置。")
        project_store: ProjectStore = self._require_project_store()
        resolved_path: Path = target_path.expanduser().resolve()
        project_name: str = (
            self._project_name if self._project_path is not None else resolved_path.stem
        )
        project_service: ProjectService = ProjectService(self.data_reader, project_store)
        manifest = project_service.build_manifest(
            document=self._document,
            project_path=resolved_path,
            project_name=project_name,
            created_at=self._project_created_at,
            analysis_runs=self._analysis_runs,
            view_state=view_state,
        )
        project_store.save(resolved_path, manifest)
        self._project_path = resolved_path
        self._project_name = manifest.name
        self._modified = False
        return ProjectSaveResult(
            path=resolved_path,
            layer_count=len(manifest.layers),
            analysis_run_count=len(manifest.analysis_runs),
        )

    def persist_vector_analysis_result(
        self,
        layer: VectorLayer,
        algorithm_id: str,
        input_layer_ids: tuple[str, ...],
        parameters: Mapping[str, object],
        result_name: str | None = None,
    ) -> AnalysisResultPersisted:
        """将一次矢量分析结果写入工程 GeoPackage，并生成新的结果图层。"""
        if self._project_path is None:
            raise ProjectNotSaved("请先保存工程，再持久化分析结果。")
        if self.data_writer is None:
            raise ProjectNotSaved("分析结果写出服务尚未配置。")
        for layer_id in input_layer_ids:
            self._find_layer(layer_id)

        run_id: str = uuid4().hex
        result_directory: Path = self._project_path.parent / "project_data"
        result_directory.mkdir(parents=True, exist_ok=True)
        result_path: Path = result_directory / "results.gpkg"
        layer_name: str = f"{self._safe_algorithm_id(algorithm_id)}_{run_id[:8]}"
        result_layer_id: str = f"result-{run_id}"
        display_name: str = result_name or layer_name
        persisted_layer: VectorLayer = VectorLayer.create(
            layer_id=result_layer_id,
            name=display_name,
            features=layer.features,
            crs=layer.crs,
            source_path=result_path,
            source_layer_name=layer_name,
        )
        self.data_writer.write(persisted_layer, result_path, (), layer_name)
        self._document.add_layer(persisted_layer)

        run: AnalysisRun = self._create_analysis_run(
            algorithm_id=algorithm_id,
            input_layer_ids=input_layer_ids,
            parameters=parameters,
            output_layer_id=result_layer_id,
            output_path=result_path,
            output_layer_name=layer_name,
            run_id=run_id,
        )
        self._analysis_runs = self._analysis_runs + (run,)
        self._modified = True
        return AnalysisResultPersisted(run=run, snapshot=self.snapshot())

    def buffer_analysis(self, request: BufferRequest) -> BufferAnalysisResult:
        """执行缓冲区分析，并为成功或失败的执行追加一条历史记录。

        参数:
            request: 输入图层、输出位置、距离和几何样式等分析参数。

        返回:
            包含输出图层编号、写出路径、要素数量和最新工作区快照的结果。

        异常:
            ApplicationError: 分析参数、输入数据或结果写出失败时抛出。
        """
        started_at: str = self._now()
        started_monotonic: float = perf_counter()
        try:
            return self._execute_buffer_analysis(request, started_at, started_monotonic)
        except Exception as error:
            self._append_failed_analysis_run(request, started_at, started_monotonic, error)
            raise

    def _execute_buffer_analysis(
        self,
        request: BufferRequest,
        started_at: str,
        started_monotonic: float,
    ) -> BufferAnalysisResult:
        """执行缓冲区分析、写出结果并将结果图层加入当前工作区。

        参数:
            request: 输入图层、输出位置、距离和几何样式等分析参数。

        返回:
            包含输出图层编号、写出路径、要素数量和最新工作区快照的结果。

        异常:
            UnsupportedBufferInput: 输入图层不是有坐标系的矢量图层。
            DataWriteFailed: 输出服务未配置或结果无法写出。
            ApplicationError: 输入图层转换或缓冲计算失败。
        """
        if self.data_writer is None:
            raise DataWriteFailed("空间数据写出服务尚未配置。")

        input_layer: SpatialLayer = self._find_layer(request.input_layer_id)
        if not isinstance(input_layer, VectorLayer):
            raise UnsupportedBufferInput("缓冲区分析的输入必须是矢量图层。")
        if input_layer.crs is None:
            raise UnsupportedBufferInput(
                f"输入图层“{input_layer.name}”没有坐标参考系统，无法安全执行缓冲区分析。"
            )
        display_crs: CRS | None = self._document.display_crs
        if display_crs is None:
            raise UnsupportedBufferInput("当前地图没有坐标参考系统，无法加入缓冲区结果。")

        output_path: Path = request.output_path.expanduser().resolve()
        output_name: str = request.output_layer_name.strip()
        if not output_name:
            raise InvalidBufferParameters("缓冲区输出图层名不能为空。")
        if (
            input_layer.source_path is not None
            and output_path == input_layer.source_path.resolve()
            and output_path.suffix.lower() != ".gpkg"
        ):
            raise InvalidBufferParameters("缓冲区输出位置不能覆盖输入图层源文件。")
        if output_path.exists() and output_path.suffix.lower() != ".gpkg":
            raise InvalidBufferParameters("分析结果输出已存在，请使用新的结果文件或图层名称。")

        calculation_crs: CRS = resolve_buffer_analysis_crs(input_layer, request.analysis_crs)
        prepared_layer: VectorLayer = reproject_vector_layer(input_layer, calculation_crs)
        calculation_distance: float = distance_to_crs_units(
            request.distance,
            request.distance_unit,
            calculation_crs,
        )
        calculated_features = buffer_features(
            prepared_layer,
            request,
            distance=calculation_distance,
        )
        try:
            output_features = reproject_features(
                calculated_features,
                calculation_crs,
                display_crs,
            )
        except BufferAnalysisFailed:
            raise
        except Exception as error:
            raise BufferAnalysisFailed("缓冲区结果无法转换到地图显示坐标系。") from error

        source_layer_name: str | None = (
            output_name if output_path.suffix.lower() == ".gpkg" else None
        )
        output_layer: VectorLayer = VectorLayer.create(
            name=output_name,
            features=output_features,
            crs=display_crs,
            source_path=output_path,
            source_layer_name=source_layer_name,
        )
        self.data_writer.write(output_layer, output_path, (), output_name)
        self._document.add_layer(output_layer)
        self._document.set_active_layer(output_layer.layer_id)
        run: AnalysisRun = self._create_analysis_run(
            algorithm_id="buffer",
            input_layer_ids=(input_layer.layer_id,),
            parameters={
                "geometry_family": input_layer.geometry_family.value,
                "distance": request.distance,
                "distance_unit": request.distance_unit,
                "distance_meters": distance_to_meters(
                    request.distance,
                    request.distance_unit,
                ),
                "side_type": request.side_type,
                "segments": request.segments,
                "cap_style": request.cap_style,
                "join_style": request.join_style,
                "mitre_limit": request.mitre_limit,
                "dissolve": request.dissolve,
                "analysis_crs": (
                    request.analysis_crs.to_string()
                    if request.analysis_crs is not None
                    else None
                ),
                "calculation_crs": calculation_crs.to_string(),
                "output_path": str(output_path),
            },
            output_layer_id=output_layer.layer_id,
            output_path=output_path,
            output_layer_name=output_name,
            started_at=started_at,
            completed_at=self._now(),
            duration_seconds=perf_counter() - started_monotonic,
        )
        self._analysis_runs = self._analysis_runs + (run,)
        self._modified = True
        return BufferAnalysisResult(
            input_layer_id=input_layer.layer_id,
            output_layer_id=output_layer.layer_id,
            output_layer_name=output_name,
            output_path=output_path,
            feature_count=len(output_layer.features),
            snapshot=self.snapshot(),
        )

    def set_display_crs(self, target_crs: CRS) -> WorkspaceSnapshot:
        """设置地图显示坐标系，并从原始数据源原子重建已有图层。"""
        if self._document.display_crs == target_crs:
            self._document.set_display_crs(target_crs)
            self._modified = True
            return self.snapshot()
        if not self._document.layers:
            self._document.set_display_crs(target_crs)
            self._modified = True
            return self.snapshot()

        old_layers: tuple[SpatialLayer, ...] = self._document.layers
        projected_layers: tuple[SpatialLayer, ...] = tuple(
            self._project_layer_from_source(layer, target_crs) for layer in old_layers
        )
        replacement_document: MapDocument = MapDocument()
        replacement_document.set_display_crs(target_crs)
        for old_layer, projected_layer in zip(old_layers, projected_layers, strict=True):
            replacement_document.add_layer(projected_layer)
            replacement_document.set_layer_visibility(
                projected_layer.layer_id,
                self._document.is_visible(old_layer.layer_id),
            )
            if isinstance(projected_layer, VectorLayer):
                replacement_document.set_selection(
                    projected_layer.layer_id,
                    self._document.selected_feature_ids(old_layer.layer_id),
                )
        if self._document.active_layer_id is not None:
            replacement_document.set_active_layer(self._document.active_layer_id)
        self._document = replacement_document
        self._modified = True
        return self.snapshot()

    def create_analysis_environment(self, analysis_crs: CRS | None = None) -> AnalysisEnvironment:
        """创建分析环境；未指定时使用当前地图 CRS 作为明确兜底。"""
        resolved_crs: CRS | None = analysis_crs or self._document.display_crs
        if resolved_crs is None:
            raise ValueError("当前地图没有可用坐标系，请先指定分析坐标系。")
        return AnalysisEnvironment(analysis_crs=resolved_crs)

    def prepare_analysis_layers(
        self,
        layer_ids: tuple[str, ...],
        environment: AnalysisEnvironment,
    ) -> tuple[SpatialLayer, ...]:
        """按分析目标 CRS 准备输入临时副本，不修改工作区原始图层。"""
        prepared_layers: list[SpatialLayer] = []
        for layer_id in layer_ids:
            layer: SpatialLayer = self._find_layer(layer_id)
            if layer.crs == environment.analysis_crs:
                prepared_layers.append(layer)
            else:
                prepared_layers.append(
                    self._project_layer_from_source(layer, environment.analysis_crs)
                )
        return tuple(prepared_layers)

    def _project_layer_from_source(
        self,
        layer: SpatialLayer,
        target_crs: CRS,
    ) -> SpatialLayer:
        """从原始路径重新读取并转换图层，确保转换不覆盖源图层。"""
        if isinstance(layer, VectorLayer) and layer.database_layer_id is not None:
            try:
                projected_database_layer: VectorLayer = self._require_database_service().load_layer(
                    layer.database_layer_id,
                    target_crs,
                )
            except ApplicationError as error:
                raise LayerReprojectionFailed(
                    f"数据库图层“{layer.name}”无法转换到目标坐标系。"
                ) from error
            return VectorLayer.create(
                layer_id=layer.layer_id,
                name=projected_database_layer.name,
                features=projected_database_layer.features,
                crs=projected_database_layer.crs,
                source_layer_name=projected_database_layer.source_layer_name,
                database_layer_id=layer.database_layer_id,
                symbology=layer.symbology,
            )
        if layer.source_path is None:
            raise LayerReprojectionFailed(
                f"图层“{layer.name}”没有原始数据源，无法转换坐标系。"
            )
        try:
            source_layer_name: str | None = (
                layer.source_layer_name if isinstance(layer, VectorLayer) else None
            )
            projected: SpatialLayer = self.data_reader.read(
                layer.source_path,
                target_crs,
                source_layer_name,
            )
        except Exception as error:
            if isinstance(error, ApplicationError):
                raise
            raise LayerReprojectionFailed(f"图层“{layer.name}”坐标系转换失败。") from error
        if isinstance(layer, VectorLayer) and isinstance(projected, VectorLayer):
            return VectorLayer.create(
                layer_id=layer.layer_id,
                name=projected.name,
                features=projected.features,
                crs=projected.crs,
                source_path=projected.source_path,
                source_layer_name=projected.source_layer_name,
                symbology=layer.symbology,
            )
        if isinstance(layer, RasterLayer) and isinstance(projected, RasterLayer):
            restored_raster = RasterLayer.create(
                layer_id=layer.layer_id,
                name=projected.name,
                raster_data=projected.raster_data,
                image_data=projected.image_data,
                valid_mask=projected.valid_mask,
                transform=projected.transform,
                crs=projected.crs,
                bounds=projected.bounds,
                nodata=projected.nodata,
                source_path=projected.source_path,
                symbology=layer.symbology,
            )
            if layer.symbology is None:
                return restored_raster
            return apply_raster_symbology(restored_raster, layer.symbology)
        raise LayerReprojectionFailed(f"图层“{layer.name}”转换后类型发生变化。")

    def _find_layer(self, layer_id: str) -> SpatialLayer:
        """按编号查找工作区图层。"""
        for layer in self._document.layers:
            if layer.layer_id == layer_id:
                return layer
        raise LayerNotFound(f"图层不存在：{layer_id}")

    def export_active_layer(
        self,
        path: Path,
        layer_name: str | None = None,
    ) -> ExportDataResult:
        """将活动图层按当前坐标系导出到指定本地路径。

        矢量图层存在选择集时仅导出选中要素；否则导出全部要素。栅格图层
        始终导出真实分析像元，不使用拉伸后的 RGBA 显示缓存。
        """
        active_layer_id: str | None = self._document.active_layer_id
        if active_layer_id is None:
            raise NoActiveLayer("请先打开并选择一个活动图层。")
        if self.data_writer is None:
            raise DataWriteFailed("空间数据导出服务尚未配置。")

        layer: SpatialLayer = next(
            item for item in self._document.layers if item.layer_id == active_layer_id
        )
        selected_feature_ids: tuple[FeatureId, ...] = (
            self._document.selected_feature_ids(active_layer_id)
            if isinstance(layer, VectorLayer)
            else ()
        )
        if layer_name is None:
            self.data_writer.write(layer, path, selected_feature_ids)
        else:
            self.data_writer.write(layer, path, selected_feature_ids, layer_name)
        exported_feature_count: int | None = None
        if isinstance(layer, VectorLayer):
            exported_feature_count = (
                len(selected_feature_ids) if selected_feature_ids else len(layer.features)
            )
        return ExportDataResult(
            path=path.expanduser().resolve(),
            layer_id=active_layer_id,
            exported_feature_count=exported_feature_count,
        )

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
            layer
            for layer in self._document.layers
            if isinstance(layer, VectorLayer) and layer.layer_id == active_layer_id
        )
        remaining_layers: tuple[VectorLayer, ...] = tuple(
            layer
            for layer in reversed(self._document.layers)
            if isinstance(layer, VectorLayer) and layer.layer_id != active_layer_id
        )
        return active_layers + remaining_layers

    def _require_project_store(self) -> ProjectStore:
        """返回工程存储端口，未组装时给出应用层错误。"""
        if self.project_store is None:
            raise ProjectStoreNotConfigured("工程存储服务尚未配置。")
        return self.project_store

    def _require_database_service(self) -> DatabaseService:
        """返回数据库服务；未组装时给出统一应用异常。"""
        if self.database_service is None:
            raise DatabaseNotConfigured("数据库服务尚未配置。")
        return self.database_service

    def _relative_to_project(self, path: Path) -> str:
        """返回相对于当前工程目录的路径。"""
        if self._project_path is None:
            raise ProjectNotSaved("当前工程尚未命名。")
        try:
            relative_path: Path = path.resolve().relative_to(self._project_path.parent.resolve())
            return str(relative_path).replace("\\", "/")
        except ValueError:
            return str(path.resolve()).replace("\\", "/")

    def _append_failed_analysis_run(
        self,
        request: BufferRequest,
        started_at: str,
        started_monotonic: float,
        error: Exception,
    ) -> None:
        """将失败的缓冲区执行写入历史，保留输入和用户参数便于回溯。"""
        parameters: dict[str, object] = self._buffer_request_parameters(request)
        input_layer: SpatialLayer | None = next(
            (layer for layer in self._document.layers if layer.layer_id == request.input_layer_id),
            None,
        )
        if isinstance(input_layer, VectorLayer):
            parameters["geometry_family"] = input_layer.geometry_family.value
        run: AnalysisRun = self._create_analysis_run(
            algorithm_id="buffer",
            input_layer_ids=(request.input_layer_id,),
            parameters=parameters,
            status="failed",
            message=str(error),
            started_at=started_at,
            completed_at=self._now(),
            duration_seconds=perf_counter() - started_monotonic,
        )
        self._analysis_runs = self._analysis_runs + (run,)
        self._modified = True

    def _create_analysis_run(
        self,
        algorithm_id: str,
        input_layer_ids: tuple[str, ...],
        parameters: Mapping[str, object],
        output_layer_id: str | None = None,
        output_path: Path | None = None,
        output_layer_name: str | None = None,
        run_id: str | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
        duration_seconds: float | None = None,
        status: str = "completed",
        message: str | None = None,
    ) -> AnalysisRun:
        """为分析服务创建统一格式的不可变历史记录。"""
        resolved_run_id: str = run_id or uuid4().hex
        parent_run_ids: tuple[str, ...] = tuple(
            run.run_id
            for run in self._analysis_runs
            if set(run.output_layer_ids).intersection(input_layer_ids)
        )
        outputs: tuple[AnalysisOutputReference, ...] = ()
        if output_layer_id is not None and output_path is not None:
            source_path: str = (
                self._relative_to_project(output_path)
                if self._project_path is not None
                else str(output_path.expanduser().resolve()).replace("\\", "/")
            )
            outputs = (
                AnalysisOutputReference(
                    layer_id=output_layer_id,
                    source_path=source_path,
                    source_layer_name=output_layer_name,
                ),
            )
        return AnalysisRun(
            run_id=resolved_run_id,
            algorithm_id=algorithm_id,
            input_layer_ids=input_layer_ids,
            parameters=parameters,
            output_layer_ids=((output_layer_id,) if output_layer_id is not None else ()),
            outputs=outputs,
            parent_run_ids=parent_run_ids,
            status=status,
            created_at=started_at or self._now(),
            completed_at=completed_at or self._now(),
            duration_seconds=duration_seconds,
            message=message,
        )

    @staticmethod
    def _buffer_request_parameters(request: BufferRequest) -> dict[str, object]:
        """将缓冲区请求转换为可持久化的历史参数。"""
        return {
            "distance": request.distance,
            "distance_unit": request.distance_unit,
            "distance_meters": distance_to_meters(request.distance, request.distance_unit),
            "side_type": request.side_type,
            "segments": request.segments,
            "cap_style": request.cap_style,
            "join_style": request.join_style,
            "mitre_limit": request.mitre_limit,
            "dissolve": request.dissolve,
            "analysis_crs": (
                request.analysis_crs.to_string() if request.analysis_crs is not None else None
            ),
            "output_path": str(request.output_path.expanduser().resolve()),
            "output_layer_name": request.output_layer_name,
        }

    @staticmethod
    def _safe_algorithm_id(algorithm_id: str) -> str:
        """将算法编号转换为适合作为 GeoPackage 图层名的前缀。"""
        normalized: str = "".join(
            character if character.isalnum() or character == "_" else "_"
            for character in algorithm_id.strip().lower()
        ).strip("_")
        return normalized or "analysis"

    @staticmethod
    def _now() -> str:
        """返回当前 UTC ISO 时间。"""
        return datetime.now(timezone.utc).isoformat()
