"""显示设置对话框回归测试。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pyproj import CRS
from PySide6.QtWidgets import QApplication
from shapely.geometry import Point

from app.application.results import LayerSnapshot
from app.domain.feature import Feature
from app.domain.vector_layer import VectorLayer
from app.presentation.widgets.display_settings_dialog import DisplaySettingsDialog


def _make_layer(layer_id: str) -> VectorLayer:
    """创建包含一个点要素的测试图层。"""
    return VectorLayer.create(
        layer_id=layer_id,
        name=layer_id,
        features=(Feature(fid=1, geometry=Point(0, 0), attributes={}),),
        crs=CRS.from_epsg(4326),
    )


def test_switching_layer_refreshes_display_controls() -> None:
    """切换图层后透明度和比例范围控件应回填新图层状态。"""
    application = QApplication.instance() or QApplication([])
    first_layer = _make_layer("first")
    second_layer = _make_layer("second")
    dialog = DisplaySettingsDialog()
    dialog.set_layers(
        (
            LayerSnapshot(
                layer=first_layer,
                visible=True,
                selected_feature_ids=(),
                opacity=0.25,
                min_scale_percent=10.0,
                max_scale_percent=100.0,
            ),
            LayerSnapshot(
                layer=second_layer,
                visible=True,
                selected_feature_ids=(),
                opacity=0.75,
                min_scale_percent=20.0,
                max_scale_percent=200.0,
            ),
        ),
        active_layer_id=first_layer.layer_id,
    )

    dialog._layer_combo.setCurrentIndex(1)

    assert application is not None
    assert dialog._opacity_slider.value() == 75
    assert dialog._min_scale.value() == 20.0
    assert dialog._max_scale.value() == 200.0
