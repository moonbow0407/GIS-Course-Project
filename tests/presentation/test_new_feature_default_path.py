"""新增要素默认输出路径回归测试。"""

import os

# 必须在导入 Qt 前启用无界面平台，测试才能稳定复现原生控件事件。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from pyproj import CRS
from PySide6.QtWidgets import QApplication
from shapely.geometry import Point

from app.application.gis_application import GisApplication
from app.domain.feature import Feature
from app.domain.map_document import MapDocument
from app.domain.vector_layer import VectorLayer
from app.infrastructure.file_io.auto_reader import AutoDataReader
from app.infrastructure.file_io.auto_writer import AutoDataWriter
from app.presentation.main_window import MainWindow


def _make_layer(layer_id: str, name: str, source_path: Path | None) -> VectorLayer:
    """构造带单个点要素和可选数据文件路径的测试矢量图层。"""
    return VectorLayer.create(
        layer_id=layer_id,
        name=name,
        features=(Feature(fid=1, geometry=Point(0, 0), attributes={}),),
        crs=CRS.from_epsg(4549),
        source_path=source_path,
    )


def _window_with_layer(layer: VectorLayer) -> MainWindow:
    """构造以给定图层为活动图层的空主窗口。"""
    document: MapDocument = MapDocument()
    document.add_layer(layer)
    document.set_active_layer(layer.layer_id)
    window: MainWindow = MainWindow()
    window._application = GisApplication(AutoDataReader(), AutoDataWriter(), document)
    return window


def test_default_path_uses_active_layer_source_directory(tmp_path: Path) -> None:
    """新增要素默认保存到活动图层源文件所在目录。"""
    _ = QApplication.instance() or QApplication([])
    source: Path = tmp_path / "data" / "roads.shp"
    window: MainWindow = _window_with_layer(_make_layer("l1", "道路", source))
    path: Path = window._default_feature_output_path(
        "新建点_20260804", window._application.snapshot()
    )
    assert path == source.parent / "新建点_20260804.geojson"
    window.close()


def test_default_path_falls_back_to_project_directory(tmp_path: Path) -> None:
    """活动图层没有数据文件时默认保存到工程文件所在目录。"""
    _ = QApplication.instance() or QApplication([])
    window: MainWindow = _window_with_layer(_make_layer("l1", "内存图层", None))
    project: Path = tmp_path / "工程" / "demo.gisproj"
    window._application._project_path = project
    path: Path = window._default_feature_output_path(
        "新建点_20260804", window._application.snapshot()
    )
    assert path == project.parent / "新建点_20260804.geojson"
    window.close()


def test_default_path_falls_back_to_user_home() -> None:
    """活动图层和工程都不可用时回退到用户主目录。"""
    _ = QApplication.instance() or QApplication([])
    window: MainWindow = _window_with_layer(_make_layer("l1", "内存图层", None))
    path: Path = window._default_feature_output_path(
        "新建点_20260804", window._application.snapshot()
    )
    assert path == Path.home() / "新建点_20260804.geojson"
    window.close()


def test_digitized_feature_status_bar_shows_output_path(tmp_path: Path) -> None:
    """数字化完成后状态栏提示应包含输出文件的完整路径。"""
    _ = QApplication.instance() or QApplication([])
    data_dir: Path = tmp_path / "data"
    data_dir.mkdir()
    source: Path = data_dir / "roads.shp"
    layer: VectorLayer = _make_layer("l1", "道路", source)
    document: MapDocument = MapDocument()
    document.add_layer(layer)
    document.set_active_layer(layer.layer_id)
    document.set_display_crs(CRS.from_epsg(4549))
    window: MainWindow = MainWindow()
    window._application = GisApplication(AutoDataReader(), AutoDataWriter(), document)

    window._on_feature_digitized(Point(10, 10))

    text: str = window._ready_label.text()
    assert "已创建点要素 → " in text
    assert str(data_dir.resolve()) in text
    window.close()
