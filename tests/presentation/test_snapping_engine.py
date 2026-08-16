"""捕捉引擎按图层缓存索引的行为测试。"""

from pyproj import CRS
from shapely.geometry import LineString, Point

from app.application.results import LayerSnapshot, WorkspaceSnapshot
from app.domain.feature import Feature
from app.domain.vector_layer import VectorLayer
from app.presentation.snapping_engine import SnappingEngine


def make_snap_layer(layer_id: str, offset: float = 0.0) -> VectorLayer:
    """创建含点和线的可捕捉图层。"""
    return VectorLayer.create(
        layer_id=layer_id,
        name=layer_id,
        features=(
            Feature(fid=1, geometry=Point(1.0 + offset, 1.0 + offset), attributes={}),
            Feature(
                fid=2,
                geometry=LineString(
                    [(5.0 + offset, 5.0 + offset), (9.0 + offset, 5.0 + offset)]
                ),
                attributes={},
            ),
        ),
        crs=CRS.from_epsg(4326),
    )


def make_snapshot(*layers: VectorLayer) -> WorkspaceSnapshot:
    """把矢量图层包装成工作区快照。"""
    return WorkspaceSnapshot(
        layers=tuple(
            LayerSnapshot(layer=layer, visible=True, selected_feature_ids=())
            for layer in layers
        ),
        active_layer_id=layers[0].layer_id if layers else None,
        display_crs=layers[0].crs if layers else None,
    )


def test_build_index_reuses_cache_when_layer_unchanged() -> None:
    """同一图层对象重复构建索引时，应复用缓存的候选而不是重建。"""
    layer: VectorLayer = make_snap_layer("roads")
    snapshot: WorkspaceSnapshot = make_snapshot(layer)
    engine: SnappingEngine = SnappingEngine()
    engine.build_index(snapshot, {"roads"}, "roads")

    cached_index = engine._layer_indexes["roads"]
    assert cached_index is not None
    engine.build_index(snapshot, {"roads"}, "roads")

    assert engine._layer_indexes["roads"] is cached_index
    assert engine._active_indexes == [cached_index]


def test_build_index_rebuilds_when_layer_object_replaced() -> None:
    """图层编辑生成新对象后，缓存应失效并按新几何重建候选。"""
    engine: SnappingEngine = SnappingEngine()
    engine.build_index(make_snapshot(make_snap_layer("roads")), {"roads"}, "roads")

    replaced: VectorLayer = make_snap_layer("roads", offset=100.0)
    engine.build_index(make_snapshot(replaced), {"roads"}, "roads")
    engine.enabled = True

    snap = engine.find_snap(Point(101.0, 101.0), map_units_per_pixel=1.0)

    assert snap is not None
    assert snap.map_point == Point(101.0, 101.0)
    # 旧位置的要素不再出现在索引中。
    old_position = engine.find_snap(Point(1.0, 1.0), map_units_per_pixel=1.0)
    assert old_position is None


def test_find_snap_keeps_vertex_and_edge_hits_across_layers() -> None:
    """多图层索引应分别命中各层的顶点与边，结果与整树方案一致。"""
    first: VectorLayer = make_snap_layer("first")
    second: VectorLayer = make_snap_layer("second", offset=1000.0)
    engine: SnappingEngine = SnappingEngine()
    engine.build_index(make_snapshot(first, second), {"first", "second"}, "first")
    engine.enabled = True

    vertex_snap = engine.find_snap(Point(1.2, 1.1), map_units_per_pixel=1.0)
    edge_snap = engine.find_snap(Point(7.0, 5.05), map_units_per_pixel=1.0)
    far_snap = engine.find_snap(Point(500.0, 500.0), map_units_per_pixel=1.0)

    assert vertex_snap is not None
    assert vertex_snap.snap_type == "vertex"
    assert vertex_snap.layer_id == "first"
    assert edge_snap is not None
    assert edge_snap.snap_type == "edge"
    assert edge_snap.layer_id == "first"
    assert far_snap is None


def test_active_layer_filter_restricts_query_to_single_layer() -> None:
    """仅活动图层模式下，其他图层的候选不应参与捕捉。"""
    first: VectorLayer = make_snap_layer("first")
    second: VectorLayer = make_snap_layer("second", offset=1000.0)
    engine: SnappingEngine = SnappingEngine()
    engine.all_layers = False
    engine.build_index(make_snapshot(first, second), {"first", "second"}, "second")
    engine.enabled = True

    # (1, 1) 只有 first 图层有顶点；活动图层是 second，应无命中。
    snap = engine.find_snap(
        Point(1.0, 1.0), map_units_per_pixel=1.0, active_layer_id="second"
    )

    assert snap is None
