"""地图文档领域模型测试。"""

from pathlib import Path

import numpy as np
import pytest
from affine import Affine
from pyproj import CRS
from shapely.geometry import Point

from app.domain.feature import Feature
from app.domain.lod import LodLevel, LodPyramid
from app.domain.map_document import MapDocument
from app.domain.raster_layer import RasterLayer
from app.domain.spatial_layer import SpatialLayer
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


def test_add_layer_allows_known_mixed_coordinate_reference_systems() -> None:
    """地图文档应允许不同已知 CRS 图层共存，并保持首个显示 CRS。"""
    document: MapDocument = MapDocument()
    document.add_layer(make_layer("wgs84", 4326))

    document.add_layer(make_layer("web_mercator", 3857))

    assert tuple(layer.layer_id for layer in document.layers) == (
        "wgs84",
        "web_mercator",
    )
    assert document.display_crs == CRS.from_epsg(4326)


def test_unknown_crs_layer_is_rejected_before_entering_document() -> None:
    """未知 CRS 图层必须在进入地图文档前完成定义。"""
    document: MapDocument = MapDocument()

    with pytest.raises(ValueError, match="未定义 CRS"):
        document.add_layer(make_layer("unknown", None))


def test_empty_document_has_no_display_crs() -> None:
    """空地图没有显示 CRS，必须先加入已定义 CRS 的图层。"""
    document: MapDocument = MapDocument()
    display_crs: CRS = CRS.from_epsg(3857)
    with pytest.raises(ValueError, match="空地图"):
        document.set_display_crs(display_crs)

    document.add_layer(make_layer("roads", 3857))
    document.remove_layer("roads")
    assert document.layers == ()
    assert document.display_crs is None


def test_display_crs_can_change_after_first_layer() -> None:
    """首个图层建立显示 CRS 后，用户可以切换地图显示 CRS。"""
    document: MapDocument = MapDocument()
    document.add_layer(make_layer("wgs84", 4326))
    document.set_display_crs(CRS.from_epsg(3857))

    assert document.display_crs == CRS.from_epsg(3857)


# ── 图层版本号 ─────────────────────────────────────────


def test_add_layer_starts_revision_at_one() -> None:
    """新加入的图层应具有初始版本号一。"""
    document: MapDocument = MapDocument()
    document.add_layer(make_layer("roads"))

    assert document.layer_revision("roads") == 1


def test_replace_layer_increments_revision() -> None:
    """替换图层内容应使版本号递增，使显示缓存失效。"""
    document: MapDocument = MapDocument()
    document.add_layer(make_layer("roads"))
    document.replace_layer(make_layer("roads"))

    assert document.layer_revision("roads") == 2


def test_remove_layer_cleans_up_revision() -> None:
    """移除图层后其版本号应被清理，不再可查询。"""
    document: MapDocument = MapDocument()
    document.add_layer(make_layer("roads"))
    document.remove_layer("roads")

    with pytest.raises(KeyError, match="图层不存在"):
        document.layer_revision("roads")


def test_display_state_changes_do_not_increment_revision() -> None:
    """显隐、选择、活动状态和显示设置变化不应影响内容版本号。"""
    document: MapDocument = MapDocument()
    document.add_layer(make_layer("roads"))
    document.set_layer_visibility("roads", False)
    document.set_layer_visibility("roads", True)
    document.set_selection("roads", (1,))
    document.set_active_layer("roads")
    document.set_layer_opacity("roads", 0.5)
    document.set_layer_blend_mode("roads", "multiply")
    document.set_layer_scale_range("roads", 10.0, 100.0)
    document.move_layer("roads", 0)

    assert document.layer_revision("roads") == 1


def test_set_layer_lod_attaches_without_incrementing_revision() -> None:
    """挂接 LOD 金字塔不改变几何与显示载荷，无需递增内容版本。"""
    document: MapDocument = MapDocument()
    document.add_layer(make_layer("roads"))
    pyramid: LodPyramid = LodPyramid((
        LodLevel(0.0, document.layers[0].features),
    ))

    document.set_layer_lod("roads", pyramid)

    updated: SpatialLayer = document.layers[0]
    assert isinstance(updated, VectorLayer)
    assert updated.lod is pyramid
    assert updated.layer_id == "roads"
    assert document.layer_revision("roads") == 1


def test_set_layer_lod_rejects_missing_layer() -> None:
    """不存在的图层不应被挂接 LOD 金字塔。"""
    document: MapDocument = MapDocument()
    pyramid: LodPyramid = LodPyramid((LodLevel(0.0, ()),))

    with pytest.raises(KeyError, match="图层不存在"):
        document.set_layer_lod("missing", pyramid)


def test_set_layer_lod_clears_pyramid_when_none() -> None:
    """传入 None 应移除已挂载的金字塔，恢复完整几何渲染。"""
    document: MapDocument = MapDocument()
    document.add_layer(make_layer("roads"))
    document.set_layer_lod(
        "roads",
        LodPyramid((LodLevel(0.0, document.layers[0].features),)),
    )

    document.set_layer_lod("roads", None)

    updated: SpatialLayer = document.layers[0]
    assert isinstance(updated, VectorLayer)
    assert updated.lod is None
    assert document.layer_revision("roads") == 1


# ── 图层重命名 ─────────────────────────────────────────


def test_rename_layer_updates_name_and_preserves_workspace_state() -> None:
    """重命名应只改显示名称，保留顺序、源路径、活动状态、显隐和选择。"""
    document: MapDocument = MapDocument()
    roads: VectorLayer = make_layer("roads")
    roads = VectorLayer.create(
        layer_id=roads.layer_id,
        name=roads.name,
        features=roads.features,
        crs=roads.crs,
        source_path=Path("D:/data/roads.shp"),
    )
    document.add_layer(roads)
    document.add_layer(make_layer("rivers"))
    document.set_active_layer("roads")
    document.set_selection("roads", (1,))

    document.rename_layer("roads", "主干道")

    renamed: VectorLayer = document.layers[0]
    assert renamed.name == "主干道"
    assert renamed.source_path == Path("D:/data/roads.shp")
    assert renamed.layer_id == "roads"
    assert tuple(layer.layer_id for layer in document.layers) == ("roads", "rivers")
    assert document.active_layer_id == "roads"
    assert document.is_visible("roads") is True
    assert document.selected_feature_ids("roads") == (1,)


def test_rename_layer_does_not_increment_revision() -> None:
    """重命名是纯元数据变更，不应使显示缓存失效。"""
    document: MapDocument = MapDocument()
    document.add_layer(make_layer("roads"))

    document.rename_layer("roads", "新名称")

    assert document.layer_revision("roads") == 1


def test_rename_layer_rejects_blank_name() -> None:
    """空白名称应被拒绝，避免产生无意义图层名。"""
    document: MapDocument = MapDocument()
    document.add_layer(make_layer("roads"))

    with pytest.raises(ValueError, match="不能为空"):
        document.rename_layer("roads", "   ")


def test_rename_layer_rejects_missing_layer() -> None:
    """不存在的图层不应被重命名。"""
    document: MapDocument = MapDocument()

    with pytest.raises(KeyError, match="图层不存在"):
        document.rename_layer("missing", "新名称")


def test_rename_lazy_raster_layer_does_not_load_analysis_data() -> None:
    """重命名延迟栅格应直接替换身份，不触发完整像元加载。"""
    def fail_loader() -> tuple[np.ndarray, np.ndarray]:
        raise AssertionError("重命名不应触发延迟加载")

    raster: RasterLayer = RasterLayer.create_lazy(
        name="旧栅格名",
        image_data=np.full((2, 2, 4), 255, dtype=np.uint8),
        transform=Affine.identity(),
        display_transform=Affine.identity(),
        crs=CRS.from_epsg(4326),
        bounds=(0.0, 0.0, 2.0, 2.0),
        raster_shape=(2, 2),
        band_count=1,
        analysis_loader=fail_loader,
    )
    document: MapDocument = MapDocument()
    document.add_layer(raster)

    document.rename_layer(raster.layer_id, "新栅格名")

    renamed: SpatialLayer = document.layers[0]
    assert renamed.name == "新栅格名"
    assert isinstance(renamed, RasterLayer)
    assert renamed.analysis_data_loaded is False
    assert document.layer_revision(raster.layer_id) == 1
