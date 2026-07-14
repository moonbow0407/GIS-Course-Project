"""地图文档领域模型测试。"""

import pytest
from pyproj import CRS
from shapely.geometry import Point

from app.domain.feature import Feature
from app.domain.map_document import MapDocument
from app.domain.vector_layer import VectorLayer


def make_layer(layer_id: str, epsg: int | None = 4326) -> VectorLayer:
    """创建包含单个点要素的测试图层。"""
    crs: CRS | None = CRS.from_epsg(epsg) if epsg is not None else None
    feature: Feature = Feature(fid=1, geometry=Point(0, 0), attributes={"名称": layer_id})
    return VectorLayer.create(
        layer_id=layer_id,
        name=layer_id,
        features=(feature,),
        crs=crs,
    )


def test_add_layer_establishes_order_active_layer_and_display_crs() -> None:
    """首个图层应建立地图文档顺序、活动图层和显示坐标系。"""
    document: MapDocument = MapDocument()
    layer: VectorLayer = make_layer("roads")

    document.add_layer(layer)

    assert document.layers == (layer,)
    assert document.active_layer_id == "roads"
    assert document.display_crs == CRS.from_epsg(4326)
    assert document.is_visible("roads") is True


def test_add_layer_rejects_duplicate_identifier() -> None:
    """地图文档不应接受重复图层编号。"""
    document: MapDocument = MapDocument()
    layer: VectorLayer = make_layer("roads")
    document.add_layer(layer)

    with pytest.raises(ValueError, match="图层编号已存在"):
        document.add_layer(layer)


def test_remove_active_layer_selects_next_layer_and_clears_selection() -> None:
    """删除活动图层后应选择相邻图层并清除被删图层选择。"""
    document: MapDocument = MapDocument()
    roads: VectorLayer = make_layer("roads")
    rivers: VectorLayer = make_layer("rivers")
    document.add_layer(roads)
    document.add_layer(rivers)
    document.set_active_layer("roads")
    document.set_selection("roads", (1,))

    removed: VectorLayer = document.remove_layer("roads")

    assert removed is roads
    assert document.layers == (rivers,)
    assert document.active_layer_id == "rivers"
    assert document.selected_feature_ids("roads") == ()


def test_move_layer_preserves_active_layer() -> None:
    """调整图层顺序时不应改变当前活动图层。"""
    document: MapDocument = MapDocument()
    roads: VectorLayer = make_layer("roads")
    rivers: VectorLayer = make_layer("rivers")
    document.add_layer(roads)
    document.add_layer(rivers)
    document.set_active_layer("roads")

    document.move_layer("roads", 1)

    assert document.layers == (rivers, roads)
    assert document.active_layer_id == "roads"


def test_move_layer_rejects_invalid_target_position() -> None:
    """图层目标位置超出范围时应拒绝移动。"""
    document: MapDocument = MapDocument()
    document.add_layer(make_layer("roads"))

    with pytest.raises(IndexError, match="目标位置超出范围"):
        document.move_layer("roads", 2)


def test_hiding_layer_clears_its_selection() -> None:
    """隐藏图层时应清除该图层中的已选要素。"""
    document: MapDocument = MapDocument()
    document.add_layer(make_layer("roads"))
    document.set_selection("roads", (1,))

    document.set_layer_visibility("roads", False)

    assert document.is_visible("roads") is False
    assert document.selected_feature_ids("roads") == ()


def test_add_layer_rejects_incompatible_coordinate_reference_system() -> None:
    """进入地图文档的图层坐标系必须与显示坐标系一致。"""
    document: MapDocument = MapDocument()
    document.add_layer(make_layer("wgs84", 4326))

    with pytest.raises(ValueError, match="坐标参考系统不一致"):
        document.add_layer(make_layer("web_mercator", 3857))


def test_known_crs_cannot_follow_unknown_crs_document() -> None:
    """已有未知坐标系图层时不能静默加入已知坐标系图层。"""
    document: MapDocument = MapDocument()
    document.add_layer(make_layer("unknown", None))

    with pytest.raises(ValueError, match="无法与未知坐标系"):
        document.add_layer(make_layer("wgs84", 4326))
