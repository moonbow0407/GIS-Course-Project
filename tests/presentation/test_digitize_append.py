"""数字化追加要素到目标图层的界面回归测试。"""

import os

# 必须在导入 Qt 前启用无界面平台，测试才能稳定复现原生控件事件。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import numpy as np
from affine import Affine
from pyproj import CRS
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from shapely.geometry import LineString, Point, Polygon

import app.presentation.main_window as main_window_module
from app.application.display_projection_service import DisplayProjectionService
from app.application.gis_application import GisApplication
from app.domain.feature import Feature
from app.domain.map_document import MapDocument
from app.domain.raster_layer import RasterLayer
from app.domain.vector_layer import VectorLayer
from app.infrastructure.file_io.auto_reader import AutoDataReader
from app.infrastructure.file_io.auto_writer import AutoDataWriter
from app.infrastructure.projection.pyproj_coordinate_transformer import (
    PyprojCoordinateTransformer,
)
from app.presentation.main_window import MainWindow

CRS_4549: CRS = CRS.from_epsg(4549)


class FakeEditDialog:
    """替代 EditFeatureDialog 的确定性假对话框。"""

    class DialogCode:
        Accepted = 1
        Rejected = 0

    # 类级开关：测试可置 False 模拟用户取消。
    accepted: bool = True

    def __init__(
        self,
        attributes: dict[str, object],
        feature_label: str,
        parent: object = None,
    ) -> None:
        self.received_attributes: dict[str, object] = dict(attributes)
        self.received_label: str = feature_label

    def exec(self) -> int:
        return self.DialogCode.Accepted if self.accepted else self.DialogCode.Rejected

    def attributes(self) -> dict[str, object]:
        return {"名称": "新点"}


class FakeTargetLayerDialog:
    """替代 TargetLayerDialog 的确定性假对话框。"""

    class DialogCode:
        Accepted = 1
        Rejected = 0

    # 类级开关：置 False 模拟用户取消。
    accepted: bool = True

    def __init__(
        self,
        options: tuple[object, ...],
        geometry_label: str,
        default_layer_id: str | None = None,
        parent: object = None,
    ) -> None:
        self.received_options: list[object] = list(options)
        self.received_default: str | None = default_layer_id

    def exec(self) -> int:
        return self.DialogCode.Accepted if self.accepted else self.DialogCode.Rejected

    def selected_layer_id(self) -> str | None:
        if self.received_options:
            return self.received_options[0].layer_id  # type: ignore[attr-defined]
        return None


def _make_point_layer(layer_id: str, source_path: Path | None) -> VectorLayer:
    """构造带单个点要素和可选数据文件路径的测试图层。"""
    return VectorLayer.create(
        layer_id=layer_id,
        name="监测点",
        features=(
            Feature(fid=1, geometry=Point(0, 0), attributes={"名称": "甲"}),
        ),
        crs=CRS_4549,
        source_path=source_path,
    )


def _make_line_layer(layer_id: str, source_path: Path | None) -> VectorLayer:
    """构造带单个线要素和可选数据文件路径的测试图层。"""
    return VectorLayer.create(
        layer_id=layer_id,
        name="巡查路线",
        features=(
            Feature(
                fid=1,
                geometry=LineString([(0, 0), (1, 1)]),
                attributes={},
            ),
        ),
        crs=CRS_4549,
        source_path=source_path,
    )


def _make_raster_layer(layer_id: str) -> RasterLayer:
    """构造带显示影像的测试栅格图层。"""
    return RasterLayer.create(
        layer_id=layer_id,
        name="影像",
        raster_data=np.ones((1, 2, 2), dtype=np.uint8),
        image_data=np.full((2, 2, 4), 255, dtype=np.uint8),
        valid_mask=np.ones((2, 2), dtype=np.bool_),
        transform=Affine(1, 0, 0, 0, -1, 2),
        crs=CRS_4549,
        bounds=(0, 0, 2, 2),
    )


def _make_polygon_layer(layer_id: str, source_path: Path | None) -> VectorLayer:
    """构造带单个面要素和可选数据文件路径的测试图层。"""
    return VectorLayer.create(
        layer_id=layer_id,
        name="管理分区",
        features=(
            Feature(
                fid=1,
                geometry=Polygon([(0, 0), (0, 4), (4, 4), (4, 0)]),
                attributes={},
            ),
        ),
        crs=CRS_4549,
        source_path=source_path,
    )


def _make_window(document: MapDocument) -> MainWindow:
    """创建主窗口并替换为使用指定文档的应用服务。"""
    QApplication.instance() or QApplication([])
    window: MainWindow = MainWindow()
    window._application = GisApplication(
        AutoDataReader(),
        AutoDataWriter(),
        document,
        display_projection_service=DisplayProjectionService(
            PyprojCoordinateTransformer()
        ),
    )
    window._refresh_workspace()
    return window


def _collect_messages(monkeypatch) -> list[str]:
    """替换消息弹窗为记录列表，返回收集到的提示文本。"""
    messages: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(lambda parent, title, text: messages.append(text)),
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda parent, title, text: messages.append(text)),
    )
    return messages


def _patch_dialogs(monkeypatch) -> None:
    """用确定性假对话框替换新增要素相关的两个对话框。"""
    monkeypatch.setattr(
        main_window_module, "EditFeatureDialog", FakeEditDialog
    )
    monkeypatch.setattr(
        main_window_module, "TargetLayerDialog", FakeTargetLayerDialog
    )


def test_digitized_feature_appends_to_selected_layer_with_attributes(
    tmp_path: Path, monkeypatch
) -> None:
    """数字化完成后应把要素追加到弹窗选中的图层并带上表单属性。"""
    source: Path = tmp_path / "data" / "points.geojson"
    source.parent.mkdir()
    layer: VectorLayer = _make_point_layer("l1", source)
    document: MapDocument = MapDocument()
    document.add_layer(layer)
    document.set_active_layer(layer.layer_id)
    window: MainWindow = _make_window(document)
    _patch_dialogs(monkeypatch)

    window._start_digitize("point", "点")
    window._on_feature_digitized(Point(10, 10))

    features = window._application.snapshot().layers[0].layer.features
    assert len(features) == 2
    assert features[1].attributes == {"名称": "新点"}
    assert features[1].fid == 2
    assert "已向图层「监测点」添加点要素" in window._ready_label.text()
    window.close()


def test_digitized_feature_dialog_receives_layer_fields(
    tmp_path: Path, monkeypatch
) -> None:
    """属性表单应收到目标图层已有要素的字段结构。"""
    source: Path = tmp_path / "data" / "points.geojson"
    source.parent.mkdir()
    layer: VectorLayer = _make_point_layer("l1", source)
    document: MapDocument = MapDocument()
    document.add_layer(layer)
    document.set_active_layer(layer.layer_id)
    window: MainWindow = _make_window(document)
    _patch_dialogs(monkeypatch)

    class CapturingDialog(FakeEditDialog):
        instances: list[FakeEditDialog] = []

        def __init__(
            self,
            attributes: dict[str, object],
            feature_label: str,
            parent: object = None,
        ) -> None:
            super().__init__(attributes, feature_label, parent)
            CapturingDialog.instances.append(self)

    monkeypatch.setattr(
        main_window_module, "EditFeatureDialog", CapturingDialog
    )

    window._start_digitize("point", "点")
    window._on_feature_digitized(Point(10, 10))

    assert len(CapturingDialog.instances) == 1
    assert CapturingDialog.instances[0].received_attributes == {"名称": None}
    window.close()


def test_digitize_cancel_keeps_layer_unchanged(tmp_path: Path, monkeypatch) -> None:
    """取消属性表单不应产生任何图层改动。"""
    source: Path = tmp_path / "data" / "points.geojson"
    source.parent.mkdir()
    layer: VectorLayer = _make_point_layer("l1", source)
    document: MapDocument = MapDocument()
    document.add_layer(layer)
    document.set_active_layer(layer.layer_id)
    window: MainWindow = _make_window(document)
    _patch_dialogs(monkeypatch)
    FakeEditDialog.accepted = False

    window._start_digitize("point", "点")
    window._on_feature_digitized(Point(10, 10))

    assert len(window._application.snapshot().layers[0].layer.features) == 1
    FakeEditDialog.accepted = True
    window.close()


def test_digitize_appends_to_locked_target_when_canvas_click_clears_active(
    tmp_path: Path, monkeypatch
) -> None:
    """数字化启动后画布点击清除了活动图层，追加仍应回到锁定目标。

    回归场景：绘制时每次画布点击都会清除图层面板选中，进而级联清除
    活动图层；追加目标必须在启动数字化时锁定，不能依赖当时的活动图层。
    """
    source: Path = tmp_path / "data" / "points.geojson"
    source.parent.mkdir()
    layer: VectorLayer = _make_point_layer("l1", source)
    document: MapDocument = MapDocument()
    document.add_layer(layer)
    document.set_active_layer(layer.layer_id)
    window: MainWindow = _make_window(document)
    _patch_dialogs(monkeypatch)

    window._start_digitize("point", "点")
    # 模拟绘制过程中点击画布：图层面板选中被清除，级联清除活动图层。
    window._on_canvas_clicked()
    assert window._application.snapshot().active_layer_id is None

    window._on_feature_digitized(Point(10, 10))

    features = window._application.snapshot().layers[0].layer.features
    assert len(features) == 2
    assert "已向图层「监测点」添加点要素" in window._ready_label.text()
    window.close()


def test_start_digitize_requires_map_crs(monkeypatch) -> None:
    """空地图没有显示 CRS 时应先提示用户打开已定义 CRS 的图层。"""
    document: MapDocument = MapDocument()
    window: MainWindow = _make_window(document)
    messages: list[str] = _collect_messages(monkeypatch)

    window._start_digitize("point", "点")

    assert any("请先打开一个具有坐标系的图层" in message for message in messages)
    assert window._map_canvas._digitize_mode == "none"
    window.close()


def test_start_digitize_raster_layer_is_not_candidate(monkeypatch) -> None:
    """活动图层为栅格时不会进入候选列表，应提示没有可用图层。"""
    raster: RasterLayer = _make_raster_layer("raster")
    document: MapDocument = MapDocument()
    document.add_layer(raster)
    window: MainWindow = _make_window(document)
    messages: list[str] = _collect_messages(monkeypatch)

    window._start_digitize("point", "点")

    assert any("没有可用于添加点要素的图层" in message for message in messages)
    assert window._map_canvas._digitize_mode == "none"
    window.close()


def test_start_digitize_filters_candidates_by_geometry(tmp_path: Path, monkeypatch) -> None:
    """线图层不应出现在点要素的候选列表中。"""
    source: Path = tmp_path / "routes.geojson"
    layer: VectorLayer = _make_line_layer("l1", source)
    document: MapDocument = MapDocument()
    document.add_layer(layer)
    document.set_active_layer(layer.layer_id)
    window: MainWindow = _make_window(document)
    messages: list[str] = _collect_messages(monkeypatch)

    window._start_digitize("point", "点")

    assert any("没有可用于添加点要素的图层" in message for message in messages)
    assert window._map_canvas._digitize_mode == "none"
    window.close()


def test_start_digitize_filters_candidates_by_source_format(
    tmp_path: Path, monkeypatch
) -> None:
    """GeoPackage 等不可写回格式的图层不应进入候选列表。"""
    source: Path = tmp_path / "zones.gpkg"
    layer: VectorLayer = _make_point_layer("l1", source)
    document: MapDocument = MapDocument()
    document.add_layer(layer)
    document.set_active_layer(layer.layer_id)
    window: MainWindow = _make_window(document)
    messages: list[str] = _collect_messages(monkeypatch)

    window._start_digitize("point", "点")

    assert any("没有可用于添加点要素的图层" in message for message in messages)
    assert window._map_canvas._digitize_mode == "none"
    window.close()


def test_start_digitize_passes_only_eligible_options(
    tmp_path: Path, monkeypatch
) -> None:
    """候选列表应只包含几何类型匹配且可写回的矢量图层。"""
    point_source: Path = tmp_path / "points.geojson"
    line_source: Path = tmp_path / "routes.geojson"
    gpkg_source: Path = tmp_path / "zones.gpkg"
    document: MapDocument = MapDocument()
    document.add_layer(_make_point_layer("points", point_source))
    document.add_layer(_make_line_layer("routes", line_source))
    document.add_layer(_make_point_layer("gpkg", gpkg_source))
    document.add_layer(_make_raster_layer("raster"))
    document.set_active_layer("points")
    window: MainWindow = _make_window(document)

    class CapturingTargetDialog(FakeTargetLayerDialog):
        instances: list[FakeTargetLayerDialog] = []

        def __init__(
            self,
            options: tuple[object, ...],
            geometry_label: str,
            default_layer_id: str | None = None,
            parent: object = None,
        ) -> None:
            super().__init__(options, geometry_label, default_layer_id, parent)
            CapturingTargetDialog.instances.append(self)

    monkeypatch.setattr(
        main_window_module, "TargetLayerDialog", CapturingTargetDialog
    )
    _collect_messages(monkeypatch)

    window._start_digitize("point", "点")

    assert len(CapturingTargetDialog.instances) == 1
    captured_dialog = CapturingTargetDialog.instances[0]
    assert [option.layer_id for option in captured_dialog.received_options] == [
        "points"
    ]
    assert captured_dialog.received_default == "points"
    assert window._map_canvas._digitize_mode == "point"
    window.close()


def test_start_digitize_cancel_does_not_activate(tmp_path: Path, monkeypatch) -> None:
    """取消目标图层选择对话框不应激活数字化工具。"""
    source: Path = tmp_path / "points.geojson"
    layer: VectorLayer = _make_point_layer("l1", source)
    document: MapDocument = MapDocument()
    document.add_layer(layer)
    document.set_active_layer(layer.layer_id)
    window: MainWindow = _make_window(document)
    monkeypatch.setattr(
        main_window_module, "TargetLayerDialog", FakeTargetLayerDialog
    )
    FakeTargetLayerDialog.accepted = False

    window._start_digitize("point", "点")

    assert window._map_canvas._digitize_mode == "none"
    assert window._digitize_target_layer_id is None
    FakeTargetLayerDialog.accepted = True
    window.close()


def test_digitize_line_finishes_with_double_click(
    tmp_path: Path, monkeypatch
) -> None:
    """线要素数字化时双击应完成绘制并追加要素，不产生重复顶点。"""
    source: Path = tmp_path / "data" / "routes.geojson"
    source.parent.mkdir()
    layer: VectorLayer = _make_line_layer("l1", source)
    document: MapDocument = MapDocument()
    document.add_layer(layer)
    document.set_active_layer(layer.layer_id)
    window: MainWindow = _make_window(document)
    _patch_dialogs(monkeypatch)
    qt: QApplication = QApplication.instance() or QApplication([])
    window.show()
    qt.processEvents()

    window._start_digitize("line", "线")
    canvas = window._map_canvas
    rect = canvas.viewport().rect()
    first: QPoint = rect.center()
    second: QPoint = rect.center() + QPoint(50, 0)
    QTest.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, first)
    QTest.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, second)
    # 真实双击 = 第一次按下（放置顶点）+ 双击事件（完成）；
    # QTest.mouseDClick 只发送双击事件，需要先补一次按下模拟真实序列。
    QTest.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, second)
    QTest.mouseDClick(canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, second)

    features = window._application.snapshot().layers[0].layer.features
    assert len(features) == 2
    added = features[1].geometry
    # 三个顶点各出现一次：两次单击 + 双击位置的第一次按下。
    assert len(added.coords) == 3
    assert "已向图层「巡查路线」添加线要素" in window._ready_label.text()
    window.close()


def test_digitize_polygon_finishes_with_double_click(
    tmp_path: Path, monkeypatch
) -> None:
    """面要素数字化时双击应完成绘制并追加要素。"""
    source: Path = tmp_path / "data" / "zones.geojson"
    source.parent.mkdir()
    layer: VectorLayer = _make_polygon_layer("l1", source)
    document: MapDocument = MapDocument()
    document.add_layer(layer)
    document.set_active_layer(layer.layer_id)
    window: MainWindow = _make_window(document)
    _patch_dialogs(monkeypatch)
    qt: QApplication = QApplication.instance() or QApplication([])
    window.show()
    qt.processEvents()

    window._start_digitize("polygon", "面")
    canvas = window._map_canvas
    rect = canvas.viewport().rect()
    first: QPoint = rect.center()
    second: QPoint = rect.center() + QPoint(50, 0)
    third: QPoint = rect.center() + QPoint(50, 50)
    QTest.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, first)
    QTest.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, second)
    # 双击位置先放置顶点，再触发完成事件（对应真实双击序列）。
    QTest.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, third)
    QTest.mouseDClick(canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, third)

    features = window._application.snapshot().layers[0].layer.features
    assert len(features) == 2
    assert features[1].geometry.geom_type == "Polygon"
    assert "已向图层「管理分区」添加面要素" in window._ready_label.text()
    window.close()


def _make_raster_layer_epsg4326(layer_id: str) -> RasterLayer:
    """构造 WGS84 栅格图层，用于先建立与点图层不同的显示坐标系。"""
    return RasterLayer.create(
        layer_id=layer_id,
        name="底图影像",
        raster_data=np.ones((1, 2, 2), dtype=np.uint8),
        image_data=np.full((2, 2, 4), 255, dtype=np.uint8),
        valid_mask=np.ones((2, 2), dtype=np.bool_),
        transform=Affine(1, 0, 0, 0, -1, 2),
        crs=CRS.from_epsg(4326),
        bounds=(0, 0, 2, 2),
    )


def test_add_point_reports_crs_mismatch_reason(
    tmp_path: Path, monkeypatch
) -> None:
    """仅有的点图层因坐标系不一致被排除时，应说明原因和做法。"""
    messages: list[str] = _collect_messages(monkeypatch)
    document: MapDocument = MapDocument()
    document.add_layer(_make_raster_layer_epsg4326("base"))
    point_layer: VectorLayer = _make_point_layer(
        "points", tmp_path / "points.shp"
    )
    document.add_layer(point_layer)
    window: MainWindow = _make_window(document)

    window._start_digitize("point", "点")

    assert len(messages) == 1
    assert "坐标系" in messages[0]
    assert "监测点" in messages[0]
    assert "重投影" in messages[0]
    assert "Shapefile 或 GeoJSON" not in messages[0]
    window.close()


def test_add_point_without_matching_layers_keeps_generic_hint(
    monkeypatch,
) -> None:
    """没有任何几何类型匹配的图层时，仍提示打开矢量图层的通用做法。"""
    messages: list[str] = _collect_messages(monkeypatch)
    document: MapDocument = MapDocument()
    document.add_layer(_make_line_layer("routes", Path("routes.shp")))
    window: MainWindow = _make_window(document)

    window._start_digitize("point", "点")

    assert len(messages) == 1
    assert "Shapefile 或 GeoJSON" in messages[0]
    assert "坐标系" not in messages[0]
    window.close()


def test_specified_digitize_target_distinguishes_crs_and_geometry(
    tmp_path: Path, monkeypatch
) -> None:
    """属性表指定目标图层时，坐标系问题与几何/格式问题应分开提示。"""
    messages: list[str] = _collect_messages(monkeypatch)

    # 场景一：目标点图层坐标系与显示坐标系不一致。
    document: MapDocument = MapDocument()
    document.add_layer(_make_raster_layer_epsg4326("base"))
    point_layer: VectorLayer = _make_point_layer(
        "points", tmp_path / "points.shp"
    )
    document.add_layer(point_layer)
    window: MainWindow = _make_window(document)
    window._start_digitize("point", "点", target_layer_id="points")
    assert "坐标系" in messages[-1]
    assert "监测点" in messages[-1]
    window.close()

    # 场景二：目标图层几何类型不匹配（线图层承载点要素）。
    document_two: MapDocument = MapDocument()
    line_layer: VectorLayer = _make_line_layer("routes", Path("routes.shp"))
    document_two.add_layer(line_layer)
    window_two: MainWindow = _make_window(document_two)
    window_two._start_digitize("point", "点", target_layer_id="routes")
    assert "图层不支持新增点要素" in messages[-1]
    assert "坐标系" not in messages[-1]
    window_two.close()
