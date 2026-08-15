"""矢量与栅格符号系统应用服务测试。"""

import warnings

import numpy as np
import pytest
from affine import Affine
from pyproj import CRS
from shapely.geometry import Point

from app.application.gis_application import GisApplication
from app.application.symbology_service import (
    apply_raster_symbology,
    create_dem_result_symbology,
    create_graduated_symbology,
    create_raster_classified_symbology,
    create_unique_value_symbology,
)
from app.domain.feature import Feature
from app.domain.map_document import MapDocument
from app.domain.raster_layer import RasterLayer
from app.domain.symbology import (
    RasterClass,
    RasterRendererType,
    RasterSymbology,
    StretchType,
    VectorRendererType,
    raster_symbology_from_dict,
    symbology_to_dict,
)
from app.domain.vector_layer import VectorLayer
from app.infrastructure.file_io.auto_reader import AutoDataReader


def test_unique_values_are_limited_to_one_hundred_with_other_symbol() -> None:
    """高基数字段只自动生成一百类，其余要素使用“其他值”符号。"""
    layer = VectorLayer.create(
        name="编号点",
        features=tuple(
            Feature(fid=index, geometry=Point(index, 0), attributes={"code": index})
            for index in range(105)
        ),
        crs=CRS.from_epsg(4326),
    )

    symbology = create_unique_value_symbology(layer, "code", "standard")

    assert symbology.renderer_type is VectorRendererType.UNIQUE
    assert len(symbology.unique_classes) == 100
    assert symbology.other_symbol is not None
    categorized_keys = {category.value_key for category in symbology.unique_classes}
    uncategorized = next(
        feature
        for feature in layer.features
        if f"int:{feature.attributes['code']}" not in categorized_keys
    )
    assert symbology.symbol_for(uncategorized) == symbology.other_symbol


def test_graduated_colors_support_equal_interval_and_quantile() -> None:
    """数值字段应生成至少三级的等间隔或分位数颜色。"""
    layer = VectorLayer.create(
        name="监测点",
        features=tuple(
            Feature(fid=index, geometry=Point(index, 0), attributes={"value": index})
            for index in range(10)
        ),
        crs=CRS.from_epsg(4326),
    )

    equal = create_graduated_symbology(layer, "value", "blue", "equal_interval", 5)
    quantile = create_graduated_symbology(layer, "value", "viridis", "quantile", 4)

    assert len(equal.graduated_classes) == 5
    assert equal.graduated_classes[0].lower == 0
    assert equal.graduated_classes[-1].upper == 9
    assert len(quantile.graduated_classes) == 4


def test_graduated_class_count_cannot_exceed_numeric_sample_count() -> None:
    """分级数超过字段有效数值样本数时应给出明确错误。"""
    layer = VectorLayer.create(
        name="七个行政区",
        features=tuple(
            Feature(fid=index, geometry=Point(index, 0), attributes={"value": index})
            for index in range(7)
        ),
        crs=CRS.from_epsg(4326),
    )

    with pytest.raises(ValueError, match="不能超过可用于分级的数值样本数（当前为 7）"):
        create_graduated_symbology(layer, "value", "gray", "equal_interval", 8)


def test_single_band_raster_stretch_generates_color_ramp_and_transparent_nodata() -> None:
    """单波段拉伸应按色带生成 RGB，并保持无效像元透明。"""
    data = np.asarray([[[0.0, 50.0], [100.0, 999.0]]])
    valid = np.asarray([[True, True], [True, False]], dtype=np.bool_)
    layer = RasterLayer.create(
        name="高程",
        raster_data=data,
        image_data=np.zeros((2, 2, 4), dtype=np.uint8),
        valid_mask=valid,
        transform=Affine.identity(),
        crs=CRS.from_epsg(4326),
        bounds=(0, 0, 2, 2),
    )
    config = RasterSymbology(
        renderer_type=RasterRendererType.STRETCH,
        stretch_type=StretchType.MIN_MAX,
        color_scheme="blue",
    )

    styled = apply_raster_symbology(layer, config)

    assert styled.image_data[0, 0, :3].tolist() == [247, 251, 255]
    assert styled.image_data[1, 0, :3].tolist() == [8, 48, 107]
    assert styled.image_data[1, 1, 3] == 0


def test_raster_stretch_handles_nan_pixels_without_casting_warning() -> None:
    """含 NaN 像元的栅格拉伸不应触发取整告警或越界索引。"""
    data = np.asarray([[[0.0, np.nan], [100.0, 999.0]]])
    valid = np.asarray([[True, False], [True, False]], dtype=np.bool_)
    layer = RasterLayer.create(
        name="含NaN高程",
        raster_data=data,
        image_data=np.zeros((2, 2, 4), dtype=np.uint8),
        valid_mask=valid,
        transform=Affine.identity(),
        crs=CRS.from_epsg(4326),
        bounds=(0, 0, 2, 2),
    )
    config = RasterSymbology(
        renderer_type=RasterRendererType.STRETCH,
        stretch_type=StretchType.MIN_MAX,
        color_scheme="blue",
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        styled = apply_raster_symbology(layer, config)

    # 无效像元保持透明，有效像元颜色不受影响。
    assert styled.image_data[0, 1, 3] == 0
    assert styled.image_data[0, 0, :3].tolist() == [247, 251, 255]
    assert styled.image_data[1, 0, :3].tolist() == [8, 48, 107]


def test_classified_raster_uses_discrete_colors_and_round_trips() -> None:
    """重分类值应按离散颜色渲染，工程保存后分类配置仍可恢复。"""
    data = np.asarray([[[1.0, 2.0], [3.0, 99.0]]])
    valid = np.asarray([[True, True], [True, False]], dtype=np.bool_)
    layer = RasterLayer.create(
        name="重分类",
        raster_data=data,
        image_data=np.zeros((2, 2, 4), dtype=np.uint8),
        valid_mask=valid,
        transform=Affine.identity(),
        crs=CRS.from_epsg(4326),
        bounds=(0, 0, 2, 2),
    )
    config = create_raster_classified_symbology((1.0, 2.0, 3.0))

    styled = apply_raster_symbology(layer, config)

    assert styled.image_data[0, 0, :3].tolist() == [78, 121, 167]
    assert styled.image_data[0, 1, :3].tolist() == [242, 142, 43]
    assert styled.image_data[1, 0, :3].tolist() == [89, 161, 79]
    assert styled.image_data[1, 1, 3] == 0
    restored = raster_symbology_from_dict(symbology_to_dict(config))
    assert restored == config


def test_raster_classification_accepts_custom_class_visibility() -> None:
    """分类渲染器应支持隐藏某个等级并显示未匹配值颜色。"""
    data = np.asarray([[[1.0, 2.0]]])
    layer = RasterLayer.create(
        name="分类",
        raster_data=data,
        image_data=np.zeros((1, 2, 4), dtype=np.uint8),
        valid_mask=np.ones((1, 2), dtype=np.bool_),
        transform=Affine.identity(),
        crs=CRS.from_epsg(4326),
        bounds=(0, 0, 2, 1),
    )
    config = RasterSymbology(
        renderer_type=RasterRendererType.CLASSIFIED,
        classes=(RasterClass(1.0, "一级", "#FF0000", visible=False),),
        other_color="#00FF00",
    )

    styled = apply_raster_symbology(layer, config)

    assert styled.image_data[0, 0, 3] == 0
    assert styled.image_data[0, 1, :3].tolist() == [0, 255, 0]


def test_classified_raster_matches_value_range_when_upper_is_set() -> None:
    """区间分类应按 [下限, 上限] 着色，精确值分类保持原语义。"""
    data = np.asarray([[[1.5, 26.0], [90.0, 99.0]]])
    valid = np.asarray([[True, True], [True, False]], dtype=np.bool_)
    layer = RasterLayer.create(
        name="坡度预览",
        raster_data=data,
        image_data=np.zeros((2, 2, 4), dtype=np.uint8),
        valid_mask=valid,
        transform=Affine.identity(),
        crs=CRS.from_epsg(4326),
        bounds=(0, 0, 2, 2),
    )
    config = RasterSymbology(
        renderer_type=RasterRendererType.CLASSIFIED,
        classes=(
            RasterClass(0.0, "平缓", "#00FF00", upper=2.0),
            RasterClass(25.0, "陡坡", "#FF8800", upper=35.0),
            RasterClass(45.0, "峭壁", "#FF0000", upper=90.0),
        ),
        other_visible=False,
    )

    styled = apply_raster_symbology(layer, config)

    assert styled.image_data[0, 0, :3].tolist() == [0, 255, 0]
    assert styled.image_data[0, 1, :3].tolist() == [255, 136, 0]
    assert styled.image_data[1, 0, :3].tolist() == [255, 0, 0]
    assert styled.image_data[1, 1, 3] == 0


def test_classified_raster_range_can_wrap_around_zero() -> None:
    """上限小于下限时按环形区间匹配，用于坡向正北。"""
    data = np.asarray([[[350.0, 10.0, 90.0]]])
    layer = RasterLayer.create(
        name="坡向预览",
        raster_data=data,
        image_data=np.zeros((1, 3, 4), dtype=np.uint8),
        valid_mask=np.ones((1, 3), dtype=np.bool_),
        transform=Affine.identity(),
        crs=CRS.from_epsg(4326),
        bounds=(0, 0, 3, 1),
    )
    config = RasterSymbology(
        renderer_type=RasterRendererType.CLASSIFIED,
        classes=(
            RasterClass(337.5, "北", "#FF0000", upper=22.5),
            RasterClass(67.5, "东", "#FFFF00", upper=112.5),
        ),
        other_visible=False,
    )

    styled = apply_raster_symbology(layer, config)

    assert styled.image_data[0, 0, :3].tolist() == [255, 0, 0]
    assert styled.image_data[0, 1, :3].tolist() == [255, 0, 0]
    assert styled.image_data[0, 2, :3].tolist() == [255, 255, 0]


def test_slope_result_symbology_is_classified_with_degree_labels() -> None:
    """坡度结果应使用带度数标签的分类色，而不是默认灰度拉伸。"""
    symbology = create_dem_result_symbology("slope")

    assert symbology.renderer_type is RasterRendererType.CLASSIFIED
    assert all(category.upper is not None for category in symbology.classes)
    assert any("平缓" in category.label for category in symbology.classes)
    assert any("峭壁" in category.label for category in symbology.classes)
    restored = raster_symbology_from_dict(symbology_to_dict(symbology))
    assert restored == symbology


def test_aspect_result_symbology_classifies_compass_directions() -> None:
    """坡向结果应按八方位分类，并覆盖跨越 0° 的正北区间。"""
    symbology = create_dem_result_symbology("aspect")

    assert symbology.renderer_type is RasterRendererType.CLASSIFIED
    labels = [category.label for category in symbology.classes]
    assert any(label.startswith("北") for label in labels)
    assert any(label.startswith("东") for label in labels)
    north = next(category for category in symbology.classes if category.label.startswith("北"))
    assert north.upper is not None
    assert north.value > north.upper


def test_hillshade_result_symbology_uses_minmax_gray_stretch() -> None:
    """山体阴影应使用最小—最大灰度拉伸，呈现明暗起伏。"""
    symbology = create_dem_result_symbology("hillshade")

    assert symbology.renderer_type is RasterRendererType.STRETCH
    assert symbology.stretch_type is StretchType.MIN_MAX
    assert symbology.color_scheme == "gray"


def test_application_replaces_symbology_without_changing_layer_identity() -> None:
    """应用符号后应保留图层编号并标记工程已修改。"""
    layer = VectorLayer.create(
        layer_id="stable",
        name="地类",
        features=(
            Feature(fid=1, geometry=Point(0, 0), attributes={"type": "林地"}),
            Feature(fid=2, geometry=Point(1, 1), attributes={"type": "水域"}),
        ),
        crs=CRS.from_epsg(4326),
    )
    document = MapDocument()
    document.add_layer(layer)
    application = GisApplication(AutoDataReader(), document=document)

    snapshot = application.apply_unique_value_symbology("stable", "type", "soft")

    assert snapshot.layers[0].layer_id == "stable"
    assert snapshot.layers[0].layer.symbology.renderer_type is VectorRendererType.UNIQUE
    assert application.is_modified is True
