"""捕捉引擎：STRtree 空间索引 + 顶点/边捕捉。

提供 SnappingEngine 类，负责从工作区快照构建空间索引，
并按给定地图坐标和容差返回最近的捕捉候选。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shapely import STRtree
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry

from app.application.results import LayerSnapshot, WorkspaceSnapshot
from app.domain.vector_layer import VectorLayer


@dataclass
class SnapResult:
    """一次捕捉命中结果。

    属性:
        map_point: 捕捉后的地图坐标（吸附位置）。
        snap_type: 捕捉类型标识（"vertex"/"edge"/"endpoint"/"midpoint"）。
        layer_id: 捕捉来源图层编号。
        source_coords: 关联的原始几何坐标，供绘制边高亮等标记使用。
    """

    map_point: Point
    snap_type: str
    layer_id: str
    source_coords: tuple[tuple[float, float], ...] = field(default_factory=tuple)


@dataclass
class _SnapCandidate:
    """捕捉候选：存储在 STRtree 旁，命中后精确计算距离。"""

    kind: str  # "vertex" | "edge"
    layer_id: str
    # vertex: (x, y)；edge: (ax, ay, bx, by)
    data: tuple[float, ...]

    @property
    def query_point(self) -> Point:
        """返回用于 STRtree 空间索引的 Point 几何。"""
        if self.kind == "vertex":
            return Point(self.data[0], self.data[1])
        # edge: 用线段中点做索引几何
        ax, ay, bx, by = self.data
        return Point((ax + bx) / 2.0, (ay + by) / 2.0)


class SnappingEngine:
    """捕捉引擎：空间索引 + 类型检测。

    使用 Shapely STRtree（R-tree）做 O(log n) 的候选查找，
    支持顶点和边两种捕捉类型。
    """

    def __init__(self) -> None:
        """创建空的捕捉引擎；调用 build_index 填充候选。"""
        self._strtree: STRtree | None = None
        self._candidates: list[_SnapCandidate] = []
        self._indexed_layer_ids: frozenset[str] = frozenset()
        # 用户可配置参数。
        self._enabled: bool = False
        self._tolerance_pixels: float = 12.0
        self._snap_types: set[str] = {"vertex", "edge"}
        self._all_layers: bool = True  # True=所有可见图层, False=仅活动图层

    # ── 属性 ──────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        """捕捉开关状态。"""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @property
    def tolerance_pixels(self) -> float:
        """屏幕像素容差。"""
        return self._tolerance_pixels

    @tolerance_pixels.setter
    def tolerance_pixels(self, value: float) -> None:
        self._tolerance_pixels = max(1.0, min(value, 100.0))

    @property
    def snap_types(self) -> set[str]:
        """启用的捕捉类型集合。"""
        return self._snap_types

    @snap_types.setter
    def snap_types(self, value: set[str]) -> None:
        self._snap_types = value

    @property
    def all_layers(self) -> bool:
        """True=所有可见图层参与捕捉，False=仅活动图层。"""
        return self._all_layers

    @all_layers.setter
    def all_layers(self, value: bool) -> None:
        self._all_layers = value

    # ── 索引构建 ──────────────────────────────────────────

    def build_index(
        self,
        snapshot: WorkspaceSnapshot,
        queryable_ids: set[str],
        active_layer_id: str | None = None,
    ) -> None:
        """从工作区快照重建空间索引。

        参数:
            snapshot: 当前工作区快照。
            queryable_ids: 当前可查询（可见且未超比例范围）的图层编号集合。
            active_layer_id: 当前活动图层编号；all_layers=False 时仅使用该图层。
        """
        candidates: list[_SnapCandidate] = []
        target_ids: set[str] = (
            queryable_ids
            if self._all_layers
            else ({active_layer_id} & queryable_ids if active_layer_id else set())
        )

        for layer in snapshot.layers:
            if layer.layer_id not in target_ids:
                continue
            if not isinstance(layer.layer, VectorLayer):
                continue

            for feature in layer.layer.features:
                geom: BaseGeometry = feature.geometry
                if geom.is_empty:
                    continue
                self._collect_candidates(
                    geom, layer.layer_id, candidates
                )

        self._indexed_layer_ids = frozenset(target_ids)
        self._candidates = candidates
        if candidates:
            geoms: list[Point] = [c.query_point for c in candidates]
            self._strtree = STRtree(geoms)
        else:
            self._strtree = None

    @staticmethod
    def _collect_candidates(
        geometry: BaseGeometry,
        layer_id: str,
        candidates: list[_SnapCandidate],
    ) -> None:
        """递归收集几何对象中的捕捉候选。

        每个顶点生成一个 vertex 候选，
        每条线段生成一个 edge 候选。
        """
        gtype: str = geometry.geom_type
        if gtype == "Point":
            candidates.append(
                _SnapCandidate("vertex", layer_id, (geometry.x, geometry.y))
            )
        elif gtype in ("LineString", "LinearRing"):
            # 三维几何的坐标是 (x, y, z) 三元组；捕捉只关心平面位置，统一取前两维。
            coords: list[tuple[float, float]] = [
                (c[0], c[1]) for c in geometry.coords
            ]
            for x, y in coords:
                candidates.append(_SnapCandidate("vertex", layer_id, (x, y)))
            # 为每条线段生成 edge 候选。
            for i in range(len(coords) - 1):
                ax, ay = coords[i]
                bx, by = coords[i + 1]
                candidates.append(
                    _SnapCandidate("edge", layer_id, (ax, ay, bx, by))
                )
        elif gtype == "Polygon":
            # 外环。
            SnappingEngine._collect_candidates(
                geometry.exterior, layer_id, candidates
            )
            # 内环。
            for ring in geometry.interiors:
                SnappingEngine._collect_candidates(
                    ring, layer_id, candidates
                )
        elif gtype in (
            "MultiPoint", "MultiLineString", "MultiPolygon",
            "GeometryCollection",
        ):
            for member in geometry.geoms:
                SnappingEngine._collect_candidates(
                    member, layer_id, candidates
                )

    # ── 查询 ──────────────────────────────────────────────

    def find_snap(
        self,
        cursor_point: Point,
        map_units_per_pixel: float,
        active_layer_id: str | None = None,
    ) -> SnapResult | None:
        """查询距离光标最近的捕捉候选。

        参数:
            cursor_point: 光标在地图坐标系下的位置。
            map_units_per_pixel: 当前视图每像素对应的地图单位。
            active_layer_id: 活动图层；用于仅活动图层模式下的优先级。

        返回:
            最近的 SnapResult；无命中返回 None。
        """
        if not self._enabled or self._strtree is None or not self._candidates:
            return None

        tol_map: float = self._tolerance_pixels * map_units_per_pixel

        # STRtree 范围查询。
        query_geom: Point = Point(cursor_point.x, cursor_point.y)
        query_region: Point = query_geom.buffer(
            tol_map * 2.0
        )  # 放宽容差确保不漏检。
        try:
            hit_indices: list[int] = list(self._strtree.query(query_region))
        except Exception:
            return None

        if not hit_indices:
            return None

        px, py = cursor_point.x, cursor_point.y
        best: SnapResult | None = None
        best_dist: float = tol_map

        for idx in hit_indices:
            candidate: _SnapCandidate = self._candidates[idx]
            kind: str = candidate.kind

            if kind not in self._snap_types:
                continue

            if not self._all_layers and active_layer_id is not None:
                if candidate.layer_id != active_layer_id:
                    continue

            if kind == "vertex":
                vx, vy = candidate.data[0], candidate.data[1]
                d: float = ((px - vx) ** 2 + (py - vy) ** 2) ** 0.5
                if d < best_dist:
                    best_dist = d
                    best = SnapResult(
                        map_point=Point(vx, vy),
                        snap_type="vertex",
                        layer_id=candidate.layer_id,
                        source_coords=((vx, vy),),
                    )
            elif kind == "edge":
                ax, ay, bx, by = candidate.data
                d, proj_pt = SnappingEngine._point_to_segment(
                    px, py, ax, ay, bx, by
                )
                if d < best_dist:
                    best_dist = d
                    best = SnapResult(
                        map_point=proj_pt,
                        snap_type="edge",
                        layer_id=candidate.layer_id,
                        source_coords=((ax, ay), (bx, by)),
                    )

        return best

    @staticmethod
    def _point_to_segment(
        px: float, py: float,
        ax: float, ay: float,
        bx: float, by: float,
    ) -> tuple[float, Point]:
        """计算点到线段 AB 的最短距离和投影点。

        返回:
            (最短距离, 投影点 Point)。
        """
        dx, dy = bx - ax, by - ay
        seg_len_sq: float = dx * dx + dy * dy
        if seg_len_sq < 1e-20:
            d: float = ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
            return d, Point(ax, ay)
        t: float = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))
        proj_x: float = ax + t * dx
        proj_y: float = ay + t * dy
        d = ((px - proj_x) ** 2 + (py - proj_y) ** 2) ** 0.5
        return d, Point(proj_x, proj_y)

    @property
    def indexed_layer_ids(self) -> frozenset[str]:
        """上次构建索引时使用的图层编号集合。"""
        return self._indexed_layer_ids

    def clear(self) -> None:
        """清除索引和候选。"""
        self._strtree = None
        self._candidates.clear()
        self._indexed_layer_ids = frozenset()
