"""符号系统侧边面板行为测试。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from affine import Affine
from pyproj import CRS
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton
from shapely.geometry import LineString, Point

from app.application.results import LayerSnapshot
from app.application.symbology_service import create_unique_value_symbology
from app.domain.feature import Feature
from app.domain.layer_style import LayerStyle
from app.domain.raster_layer import RasterLayer
from app.domain.symbology import (
    RasterRendererType,
    RasterSymbology,
    VectorRendererType,
    VectorSymbology,
)
from app.domain.vector_layer import VectorLayer
from app.presentation.widgets.symbology_panel import SymbologyPanel
from main import load_style


def test_panel_follows_vector_layer_and_auto_requests_unique_values() -> None:
    """选择唯一值后应立即按当前字段和配色请求更新，不显示应用按钮。"""
    application: QApplication = QApplication.instance() or QApplication([])
    layer = VectorLayer.create(
        layer_id="landuse",
        name="土地利用",
        features=(
            Feature(fid=1, geometry=Point(0, 0), attributes={"类型": "林地", "面积": 10}),
            Feature(fid=2, geometry=Point(1, 1), attributes={"类型": "水域", "面积": 20}),
        ),
        crs=CRS.from_epsg(4326),
    )
    panel = SymbologyPanel()
    panel.set_layer(LayerSnapshot(layer=layer, visible=True, selected_feature_ids=()))
    requests: list[tuple[str, str, str]] = []
    panel.unique_requested.connect(
        lambda layer_id, field, scheme: requests.append((layer_id, field, scheme))
    )

    unique_index = panel._renderer.findData(VectorRendererType.UNIQUE.value)
    panel._renderer.setCurrentIndex(unique_index)
    application.processEvents()

    assert panel._field.currentText() == "类型"
    assert requests[-1] == ("landuse", "类型", "standard")
    assert all(
        button.text() not in {"应用", "确定"}
        for button in panel.findChildren(QPushButton)
    )


def test_symbology_panel_stays_light_with_dark_system_palette() -> None:
    """深色系统主题下符号面板和类别表仍应使用浅色可读背景。"""
    application: QApplication = QApplication.instance() or QApplication([])
    original_palette: QPalette = application.palette()
    dark_palette = QPalette(original_palette)
    dark_palette.setColor(QPalette.ColorRole.Window, QColor("#101010"))
    dark_palette.setColor(QPalette.ColorRole.Base, QColor("#080808"))
    dark_palette.setColor(QPalette.ColorRole.Text, QColor("#ffffff"))
    application.setPalette(dark_palette)
    load_style(application)
    panel = SymbologyPanel()
    panel.show()
    application.processEvents()

    assert panel.palette().color(QPalette.ColorRole.Window).lightness() >= 180
    assert panel._classes.palette().color(QPalette.ColorRole.Base).lightness() >= 180

    panel.close()
    application.setPalette(original_palette)


def test_color_controls_show_visual_swatch_and_chinese_mapping() -> None:
    """颜色选项和类别颜色列不能只显示难以识别的十六进制编号。"""
    application: QApplication = QApplication.instance() or QApplication([])
    layer = VectorLayer.create(
        layer_id="landuse-colors",
        name="土地利用",
        features=(
            Feature(fid=1, geometry=Point(0, 0), attributes={"类型": "林地"}),
            Feature(fid=2, geometry=Point(1, 1), attributes={"类型": "水域"}),
        ),
        crs=CRS.from_epsg(4326),
    )
    styled_layer = VectorLayer.create(
        layer_id=layer.layer_id,
        name=layer.name,
        features=layer.features,
        crs=layer.crs,
        symbology=create_unique_value_symbology(layer, "类型", "standard"),
    )
    panel = SymbologyPanel()
    panel.set_layer(
        LayerSnapshot(layer=styled_layer, visible=True, selected_feature_ids=())
    )
    color_item = panel._classes.item(0, 2)

    assert not panel._simple_color.itemIcon(0).isNull()
    assert not panel._scheme.itemIcon(0).isNull()
    assert color_item is not None
    assert not color_item.text().startswith("#")
    assert color_item.data(1) is not None
    assert application is not None


def test_simple_color_control_follows_each_active_layers_actual_symbol() -> None:
    """切换图层后颜色控件必须回填真实符号，避免旧选项阻止再次触发变色。"""
    application: QApplication = QApplication.instance() or QApplication([])

    def create_line_layer(layer_id: str, color: str) -> VectorLayer:
        symbol = LayerStyle(
            stroke_color=color,
            fill_color="transparent",
            line_width=2.0,
            point_size=0.0,
            opacity=1.0,
        )
        return VectorLayer.create(
            layer_id=layer_id,
            name=layer_id,
            features=(
                Feature(
                    fid=1,
                    geometry=LineString([(0, 0), (1, 1)]),
                    attributes={},
                ),
            ),
            crs=CRS.from_epsg(4326),
            symbology=VectorSymbology(VectorRendererType.SIMPLE, symbol),
        )

    purple_layer = create_line_layer("purple-road", "#8B5CF6")
    green_layer = create_line_layer("green-road", "#39A96B")
    panel = SymbologyPanel()

    panel.set_layer(
        LayerSnapshot(layer=purple_layer, visible=True, selected_feature_ids=())
    )
    assert QColor(str(panel._simple_color.currentData())).name() == "#8b5cf6"

    panel.set_layer(
        LayerSnapshot(layer=green_layer, visible=True, selected_feature_ids=())
    )
    assert QColor(str(panel._simple_color.currentData())).name() == "#39a96b"
    assert panel._simple_color.currentText() == "绿色"
    assert not panel._simple_color.itemIcon(panel._simple_color.currentIndex()).isNull()
    assert application is not None


def test_panel_uses_layer_header_preview_cards_and_auto_apply_status() -> None:
    """符号面板应具有接近专业 GIS 的清晰标题、预览、设置和类别层级。"""
    application: QApplication = QApplication.instance() or QApplication([])
    layer = VectorLayer.create(
        layer_id="visual-hierarchy",
        name="土地利用现状",
        features=(
            Feature(fid=1, geometry=Point(0, 0), attributes={"类型": "林地"}),
            Feature(fid=2, geometry=Point(1, 1), attributes={"类型": "水域"}),
        ),
        crs=CRS.from_epsg(4326),
    )
    styled_layer = VectorLayer.create(
        layer_id=layer.layer_id,
        name=layer.name,
        features=layer.features,
        crs=layer.crs,
        symbology=create_unique_value_symbology(layer, "类型", "standard"),
    )
    panel = SymbologyPanel()
    panel.set_layer(
        LayerSnapshot(layer=styled_layer, visible=True, selected_feature_ids=())
    )

    header = panel.findChild(QFrame, "symbologyHeaderCard")
    settings = panel.findChild(QFrame, "symbologySettingsCard")
    classes = panel.findChild(QFrame, "symbologyClassesCard")
    preview = panel.findChild(QLabel, "symbologyPreview")
    metadata = panel.findChild(QLabel, "symbologyLayerMetadata")
    status = panel.findChild(QLabel, "symbologyAutoApplyStatus")

    assert header is not None
    assert settings is not None
    assert classes is not None and not classes.isHidden()
    assert preview is not None and preview.pixmap() is not None
    assert metadata is not None and "矢量" in metadata.text() and "2 个要素" in metadata.text()
    assert status is not None and "自动应用" in status.text()
    assert panel._classes.verticalHeader().isHidden()
    assert panel._classes.alternatingRowColors()
    assert panel._scheme.iconSize().width() >= 80
    assert panel._classes.iconSize().width() >= 40
    assert application is not None


def test_raster_panel_uses_preview_and_hides_irrelevant_class_card() -> None:
    """栅格模式应突出色带预览和波段参数，不显示空的类别管理区域。"""
    application: QApplication = QApplication.instance() or QApplication([])
    data = np.arange(100, dtype=np.float32).reshape(1, 10, 10)
    layer = RasterLayer.create(
        name="高程模型",
        raster_data=data,
        image_data=np.zeros((10, 10, 4), dtype=np.uint8),
        valid_mask=np.ones((10, 10), dtype=np.bool_),
        transform=Affine.identity(),
        crs=CRS.from_epsg(4326),
        bounds=(0, 0, 10, 10),
        symbology=RasterSymbology(
            renderer_type=RasterRendererType.STRETCH,
            color_scheme="terrain",
        ),
    )
    panel = SymbologyPanel()
    panel.set_layer(LayerSnapshot(layer=layer, visible=True, selected_feature_ids=()))

    preview = panel.findChild(QLabel, "symbologyPreview")
    metadata = panel.findChild(QLabel, "symbologyLayerMetadata")
    classes = panel.findChild(QFrame, "symbologyClassesCard")

    assert preview is not None and preview.pixmap() is not None
    assert metadata is not None and "栅格" in metadata.text() and "1 个波段" in metadata.text()
    assert classes is not None and classes.isHidden()
    assert panel._scheme.currentData() == "terrain"
    assert not panel._scheme.itemIcon(panel._scheme.currentIndex()).isNull()
    assert application is not None
