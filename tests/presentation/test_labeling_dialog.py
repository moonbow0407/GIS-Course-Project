"""动态标注配置对话框测试。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pyproj import CRS
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QLabel
from shapely.geometry import Point

from app.application.results import LayerSnapshot
from app.domain.feature import Feature
from app.domain.vector_layer import VectorLayer
from app.presentation.widgets.labeling_dialog import LabelingDialog
from main import load_style


def _snapshot() -> LayerSnapshot:
    """创建带名称和类型字段的标注测试图层。"""
    layer = VectorLayer.create(
        layer_id="cities",
        name="城市",
        features=(
            Feature(
                fid=1,
                geometry=Point(0, 0),
                attributes={"name": "合肥", "kind": "capital"},
            ),
        ),
        crs=CRS.from_epsg(4326),
    )
    return LayerSnapshot(layer=layer, visible=True, selected_feature_ids=())


def test_dialog_edits_field_and_can_add_label_class() -> None:
    """标注分类窗口应加载字段，并能新增分类后返回不可变配置。"""
    application: QApplication = QApplication.instance() or QApplication([])
    dialog = LabelingDialog(_snapshot())

    assert dialog._field.findText("name") >= 0
    assert dialog._field.findText("kind") >= 0
    assert dialog._halo_enabled_checkbox.isChecked() is False
    dialog._add_class()
    dialog._halo_enabled_checkbox.setChecked(True)
    dialog._class_name.setText("类别标注")
    dialog._accept()

    assert dialog.result_config is not None
    assert len(dialog.result_config.classes) == 2
    assert dialog.result_config.classes[-1].name == "类别标注"
    assert dialog.result_config.classes[-1].halo_enabled is True
    assert application is not None
    dialog.close()


def test_dialog_forces_light_high_contrast_palette() -> None:
    """系统深色调色板下，标注弹窗仍应使用不透明浅色底和深色文字。"""
    application: QApplication = QApplication.instance() or QApplication([])
    original_palette: QPalette = application.palette()
    dark_palette = QPalette(original_palette)
    dark_palette.setColor(QPalette.ColorRole.Window, QColor("#202020"))
    dark_palette.setColor(QPalette.ColorRole.WindowText, QColor("#EEEEEE"))
    dark_palette.setColor(QPalette.ColorRole.Base, QColor("#2B2B2B"))
    dark_palette.setColor(QPalette.ColorRole.Text, QColor("#EEEEEE"))
    application.setPalette(dark_palette)
    load_style(application)

    dialog = LabelingDialog(_snapshot())
    dialog.show()
    application.processEvents()
    title: QLabel | None = dialog.findChild(QLabel, "labelingTitle")

    assert dialog.palette().color(QPalette.ColorRole.Window).name() == "#ffffff"
    assert dialog.palette().color(QPalette.ColorRole.WindowText).lightness() < 180
    assert title is not None
    assert title.palette().color(QPalette.ColorRole.WindowText).lightness() < 180

    dialog.close()
    application.setPalette(original_palette)
