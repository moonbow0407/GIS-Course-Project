"""缓冲区分析参数对话框测试。"""

import os
from pathlib import Path

# 测试只验证控件和请求组装，不需要真实显示器。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pyproj import CRS
from PySide6.QtWidgets import QApplication
from shapely.geometry import Point

from app.application.results import LayerSnapshot
from app.domain.feature import Feature
from app.domain.vector_layer import VectorLayer
from app.presentation.widgets.buffer_analysis_dialog import BufferAnalysisDialog


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
    dialog._segments_spin.setValue(16)
    dialog._dissolve_check.setChecked(True)

    request = dialog.request()

    assert request.input_layer_id == "roads"
    assert request.output_layer_name == "道路 100 米缓冲"
    assert request.output_path == tmp_path / "roads_buffer.geojson"
    assert request.distance == 100.0
    assert request.segments == 16
    assert request.dissolve is True
    assert request.analysis_crs == CRS.from_epsg(3857)
    dialog.close()
    assert application is not None
