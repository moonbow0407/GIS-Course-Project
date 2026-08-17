"""撤销/重做完善后的按钮接线、栈覆盖与边界防御测试。"""

import os
import time
from dataclasses import replace
from pathlib import Path

import pytest

# 必须在导入 Qt 前启用无界面平台，测试才能在没有显示器的环境运行。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pyproj import CRS
from PySide6.QtWidgets import QApplication, QMessageBox
from shapely.geometry import Point

from app.application.gis_application import GisApplication
from app.domain.feature import Feature
from app.domain.map_document import MapDocument
from app.domain.symbology import VectorRendererType, VectorSymbology
from app.domain.vector_layer import VectorLayer
from app.infrastructure.file_io.auto_reader import AutoDataReader
from app.infrastructure.file_io.auto_writer import AutoDataWriter
from app.infrastructure.file_io.geopandas_vector_writer import GeoPandasVectorWriter
from app.presentation.main_window import MainWindow

CRS_4549: CRS = CRS.from_epsg(4549)


def _make_layer(layer_id: str, name: str) -> VectorLayer:
    """构造带单个点要素和数字属性的测试矢量图层。"""
    return VectorLayer.create(
        layer_id=layer_id,
        name=name,
        features=(
            Feature(fid=1, geometry=Point(0, 0), attributes={"name": "甲"}),
            Feature(fid=2, geometry=Point(1000, 1000), attributes={"name": "乙"}),
        ),
        crs=CRS_4549,
    )


def _make_window(document: MapDocument) -> MainWindow:
    """创建主窗口并替换为使用指定文档的应用服务。"""
    QApplication.instance() or QApplication([])
    window: MainWindow = MainWindow()
    window._application = GisApplication(AutoDataReader(), AutoDataWriter(), document)
    window._refresh_workspace()
    return window


def test_undo_redo_buttons_are_wired_and_follow_stack_state() -> None:
    """功能区撤销/重做按钮应随栈状态启用，并通过路由触发对应操作。"""
    window: MainWindow = _make_window(MapDocument())
    assert window._ribbon._all_buttons["undo"].isEnabled() is False
    assert window._ribbon._all_buttons["redo"].isEnabled() is False

    undo_calls: list[int] = []
    redo_calls: list[int] = []
    window._push_undo(
        "测试操作",
        undo_action=lambda: undo_calls.append(1),
        redo_action=lambda: redo_calls.append(1),
    )
    assert window._ribbon._all_buttons["undo"].isEnabled() is True
    assert window._ribbon._all_buttons["redo"].isEnabled() is False

    window._handle_action("undo")
    assert undo_calls == [1]
    assert window._ribbon._all_buttons["undo"].isEnabled() is False
    assert window._ribbon._all_buttons["redo"].isEnabled() is True

    window._handle_action("redo")
    assert redo_calls == [1]
    assert window._ribbon._all_buttons["undo"].isEnabled() is True
    assert window._ribbon._all_buttons["redo"].isEnabled() is False
    window.close()


def test_removed_layer_undo_restores_order_visibility_selection_and_active() -> None:
    """撤销删除图层应恢复原顺序、显隐、选择集和活动图层。"""
    layer_a: VectorLayer = _make_layer("a", "图层A")
    layer_b: VectorLayer = _make_layer("b", "图层B")
    layer_c: VectorLayer = _make_layer("c", "图层C")
    document: MapDocument = MapDocument()
    document.add_layer(layer_a)
    document.add_layer(layer_b)
    document.add_layer(layer_c)
    document.set_layer_visibility("b", False)
    document.set_active_layer("c")
    document.set_selection("c", (1,))
    window: MainWindow = _make_window(document)

    window._remove_layer("b")
    assert [layer.layer_id for layer in document.layers] == ["a", "c"]

    window._undo()
    layer_ids: list[str] = [layer.layer_id for layer in document.layers]
    assert layer_ids == ["a", "b", "c"]
    assert document.is_visible("b") is False
    assert document.active_layer_id == "c"
    assert document.selected_feature_ids("c") == (1,)

    window._redo()
    assert [layer.layer_id for layer in document.layers] == ["a", "c"]
    window.close()


def test_removed_active_layer_undo_restores_active_status() -> None:
    """撤销删除活动图层应恢复其活动图层状态。"""
    document: MapDocument = MapDocument()
    document.add_layer(_make_layer("a", "图层A"))
    document.set_active_layer("a")
    window: MainWindow = _make_window(document)

    window._remove_layer("a")
    assert document.active_layer_id is None
    window._undo()
    assert document.active_layer_id == "a"
    window.close()


def test_symbology_change_undo_redo_restores_configuration() -> None:
    """符号系统修改应支持撤销恢复旧配置、重做恢复新配置。"""
    layer: VectorLayer = _make_layer("a", "图层A")
    document: MapDocument = MapDocument()
    document.add_layer(layer)
    window: MainWindow = _make_window(document)
    before: VectorSymbology | None = layer.symbology
    assert before is not None

    updated: VectorSymbology = VectorSymbology(
        renderer_type=VectorRendererType.SIMPLE,
        base_symbol=replace(before.base_symbol, fill_color="#ff0000"),
        field_name=None,
    )
    assert updated != before
    window._apply_symbology("a", updated)
    assert window._application.snapshot().layers[0].layer.symbology == updated

    window._undo()
    assert window._application.snapshot().layers[0].layer.symbology == before
    window._redo()
    assert window._application.snapshot().layers[0].layer.symbology == updated
    window.close()


def test_unique_value_symbology_undo_returns_to_simple() -> None:
    """唯一值符号修改应支持撤销回退到原有单一符号。"""
    layer: VectorLayer = _make_layer("a", "图层A")
    document: MapDocument = MapDocument()
    document.add_layer(layer)
    window: MainWindow = _make_window(document)
    before: VectorSymbology | None = layer.symbology
    assert before is not None

    window._apply_unique_symbology("a", "name", "standard")
    assert (
        window._application.snapshot().layers[0].layer.symbology.renderer_type
        is VectorRendererType.UNIQUE
    )
    window._undo()
    assert (
        window._application.snapshot().layers[0].layer.symbology.renderer_type
        is VectorRendererType.SIMPLE
    )
    window.close()


def test_invalid_graduated_count_shows_numeric_sample_warning(monkeypatch) -> None:
    """分级数超过有效数值样本数时应弹出明确提示且不改变原符号。"""
    layer = VectorLayer.create(
        layer_id="graduated-warning",
        name="行政区边界",
        features=tuple(
            Feature(fid=index, geometry=Point(index, 0), attributes={"value": index})
            for index in range(7)
        ),
        crs=CRS_4549,
    )
    document: MapDocument = MapDocument()
    document.add_layer(layer)
    window: MainWindow = _make_window(document)
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )
    before = window._application.snapshot().layers[0].layer.symbology

    window._apply_graduated_symbology(
        "graduated-warning",
        "value",
        "gray",
        "equal_interval",
        8,
    )

    assert warnings
    assert "不能超过可用于分级的数值样本数（当前为 7）" in warnings[-1][1]
    assert window._application.snapshot().layers[0].layer.symbology == before
    window.close()


def test_category_visibility_undo_restores_category() -> None:
    """图层树类别显隐切换应支持撤销恢复。"""
    layer: VectorLayer = _make_layer("a", "图层A")
    document: MapDocument = MapDocument()
    document.add_layer(layer)
    window: MainWindow = _make_window(document)
    window._apply_unique_symbology("a", "name", "standard")
    symbology = window._application.snapshot().layers[0].layer.symbology
    assert isinstance(symbology, VectorSymbology)
    assert symbology.unique_classes[0].visible is True

    window._change_category_visibility("a", 0, False)
    assert (
        window._application.snapshot().layers[0].layer.symbology.unique_classes[
            0
        ].visible
        is False
    )
    window._undo()
    assert (
        window._application.snapshot().layers[0].layer.symbology.unique_classes[
            0
        ].visible
        is True
    )
    window.close()


def test_clear_selection_undo_restores_selection_and_skips_empty() -> None:
    """清除选择应可撤销恢复原选择；无选择时不应压入撤销记录。"""
    layer: VectorLayer = _make_layer("a", "图层A")
    document: MapDocument = MapDocument()
    document.add_layer(layer)
    window: MainWindow = _make_window(document)

    document.set_selection("a", (1, 2))
    window._clear_selection()
    assert document.selected_feature_ids("a") == ()
    window._undo()
    assert document.selected_feature_ids("a") == (1, 2)

    # 无选择时清除操作不应压入撤销记录。
    document.clear_selection()
    window._undo_stack.clear()
    window._clear_selection()
    assert len(window._undo_stack) == 0
    window.close()


def test_open_data_undo_redo_supports_cycles_with_new_layer_ids(monkeypatch) -> None:
    """打开数据撤销/重做应跟踪重新打开产生的新图层编号。"""
    document: MapDocument = MapDocument()
    window: MainWindow = _make_window(document)
    counter: list[int] = [0]

    def prepare_open_data(
        path: Path,
        layer_name: str | None = None,
        source_crs_override: CRS | None = None,
    ) -> VectorLayer:
        counter[0] += 1
        return _make_layer(f"loaded-{counter[0]}", "加载图层")

    monkeypatch.setattr(
        window,
        "_select_spatial_data_files",
        lambda: [str(Path("roads.geojson"))],
    )
    monkeypatch.setattr(window, "_choose_initial_display_crs", lambda _layer: (True, None))
    monkeypatch.setattr(window._application, "prepare_open_data", prepare_open_data)
    window._open_data()
    application = QApplication.instance()
    assert application is not None
    deadline = time.monotonic() + 3.0
    while window._open_data_progress_dialog is not None and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.005)

    assert len(document.layers) == 1
    first_id: str = document.layers[0].layer_id
    window._undo()
    assert len(document.layers) == 0
    window._redo()
    assert len(document.layers) == 1
    assert document.layers[0].layer_id != first_id
    # 重做后再撤销必须移除重做产生的图层，而不是已不存在的原始图层。
    window._undo()
    assert len(document.layers) == 0
    window.close()


def test_undo_failure_drops_record_and_does_not_crash() -> None:
    """撤销回调失败时应提示并丢弃记录，不能冒泡异常或反复失败。"""
    document: MapDocument = MapDocument()
    document.add_layer(_make_layer("a", "图层A"))
    window: MainWindow = _make_window(document)
    window._push_undo(
        "失效操作",
        undo_action=lambda: window._application.remove_layer("ghost"),
        redo_action=lambda: None,
    )

    window._undo()
    assert len(window._undo_stack) == 0
    assert len(window._redo_stack) == 0
    assert "撤销失败" in window.statusBar().currentMessage()
    window.close()


def test_empty_map_rejects_display_crs_change_without_affecting_undo() -> None:
    """删除最后图层后不能设置显示 CRS，且原有撤销操作仍可执行。"""
    document: MapDocument = MapDocument()
    document.add_layer(_make_layer("a", "图层A"))
    window: MainWindow = _make_window(document)

    window._remove_layer("a")
    with pytest.raises(ValueError, match="空地图"):
        window._application.set_display_crs(CRS.from_epsg(4326))
    window._undo()
    assert len(window._undo_stack) == 0
    assert len(document.layers) == 1
    window.close()


def test_new_project_clears_undo_history(monkeypatch) -> None:
    """新建工程应清空撤销与重做历史并禁用按钮。"""
    window: MainWindow = _make_window(MapDocument())
    monkeypatch.setattr(window, "_confirm_project_switch", lambda: True)
    window._push_undo("测试操作", undo_action=lambda: None, redo_action=lambda: None)

    window._new_project()
    assert len(window._undo_stack) == 0
    assert len(window._redo_stack) == 0
    assert window._ribbon._all_buttons["undo"].isEnabled() is False
    window.close()


class _FakeEditDialog:
    """替代属性表单的确定性假对话框，模拟用户确认。"""

    class DialogCode:
        Accepted = 1
        Rejected = 0

    def __init__(
        self,
        attributes: dict[str, object],
        feature_label: str,
        parent: object = None,
    ) -> None:
        pass

    def exec(self) -> int:
        return self.DialogCode.Accepted

    def attributes(self) -> dict[str, object]:
        return {"名称": "新点"}


class _FakeTargetDialog:
    """替代目标图层选择对话框，返回第一个候选图层。"""

    class DialogCode:
        Accepted = 1
        Rejected = 0

    def __init__(
        self,
        options: tuple[object, ...],
        geometry_label: str,
        default_layer_id: str | None = None,
        parent: object = None,
    ) -> None:
        self._options: list[object] = list(options)

    def exec(self) -> int:
        return self.DialogCode.Accepted

    def selected_layer_id(self) -> str | None:
        if self._options:
            return self._options[0].layer_id  # type: ignore[attr-defined]
        return None


def test_digitize_append_undo_redo_restores_feature_set(
    tmp_path: Path, monkeypatch
) -> None:
    """撤销应恢复追加前的要素集合，重做应重新追加。"""
    source: Path = tmp_path / "points.geojson"
    layer: VectorLayer = VectorLayer.create(
        layer_id="l1",
        name="监测点",
        features=(Feature(fid=1, geometry=Point(0, 0), attributes={"名称": "甲"}),),
        crs=CRS_4549,
        source_path=source,
    )
    document: MapDocument = MapDocument()
    document.add_layer(layer)
    document.set_active_layer(layer.layer_id)
    window: MainWindow = _make_window(document)
    monkeypatch.setattr(
        "app.presentation.main_window.EditFeatureDialog", _FakeEditDialog
    )
    monkeypatch.setattr(
        "app.presentation.main_window.TargetLayerDialog", _FakeTargetDialog
    )

    window._start_digitize("point", "点")
    window._on_feature_digitized(Point(10, 10))
    assert len(window._application.snapshot().layers[0].layer.features) == 2

    window._undo()
    assert len(window._application.snapshot().layers[0].layer.features) == 1

    window._redo()
    assert len(window._application.snapshot().layers[0].layer.features) == 2
    window.close()


def _make_batch_delete_source(tmp_path: Path) -> VectorLayer:
    """创建带三个点要素的真实 GeoPackage 图层用于批量删除测试。"""
    source: Path = tmp_path / "batch.gpkg"
    layer: VectorLayer = VectorLayer.create(
        layer_id="a",
        name="批量删除",
        features=(
            Feature(fid=1, geometry=Point(0, 0), attributes={"名称": "甲"}),
            Feature(fid=2, geometry=Point(1, 1), attributes={"名称": "乙"}),
            Feature(fid=3, geometry=Point(2, 2), attributes={"名称": "丙"}),
        ),
        crs=CRS_4549,
        source_path=source,
        source_layer_name="批量删除",
    )
    GeoPandasVectorWriter().write(layer, source, layer_name="批量删除")
    return layer


def test_batch_delete_writes_back_once_per_layer_and_supports_undo(
    tmp_path: Path, monkeypatch
) -> None:
    """同图层批量删除应只整层写回一次，不逐要素写盘，且可撤销/重做。"""
    layer: VectorLayer = _make_batch_delete_source(tmp_path)
    document: MapDocument = MapDocument()
    document.add_layer(layer)
    document.set_active_layer("a")
    document.set_selection("a", (1, 2))
    window: MainWindow = _make_window(document)
    application: GisApplication = window._application
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args: QMessageBox.StandardButton.Yes),
    )

    replace_calls: list[tuple[str, int]] = []
    patch_calls: list[tuple[str, int]] = []
    delete_calls: list[object] = []
    original_replace = application.replace_layer_features
    original_apply = application.apply_feature_edit

    def _counting_replace(layer_id: str, features: tuple) -> object:
        replace_calls.append((layer_id, len(features)))
        return original_replace(layer_id, features)

    def _counting_apply(layer_id: str, revision: int, patch) -> object:
        patch_calls.append((layer_id, len(patch.deletions)))
        return original_apply(layer_id, revision, patch)

    application.replace_layer_features = _counting_replace  # type: ignore[method-assign]
    application.apply_feature_edit = _counting_apply  # type: ignore[method-assign]
    application.delete_feature = (  # type: ignore[method-assign]
        lambda layer_id, fid: delete_calls.append(fid)
    )

    window._delete_selected_features()

    assert patch_calls == [("a", 2)]
    assert replace_calls == []
    assert delete_calls == []
    remaining = window._application.snapshot().layers[0].layer.features
    assert [f.attributes["名称"] for f in remaining] == ["丙"]

    window._undo()
    restored = window._application.snapshot().layers[0].layer.features
    assert [f.attributes["名称"] for f in restored] == ["甲", "乙", "丙"]

    window._redo()
    after_redo = window._application.snapshot().layers[0].layer.features
    assert [f.attributes["名称"] for f in after_redo] == ["丙"]
    assert replace_calls == [("a", 3), ("a", 1)]
    window.close()


def test_batch_delete_rejects_selecting_all_features(
    tmp_path: Path, monkeypatch
) -> None:
    """选中图层全部要素时批量删除应在入口被拒绝，不产生任何写回。"""
    layer: VectorLayer = _make_batch_delete_source(tmp_path)
    document: MapDocument = MapDocument()
    document.add_layer(layer)
    document.set_active_layer("a")
    document.set_selection("a", (1, 2, 3))
    window: MainWindow = _make_window(document)
    application: GisApplication = window._application
    messages: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda parent, title, text: messages.append(text)),
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *args: QMessageBox.StandardButton.Yes),
    )
    replace_calls: list[str] = []
    original_replace = application.replace_layer_features

    def _counting_replace(layer_id: str, features: tuple) -> object:
        replace_calls.append(layer_id)
        return original_replace(layer_id, features)

    application.replace_layer_features = _counting_replace  # type: ignore[method-assign]

    window._delete_selected_features()

    assert any("暂不支持删除图层中的最后一个要素" in message for message in messages)
    assert replace_calls == []
    features = window._application.snapshot().layers[0].layer.features
    assert len(features) == 3
    window.close()
