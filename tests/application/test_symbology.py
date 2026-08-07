"""矢量与栅格符号系统应用服务测试。"""

import numpy as np
import pytest
from affine import Affine
from pyproj import CRS
from shapely.geometry import Point

from app.application.gis_application import GisApplication
from app.application.symbology_service import (
    apply_raster_symbology,
    create_graduated_symbology,
    create_unique_value_symbology,
)
from app.domain.feature import Feature
from app.domain.map_document import MapDocument
from app.domain.raster_layer import RasterLayer
from app.domain.symbology import (
    RasterRendererType,
    RasterSymbology,
    StretchType,
    VectorRendererType,
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
