"""缓冲区分析参数对话框测试。"""

import os
from pathlib import Path

# 测试只验证控件和请求组装，不需要真实显示器。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pyproj import CRS
from PySide6.QtWidgets import QApplication
from shapely.geometry import LineString, Point, Polygon

from app.application.results import LayerSnapshot
from app.domain.feature import Feature
from app.domain.vector_layer import VectorLayer
from app.presentation.widgets.buffer_analysis_dialog import BufferAnalysisDialog


def _snapshot(layer: VectorLayer) -> LayerSnapshot:
    """把测试图层包装为缓冲区对话框使用的快照。"""
    return LayerSnapshot(layer=layer, visible=True, selected_feature_ids=())


def test_dialog_builds_buffer_request_from_user_parameters(tmp_path: Path) -> None:
    """对话框应把输入输出名称、位置和几何参数完整交给应用层。"""
    application: QApplication = QApplication.instance() or QApplication([])
    layer: VectorLayer = VectorLayer.create(
        layer_id="roads",
        name="道路",
        features=(Feature(fid=1, geometry=Point(0, 0), attributes={}),),
        crs=CRS.from_epsg(3857),
    )
    dialog: BufferAnalysisDialog = BufferAnalysisDialog(
        (LayerSnapshot(layer=layer, visible=True, selected_feature_ids=()),),
        display_crs=CRS.from_epsg(3857),
    )
    dialog._output_name_edit.setText("道路 100 米缓冲")
    dialog._output_path_edit.setText(str(tmp_path / "roads_buffer"))
    dialog._distance_spin.setValue(100.0)
    dialog._distance_unit_combo.setCurrentIndex(3)
    dialog._segments_spin.setValue(16)
    dialog._dissolve_check.setChecked(True)

    request = dialog.request()

    assert request.input_layer_id == "roads"
    assert request.output_layer_name == "道路 100 米缓冲"
    assert request.output_path == tmp_path / "roads_buffer.geojson"
    assert request.distance == 100.0
    assert request.distance_unit == "kilometer"
    assert request.segments == 16
    assert request.dissolve is True
    assert request.analysis_crs is None
    dialog.close()
    assert application is not None


def test_dialog_defaults_output_to_input_directory_and_switches_line_parameters(
    tmp_path: Path,
) -> None:
    """对话框应默认使用输入目录，并随输入图层切换点线专用参数。"""
    application: QApplication = QApplication.instance() or QApplication([])
    point_layer: VectorLayer = VectorLayer.create(
        layer_id="points",
        name="学校",
        features=(Feature(fid=1, geometry=Point(0, 0), attributes={}),),
        crs=CRS.from_epsg(3857),
        source_path=tmp_path / "points" / "schools.shp",
    )
    line_layer: VectorLayer = VectorLayer.create(
        layer_id="roads",
        name="道路",
        features=(Feature(fid=1, geometry=LineString([(0, 0), (10, 0)]), attributes={}),),
        crs=CRS.from_epsg(3857),
        source_path=tmp_path / "roads" / "roads.shp",
    )
    dialog: BufferAnalysisDialog = BufferAnalysisDialog(
        (_snapshot(point_layer), _snapshot(line_layer)),
        display_crs=CRS.from_epsg(3857),
    )

    assert dialog._geometry_type_label.text() == "点（Point）"
    assert Path(dialog._output_path_edit.text()) == (
        tmp_path / "points" / "学校_buffer.geojson"
    ).resolve()
    assert dialog._side_type_combo.isHidden()
    assert dialog._cap_style_combo.isHidden()
    assert dialog._join_style_combo.isHidden()

    dialog._input_layer_combo.setCurrentIndex(1)
    application.processEvents()

    assert dialog._geometry_type_label.text() == "线（Polyline）"
    assert Path(dialog._output_path_edit.text()) == (
        tmp_path / "roads" / "道路_buffer.geojson"
    ).resolve()
    assert not dialog._side_type_combo.isHidden()
    assert not dialog._cap_style_combo.isHidden()
    assert not dialog._join_style_combo.isHidden()
    assert dialog._side_type_combo.itemData(1) == "left"
    dialog.close()


def test_dialog_exposes_polygon_outside_and_negative_distance(tmp_path: Path) -> None:
    """面图层应显示仅外侧选项并允许负距离。"""
    application: QApplication = QApplication.instance() or QApplication([])
    polygon_layer: VectorLayer = VectorLayer.create(
        layer_id="areas",
        name="保护区",
        features=(
            Feature(
                fid=1,
                geometry=Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]),
                attributes={},
            ),
        ),
        crs=CRS.from_epsg(3857),
        source_path=tmp_path / "areas" / "protected.shp",
    )
    dialog: BufferAnalysisDialog = BufferAnalysisDialog(
        (_snapshot(polygon_layer),),
        display_crs=CRS.from_epsg(3857),
    )

    assert dialog._geometry_type_label.text() == "面（Polygon）"
    assert dialog._distance_spin.minimum() < 0
    assert dialog._side_type_combo.itemData(1) == "outside"
    assert dialog._cap_style_combo.isHidden()
    assert not dialog._join_style_combo.isHidden()
    dialog.close()
    assert application is not None
