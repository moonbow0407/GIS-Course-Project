"""栅格分析算法内核测试：重分类、DEM 地形和掩膜裁剪。"""

from pathlib import Path

import numpy as np
import pytest

from app.application.raster_analysis import (
    DemAnalysisRequest,
    RasterClipRequest,
    RasterReclassifyRequest,
    ReclassRule,
    apply_geometry_mask,
    build_equal_interval_rules,
    build_quantile_rules,
    build_unique_value_rules,
    compute_aspect,
    compute_hillshade,
    compute_slope,
    default_raster_nodata,
    reclassify_array,
    resolve_z_factor,
)
from app.application.raster_calculator import compute_raster_expression


def _all_valid_5x5() -> np.ndarray:
    """构建 5×5 全有效掩膜。"""
    return np.ones((5, 5), dtype=bool)


def _plane_dem() -> np.ndarray:
    """构建向东倾斜的平面 DEM：z 每列增加 5 米，像元 10 米。"""
    return np.tile(np.arange(5, dtype=np.float32) * 5.0, (5, 1))


class TestReclassify:
    """重分类算法。"""

    def test_equal_interval_rules_use_integer_class_codes(self) -> None:
        """等距分类应生成连续类别码，并让最后一个区间包含最大值。"""
        data = np.array([[0.0, 10.0, 20.0, 30.0]], dtype=np.float32)
        rules = build_equal_interval_rules(data, np.ones_like(data, dtype=bool), 3)

        assert [rule.output_value for rule in rules] == [1.0, 2.0, 3.0]
        assert rules[0].include_upper is False
        assert rules[-1].include_upper is True
        assert rules[-1].matches(30.0)

    def test_unique_value_rules_are_exact_and_bounded(self) -> None:
        """唯一值分类应为每个值生成闭区间规则并拒绝过多行。"""
        data = np.array([[4.0, 1.0, 4.0, 9.0]], dtype=np.float32)
        rules = build_unique_value_rules(data, np.ones_like(data, dtype=bool))

        assert [(rule.lower, rule.upper) for rule in rules] == [
            (1.0, 1.0),
            (4.0, 4.0),
            (9.0, 9.0),
        ]
        assert all(rule.include_lower and rule.include_upper for rule in rules)
        with pytest.raises(ValueError, match="安全上限"):
            build_unique_value_rules(data, np.ones_like(data, dtype=bool), max_rules=2)

    def test_quantile_rules_merge_duplicate_breaks(self) -> None:
        """分位数分类在重复值较多时应合并重复断点而不产生重叠。"""
        data = np.array([[1.0, 1.0, 1.0, 2.0, 3.0]], dtype=np.float32)
        rules = build_quantile_rules(data, np.ones_like(data, dtype=bool), 4)

        assert len(rules) >= 2
        assert all(
            not (left.matches(right.lower) and right.matches(right.lower))
            for left, right in zip(rules, rules[1:], strict=False)
            if left.upper is not None and right.lower is not None
        )
        assert rules[-1].matches(3.0)

    def test_interval_boundaries_follow_inclusion_flags(self) -> None:
        """区间边界应遵循包含/不包含设置。"""
        data = np.array([[0.0, 10.0], [5.0, 15.0]], dtype=np.float32)
        valid = np.ones((2, 2), dtype=bool)
        rules = (
            ReclassRule(lower=0.0, upper=10.0, output_value=1.0),
            ReclassRule(lower=10.0, upper=20.0, output_value=2.0),
        )
        result, result_valid = reclassify_array(
            data, valid, rules, "nodata", None, "float32"
        )
        # [0,10) → 1；[10,20) → 2。
        np.testing.assert_array_equal(result, [[1.0, 2.0], [1.0, 2.0]])
        assert result_valid.all()

    def test_inclusive_upper_boundary(self) -> None:
        """显式包含上限的区间应命中上边界值。"""
        data = np.array([[10.0]], dtype=np.float32)
        valid = np.ones((1, 1), dtype=bool)
        rules = (
            ReclassRule(lower=0.0, upper=10.0, output_value=9.0, include_upper=True),
        )
        result, _ = reclassify_array(data, valid, rules, "nodata", None, "float32")
        assert result[0, 0] == 9.0

    def test_unbounded_rule_matches_all(self) -> None:
        """无界规则应匹配全部像元。"""
        data = np.array([[-100.0, 100.0]], dtype=np.float32)
        valid = np.ones((1, 2), dtype=bool)
        rules = (ReclassRule(lower=None, upper=None, output_value=7.0),)
        result, _ = reclassify_array(data, valid, rules, "nodata", None, "float32")
        np.testing.assert_array_equal(result, [[7.0, 7.0]])

    def test_unmatched_policy_nodata(self) -> None:
        """未匹配策略 nodata 应将未匹配像元设为无效。"""
        data = np.array([[1.0, 50.0]], dtype=np.float32)
        valid = np.ones((1, 2), dtype=bool)
        rules = (ReclassRule(lower=0.0, upper=10.0, output_value=1.0),)
        _, result_valid = reclassify_array(
            data, valid, rules, "nodata", None, "float32"
        )
        np.testing.assert_array_equal(result_valid, [[True, False]])

    def test_unmatched_policy_keep(self) -> None:
        """未匹配策略 keep 应保留原始值。"""
        data = np.array([[1.0, 50.0]], dtype=np.float32)
        valid = np.ones((1, 2), dtype=bool)
        rules = (ReclassRule(lower=0.0, upper=10.0, output_value=1.0),)
        result, result_valid = reclassify_array(
            data, valid, rules, "keep", None, "float32"
        )
        np.testing.assert_array_equal(result, [[1.0, 50.0]])
        assert result_valid.all()

    def test_unmatched_policy_constant(self) -> None:
        """未匹配策略 constant 应使用统一常量值。"""
        data = np.array([[1.0, 50.0]], dtype=np.float32)
        valid = np.ones((1, 2), dtype=bool)
        rules = (ReclassRule(lower=0.0, upper=10.0, output_value=1.0),)
        result, _ = reclassify_array(data, valid, rules, "constant", 0.0, "float32")
        np.testing.assert_array_equal(result, [[1.0, 0.0]])

    def test_invalid_pixels_stay_invalid(self) -> None:
        """无效输入像元不参与重分类。"""
        data = np.array([[1.0, 2.0]], dtype=np.float32)
        valid = np.array([[True, False]])
        rules = (ReclassRule(lower=0.0, upper=10.0, output_value=9.0),)
        _, result_valid = reclassify_array(
            data, valid, rules, "keep", None, "float32"
        )
        np.testing.assert_array_equal(result_valid, [[True, False]])

    def test_overlapping_rules_rejected(self) -> None:
        """重叠规则在请求构造阶段应被拒绝。"""
        with pytest.raises(ValueError):
            RasterReclassifyRequest(
                input_layer_id="a",
                band_index=1,
                rules=(
                    ReclassRule(lower=0.0, upper=10.0, output_value=1.0),
                    ReclassRule(lower=5.0, upper=20.0, output_value=2.0),
                ),
                unmatched_policy="nodata",
                output_layer_name="x",
                output_path=Path("out.tif"),
            )

    def test_inclusive_touching_boundaries_rejected(self) -> None:
        """涓や釜鍖洪棿鍚屾椂鍖呭惈鎺ヨЕ绔偣鏃跺簲琚嫆缁濄€?"""
        with pytest.raises(ValueError):
            RasterReclassifyRequest(
                input_layer_id="a",
                band_index=1,
                rules=(
                    ReclassRule(
                        lower=0.0,
                        upper=1.0,
                        output_value=1.0,
                        include_upper=True,
                    ),
                    ReclassRule(
                        lower=1.0,
                        upper=2.0,
                        output_value=2.0,
                        include_lower=True,
                    ),
                ),
                unmatched_policy="nodata",
                output_layer_name="x",
                output_path=Path("out.tif"),
            )

    def test_integer_output_fills_invalid_pixels_with_nodata(self) -> None:
        """整数输出转换前应将无效像元的 NaN 替换为 NoData。"""
        data = np.array([[1.0, 50.0]], dtype=np.float32)
        valid = np.ones((1, 2), dtype=bool)
        rules = (ReclassRule(lower=0.0, upper=10.0, output_value=1.0),)

        result, result_valid = reclassify_array(
            data,
            valid,
            rules,
            "nodata",
            None,
            "int16",
            -99.0,
        )

        np.testing.assert_array_equal(result, [[1, -99]])
        np.testing.assert_array_equal(result_valid, [[True, False]])

    def test_constant_policy_requires_value(self) -> None:
        """constant 策略缺少常量值时应拒绝构造。"""
        with pytest.raises(ValueError):
            RasterReclassifyRequest(
                input_layer_id="a",
                band_index=1,
                rules=(ReclassRule(lower=0.0, upper=1.0, output_value=1.0),),
                unmatched_policy="constant",
                output_layer_name="x",
                output_path=Path("out.tif"),
            )

    def test_nodata_must_fit_output_dtype(self) -> None:
        """NoData 必须能被输出数据类型表示。"""
        with pytest.raises(ValueError):
            RasterReclassifyRequest(
                input_layer_id="a",
                band_index=1,
                rules=(ReclassRule(lower=0.0, upper=1.0, output_value=1.0),),
                unmatched_policy="nodata",
                output_dtype="uint8",
                output_nodata=-9999.0,
                output_layer_name="x",
                output_path=Path("out.tif"),
            )


class TestDefaultNodata:
    """分析结果默认 NoData。"""

    def test_float_and_signed_int_get_sentinels(self) -> None:
        """浮点和有符号整型应得到可写入的默认 NoData。"""
        assert default_raster_nodata("float32") == -9999.0
        assert default_raster_nodata("int16") == -32768.0
        assert default_raster_nodata("uint8") is None


class TestDemAnalysis:
    """DEM 地形算法。"""

    def test_slope_of_known_plane_matches_theory(self) -> None:
        """已知平面 DEM 的坡度应接近理论值 atan(0.5)。"""
        result, valid = compute_slope(_plane_dem(), _all_valid_5x5(), 10.0, 10.0, 1.0)
        expected = np.degrees(np.arctan(0.5))
        assert valid[2, 2]
        np.testing.assert_allclose(result[2, 2], expected, atol=1e-3)

    def test_slope_flat_dem_is_zero(self) -> None:
        """平坦 DEM 坡度应为零。"""
        dem = np.full((5, 5), 100.0, dtype=np.float32)
        result, valid = compute_slope(
            dem, _all_valid_5x5(), 10.0, 10.0, 1.0
        )
        assert np.allclose(result[1:-1, 1:-1][valid[1:-1, 1:-1]], 0.0)

    def test_aspect_flat_dem_is_nodata(self) -> None:
        """平坦 DEM 坡向应为 NoData（无效）。"""
        dem = np.full((5, 5), 100.0, dtype=np.float32)
        _, valid = compute_aspect(dem, _all_valid_5x5(), 10.0, 10.0, 1.0)
        assert not valid.any()

    def test_aspect_of_north_facing_slope(self) -> None:
        """南高北低（向北下降）的坡面应朝北，坡向接近 0°/360°。"""
        # z 随行号（向南）增大：北低南高，下坡方向朝北。
        dem = np.tile(
            (np.arange(5, dtype=np.float32) * 5.0).reshape(5, 1), (1, 5)
        )
        result, valid = compute_aspect(dem, _all_valid_5x5(), 10.0, 10.0, 1.0)
        assert valid[2, 2]
        aspect = result[2, 2]
        assert aspect < 1e-3 or aspect > 359.999

    def test_aspect_of_east_facing_slope(self) -> None:
        """西高东低的坡面应朝东，坡向接近 90°。"""
        # z 随列减小：西高东低，下坡方向朝东。
        dem = np.tile(
            np.arange(5, dtype=np.float32)[::-1] * 5.0, (5, 1)
        )
        result, valid = compute_aspect(dem, _all_valid_5x5(), 10.0, 10.0, 1.0)
        assert valid[2, 2]
        np.testing.assert_allclose(result[2, 2], 90.0, atol=1e-3)

    def test_hillshade_output_in_valid_range(self) -> None:
        """山体阴影输出应在 0–255 范围内。"""
        result, valid = compute_hillshade(
            _plane_dem(), _all_valid_5x5(), 10.0, 10.0, 1.0, 315.0, 45.0
        )
        assert result.dtype == np.uint8
        assert result[valid].min() >= 0
        assert result[valid].max() <= 255

    def test_neighbour_nodata_propagates(self) -> None:
        """邻域包含 NoData 的中心像元应输出 NoData。"""
        valid_mask = _all_valid_5x5()
        valid_mask[2, 3] = False  # 中心 (2,2) 的东邻无效。
        _, result_valid = compute_slope(
            _plane_dem(), valid_mask, 10.0, 10.0, 1.0
        )
        assert not result_valid[2, 2]

    def test_edge_pixels_have_no_neighbourhood(self) -> None:
        """影像边缘像元没有完整邻域，应输出无效。"""
        _, result_valid = compute_slope(
            _plane_dem(), _all_valid_5x5(), 10.0, 10.0, 1.0
        )
        assert not result_valid[0, :].any()
        assert not result_valid[:, 0].any()
        assert not result_valid[-1, :].any()
        assert not result_valid[:, -1].any()

    def test_z_factor_converts_feet_to_meters(self) -> None:
        """英尺高程默认 Z 因子应为 0.3048。"""
        assert resolve_z_factor("foot", None) == pytest.approx(0.3048)
        assert resolve_z_factor("meter", None) == 1.0
        assert resolve_z_factor("foot", 2.0) == 2.0

    def test_dem_request_validates_angles(self) -> None:
        """DEM 请求的方位角和高度角必须在合法范围内。"""
        with pytest.raises(ValueError):
            DemAnalysisRequest(input_layer_id="a", mode="hillshade", azimuth=400.0)
        with pytest.raises(ValueError):
            DemAnalysisRequest(input_layer_id="a", mode="hillshade", altitude=0.0)


class TestGeometryMask:
    """几何掩膜裁剪算法。"""

    def test_inside_kept_outside_invalid(self) -> None:
        """矢量范围内保留，范围外无效。"""
        data = np.ones((2, 2), dtype=np.float32)
        input_valid = np.ones((2, 2), dtype=bool)
        geometry_mask = np.array([[True, False], [True, False]])
        _, output_valid = apply_geometry_mask(data, input_valid, geometry_mask, False)
        np.testing.assert_array_equal(output_valid, [[True, False], [True, False]])

    def test_invert_keeps_outside(self) -> None:
        """反转掩膜后应保留矢量范围外像元。"""
        data = np.ones((2, 2), dtype=np.float32)
        input_valid = np.ones((2, 2), dtype=bool)
        geometry_mask = np.array([[True, False], [True, False]])
        _, output_valid = apply_geometry_mask(data, input_valid, geometry_mask, True)
        np.testing.assert_array_equal(output_valid, [[False, True], [False, True]])

    def test_values_unchanged(self) -> None:
        """掩膜裁剪不修改像元值，只更新有效掩膜。"""
        data = np.array([[1.0, 2.0]], dtype=np.float32)
        input_valid = np.ones((1, 2), dtype=bool)
        geometry_mask = np.array([[True, False]])
        result, _ = apply_geometry_mask(data, input_valid, geometry_mask, False)
        np.testing.assert_array_equal(result, data)

    def test_clip_request_defaults(self) -> None:
        """掩膜裁剪请求默认值应符合设计文档。"""
        request = RasterClipRequest(
            raster_layer_id="r",
            mask_layer_id="m",
            output_layer_name="x",
            output_path=Path("out.tif"),
        )
        assert request.crop is True
        assert request.all_touched is False
        assert request.invert is False


class TestExpressionAstWhitelist:
    """表达式 AST 白名单。"""

    def test_normal_expression_still_works(self) -> None:
        """合法表达式行为不变。"""
        bands = {"a": np.array([[1.0, 2.0]], dtype=np.float32)}
        result = compute_raster_expression(bands, '"a" * 2')
        np.testing.assert_array_equal(result, [[2.0, 4.0]])

    def test_attribute_access_rejected(self) -> None:
        """属性访问应被拒绝。"""
        bands = {"a": np.ones((1, 2), dtype=np.float32)}
        with pytest.raises(ValueError, match="不允许"):
            compute_raster_expression(bands, '"a".shape')

    def test_subscript_rejected(self) -> None:
        """下标访问应被拒绝。"""
        bands = {"a": np.ones((1, 2), dtype=np.float32)}
        with pytest.raises(ValueError, match="不允许"):
            compute_raster_expression(bands, '"a"[0]')

    def test_import_rejected(self) -> None:
        """导入语句应被拒绝。"""
        bands = {"a": np.ones((1, 2), dtype=np.float32)}
        with pytest.raises(ValueError):
            compute_raster_expression(bands, "__import__('os')")

    def test_unregistered_function_rejected(self) -> None:
        """未注册函数调用应被拒绝。"""
        bands = {"a": np.ones((1, 2), dtype=np.float32)}
        with pytest.raises(ValueError, match="未注册的函数"):
            compute_raster_expression(bands, "sum(1, 2)")

    def test_comprehension_rejected(self) -> None:
        """列表推导等推导式应被拒绝。"""
        bands = {"a": np.ones((1, 2), dtype=np.float32)}
        with pytest.raises(ValueError, match="不允许"):
            compute_raster_expression(bands, "[x for x in a]")

    def test_registered_functions_allowed(self) -> None:
        """注册表中的函数应正常使用。"""
        bands = {"a": np.array([[4.0, 9.0]], dtype=np.float32)}
        result = compute_raster_expression(bands, 'sqrt("a")')
        np.testing.assert_allclose(result, [[2.0, 3.0]], atol=1e-6)
