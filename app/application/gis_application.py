"""GIS 应用功能统一入口。"""

from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import numpy as np
from affine import Affine
from pyproj import CRS
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry

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
    EmptyOverlayResult,
    InvalidBufferParameters,
    InvalidOverlayParameters,
    InvalidRasterCalculatorParameters,
    LayerNotFound,
    LayerReprojectionFailed,
    NoActiveLayer,
    OverlayAnalysisFailed,
    ProjectNotSaved,
    ProjectStoreNotConfigured,
    RasterBandAlignmentError,
    RasterCalculatorFailed,
    UnsupportedBufferInput,
    UnsupportedOverlayInput,
)
from app.application.overlay_analysis import (
    OverlayRequest,
    operation_label,
    overlay_features,
)
from app.application.ports import DataReader, DataWriter, ProjectStore
from app.application.project_models import (
    AnalysisOutputReference,
    AnalysisRun,
    MapBookmark,
    MapViewState,
)
from app.application.project_service import ProjectService
from app.application.raster_calculator import (
    RasterCalculatorRequest,
    compute_raster_expression,
    generate_display_image,
    validate_band_alignment,
)
from app.application.results import (
    AnalysisResultPersisted,
    BufferAnalysisResult,
    ExportDataResult,
    LayerSnapshot,
    OpenDataResult,
    OpenVectorResult,
    OverlayAnalysisResult,
    ProjectOpenResult,
    ProjectSaveResult,
    RasterCalculatorResult,
    SelectedFeature,
    SelectionResult,
    WorkspaceSnapshot,
)
from app.application.symbology_service import (
    apply_raster_symbology,
    create_graduated_symbology,
    create_unique_value_symbology,
)
from app.domain.feature import AttributeValue, Feature, FeatureId
from app.domain.labeling import LabelingConfig
from app.domain.layer_style import GeometryFamily
from app.domain.layout import LayoutDocument
from app.domain.map_document import MapDocument
from app.domain.raster_layer import RasterLayer
from app.domain.spatial_layer import SpatialLayer
from app.domain.symbology import RasterSymbology, VectorSymbology
from app.domain.vector_layer import VectorLayer


def _chaikin_smooth(geometry: BaseGeometry, iterations: int) -> BaseGeometry:
    """Chaikin 角切算法：每轮在每条边的 1/4 和 3/4 处插入新顶点。

    对 LineString 和 Polygon 外环逐轮平滑，Multi 几何递归处理。
    """
    if iterations <= 0:
        return geometry

    geom: BaseGeometry = geometry
    for _ in range(iterations):
        geom = _chaikin_pass(geom)
    return geom


def _chaikin_pass(geometry: BaseGeometry) -> BaseGeometry:
    """执行单轮 Chaikin 角切。"""
    g: BaseGeometry = geometry
    gtype: str = g.geom_type

    if gtype == "LineString":
        return _chaikin_line(g)
    if gtype == "Polygon":
        return Polygon(
            _chaikin_line(LineString(g.exterior.coords)),
            [_chaikin_line(LineString(r.coords)) for r in g.interiors],
        )
    if gtype == "MultiLineString":
        return MultiLineString(
            [_chaikin_line(LineString(m.coords)) for m in g.geoms]
        )
    if gtype == "MultiPolygon":
        return MultiPolygon([_chaikin_pass(p) for p in g.geoms])
    return geometry  # Point, etc. 不做平滑。


def _chaikin_line(line: LineString) -> LineString:
    """对单条线的坐标执行一轮 Chaikin 1/4-3/4 角切。"""
    coords: list[tuple[float, float]] = list(line.coords)
    if len(coords) < 2:
        return line
    smoothed: list[tuple[float, float]] = [coords[0]]
    for i in range(len(coords) - 1):
        x1, y1 = coords[i]
        x2, y2 = coords[i + 1]
        q1: tuple[float, float] = (
            0.75 * x1 + 0.25 * x2, 0.75 * y1 + 0.25 * y2
        )
        q2: tuple[float, float] = (
            0.25 * x1 + 0.75 * x2, 0.25 * y1 + 0.75 * y2
        )
        smoothed.append(q1)
        smoothed.append(q2)
    smoothed.append(coords[-1])
    return LineString(smoothed)


def _geometry_priority(geom_type: str) -> int:
    """返回几何类型的点选优先级权值：点 0 < 线 1 < 面 2。

    配合容差比例罚分使用，使点选时点/线优先于面要素。
    """
    if geom_type in ("Point", "MultiPoint"):
        return 0
    if geom_type in ("LineString", "MultiLineString", "LinearRing"):
        return 1
    return 2  # Polygon, MultiPolygon, GeometryCollection


def _attribute_match(
    attr_value: object, operator: str, query_value: str
) -> bool:
    """判断单个要素的属性值是否满足查询条件。

    参数:
        attr_value: 要素字段值（可能为 None、str、int、float 等）。
        operator: 比较运算符。
        query_value: 查询输入值字符串。

    返回:
        True 表示匹配。
    """
    if operator == "is_null":
        return attr_value is None or attr_value == ""
    if operator == "not_null":
        return attr_value is not None and attr_value != ""

    attr_str: str = str(attr_value) if attr_value is not None else ""
    if operator == "contains":
        return query_value.lower() in attr_str.lower()

    # 数值运算符：尝试数字比较，失败则回退到字符串比较。
    try:
        attr_num: float = float(attr_str)
        query_num: float = float(query_value)
    except (ValueError, TypeError):
        # 字符串比较。
        if operator == "=":
            return attr_str == query_value
        if operator == "!=":
            return attr_str != query_value
        if operator == ">":
            return attr_str > query_value
        if operator == "<":
            return attr_str < query_value
        if operator == ">=":
            return attr_str >= query_value
        if operator == "<=":
            return attr_str <= query_value
        return False

    if operator == "=":
        return attr_num == query_num
    if operator == "!=":
        return attr_num != query_num
    if operator == ">":
        return attr_num > query_num
    if operator == "<":
        return attr_num < query_num
    if operator == ">=":
        return attr_num >= query_num
    if operator == "<=":
        return attr_num <= query_num
    return False


class GisApplication:
    """通过较小公开接口统一编排图层管理和空间查询流程。"""

    # 空间数据读取端口：由启动组装模块注入真实或测试适配器。
    data_reader: DataReader
    # 空间数据写入端口：为空时保留只读应用服务兼容能力。
    data_writer: DataWriter | None

    # 支持原地写回的矢量后缀：覆盖写入器只能安全重建整文件的格式。
    _APPENDABLE_SUFFIXES: frozenset[str] = frozenset({".shp", ".geojson"})

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
        # 地图书签：按名称保存当前会话的地图视图定位。
        self._bookmarks: dict[str, MapViewState] = {}

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

    def add_bookmark(self, name: str, view_state: MapViewState) -> None:
        """添加一个地图书签；同名称书签会覆盖旧定位。"""
        if not name.strip():
            raise ValueError("书签名称不能为空。")
        self._bookmarks[name] = view_state

    def remove_bookmark(self, name: str) -> None:
        """删除指定名称的地图书签。"""
        if name not in self._bookmarks:
            raise ValueError(f"书签不存在：{name}")
        del self._bookmarks[name]

    def bookmarks(self) -> tuple[MapBookmark, ...]:
        """返回当前会话保存的只读地图书签。"""
        return tuple(
            MapBookmark(name=name, view_state=view_state)
            for name, view_state in self._bookmarks.items()
        )

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

    def create_empty_layer(
        self,
        name: str,
        geometry_family: GeometryFamily,
        crs: CRS | None,
    ) -> OpenDataResult:
        """新建一个空白矢量图层并加入当前地图文档。

        参数:
            name: 图层显示名称。
            geometry_family: 几何类别，决定图层的要素类型（点/线/面）。
            crs: 坐标参考系统，需与当前地图 CRS 一致（或同时为空）。

        返回:
            包含新建图层编号和最新工作区快照的结果。
        """
        # 为该空白图层预留本地文件路径，支持后续新增要素等操作。
        # 空图层无需立即写出文件——首次新增要素时由 append_feature 写回。
        base_dir: Path = (
            self._project_path.parent
            if self._project_path is not None
            else Path.cwd()
        )
        safe_name: str = "".join(
            c if c not in '<>:"/\\|?*' else "_" for c in name
        ).rstrip(".")
        output_path: Path = (base_dir / f"{safe_name}.geojson").resolve()

        layer: VectorLayer = VectorLayer.create(
            name=name,
            features=(),
            crs=crs,
            source_path=output_path,
            geometry_family=geometry_family,
        )
        return self.add_layer(layer)

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

    def set_layer_opacity(self, layer_id: str, opacity: float) -> WorkspaceSnapshot:
        """设置指定图层透明度并返回最新快照。"""
        try:
            self._document.set_layer_opacity(layer_id, opacity)
        except KeyError as error:
            raise LayerNotFound(f"图层不存在：{layer_id}") from error
        except ValueError as error:
            raise ValueError(f"透明度设置无效：{error}") from error
        self._modified = True
        return self.snapshot()

    def set_layer_blend_mode(self, layer_id: str, blend_mode: str) -> WorkspaceSnapshot:
        """设置指定图层的混合模式并返回最新快照。"""
        try:
            self._document.set_layer_blend_mode(layer_id, blend_mode)
        except KeyError as error:
            raise LayerNotFound(f"图层不存在：{layer_id}") from error
        except ValueError as error:
            raise ValueError(f"混合模式设置无效：{error}") from error
        self._modified = True
        return self.snapshot()

    def set_layer_scale_range(
        self,
        layer_id: str,
        min_scale: float | None,
        max_scale: float | None,
    ) -> WorkspaceSnapshot:
        """设置图层显示比例尺范围并返回最新快照。"""
        try:
            self._document.set_layer_scale_range(layer_id, min_scale, max_scale)
        except KeyError as error:
            raise LayerNotFound(f"图层不存在：{layer_id}") from error
        except ValueError as error:
            raise ValueError(f"显示比例范围无效：{error}") from error
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

    def clear_active_layer(self) -> None:
        """取消当前活动图层。"""
        self._document.clear_active_layer()
        self._modified = True

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
            labeling=layer.labeling,
        )
        self._document.replace_layer(updated_layer)
        self._modified = True
        return self.snapshot()

    def set_layer_labeling(
        self,
        layer_id: str,
        labeling: LabelingConfig | None,
    ) -> WorkspaceSnapshot:
        """替换矢量图层标注配置并保留图层身份和其他样式。"""
        layer: SpatialLayer = self._find_layer(layer_id)
        if not isinstance(layer, VectorLayer):
            raise ValueError("标注只能应用到矢量图层。")
        updated_layer = VectorLayer.create(
            layer_id=layer.layer_id,
            name=layer.name,
            features=layer.features,
            crs=layer.crs,
            source_path=layer.source_path,
            source_layer_name=layer.source_layer_name,
            database_layer_id=layer.database_layer_id,
            symbology=layer.symbology,
            labeling=labeling,
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

    def identify_features(
        self,
        point: Point,
        tolerance: float,
        query_layer_ids: tuple[str, ...] | None = None,
    ) -> list[SelectedFeature]:
        """返回容差范围内所有可见图层的命中要素，按加权距离排序。

        点要素优先于线，线优先于面——避免点击面内线时被面抢走。
        与 select_point 不同，本方法不清除或修改选择状态，
        仅收集全部候选要素供界面弹出候选项使用。

        参数:
            query_layer_ids: 可选的当前视图可查询图层编号集合；为空时使用所有可见图层。
        """
        if tolerance < 0:
            raise ValueError("点选容差不能小于零。")
        candidates: list[tuple[float, SelectedFeature]] = []
        queryable_ids: set[str] | None = (
            set(query_layer_ids) if query_layer_ids is not None else None
        )
        # 几何类型罚分系数：点 0、线 1、面 2，每级罚 tolerance/100。
        type_penalty: float = tolerance * 0.01
        ordered_layers: tuple[VectorLayer, ...] = self._point_query_order()
        for layer in ordered_layers:
            if not self._document.is_visible(layer.layer_id) or (
                queryable_ids is not None and layer.layer_id not in queryable_ids
            ):
                continue
            for feature in layer.features:
                if feature.geometry.is_empty:
                    continue
                distance: float = float(feature.geometry.distance(point))
                if distance <= tolerance:
                    effective: float = (
                        distance
                        + _geometry_priority(feature.geometry.geom_type) * type_penalty
                    )
                    candidates.append(
                        (
                            effective,
                            SelectedFeature(
                                layer_id=layer.layer_id,
                                layer_name=layer.name,
                                feature=feature,
                            ),
                        )
                    )
        candidates.sort(key=lambda item: item[0])
        return [candidate for _, candidate in candidates]

    def select_point(
        self,
        point: Point,
        tolerance: float,
        add_to_selection: bool = False,
        query_layer_ids: tuple[str, ...] | None = None,
    ) -> SelectionResult:
        """选择可见图层中容差范围内优先级最高的最近要素。

        点/线要素优先于面要素：点击面内河流时不会误选行政区。

        参数:
            point: 查询用的地图坐标点。
            tolerance: 容差距离（地图单位）。
            add_to_selection: 为 True 时不先清除已有选择，在最近要素上
                切换其选中状态（已选中则取消，未选中则加入）。
            query_layer_ids: 可选的当前视图可查询图层编号集合；为空时使用所有可见图层。
        """
        if tolerance < 0:
            raise ValueError("点选容差不能小于零。")
        # 追加模式下保留已有选择，否则先清除。
        if not add_to_selection:
            self._document.clear_selection()
        self._modified = True
        # 几何类型罚分系数。
        type_penalty: float = tolerance * 0.01
        queryable_ids: set[str] | None = (
            set(query_layer_ids) if query_layer_ids is not None else None
        )
        # 点选先查活动图层，再按视觉上的顶层到下层查找。
        ordered_layers: tuple[VectorLayer, ...] = self._point_query_order()
        layer: VectorLayer
        for layer in ordered_layers:
            if not self._document.is_visible(layer.layer_id) or (
                queryable_ids is not None and layer.layer_id not in queryable_ids
            ):
                continue
            nearest_feature: Feature | None = None
            nearest_effective: float = float("inf")
            feature: Feature
            for feature in layer.features:
                if feature.geometry.is_empty:
                    continue
                distance: float = float(feature.geometry.distance(point))
                if distance > tolerance:
                    continue
                effective: float = (
                    distance
                    + _geometry_priority(feature.geometry.geom_type) * type_penalty
                )
                if effective < nearest_effective:
                    nearest_feature = feature
                    nearest_effective = effective
            if nearest_feature is not None:
                if add_to_selection:
                    # 切换：已选中则移除，未选中则加入。
                    existing: tuple[FeatureId, ...] = (
                        self._document.selected_feature_ids(layer.layer_id)
                    )
                    if nearest_feature.fid in existing:
                        updated: tuple[FeatureId, ...] = tuple(
                            fid for fid in existing if fid != nearest_feature.fid
                        )
                    else:
                        updated = existing + (nearest_feature.fid,)
                    self._document.set_selection(layer.layer_id, updated)
                else:
                    self._document.set_selection(layer.layer_id, (nearest_feature.fid,))
                selected_feature: SelectedFeature = SelectedFeature(
                    layer_id=layer.layer_id,
                    layer_name=layer.name,
                    feature=nearest_feature,
                )
                return SelectionResult(features=(selected_feature,), snapshot=self.snapshot())
        return SelectionResult(features=(), snapshot=self.snapshot())

    def select_rectangle(
        self,
        rectangle: Polygon,
        add_to_selection: bool = False,
        query_layer_ids: tuple[str, ...] | None = None,
    ) -> SelectionResult:
        """选择全部可见图层中与给定矩形相交的有效要素。

        参数:
            rectangle: 查询用的地图坐标矩形多边形。
            add_to_selection: 为 True 时不先清除已有选择，将相交要素
                合并到各图层的当前选择集中。
            query_layer_ids: 可选的当前视图可查询图层编号集合；为空时使用所有可见图层。
        """
        if not add_to_selection:
            self._document.clear_selection()
        selected_features: list[SelectedFeature] = []
        queryable_ids: set[str] | None = (
            set(query_layer_ids) if query_layer_ids is not None else None
        )
        spatial_layer: SpatialLayer
        for spatial_layer in self._document.layers:
            # 栅格没有独立矢量要素，不参与几何相交查询。
            if isinstance(spatial_layer, RasterLayer):
                continue
            layer: VectorLayer = spatial_layer
            if not self._document.is_visible(layer.layer_id) or (
                queryable_ids is not None and layer.layer_id not in queryable_ids
            ):
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
            if add_to_selection and feature_ids:
                # 合并：已有选择 + 新命中（去重）。
                existing: tuple[FeatureId, ...] = (
                    self._document.selected_feature_ids(layer.layer_id)
                )
                merged: dict[FeatureId, None] = dict.fromkeys(existing)
                for fid in feature_ids:
                    merged[fid] = None
                self._document.set_selection(layer.layer_id, tuple(merged))
            else:
                self._document.set_selection(layer.layer_id, tuple(feature_ids))
        self._modified = True
        return SelectionResult(features=tuple(selected_features), snapshot=self.snapshot())

    def select_by_attribute(
        self, layer_id: str, field_name: str, operator: str, value: str
    ) -> SelectionResult:
        """按属性条件筛选指定图层的要素并设为选中。

        参数:
            layer_id: 目标矢量图层编号。
            field_name: 用于比对的属性字段名。
            operator: 比较运算符（=, !=, >, <, >=, <=, contains, is_null, not_null）。
            value: 比较值；is_null/not_null 时忽略。

        返回:
            包含全部命中要素的 SelectionResult。
        """
        layer: SpatialLayer = self._find_layer(layer_id)
        if not isinstance(layer, VectorLayer):
            raise ApplicationError("属性查询仅支持矢量图层。")
        matched_fids: list[FeatureId] = []
        matched_features: list[SelectedFeature] = []
        for feature in layer.features:
            attr_value = feature.attributes.get(field_name)
            if _attribute_match(attr_value, operator, value):
                matched_fids.append(feature.fid)
                matched_features.append(
                    SelectedFeature(
                        layer_id=layer.layer_id,
                        layer_name=layer.name,
                        feature=feature,
                    )
                )
        self._document.set_selection(layer_id, tuple(matched_fids))
        self._modified = True
        return SelectionResult(
            features=tuple(matched_features), snapshot=self.snapshot()
        )

    def append_feature(
        self,
        layer_id: str,
        geometry: BaseGeometry,
        attributes: Mapping[str, AttributeValue],
    ) -> WorkspaceSnapshot:
        """向指定矢量图层追加一个要素并写回源文件。

        参数:
            layer_id: 目标图层编号。
            geometry: 数字化生成的地图坐标系几何对象。
            attributes: 要素属性字典。

        返回:
            追加完成后的完整工作区快照。

        异常:
            LayerNotFound: 图层不存在时抛出。
            ApplicationError: 图层不是矢量图层、几何类型与图层不符，
                或源文件格式不支持原地写回时抛出。
            DataWriteFailed: 数据写出服务未配置或写回失败时抛出。
        """
        layer: SpatialLayer = self._find_layer(layer_id)
        if not isinstance(layer, VectorLayer):
            raise ApplicationError("只能向矢量图层追加要素。")
        self._validate_append_geometry(layer, geometry)
        if layer.source_path is None:
            raise ApplicationError(
                f"图层「{layer.name}」没有本地数据文件，暂不支持追加要素。"
            )
        if layer.source_path.suffix.lower() not in self._APPENDABLE_SUFFIXES:
            raise ApplicationError(
                f"图层「{layer.name}」源文件格式 {layer.source_path.suffix} "
                "暂不支持追加要素，请使用 Shapefile 或 GeoJSON 图层。"
            )
        if self.data_writer is None:
            raise DataWriteFailed("空间数据写出服务尚未配置。")

        feature: Feature = Feature(
            fid=self._next_feature_id(layer),
            geometry=geometry,
            attributes=dict(attributes),
        )
        updated: VectorLayer = VectorLayer.create(
            layer_id=layer.layer_id,
            name=layer.name,
            features=layer.features + (feature,),
            crs=layer.crs,
            source_path=layer.source_path,
            source_layer_name=layer.source_layer_name,
            database_layer_id=layer.database_layer_id,
            symbology=layer.symbology,
            labeling=layer.labeling,
        )
        self._document.replace_layer(updated)
        self._write_layer_to_source(updated)
        self._modified = True
        return self.snapshot()

    def delete_feature(self, layer_id: str, fid: FeatureId) -> WorkspaceSnapshot:
        """从图层中删除指定要素并写回磁盘。

        若删除后图层无要素，则移除整个图层。
        """
        layer: SpatialLayer = self._find_layer(layer_id)
        if not isinstance(layer, VectorLayer):
            raise ApplicationError("只能删除矢量图层中的要素。")
        remaining: tuple[Feature, ...] = tuple(
            f for f in layer.features if f.fid != fid
        )
        if not remaining:
            return self.remove_layer(layer_id)
        updated: VectorLayer = VectorLayer.create(
            layer_id=layer.layer_id,
            name=layer.name,
            features=remaining,
            crs=layer.crs,
            source_path=layer.source_path,
            source_layer_name=layer.source_layer_name,
            database_layer_id=layer.database_layer_id,
            symbology=layer.symbology,
            labeling=layer.labeling,
        )
        self._document.replace_layer(updated)
        self._document.clear_selection()
        self._write_layer_to_source(updated)
        self._modified = True
        return self.snapshot()

    def update_feature_attributes(
        self,
        layer_id: str,
        fid: FeatureId,
        attributes: Mapping[str, AttributeValue],
    ) -> WorkspaceSnapshot:
        """修改指定要素的属性字段并写回磁盘。"""
        layer: SpatialLayer = self._find_layer(layer_id)
        if not isinstance(layer, VectorLayer):
            raise ApplicationError("只能修改矢量图层中的要素属性。")
        updated_features: tuple[Feature, ...] = tuple(
            Feature(fid=f.fid, geometry=f.geometry, attributes=attributes)
            if f.fid == fid
            else f
            for f in layer.features
        )
        updated_layer: VectorLayer = VectorLayer.create(
            layer_id=layer.layer_id,
            name=layer.name,
            features=updated_features,
            crs=layer.crs,
            source_path=layer.source_path,
            source_layer_name=layer.source_layer_name,
            database_layer_id=layer.database_layer_id,
            symbology=layer.symbology,
            labeling=layer.labeling,
        )
        self._document.replace_layer(updated_layer)
        self._write_layer_to_source(updated_layer)
        self._modified = True
        return self.snapshot()

    def update_feature_geometry(
        self, layer_id: str, fid: FeatureId, geometry: object
    ) -> WorkspaceSnapshot:
        """修改指定要素的几何形状并写回磁盘。"""
        layer: SpatialLayer = self._find_layer(layer_id)
        if not isinstance(layer, VectorLayer):
            raise ApplicationError("只能修改矢量图层中的要素几何。")
        updated_features: tuple[Feature, ...] = tuple(
            Feature(fid=f.fid, geometry=geometry, attributes=f.attributes)
            if f.fid == fid
            else f
            for f in layer.features
        )
        updated_layer: VectorLayer = VectorLayer.create(
            layer_id=layer.layer_id,
            name=layer.name,
            features=updated_features,
            crs=layer.crs,
            source_path=layer.source_path,
            source_layer_name=layer.source_layer_name,
            database_layer_id=layer.database_layer_id,
            symbology=layer.symbology,
            labeling=layer.labeling,
        )
        self._document.replace_layer(updated_layer)
        self._write_layer_to_source(updated_layer)
        self._modified = True
        return self.snapshot()

    def simplify_feature_geometry(
        self, layer_id: str, fid: FeatureId, tolerance: float
    ) -> WorkspaceSnapshot:
        """对选中要素执行 Douglas-Peucker 线简化。

        参数:
            tolerance: 简化容差（地图单位），越大顶点越少。
        """
        layer: SpatialLayer = self._find_layer(layer_id)
        if not isinstance(layer, VectorLayer):
            raise ApplicationError("只能简化矢量图层中的要素。")
        target: Feature | None = next(
            (f for f in layer.features if f.fid == fid), None
        )
        if target is None:
            raise ApplicationError("未找到目标要素。")
        simplified = target.geometry.simplify(tolerance, preserve_topology=True)
        return self.update_feature_geometry(layer_id, fid, simplified)

    def smooth_feature_geometry(
        self, layer_id: str, fid: FeatureId, iterations: int = 2
    ) -> WorkspaceSnapshot:
        """对选中要素执行 Chaikin 角切平滑。

        参数:
            iterations: 平滑迭代次数，1-5，越大越平滑。
        """
        layer: SpatialLayer = self._find_layer(layer_id)
        if not isinstance(layer, VectorLayer):
            raise ApplicationError("只能平滑矢量图层中的要素。")
        target: Feature | None = next(
            (f for f in layer.features if f.fid == fid), None
        )
        if target is None:
            raise ApplicationError("未找到目标要素。")
        smoothed = _chaikin_smooth(target.geometry, iterations)
        return self.update_feature_geometry(layer_id, fid, smoothed)

    def replace_layer_features(
        self, layer_id: str, features: tuple[Feature, ...]
    ) -> WorkspaceSnapshot:
        """用新要素集合替换图层内容并写回磁盘。

        用于撤销/重做等需要批量恢复要素的场景。
        若 features 为空则移除整个图层。
        """
        if not features:
            return self.remove_layer(layer_id)
        layer: SpatialLayer = self._find_layer(layer_id)
        if not isinstance(layer, VectorLayer):
            raise ApplicationError("只能替换矢量图层的要素。")
        updated: VectorLayer = VectorLayer.create(
            layer_id=layer.layer_id,
            name=layer.name,
            features=features,
            crs=layer.crs,
            source_path=layer.source_path,
            source_layer_name=layer.source_layer_name,
            database_layer_id=layer.database_layer_id,
            symbology=layer.symbology,
            labeling=layer.labeling,
        )
        self._document.replace_layer(updated)
        self._write_layer_to_source(updated)
        self._modified = True
        return self.snapshot()

    def _write_layer_to_source(self, layer: VectorLayer) -> None:
        """将图层写回其源文件。"""
        if layer.source_path is None:
            return
        if self.data_writer is None:
            raise DataWriteFailed("空间数据写出服务尚未配置。")
        self.data_writer.write(
            layer, layer.source_path, (), layer.source_layer_name
        )

    @staticmethod
    def _validate_append_geometry(
        layer: VectorLayer, geometry: BaseGeometry
    ) -> None:
        """校验追加几何与图层几何类别一致，混合图层接受任意类型。"""
        if layer.geometry_family == GeometryFamily.MIXED:
            return
        family_by_type: dict[str, GeometryFamily] = {
            "Point": GeometryFamily.POINT,
            "MultiPoint": GeometryFamily.POINT,
            "LineString": GeometryFamily.LINE,
            "MultiLineString": GeometryFamily.LINE,
            "Polygon": GeometryFamily.POLYGON,
            "MultiPolygon": GeometryFamily.POLYGON,
        }
        family: GeometryFamily | None = family_by_type.get(geometry.geom_type)
        if family is None or family != layer.geometry_family:
            raise ApplicationError(
                f"几何类型与图层「{layer.name}」不符，无法追加要素。"
            )

    @staticmethod
    def _next_feature_id(layer: VectorLayer) -> FeatureId:
        """返回图层下一个可用的数值要素编号。

        文件格式不保存要素编号，编号只存在于内存图层中，
        追加时取现有最大数值编号加一，保证选择与撤销逻辑稳定。
        """
        max_id: int = max(
            (feature.fid for feature in layer.features if isinstance(feature.fid, int)),
            default=0,
        )
        return max_id + 1

    def set_selection(
        self, layer_id: str, feature_ids: tuple[FeatureId, ...]
    ) -> WorkspaceSnapshot:
        """直接设置指定图层的要素选择集合。

        参数:
            layer_id: 需要更新选择状态的图层编号。
            feature_ids: 待选中的要素编号元组；空元组表示取消选择。

        返回:
            包含更新后选择状态的工作区快照。
        """
        try:
            self._document.clear_selection()
            if feature_ids:
                self._document.set_selection(layer_id, feature_ids)
        except (KeyError, ValueError) as error:
            raise ApplicationError(str(error)) from error
        self._modified = True
        return self.snapshot()

    def restore_selections(
        self, selections: dict[str, tuple[FeatureId, ...]]
    ) -> WorkspaceSnapshot:
        """一次性恢复多图层选择集合，避免逐层 set_selection 互相覆盖。

        参数:
            selections: {layer_id: feature_ids} 映射。

        返回:
            更新后选择状态的工作区快照。
        """
        self._document.clear_selection()
        for layer_id, feature_ids in selections.items():
            try:
                self._document.set_selection(layer_id, feature_ids)
            except (KeyError, ValueError):
                # 图层已删除或要素编号已失效时跳过该图层，继续恢复其余选择。
                continue
        self._modified = True
        return self.snapshot()

    def clear_selection(self) -> SelectionResult:
        """清除全部图层选择并返回空选择结果。"""
        had_selection: bool = any(
            self._document.selected_feature_ids(layer.layer_id)
            for layer in self._document.layers
        )
        self._document.clear_selection()
        if had_selection:
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
            layout_state=loaded.manifest.layout_state,
        )

    def save_project(
        self,
        path: Path | None = None,
        view_state: MapViewState | None = None,
        layout_document: LayoutDocument | None = None,
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
            layout_document=layout_document,
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
                "geometry_family": self._geometry_family_value(input_layer),
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
            old_min_scale, old_max_scale = self._document.layer_scale_range(
                old_layer.layer_id
            )
            replacement_document.set_layer_opacity(
                projected_layer.layer_id,
                self._document.layer_opacity(old_layer.layer_id),
            )
            replacement_document.set_layer_blend_mode(
                projected_layer.layer_id,
                self._document.layer_blend_mode(old_layer.layer_id),
            )
            replacement_document.set_layer_scale_range(
                projected_layer.layer_id,
                old_min_scale,
                old_max_scale,
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
                labeling=layer.labeling,
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
                labeling=layer.labeling,
            )
        if isinstance(layer, RasterLayer) and isinstance(projected, RasterLayer):
            restored_raster = projected.with_identity(
                layer_id=layer.layer_id,
                name=projected.name,
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
                opacity=self._document.layer_opacity(layer.layer_id),
                blend_mode=self._document.layer_blend_mode(layer.layer_id),
                min_scale_percent=self._document.layer_scale_range(layer.layer_id)[0],
                max_scale_percent=self._document.layer_scale_range(layer.layer_id)[1],
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
            parameters["geometry_family"] = self._geometry_family_value(input_layer)
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

    def overlay_analysis(self, request: OverlayRequest) -> OverlayAnalysisResult:
        """执行叠加分析，并为成功或失败的执行追加一条历史记录。

        参数:
            request: 两个输入图层、叠加操作类型和输出位置等分析参数。

        返回:
            包含输出图层编号、写出路径、要素数量和最新工作区快照的结果。

        异常:
            ApplicationError: 分析参数、输入数据或结果写出失败时抛出。
        """
        started_at: str = self._now()
        started_monotonic: float = perf_counter()
        try:
            return self._execute_overlay_analysis(request, started_at, started_monotonic)
        except Exception as error:
            self._append_failed_overlay_analysis(request, started_at, started_monotonic, error)
            raise

    def _execute_overlay_analysis(
        self,
        request: OverlayRequest,
        started_at: str,
        started_monotonic: float,
    ) -> OverlayAnalysisResult:
        """执行叠加分析、写出结果并将结果图层加入当前工作区。

        参数:
            request: 两个输入图层、叠加操作类型和输出位置等分析参数。
            started_at: 分析开始的 UTC 时间。
            started_monotonic: 分析开始的单调时钟值。

        返回:
            包含输出图层编号、写出路径、要素数量和最新工作区快照的结果。

        异常:
            UnsupportedOverlayInput: 输入图层不是有坐标系的矢量图层。
            DataWriteFailed: 输出服务未配置或结果无法写出。
            ApplicationError: 叠加计算失败或结果为空。
        """
        if self.data_writer is None:
            raise DataWriteFailed("空间数据写出服务尚未配置。")

        input_layer: SpatialLayer = self._find_layer(request.input_layer_id)
        overlay_layer: SpatialLayer = self._find_layer(request.overlay_layer_id)
        if not isinstance(input_layer, VectorLayer):
            raise UnsupportedOverlayInput("叠加分析的主输入必须是矢量图层。")
        if not isinstance(overlay_layer, VectorLayer):
            raise UnsupportedOverlayInput("叠加分析的叠加输入必须是矢量图层。")
        if input_layer.crs is None:
            raise UnsupportedOverlayInput(
                f"主输入图层“{input_layer.name}”没有坐标参考系统，无法执行叠加分析。"
            )
        if overlay_layer.crs is None:
            raise UnsupportedOverlayInput(
                f"叠加图层“{overlay_layer.name}”没有坐标参考系统，无法执行叠加分析。"
            )
        if input_layer.crs != overlay_layer.crs:
            raise UnsupportedOverlayInput(
                "两个输入图层的坐标参考系统不一致，无法执行叠加分析。"
            )
        display_crs: CRS | None = self._document.display_crs
        if display_crs is None:
            raise UnsupportedOverlayInput("当前地图没有坐标参考系统，无法加入叠加分析结果。")

        output_path: Path = request.output_path.expanduser().resolve()
        output_name: str = request.output_layer_name.strip()
        if not output_name:
            raise InvalidOverlayParameters("叠加分析输出图层名不能为空。")
        if (
            input_layer.source_path is not None
            and output_path == input_layer.source_path.resolve()
            and output_path.suffix.lower() != ".gpkg"
        ):
            raise InvalidOverlayParameters("叠加分析输出位置不能覆盖主输入图层源文件。")
        if (
            overlay_layer.source_path is not None
            and output_path == overlay_layer.source_path.resolve()
            and output_path.suffix.lower() != ".gpkg"
        ):
            raise InvalidOverlayParameters("叠加分析输出位置不能覆盖叠加图层源文件。")
        if output_path.exists() and output_path.suffix.lower() != ".gpkg":
            raise InvalidOverlayParameters("分析结果输出已存在，请使用新的结果文件或图层名称。")

        # 叠加分析不需要 CRS 转换：两个图层已通过 MapDocument 验证为同一 CRS。
        try:
            calculated_features = overlay_features(input_layer, overlay_layer, request)
        except EmptyOverlayResult:
            raise
        except ApplicationError:
            raise
        except Exception as error:
            raise OverlayAnalysisFailed(f"叠加分析计算失败：{error}") from error

        source_layer_name: str | None = (
            output_name if output_path.suffix.lower() == ".gpkg" else None
        )
        output_layer: VectorLayer = VectorLayer.create(
            name=output_name,
            features=calculated_features,
            crs=display_crs,
            source_path=output_path,
            source_layer_name=source_layer_name,
        )
        self.data_writer.write(output_layer, output_path, (), output_name)
        self._document.add_layer(output_layer)
        self._document.set_active_layer(output_layer.layer_id)
        run: AnalysisRun = self._create_analysis_run(
            algorithm_id="overlay",
            input_layer_ids=(input_layer.layer_id, overlay_layer.layer_id),
            parameters=self._overlay_request_parameters(request),
            output_layer_id=output_layer.layer_id,
            output_path=output_path,
            output_layer_name=output_name,
            started_at=started_at,
            completed_at=self._now(),
            duration_seconds=perf_counter() - started_monotonic,
        )
        self._analysis_runs = self._analysis_runs + (run,)
        self._modified = True
        return OverlayAnalysisResult(
            input_layer_id=input_layer.layer_id,
            overlay_layer_id=overlay_layer.layer_id,
            output_layer_id=output_layer.layer_id,
            output_layer_name=output_name,
            output_path=output_path,
            feature_count=len(output_layer.features),
            snapshot=self.snapshot(),
        )

    def _append_failed_overlay_analysis(
        self,
        request: OverlayRequest,
        started_at: str,
        started_monotonic: float,
        error: Exception,
    ) -> None:
        """将失败的叠加分析执行写入历史，保留输入和用户参数便于回溯。"""
        parameters: dict[str, object] = self._overlay_request_parameters(request)
        input_layer: SpatialLayer | None = next(
            (
                layer
                for layer in self._document.layers
                if layer.layer_id == request.input_layer_id
            ),
            None,
        )
        if isinstance(input_layer, VectorLayer):
            parameters["input_geometry_family"] = self._geometry_family_value(input_layer)
        overlay_ref: SpatialLayer | None = next(
            (
                layer
                for layer in self._document.layers
                if layer.layer_id == request.overlay_layer_id
            ),
            None,
        )
        if isinstance(overlay_ref, VectorLayer):
            parameters["overlay_geometry_family"] = self._geometry_family_value(overlay_ref)
        run: AnalysisRun = self._create_analysis_run(
            algorithm_id="overlay",
            input_layer_ids=(request.input_layer_id, request.overlay_layer_id),
            parameters=parameters,
            status="failed",
            message=str(error),
            started_at=started_at,
            completed_at=self._now(),
            duration_seconds=perf_counter() - started_monotonic,
        )
        self._analysis_runs = self._analysis_runs + (run,)
        self._modified = True

    # ── 栅格计算器 ─────────────────────────────────────────

    def raster_calculation(
        self, request: RasterCalculatorRequest
    ) -> RasterCalculatorResult:
        """执行栅格逐像素表达式计算，并为结果追加历史记录。

        参数:
            request: 波段映射、表达式、输出位置和图层名等参数。

        返回:
            包含输出图层编号、写出路径和最新工作区快照的结果。

        异常:
            ApplicationError: 输入数据无效、计算失败或结果写出失败时抛出。
        """
        started_at: str = self._now()
        started_monotonic: float = perf_counter()
        try:
            return self._execute_raster_calculation(
                request, started_at, started_monotonic
            )
        except Exception as error:
            self._append_failed_raster_calculation(
                request, started_at, started_monotonic, error
            )
            raise

    def _execute_raster_calculation(
        self,
        request: RasterCalculatorRequest,
        started_at: str,
        started_monotonic: float,
    ) -> RasterCalculatorResult:
        """执行栅格表达式求值、生成显示图、写出 GeoTIFF 并加入工作区。"""
        if self.data_writer is None:
            raise DataWriteFailed("空间数据写出服务尚未配置。")

        # 1. 查找所有引用的栅格图层并提取数据。
        band_arrays: dict[str, np.ndarray] = {}
        transforms: list[Affine] = []
        crss: list[object | None] = []
        shapes: list[tuple[int, int]] = []
        layer_names: list[str] = []
        input_layer_ids: list[str] = []
        reference_transform = None
        reference_crs = None

        for mapping in request.band_mappings:
            layer: SpatialLayer = self._find_layer(mapping.layer_id)
            if not isinstance(layer, RasterLayer):
                raise InvalidRasterCalculatorParameters(
                    f"图层「{layer.name}」不是栅格图层。"
                )
            band_idx: int = mapping.band_index - 1  # 1-based → 0-based
            if band_idx < 0 or band_idx >= layer.band_count:
                raise InvalidRasterCalculatorParameters(
                    f"图层「{layer.name}」没有波段 {mapping.band_index}"
                    f"（共 {layer.band_count} 个波段）。"
                )
            # 提取单波段 2D 数据
            band_2d: np.ndarray = layer.raster_data[band_idx]
            band_arrays[mapping.alias] = band_2d
            transforms.append(layer.transform)
            crss.append(layer.crs)
            shapes.append(band_2d.shape)
            layer_names.append(layer.name)
            if mapping.layer_id not in input_layer_ids:
                input_layer_ids.append(mapping.layer_id)
            if reference_transform is None:
                reference_transform = layer.transform
                reference_crs = layer.crs

        # 2. 校验对齐。
        warnings_list: list[str] = validate_band_alignment(
            tuple(transforms), tuple(crss), tuple(shapes), tuple(layer_names)
        )
        if warnings_list:
            # 检查是否有 CRS 不一致（硬错误）
            if crss and len(set(crss)) > 1:
                raise RasterBandAlignmentError(
                    "输入栅格波段坐标系不一致，无法执行逐像素计算。\n"
                    + "\n".join(warnings_list)
                )
            # 其他不一致仅记录（尺寸/分辨率），由 np 广播处理

        # 3. 执行表达式求值。
        try:
            result_data: np.ndarray = compute_raster_expression(
                band_arrays, request.expression
            )
        except ValueError as exc:
            raise RasterCalculatorFailed(str(exc)) from exc

        # 4. 构建有效掩码（所有输入波段掩码 AND 结果有限性）。
        combined_mask: np.ndarray = np.ones(shapes[0], dtype=bool)
        for mapping in request.band_mappings:
            layer_ref: SpatialLayer = self._find_layer(mapping.layer_id)
            if isinstance(layer_ref, RasterLayer):
                combined_mask &= layer_ref.valid_mask
        combined_mask &= np.isfinite(result_data)
        if request.nodata is not None:
            combined_mask &= ~np.isclose(result_data, request.nodata)

        # 5. 生成 RGBA 显示图。
        image_data: np.ndarray = generate_display_image(result_data, combined_mask)

        # 6. 构建输出 RasterLayer。
        output_path: Path = request.output_path.expanduser().resolve()
        output_name: str = request.output_layer_name.strip()
        import rasterio.transform

        height, width = result_data.shape
        output_bounds: tuple[float, float, float, float] = rasterio.transform.array_bounds(
            height, width, reference_transform
        )
        output_raster: np.ndarray = result_data[np.newaxis, ...]  # (1, H, W)

        output_layer = RasterLayer.create(
            name=output_name,
            raster_data=output_raster,
            image_data=image_data,
            valid_mask=combined_mask,
            transform=reference_transform,
            crs=reference_crs,
            bounds=output_bounds,
            nodata=request.nodata,
            source_path=output_path,
        )

        # 7. 写出 GeoTIFF。
        self.data_writer.write(output_layer, output_path)

        # 8. 加入工作区。
        self._document.add_layer(output_layer)
        self._document.set_active_layer(output_layer.layer_id)

        # 9. 创建分析历史记录。
        run: AnalysisRun = self._create_analysis_run(
            algorithm_id="raster_calculator",
            input_layer_ids=tuple(input_layer_ids),
            parameters={
                "expression": request.expression,
                "variable_count": len(request.band_mappings),
                "aliases": {m.alias: m.band_index for m in request.band_mappings},
                "output_path": str(output_path),
                "nodata": request.nodata,
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

        return RasterCalculatorResult(
            output_layer_id=output_layer.layer_id,
            output_layer_name=output_name,
            output_path=output_path,
            expression=request.expression,
            variable_count=len(request.band_mappings),
            snapshot=self.snapshot(),
        )

    def _append_failed_raster_calculation(
        self,
        request: RasterCalculatorRequest,
        started_at: str,
        started_monotonic: float,
        error: Exception,
    ) -> None:
        """将失败的栅格计算写入历史，便于用户回溯。"""
        input_ids: tuple[str, ...] = tuple(
            {m.layer_id for m in request.band_mappings}
        )
        run: AnalysisRun = self._create_analysis_run(
            algorithm_id="raster_calculator",
            input_layer_ids=input_ids,
            parameters={
                "expression": request.expression,
                "variable_count": len(request.band_mappings),
                "output_path": str(request.output_path.expanduser().resolve()),
            },
            status="failed",
            message=str(error),
            started_at=started_at,
            completed_at=self._now(),
            duration_seconds=perf_counter() - started_monotonic,
        )
        self._analysis_runs = self._analysis_runs + (run,)
        self._modified = True

    @staticmethod
    def _geometry_family_value(layer: VectorLayer) -> str:
        """返回图层几何类别的持久化名称，无法确定时保留明确占位值。"""
        family = layer.geometry_family
        return family.value if family is not None else "unknown"

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
    def _overlay_request_parameters(request: OverlayRequest) -> dict[str, object]:
        """将叠加分析请求转换为可持久化的历史参数。"""
        return {
            "operation": request.operation,
            "operation_label": operation_label(request.operation),
            "keep_geom_type": request.keep_geom_type,
            "make_valid": request.make_valid,
            "sjoin_predicate": request.sjoin_predicate,
            "sjoin_how": request.sjoin_how,
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
