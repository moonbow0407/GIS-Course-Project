"""要素编辑会话、能力计算和复杂几何顶点寻址。"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from types import MappingProxyType

import numpy as np
from shapely.geometry import (
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

from app.application.crs_utils import crs_equivalent
from app.application.results import WorkspaceSnapshot
from app.domain.feature import FeatureId
from app.domain.vector_layer import VectorLayer


class EditOperation(str, Enum):
    """单次要素编辑会话支持的操作类型。"""

    ADD = "add"
    VERTEX = "vertex"
    MOVE = "move"
    ROTATE = "rotate"
    SCALE = "scale"
    SPLIT = "split"
    MERGE = "merge"
    SIMPLIFY = "simplify"
    SMOOTH = "smooth"
    DELETE = "delete"
    ATTRIBUTES = "attributes"

    @property
    def label(self) -> str:
        """返回操作的中文显示名称。"""
        return {
            self.ADD: "新增要素",
            self.VERTEX: "顶点编辑",
            self.MOVE: "移动要素",
            self.ROTATE: "旋转要素",
            self.SCALE: "缩放要素",
            self.SPLIT: "拆分要素",
            self.MERGE: "合并要素",
            self.SIMPLIFY: "简化要素",
            self.SMOOTH: "平滑要素",
            self.DELETE: "删除要素",
            self.ATTRIBUTES: "属性编辑",
        }[self]


@dataclass(frozen=True, slots=True, order=True)
class VertexAddress:
    """稳定定位单部件、多部件和面内外环中的一个顶点。"""

    part_index: int
    ring_index: int
    vertex_index: int


@dataclass(frozen=True, slots=True)
class FeatureEditSession:
    """描述一个尚未写回数据源的单操作编辑会话。"""

    operation: EditOperation
    layer_id: str
    layer_name: str
    fid: FeatureId | None
    layer_revision: int
    original_geometry: BaseGeometry | None
    working_geometry: BaseGeometry | None
    parameters: Mapping[str, object]
    dirty: bool
    valid: bool
    validation_message: str = ""

    def __post_init__(self) -> None:
        """复制参数映射，禁止调用方绕过会话控制器修改状态。"""
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))


@dataclass(frozen=True, slots=True)
class EditCapabilities:
    """保存各操作当前是否可用以及不可用原因。"""

    states: Mapping[EditOperation, tuple[bool, str]]

    def __post_init__(self) -> None:
        """把能力表转为只读映射。"""
        object.__setattr__(self, "states", MappingProxyType(dict(self.states)))

    def enabled(self, operation: EditOperation) -> bool:
        """返回指定操作是否可用。"""
        return self.states.get(operation, (False, "当前不可用。"))[0]

    def reason(self, operation: EditOperation) -> str:
        """返回指定操作的禁用原因；可用时返回空字符串。"""
        return self.states.get(operation, (False, "当前不可用。"))[1]


class FeatureEditController:
    """独占当前编辑会话并维护提交前的预览撤销历史。"""

    def __init__(self) -> None:
        self._session: FeatureEditSession | None = None
        self._undo: list[FeatureEditSession] = []
        self._redo: list[FeatureEditSession] = []

    @property
    def session(self) -> FeatureEditSession | None:
        """返回当前只读编辑会话。"""
        return self._session

    @property
    def active(self) -> bool:
        """返回当前是否存在编辑会话。"""
        return self._session is not None

    def begin(
        self,
        operation: EditOperation,
        layer_id: str,
        layer_name: str,
        fid: FeatureId | None,
        layer_revision: int,
        geometry: BaseGeometry | None,
        *,
        parameters: Mapping[str, object] | None = None,
    ) -> FeatureEditSession:
        """开始新会话；调用方必须先处理已有会话。"""
        if self._session is not None:
            raise RuntimeError("已有要素编辑会话，请先应用或取消。")
        valid, message = _validate_preview_geometry(geometry, allow_none=operation == EditOperation.ADD)
        self._session = FeatureEditSession(
            operation=operation,
            layer_id=layer_id,
            layer_name=layer_name,
            fid=fid,
            layer_revision=layer_revision,
            original_geometry=geometry,
            working_geometry=geometry,
            parameters=parameters or {},
            dirty=False,
            valid=valid,
            validation_message=message,
        )
        self._undo.clear()
        self._redo.clear()
        return self._session

    def update_working_geometry(
        self,
        geometry: BaseGeometry,
        *,
        parameters: Mapping[str, object] | None = None,
    ) -> FeatureEditSession:
        """保存新的工作几何，并把上一步压入会话内部撤销栈。"""
        if self._session is None:
            raise RuntimeError("当前没有要素编辑会话。")
        valid, message = _validate_preview_geometry(geometry)
        self._undo.append(self._session)
        if len(self._undo) > 100:
            self._undo.pop(0)
        self._redo.clear()
        original = self._session.original_geometry
        dirty = (
            self._session.operation in (EditOperation.MERGE, EditOperation.SPLIT)
            or original is None
            or not geometry.equals_exact(original, tolerance=0.0)
        )
        merged_parameters = dict(self._session.parameters)
        if parameters is not None:
            merged_parameters.update(parameters)
        self._session = replace(
            self._session,
            working_geometry=geometry,
            parameters=merged_parameters,
            dirty=dirty,
            valid=valid,
            validation_message=message,
        )
        return self._session

    def undo_preview(self) -> bool:
        """撤销一次会话内部的预览变化。"""
        if self._session is None or not self._undo:
            return False
        self._redo.append(self._session)
        self._session = self._undo.pop()
        return True

    def redo_preview(self) -> bool:
        """重做一次会话内部的预览变化。"""
        if self._session is None or not self._redo:
            return False
        self._undo.append(self._session)
        self._session = self._redo.pop()
        return True

    def cancel(self) -> FeatureEditSession | None:
        """取消并返回被丢弃的会话。"""
        session = self._session
        self._session = None
        self._undo.clear()
        self._redo.clear()
        return session

    def finish(self) -> FeatureEditSession:
        """在成功写回后结束并返回会话。"""
        if self._session is None:
            raise RuntimeError("当前没有要素编辑会话。")
        session = self._session
        self.cancel()
        return session

    @staticmethod
    def capabilities(snapshot: WorkspaceSnapshot, *, writable: bool) -> EditCapabilities:
        """按选择、CRS、写权限和几何类型计算编辑入口状态。"""
        operations = tuple(EditOperation)
        states: dict[EditOperation, tuple[bool, str]] = {
            operation: (False, "请先选择要素。") for operation in operations
        }
        selected_layers = [layer for layer in snapshot.layers if layer.selected_feature_ids]
        if not selected_layers:
            return EditCapabilities(states)
        if len(selected_layers) != 1:
            reason = "请选择同一图层中的要素。"
            return EditCapabilities({operation: (False, reason) for operation in operations})
        selected = selected_layers[0]
        if not isinstance(selected.layer, VectorLayer):
            reason = "栅格图层不支持要素编辑。"
            return EditCapabilities({operation: (False, reason) for operation in operations})
        if not crs_equivalent(selected.layer.crs, snapshot.display_crs):
            reason = "图层 CRS 与地图显示 CRS 不一致。"
            return EditCapabilities({operation: (False, reason) for operation in operations})
        if not writable:
            reason = "图层数据源不可写。"
            return EditCapabilities({operation: (False, reason) for operation in operations})

        count = len(selected.selected_feature_ids)
        if count > 1:
            states[EditOperation.MERGE] = (True, "")
            states[EditOperation.DELETE] = (True, "")
            for operation in operations:
                if operation not in (EditOperation.MERGE, EditOperation.DELETE):
                    states[operation] = (False, "该操作要求只选择一个要素。")
            return EditCapabilities(states)

        fid = selected.selected_feature_ids[0]
        feature = next(feature for feature in selected.layer.features if feature.fid == fid)
        geom_type = feature.geometry.geom_type
        for operation in (
            EditOperation.VERTEX,
            EditOperation.MOVE,
            EditOperation.DELETE,
            EditOperation.ATTRIBUTES,
        ):
            states[operation] = (True, "")
        if geom_type in ("LineString", "MultiLineString", "Polygon", "MultiPolygon"):
            for operation in (
                EditOperation.ROTATE,
                EditOperation.SCALE,
                EditOperation.SPLIT,
                EditOperation.SIMPLIFY,
                EditOperation.SMOOTH,
            ):
                states[operation] = (True, "")
        else:
            reason = "点要素仅支持移动和顶点位置编辑。"
            states[EditOperation.ROTATE] = (False, reason)
            states[EditOperation.SCALE] = (False, reason)
            states[EditOperation.SPLIT] = (False, reason)
            states[EditOperation.SIMPLIFY] = (False, reason)
            states[EditOperation.SMOOTH] = (False, reason)
        return EditCapabilities(states)


def iter_vertices(geometry: BaseGeometry) -> tuple[tuple[VertexAddress, tuple[float, float]], ...]:
    """按稳定地址枚举点、线、面及其 Multi 几何的全部顶点。"""
    if geometry.geom_type == "GeometryCollection":
        raise ValueError("GeometryCollection 暂不支持顶点编辑。")
    result: list[tuple[VertexAddress, tuple[float, float]]] = []
    if isinstance(geometry, Point):
        result.append((VertexAddress(0, 0, 0), (geometry.x, geometry.y)))
    elif isinstance(geometry, MultiPoint):
        for part_index, point in enumerate(geometry.geoms):
            result.append((VertexAddress(part_index, 0, 0), (point.x, point.y)))
    elif isinstance(geometry, LineString):
        _append_ring_vertices(result, 0, 0, geometry.coords)
    elif isinstance(geometry, MultiLineString):
        for part_index, line in enumerate(geometry.geoms):
            _append_ring_vertices(result, part_index, 0, line.coords)
    elif isinstance(geometry, Polygon):
        _append_polygon_vertices(result, 0, geometry)
    elif isinstance(geometry, MultiPolygon):
        for part_index, polygon in enumerate(geometry.geoms):
            _append_polygon_vertices(result, part_index, polygon)
    return tuple(result)


def replace_vertex(
    geometry: BaseGeometry,
    address: VertexAddress,
    coordinate: tuple[float, float],
) -> BaseGeometry:
    """只替换指定顶点，完整保留其他部件和面内环。"""
    if not all(np.isfinite(value) for value in coordinate):
        raise ValueError("顶点坐标必须为有限数值。")
    if isinstance(geometry, Point):
        _require_address(address, part=0, ring=0, vertex_count=1)
        return Point(coordinate)
    if isinstance(geometry, MultiPoint):
        points = [(point.x, point.y) for point in geometry.geoms]
        _require_address(address, part_count=len(points), ring=0, vertex_count=1)
        points[address.part_index] = coordinate
        return MultiPoint(points)
    if isinstance(geometry, LineString):
        _require_address(address, part=0, ring=0, vertex_count=len(geometry.coords))
        return LineString(_replace_coordinate(list(geometry.coords), address.vertex_index, coordinate))
    if isinstance(geometry, MultiLineString):
        lines = [list(line.coords) for line in geometry.geoms]
        _require_address(address, part_count=len(lines), ring=0, vertex_count=len(lines[address.part_index]))
        lines[address.part_index] = _replace_coordinate(
            lines[address.part_index], address.vertex_index, coordinate
        )
        return MultiLineString(lines)
    if isinstance(geometry, Polygon):
        _require_address(address, part=0)
        return _replace_polygon_vertex(geometry, address.ring_index, address.vertex_index, coordinate)
    if isinstance(geometry, MultiPolygon):
        polygons = list(geometry.geoms)
        _require_address(address, part_count=len(polygons))
        polygons[address.part_index] = _replace_polygon_vertex(
            polygons[address.part_index], address.ring_index, address.vertex_index, coordinate
        )
        return MultiPolygon(polygons)
    raise ValueError(f"{geometry.geom_type} 暂不支持顶点编辑。")


def rebuild_geometry(
    geometry: BaseGeometry,
    vertices: tuple[tuple[VertexAddress, tuple[float, float]], ...],
) -> BaseGeometry:
    """从带地址的可编辑顶点重建同类型几何，并保留所有部件和环。"""
    grouped: dict[tuple[int, int], list[tuple[int, tuple[float, float]]]] = {}
    for address, coordinate in vertices:
        grouped.setdefault((address.part_index, address.ring_index), []).append(
            (address.vertex_index, coordinate)
        )

    def group(part_index: int, ring_index: int, minimum: int) -> list[tuple[float, float]]:
        values = sorted(grouped.get((part_index, ring_index), ()), key=lambda item: item[0])
        coordinates = [coordinate for _vertex_index, coordinate in values]
        if len(coordinates) < minimum:
            raise ValueError("编辑后顶点数量不足，无法构成有效几何。")
        return coordinates

    if isinstance(geometry, Point):
        return Point(group(0, 0, 1)[0])
    if isinstance(geometry, MultiPoint):
        return MultiPoint([group(part_index, 0, 1)[0] for part_index in range(len(geometry.geoms))])
    if isinstance(geometry, LineString):
        return LineString(group(0, 0, 2))
    if isinstance(geometry, MultiLineString):
        return MultiLineString(
            [group(part_index, 0, 2) for part_index in range(len(geometry.geoms))]
        )
    if isinstance(geometry, Polygon):
        return _polygon_from_groups(geometry, 0, group)
    if isinstance(geometry, MultiPolygon):
        return MultiPolygon(
            [_polygon_from_groups(polygon, part_index, group) for part_index, polygon in enumerate(geometry.geoms)]
        )
    raise ValueError(f"{geometry.geom_type} 暂不支持顶点编辑。")


def _validate_preview_geometry(
    geometry: BaseGeometry | None, *, allow_none: bool = False
) -> tuple[bool, str]:
    if geometry is None:
        return (allow_none, "" if allow_none else "尚未生成预览几何。")
    if geometry.is_empty:
        return False, "预览结果不能为空。"
    if geometry.geom_type == "GeometryCollection":
        return False, "GeometryCollection 暂不支持编辑。"
    if not all(np.isfinite(value) for _address, coord in iter_vertices(geometry) for value in coord):
        return False, "坐标必须为有限数值。"
    if geometry.geom_type in ("Polygon", "MultiPolygon") and not geometry.is_valid:
        return False, "面几何无效，请调整后再应用。"
    return True, ""


def _append_ring_vertices(
    result: list[tuple[VertexAddress, tuple[float, float]]],
    part_index: int,
    ring_index: int,
    coordinates: Iterable[tuple[float, ...]],
    *,
    closed: bool = False,
) -> None:
    coords = list(coordinates)
    if closed and coords:
        coords = coords[:-1]
    for vertex_index, coord in enumerate(coords):
        result.append(
            (VertexAddress(part_index, ring_index, vertex_index), (float(coord[0]), float(coord[1])))
        )


def _append_polygon_vertices(
    result: list[tuple[VertexAddress, tuple[float, float]]],
    part_index: int,
    polygon: Polygon,
) -> None:
    _append_ring_vertices(result, part_index, 0, polygon.exterior.coords, closed=True)
    for ring_index, ring in enumerate(polygon.interiors, start=1):
        _append_ring_vertices(result, part_index, ring_index, ring.coords, closed=True)


def _replace_coordinate(
    coordinates: list[tuple[float, ...]], vertex_index: int, coordinate: tuple[float, float]
) -> list[tuple[float, ...]]:
    if not 0 <= vertex_index < len(coordinates):
        raise IndexError("顶点地址超出几何范围。")
    coordinates[vertex_index] = coordinate
    return coordinates


def _replace_polygon_vertex(
    polygon: Polygon,
    ring_index: int,
    vertex_index: int,
    coordinate: tuple[float, float],
) -> Polygon:
    rings = [list(polygon.exterior.coords)] + [list(ring.coords) for ring in polygon.interiors]
    if not 0 <= ring_index < len(rings):
        raise IndexError("环地址超出面几何范围。")
    ring = rings[ring_index]
    unique_count = max(len(ring) - 1, 0)
    if not 0 <= vertex_index < unique_count:
        raise IndexError("顶点地址超出环范围。")
    ring[vertex_index] = coordinate
    if vertex_index == 0:
        ring[-1] = coordinate
    rings[ring_index] = ring
    return Polygon(rings[0], rings[1:])


def _polygon_from_groups(
    polygon: Polygon,
    part_index: int,
    group: Callable[[int, int, int], list[tuple[float, float]]],
) -> Polygon:
    """使用分组坐标重建一个面部件。"""
    exterior = group(part_index, 0, 3)
    interiors = [
        group(part_index, ring_index, 3)
        for ring_index in range(1, len(polygon.interiors) + 1)
    ]
    return Polygon(exterior, interiors)


def _require_address(
    address: VertexAddress,
    *,
    part: int | None = None,
    part_count: int | None = None,
    ring: int | None = None,
    vertex_count: int | None = None,
) -> None:
    if part is not None and address.part_index != part:
        raise IndexError("部件地址超出几何范围。")
    if part_count is not None and not 0 <= address.part_index < part_count:
        raise IndexError("部件地址超出几何范围。")
    if ring is not None and address.ring_index != ring:
        raise IndexError("环地址超出几何范围。")
    if vertex_count is not None and not 0 <= address.vertex_index < vertex_count:
        raise IndexError("顶点地址超出几何范围。")
