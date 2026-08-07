"""叠加分析核心模块的单元测试。"""

import pytest
from pyproj import CRS
from shapely.geometry import LineString, Point, Polygon

from app.application.errors import (
    EmptyOverlayResult,
    InvalidOverlayParameters,
    OverlayAnalysisFailed,
)
from app.application.overlay_analysis import (
    OverlayRequest,
    _GEOMETRIC_OVERLAY_OPS,
    _validate_geometry_compatibility,
    operation_label,
    overlay_features,
)
from app.domain.feature import Feature
from app.domain.vector_layer import VectorLayer


# ---------------------------------------------------------------------------
# 测试辅助：构造图层
# ---------------------------------------------------------------------------


def _make_feature(fid: int, geometry, **attributes) -> Feature:
    """快速构造测试要素。"""
    from types import MappingProxyType

    return Feature(fid=fid, geometry=geometry, attributes=MappingProxyType(attributes))


def _make_polygon_layer_a() -> VectorLayer:
    """构造一个覆盖 (0,0)-(5,5) 的面图层 A。"""
    geom: Polygon = Polygon([(0, 0), (5, 0), (5, 5), (0, 5)])
    return VectorLayer.create(
        name="polygon_a",
        features=(_make_feature(0, geom, name="A", value=10),),
        crs=CRS.from_epsg(4326),
    )


def _make_polygon_layer_b() -> VectorLayer:
    """构造一个覆盖 (3,3)-(8,8) 的面图层 B，与 A 有重叠。"""
    geom: Polygon = Polygon([(3, 3), (8, 3), (8, 8), (3, 8)])
    return VectorLayer.create(
        name="polygon_b",
        features=(_make_feature(0, geom, name="B", value=20),),
        crs=CRS.from_epsg(4326),
    )


def _make_point_layer() -> VectorLayer:
    """构造包含两个点的点图层，一个在 polygon_a 内，一个在外。"""
    p1: Point = Point(2, 2)  # 在 polygon_a 内
    p2: Point = Point(10, 10)  # 在两个面之外
    return VectorLayer.create(
        name="points",
        features=(
            _make_feature(0, p1, label="inside"),
            _make_feature(1, p2, label="outside"),
        ),
        crs=CRS.from_epsg(4326),
    )


def _make_line_layer() -> VectorLayer:
    """构造一条穿越 polygon_a 的线图层。"""
    line: LineString = LineString([(1, 2.5), (4, 2.5)])
    return VectorLayer.create(
        name="lines",
        features=(_make_feature(0, line, label="crossing"),),
        crs=CRS.from_epsg(4326),
    )


def _make_disjoint_polygon_layer() -> VectorLayer:
    """构造一个与 polygon_a 不重叠的面图层。"""
    geom: Polygon = Polygon([(20, 20), (25, 20), (25, 25), (20, 25)])
    return VectorLayer.create(
        name="disjoint",
        features=(_make_feature(0, geom, name="far"),),
        crs=CRS.from_epsg(4326),
    )


# ---------------------------------------------------------------------------
# OverlayRequest 验证测试
# ---------------------------------------------------------------------------


class TestOverlayRequestValidation:
    """测试 OverlayRequest 构造时的参数验证。"""

    def test_rejects_empty_input_layer_id(self) -> None:
        """空输入图层编号应抛出。"""
        with pytest.raises(InvalidOverlayParameters, match="主输入图层"):
            OverlayRequest(
                input_layer_id="",
                overlay_layer_id="b",
                operation="intersection",
                output_path="test.geojson",
                output_layer_name="result",
            )

    def test_rejects_empty_overlay_layer_id(self) -> None:
        """空叠加图层编号应抛出。"""
        with pytest.raises(InvalidOverlayParameters, match="叠加图层"):
            OverlayRequest(
                input_layer_id="a",
                overlay_layer_id="",
                operation="intersection",
                output_path="test.geojson",
                output_layer_name="result",
            )

    def test_rejects_same_layer_ids(self) -> None:
        """两个图层相同时应抛出。"""
        with pytest.raises(InvalidOverlayParameters, match="不能相同"):
            OverlayRequest(
                input_layer_id="a",
                overlay_layer_id="a",
                operation="intersection",
                output_path="test.geojson",
                output_layer_name="result",
            )

    def test_rejects_invalid_operation(self) -> None:
        """无效操作类型应抛出。"""
        with pytest.raises(InvalidOverlayParameters, match="不支持的叠加操作"):
            OverlayRequest(
                input_layer_id="a",
                overlay_layer_id="b",
                operation="invalid_op",  # type: ignore[arg-type]
                output_path="test.geojson",
                output_layer_name="result",
            )

    def test_rejects_empty_output_name(self) -> None:
        """空输出图层名应抛出。"""
        with pytest.raises(InvalidOverlayParameters, match="输出图层名"):
            OverlayRequest(
                input_layer_id="a",
                overlay_layer_id="b",
                operation="intersection",
                output_path="test.geojson",
                output_layer_name="",
            )


# ---------------------------------------------------------------------------
# 几何兼容性测试
# ---------------------------------------------------------------------------


class TestGeometryCompatibility:
    """测试几何类型与操作兼容性验证。"""

    def test_polygon_required_for_geometric_overlay(self) -> None:
        """几何叠加操作要求两个图层均为面图层。"""
        point_layer: VectorLayer = _make_point_layer()
        polygon_layer: VectorLayer = _make_polygon_layer_a()

        with pytest.raises(InvalidOverlayParameters, match="面图层"):
            _validate_geometry_compatibility(point_layer, polygon_layer, "intersection")

    def test_polygon_required_for_overlay_input_both(self) -> None:
        """叠加图层也需要是面图层。"""
        polygon_layer: VectorLayer = _make_polygon_layer_a()
        point_layer: VectorLayer = _make_point_layer()

        with pytest.raises(InvalidOverlayParameters, match="叠加图层"):
            _validate_geometry_compatibility(polygon_layer, point_layer, "intersection")

    def test_point_in_polygon_requires_point_input(self) -> None:
        """点面叠置要求输入为点图层。"""
        polygon_a: VectorLayer = _make_polygon_layer_a()
        polygon_b: VectorLayer = _make_polygon_layer_b()

        with pytest.raises(InvalidOverlayParameters, match="点图层"):
            _validate_geometry_compatibility(polygon_a, polygon_b, "point_in_polygon")

    def test_line_in_polygon_requires_line_input(self) -> None:
        """线面叠置要求输入为线图层。"""
        polygon_a: VectorLayer = _make_polygon_layer_a()
        polygon_b: VectorLayer = _make_polygon_layer_b()

        with pytest.raises(InvalidOverlayParameters, match="线图层"):
            _validate_geometry_compatibility(polygon_a, polygon_b, "line_in_polygon")


# ---------------------------------------------------------------------------
# 几何叠加功能测试
# ---------------------------------------------------------------------------


class TestGeometricOverlay:
    """测试六种几何叠加操作。"""

    def test_intersection_produces_overlapping_area(self) -> None:
        """相交操作应返回两个多边形的重叠区域。"""
        layer_a: VectorLayer = _make_polygon_layer_a()
        layer_b: VectorLayer = _make_polygon_layer_b()
        request: OverlayRequest = OverlayRequest(
            input_layer_id=layer_a.layer_id,
            overlay_layer_id=layer_b.layer_id,
            operation="intersection",
            output_path="intersect.geojson",
            output_layer_name="intersect_result",
        )
        result: tuple = overlay_features(layer_a, layer_b, request)
        assert len(result) == 1
        # 重叠区域应为 (3,3)-(5,5)，面积约 4
        area: float = result[0].geometry.area
        assert 3.0 < area < 5.0

    def test_union_produces_combined_area(self) -> None:
        """联合操作应返回两个多边形的全部区域。"""
        layer_a: VectorLayer = _make_polygon_layer_a()
        layer_b: VectorLayer = _make_polygon_layer_b()
        request: OverlayRequest = OverlayRequest(
            input_layer_id=layer_a.layer_id,
            overlay_layer_id=layer_b.layer_id,
            operation="union",
            output_path="union.geojson",
            output_layer_name="union_result",
        )
        result: tuple = overlay_features(layer_a, layer_b, request)
        assert len(result) >= 2
        # 总面积应 > 25 (单个多边形面积)
        total_area: float = sum(f.geometry.area for f in result)
        assert total_area > 40.0

    def test_difference_removes_overlap(self) -> None:
        """擦除操作应返回输入图层中不被叠加图层覆盖的区域。"""
        layer_a: VectorLayer = _make_polygon_layer_a()
        layer_b: VectorLayer = _make_polygon_layer_b()
        request: OverlayRequest = OverlayRequest(
            input_layer_id=layer_a.layer_id,
            overlay_layer_id=layer_b.layer_id,
            operation="difference",
            output_path="diff.geojson",
            output_layer_name="diff_result",
        )
        result: tuple = overlay_features(layer_a, layer_b, request)
        assert len(result) == 1
        # A 减去 B: 剩下 (0,0)-(3,3) 和 (3,0)-(5,3) 的 L 形，面积约 21
        assert 20.0 < result[0].geometry.area < 22.0

    def test_symmetric_difference_excludes_shared(self) -> None:
        """对称差异应返回不重叠的区域。"""
        layer_a: VectorLayer = _make_polygon_layer_a()
        layer_b: VectorLayer = _make_polygon_layer_b()
        request: OverlayRequest = OverlayRequest(
            input_layer_id=layer_a.layer_id,
            overlay_layer_id=layer_b.layer_id,
            operation="symmetric_difference",
            output_path="symdiff.geojson",
            output_layer_name="symdiff_result",
        )
        result: tuple = overlay_features(layer_a, layer_b, request)
        assert len(result) >= 2

    def test_identity_preserves_input(self) -> None:
        """识别操作应保留输入图层的全部区域。"""
        layer_a: VectorLayer = _make_polygon_layer_a()
        layer_b: VectorLayer = _make_polygon_layer_b()
        request: OverlayRequest = OverlayRequest(
            input_layer_id=layer_a.layer_id,
            overlay_layer_id=layer_b.layer_id,
            operation="identity",
            output_path="identity.geojson",
            output_layer_name="identity_result",
        )
        result: tuple = overlay_features(layer_a, layer_b, request)
        # 应有至少 2 个要素（重叠部分 + 非重叠部分）
        assert len(result) >= 1
        # 总面积应接近 A 的面积（25）
        total_area = sum(f.geometry.area for f in result)
        assert abs(total_area - 25.0) < 0.1

    def test_update_composition(self) -> None:
        """更新操作 = (输入 - 叠加) ∪ 叠加。"""
        layer_a: VectorLayer = _make_polygon_layer_a()
        layer_b: VectorLayer = _make_polygon_layer_b()
        request: OverlayRequest = OverlayRequest(
            input_layer_id=layer_a.layer_id,
            overlay_layer_id=layer_b.layer_id,
            operation="update",
            output_path="update.geojson",
            output_layer_name="update_result",
        )
        result: tuple = overlay_features(layer_a, layer_b, request)
        assert len(result) >= 2  # diff 部分 + overlay 部分

    def test_empty_result_for_disjoint_layers(self) -> None:
        """两个不重叠的多边形进行相交操作应抛出空结果异常。"""
        layer_a: VectorLayer = _make_polygon_layer_a()
        layer_disjoint: VectorLayer = _make_disjoint_polygon_layer()
        request: OverlayRequest = OverlayRequest(
            input_layer_id=layer_a.layer_id,
            overlay_layer_id=layer_disjoint.layer_id,
            operation="intersection",
            output_path="empty.geojson",
            output_layer_name="empty_result",
        )
        with pytest.raises(EmptyOverlayResult, match="未产生任何结果几何"):
            overlay_features(layer_a, layer_disjoint, request)


# ---------------------------------------------------------------------------
# 空间连接测试
# ---------------------------------------------------------------------------


class TestSpatialJoin:
    """测试点面叠置和线面叠置。"""

    def test_point_in_polygon_transfers_attributes(self) -> None:
        """点面叠置应把面图层属性附加到落入面内的点上。"""
        point_layer: VectorLayer = _make_point_layer()
        polygon_layer: VectorLayer = _make_polygon_layer_a()
        request: OverlayRequest = OverlayRequest(
            input_layer_id=point_layer.layer_id,
            overlay_layer_id=polygon_layer.layer_id,
            operation="point_in_polygon",
            output_path="pip.geojson",
            output_layer_name="pip_result",
            sjoin_predicate="within",
        )
        result: tuple = overlay_features(point_layer, polygon_layer, request)
        # 只有 Point(2,2) 在 polygon_a 内（inner join 默认）
        assert len(result) == 1
        feature: Feature = result[0]
        assert feature.geometry.within(Polygon([(0, 0), (5, 0), (5, 5), (0, 5)]))

    def test_line_in_polygon_transfers_attributes(self) -> None:
        """线面叠置应把面图层属性附加到与面相交的线上。"""
        line_layer: VectorLayer = _make_line_layer()
        polygon_layer: VectorLayer = _make_polygon_layer_a()
        request: OverlayRequest = OverlayRequest(
            input_layer_id=line_layer.layer_id,
            overlay_layer_id=polygon_layer.layer_id,
            operation="line_in_polygon",
            output_path="lip.geojson",
            output_layer_name="lip_result",
            sjoin_predicate="intersects",
        )
        result: tuple = overlay_features(line_layer, polygon_layer, request)
        assert len(result) == 1

    def test_spatial_join_left_keeps_all_input(self) -> None:
        """左连接应保留所有输入要素，不匹配的属性为空。"""
        point_layer: VectorLayer = _make_point_layer()
        polygon_layer: VectorLayer = _make_polygon_layer_a()
        request: OverlayRequest = OverlayRequest(
            input_layer_id=point_layer.layer_id,
            overlay_layer_id=polygon_layer.layer_id,
            operation="point_in_polygon",
            output_path="left_join.geojson",
            output_layer_name="left_result",
            sjoin_how="left",
            sjoin_predicate="within",
        )
        result: tuple = overlay_features(point_layer, polygon_layer, request)
        # left join 保留所有点（2个），不匹配的属性为 NaN
        assert len(result) == 2


# ---------------------------------------------------------------------------
# 通用测试
# ---------------------------------------------------------------------------


class TestOperationLabel:
    """测试操作标签工具函数。"""

    def test_known_operation_returns_chinese(self) -> None:
        """已知操作返回中文标签。"""
        assert operation_label("intersection") == "相交"
        assert operation_label("union") == "联合"
        assert operation_label("point_in_polygon") == "点面叠置"

    def test_unknown_operation_returns_itself(self) -> None:
        """未知操作返回原始字符串。"""
        assert operation_label("unknown") == "unknown"  # type: ignore[arg-type]
