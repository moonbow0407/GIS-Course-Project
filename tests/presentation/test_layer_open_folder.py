"""图层右键"打开文件夹"菜单与打开行为测试。"""

import os
from pathlib import Path
from types import SimpleNamespace

# 必须在导入 Qt 前启用无界面平台，测试才能在没有显示器的环境运行。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from affine import Affine
from pyproj import CRS
from PySide6.QtCore import QPoint, QUrl
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QTreeWidgetItem
from shapely.geometry import Point

from app.application.gis_application import GisApplication
from app.application.results import LayerSnapshot, WorkspaceSnapshot
from app.domain.feature import Feature
from app.domain.map_document import MapDocument
from app.domain.raster_layer import RasterLayer
from app.domain.vector_layer import VectorLayer
from app.infrastructure.file_io.auto_reader import AutoDataReader
from app.infrastructure.file_io.auto_writer import AutoDataWriter
from app.presentation.main_window import MainWindow
from app.presentation.widgets.layer_panel import LayerPanel
from app.presentation.widgets.layer_properties_dialog import LayerPropertiesDialog


def _make_layer(layer_id: str, name: str, source_path: Path | None) -> VectorLayer:
    """构造带单个点要素和可选数据文件路径的测试矢量图层。"""
    return VectorLayer.create(
        layer_id=layer_id,
        name=name,
        features=(Feature(fid=1, geometry=Point(0, 0), attributes={}),),
        crs=CRS.from_epsg(4549),
        source_path=source_path,
    )


def _snapshot(layer: VectorLayer) -> WorkspaceSnapshot:
    """构造只含一个图层的完整工作区快照。"""
    return WorkspaceSnapshot(
        layers=(LayerSnapshot(layer=layer, visible=True, selected_feature_ids=()),),
        active_layer_id=None,
        display_crs=layer.crs,
    )


def test_context_menu_contains_open_folder_and_emits_layer_id(monkeypatch) -> None:
    """右键菜单应包含"打开文件夹"项，选择后发出携带图层编号的信号。"""
    QApplication.instance() or QApplication([])
    panel: LayerPanel = LayerPanel()
    panel.apply_snapshot(
        _snapshot(_make_layer("a", "图层A", Path("D:/data/roads.geojson")))
    )
    panel.resize(400, 300)
    panel.show()
    QApplication.processEvents()
    item: QTreeWidgetItem | None = panel._tree.topLevelItem(0)
    assert item is not None

    menu_texts: list[str] = []
    emitted: list[str] = []
    panel.layer_folder_requested.connect(emitted.append)

    def execute(menu: QMenu, position: QPoint) -> QAction | None:
        menu_texts.extend(action.text() for action in menu.actions())
        return next(action for action in menu.actions() if action.text() == "打开文件夹")

    monkeypatch.setattr(panel, "_execute_context_menu", execute)
    rect = panel._tree.visualItemRect(item)
    panel._on_context_menu_requested(rect.topLeft() + QPoint(5, 5))

    assert menu_texts == [
        "缩放至图层",
        "图层属性",
        "符号系统",
        "打开属性表",
        "打开文件夹",
        "删除图层",
    ]
    assert emitted == ["a"]
    panel.close()


def _make_window(layer: VectorLayer) -> MainWindow:
    """创建主窗口并替换为使用指定图层的应用服务。"""
    QApplication.instance() or QApplication([])
    document: MapDocument = MapDocument()
    document.add_layer(layer)
    window: MainWindow = MainWindow()
    window._application = GisApplication(AutoDataReader(), AutoDataWriter(), document)
    window._refresh_workspace()
    return window


def test_open_layer_folder_opens_parent_directory(monkeypatch) -> None:
    """有数据文件的图层应打开其所在父目录。"""
    source: Path = Path("D:/data/roads.geojson")
    window: MainWindow = _make_window(_make_layer("a", "图层A", source))
    opened: list[QUrl] = []
    monkeypatch.setattr(
        "app.presentation.main_window.QDesktopServices",
        SimpleNamespace(openUrl=lambda url: opened.append(url) or True),
    )

    window._open_layer_folder("a")

    assert opened == [QUrl.fromLocalFile(str(source.parent))]
    window.close()


def test_open_layer_folder_reports_missing_local_file(monkeypatch) -> None:
    """没有本地数据文件的图层应提示而不是打开文件夹。"""
    window: MainWindow = _make_window(_make_layer("a", "图层A", None))
    opened: list[QUrl] = []
    monkeypatch.setattr(
        "app.presentation.main_window.QDesktopServices",
        SimpleNamespace(openUrl=lambda url: opened.append(url) or True),
    )

    window._open_layer_folder("a")

    assert opened == []
    assert "没有本地数据文件" in window.statusBar().currentMessage()
    window.close()


def test_layer_folder_requested_signal_is_wired(monkeypatch) -> None:
    """图层面板打开文件夹请求应连接到主窗口处理。"""
    source: Path = Path("D:/data/roads.geojson")
    window: MainWindow = _make_window(_make_layer("a", "图层A", source))
    opened: list[QUrl] = []
    monkeypatch.setattr(
        "app.presentation.main_window.QDesktopServices",
        SimpleNamespace(openUrl=lambda url: opened.append(url) or True),
    )

    window._layer_panel.layer_folder_requested.emit("a")

    assert opened == [QUrl.fromLocalFile(str(source.parent))]
    window.close()


def test_vector_layer_properties_show_crs_source_and_associations() -> None:
    """矢量属性窗口应展示名称、CRS、源文件、容器子图层和数据库关联。"""
    QApplication.instance() or QApplication([])
    layer: VectorLayer = VectorLayer.create(
        layer_id="roads",
        name="道路",
        features=(
            Feature(fid=1, geometry=Point(117.2, 31.8), attributes={"name": "长江路"}),
        ),
        crs=CRS.from_epsg(4326),
        source_path=Path("D:/data/base.gpkg"),
        source_layer_name="roads",
    )
    dialog: LayerPropertiesDialog = LayerPropertiesDialog(
        LayerSnapshot(layer=layer, visible=True, selected_feature_ids=(1,)),
        CRS.from_epsg(4326),
    )

    assert dialog.property_value("name") == "道路"
    assert dialog.property_value("crs") == "EPSG:4326 · WGS 84"
    assert dialog.property_value("crs_relation") == "与地图显示坐标系一致"
    assert dialog.property_value("source_type") == "本地文件"
    assert dialog.property_value("source_path") == str(Path("D:/data/base.gpkg"))
    assert dialog.property_value("source_layer_name") == "roads"
    assert dialog.property_value("geometry_type") == "点"
    assert dialog.property_value("feature_count") == "1"
    dialog.close()

    database_layer: VectorLayer = VectorLayer.create(
        layer_id="database-roads",
        name="数据库道路",
        features=layer.features,
        crs=layer.crs,
        database_layer_id=27,
    )
    database_dialog: LayerPropertiesDialog = LayerPropertiesDialog(
        LayerSnapshot(layer=database_layer, visible=True, selected_feature_ids=()),
        CRS.from_epsg(4326),
    )
    assert database_dialog.property_value("source_type") == "PostgreSQL / PostGIS 数据库"
    assert database_dialog.property_value("source_path") == "未关联本地文件"
    assert database_dialog.property_value("database_layer_id") == "27"
    database_dialog.close()


def test_raster_layer_properties_show_raster_metadata() -> None:
    """栅格属性窗口应展示波段、尺寸、像元大小和 NoData。"""
    QApplication.instance() or QApplication([])
    layer: RasterLayer = RasterLayer.create(
        layer_id="dem",
        name="高程",
        raster_data=np.ones((2, 3, 4), dtype=np.float32),
        image_data=np.full((3, 4, 4), 255, dtype=np.uint8),
        valid_mask=np.ones((3, 4), dtype=bool),
        transform=Affine(10.0, 0.0, 0.0, 0.0, -10.0, 30.0),
        crs=CRS.from_epsg(4549),
        bounds=(0.0, 0.0, 40.0, 30.0),
        nodata=-9999,
        source_path=Path("D:/data/dem.tif"),
    )
    dialog: LayerPropertiesDialog = LayerPropertiesDialog(
        LayerSnapshot(layer=layer, visible=False, selected_feature_ids=()),
        CRS.from_epsg(4549),
    )

    assert dialog.property_value("layer_type") == "栅格图层"
    assert dialog.property_value("band_count") == "2"
    assert dialog.property_value("raster_size") == "4 × 3 像元"
    assert dialog.property_value("pixel_size") == "10 × 10"
    assert dialog.property_value("nodata") == "-9999"
    dialog.close()


def test_layer_properties_requested_signal_is_wired(monkeypatch) -> None:
    """图层面板属性请求应由主窗口打开对应图层的属性窗口。"""
    layer: VectorLayer = _make_layer("a", "图层A", Path("D:/data/roads.geojson"))
    window: MainWindow = _make_window(layer)
    opened_names: list[str] = []

    def record_dialog(dialog: LayerPropertiesDialog) -> QDialog.DialogCode:
        opened_names.append(dialog.property_value("name") or "")
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(LayerPropertiesDialog, "exec", record_dialog)
    window._layer_panel.layer_properties_requested.emit("a")

    assert opened_names == ["图层A"]
    window.close()
