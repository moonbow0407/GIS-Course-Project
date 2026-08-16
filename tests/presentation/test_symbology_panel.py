"""符号系统侧边面板行为测试。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from affine import Affine
from pyproj import CRS
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QCheckBox, QFrame, QLabel, QPushButton
from shapely.geometry import LineString, Point

from app.application.results import LayerSnapshot
from app.application.symbology_service import (
    apply_raster_symbology,
    create_graduated_symbology,
    create_raster_classified_symbology,
    create_unique_value_symbology,
)
from app.domain.feature import Feature
from app.domain.layer_style import LayerStyle
from app.domain.raster_layer import RasterLayer
from app.domain.symbology import (
    RasterClass,
    RasterRendererType,
    RasterSymbology,
    VectorRendererType,
    VectorSymbology,
)
from app.domain.vector_layer import VectorLayer
from app.presentation.widgets.color_wheel_picker import ColorWheelPicker
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
        button.text() not in {"应用", "确定"} or button.isHidden()
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

    assert not panel._simple_color_button.icon().isNull()
    assert not panel._scheme.itemIcon(0).isNull()
    assert color_item is not None
    assert not color_item.text().startswith("#")
    assert color_item.data(1) is not None
    assert application is not None


def test_graduated_class_count_round_trips_and_accepts_invalid_input_for_feedback() -> None:
    """回填分级符号时应保留实际级数，并允许输入超出样本数以便提示错误。"""
    application: QApplication = QApplication.instance() or QApplication([])
    source_layer = VectorLayer.create(
        layer_id="graduated-count",
        name="行政区边界",
        features=tuple(
            Feature(fid=index, geometry=Point(index, 0), attributes={"value": index})
            for index in range(7)
        ),
        crs=CRS.from_epsg(4326),
    )
    styled_layer = VectorLayer.create(
        layer_id=source_layer.layer_id,
        name=source_layer.name,
        features=source_layer.features,
        crs=source_layer.crs,
        symbology=create_graduated_symbology(
            source_layer,
            "value",
            "gray",
            "equal_interval",
            3,
        ),
    )
    panel = SymbologyPanel()
    panel.set_layer(
        LayerSnapshot(layer=styled_layer, visible=True, selected_feature_ids=())
    )

    assert panel._class_count.value() == 3
    assert panel._class_count.maximum() > 7
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
    assert QColor(panel._current_simple_color).name() == "#8b5cf6"

    panel.set_layer(
        LayerSnapshot(layer=green_layer, visible=True, selected_feature_ids=())
    )
    assert QColor(panel._current_simple_color).name() == "#39a96b"
    assert not panel._simple_color_button.icon().isNull()
    assert panel._simple_color_button.text() == "颜色 ▾"
    assert application is not None


def test_clearing_active_layer_clears_all_stale_symbology_controls() -> None:
    """取消活动图层后，符号面板不能残留上一图层的字段和配色选项。"""
    application: QApplication = QApplication.instance() or QApplication([])
    source_layer = VectorLayer.create(
        layer_id="landuse-clear",
        name="土地利用",
        features=(
            Feature(fid=1, geometry=Point(0, 0), attributes={"类型": "林地"}),
            Feature(fid=2, geometry=Point(1, 1), attributes={"类型": "水域"}),
        ),
        crs=CRS.from_epsg(4326),
    )
    layer = VectorLayer.create(
        layer_id=source_layer.layer_id,
        name=source_layer.name,
        features=source_layer.features,
        crs=source_layer.crs,
        symbology=create_unique_value_symbology(source_layer, "类型", "standard"),
    )
    panel = SymbologyPanel()
    panel.set_layer(LayerSnapshot(layer=layer, visible=True, selected_feature_ids=()))

    assert panel._field.count() > 0
    assert panel._scheme.count() > 0
    panel.set_layer(None)

    assert panel._title.text() == "请选择图层"
    assert panel._metadata.text() == "打开图层后可配置显示方式"
    assert panel._settings_card.isHidden()
    assert panel._renderer.count() == 0
    assert panel._field.count() == 0
    assert panel._scheme.count() == 0
    assert panel._classes.rowCount() == 0
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
    auto_apply = panel.findChild(QCheckBox, "symbologyAutoApplyCheck")

    assert header is not None
    assert settings is not None
    assert classes is not None and not classes.isHidden()
    assert preview is not None and preview.pixmap() is not None
    assert metadata is not None and "矢量" in metadata.text() and "2 个要素" in metadata.text()
    assert auto_apply is not None and auto_apply.isChecked()
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


def test_raster_panel_edits_nodata_visibility_and_color(monkeypatch) -> None:
    """栅格面板应回填并发送用户修改的 NoData 显示配置。"""
    application: QApplication = QApplication.instance() or QApplication([])
    layer = RasterLayer.create(
        layer_id="nodata-raster",
        name="含空值高程",
        raster_data=np.asarray([[[1.0, -9999.0]]]),
        image_data=np.zeros((1, 2, 4), dtype=np.uint8),
        valid_mask=np.asarray([[True, False]], dtype=np.bool_),
        transform=Affine.identity(),
        crs=CRS.from_epsg(4326),
        bounds=(0, 0, 2, 1),
        symbology=RasterSymbology(
            renderer_type=RasterRendererType.STRETCH,
            color_scheme="terrain",
            nodata_color="#334155",
            nodata_visible=False,
        ),
    )
    panel = SymbologyPanel()
    panel.set_layer(LayerSnapshot(layer=layer, visible=True, selected_feature_ids=()))
    emitted: list[RasterSymbology] = []
    panel.symbology_changed.connect(
        lambda _layer_id, symbology: emitted.append(symbology)
    )
    monkeypatch.setattr(
        ColorWheelPicker,
        "get_color",
        lambda *_args, **_kwargs: QColor("#e11d48"),
    )

    assert panel._nodata_visible.isChecked() is False
    assert panel._nodata_color_button.isHidden() is False
    panel._nodata_visible.setChecked(True)
    panel._nodata_color_button.click()
    application.processEvents()

    assert emitted
    assert emitted[-1].nodata_visible is True
    assert emitted[-1].nodata_color == "#e11d48"


def test_graduated_raster_panel_persists_nodata_visibility_and_color(monkeypatch) -> None:
    """分级着色时勾选显示 NoData 并改颜色应写回图层，再次打开仍保持勾选。"""
    application: QApplication = QApplication.instance() or QApplication([])
    layer = RasterLayer.create(
        layer_id="graduated-nodata",
        name="test_clip",
        raster_data=np.asarray([[[100.0, -9999.0]]]),
        image_data=np.zeros((1, 2, 4), dtype=np.uint8),
        valid_mask=np.asarray([[True, False]], dtype=np.bool_),
        transform=Affine.identity(),
        crs=CRS.from_epsg(4490),
        bounds=(0, 0, 2, 1),
        nodata=-9999.0,
        symbology=RasterSymbology(
            renderer_type=RasterRendererType.CLASSIFIED,
            color_scheme="gray",
            classification_method="equal_interval",
            classes=(
                RasterClass(0.0, "0 – 200", "#111111", upper=200.0),
                RasterClass(200.0, "200 – 400", "#888888", upper=400.0),
                RasterClass(400.0, "400 – 600", "#eeeeee", upper=600.0),
            ),
            nodata_color="#000000",
            nodata_visible=False,
        ),
    )
    panel = SymbologyPanel()
    panel.set_layer(LayerSnapshot(layer=layer, visible=True, selected_feature_ids=()))
    emitted: list[RasterSymbology] = []
    panel.symbology_changed.connect(
        lambda _layer_id, symbology: emitted.append(symbology)
    )
    monkeypatch.setattr(
        ColorWheelPicker,
        "get_color",
        lambda *_args, **_kwargs: QColor("#2563eb"),
    )

    assert panel._renderer.currentData() == SymbologyPanel._RASTER_GRADUATED
    assert panel._nodata_visible.isChecked() is False
    panel._nodata_visible.setChecked(True)
    panel._nodata_color_button.click()
    application.processEvents()

    assert emitted
    applied = emitted[-1]
    assert applied.renderer_type is RasterRendererType.CLASSIFIED
    assert applied.nodata_visible is True
    assert applied.nodata_color == "#2563eb"
    styled = apply_raster_symbology(layer, applied)
    assert styled.image_data[0, 1].tolist() == [37, 99, 235, 255]

    panel.set_layer(LayerSnapshot(layer=styled, visible=True, selected_feature_ids=()))
    assert panel._nodata_visible.isChecked() is True
    assert application is not None


def test_classified_raster_panel_shows_discrete_levels() -> None:
    """重分类结果应在符号面板显示分类值和离散类别表。"""
    application: QApplication = QApplication.instance() or QApplication([])
    layer = RasterLayer.create(
        name="重分类结果",
        raster_data=np.asarray([[[1.0, 2.0], [3.0, 1.0]]]),
        image_data=np.zeros((2, 2, 4), dtype=np.uint8),
        valid_mask=np.ones((2, 2), dtype=np.bool_),
        transform=Affine.identity(),
        crs=CRS.from_epsg(4326),
        bounds=(0, 0, 2, 2),
        symbology=create_raster_classified_symbology((1.0, 2.0, 3.0)),
    )
    panel = SymbologyPanel()
    panel.set_layer(LayerSnapshot(layer=layer, visible=True, selected_feature_ids=()))

    classes = panel.findChild(QFrame, "symbologyClassesCard")

    assert panel._renderer.currentData() == RasterRendererType.CLASSIFIED.value
    assert classes is not None and not classes.isHidden()
    assert panel._classes.rowCount() == 4
    assert panel._classes.item(0, 1).text() == "1"
    assert application is not None


def test_raster_panel_requests_graduated_classes_when_switching_renderer() -> None:
    """切换到分级着色时应请求按色带和分级方法生成区间类别。"""
    application: QApplication = QApplication.instance() or QApplication([])
    data = np.linspace(0.0, 100.0, 25, dtype=np.float32).reshape(1, 5, 5)
    layer = RasterLayer.create(
        layer_id="dem",
        name="高程",
        raster_data=data,
        image_data=np.zeros((5, 5, 4), dtype=np.uint8),
        valid_mask=np.ones((5, 5), dtype=np.bool_),
        transform=Affine.identity(),
        crs=CRS.from_epsg(4326),
        bounds=(0, 0, 5, 5),
        symbology=RasterSymbology(
            renderer_type=RasterRendererType.STRETCH,
            color_scheme="terrain",
        ),
    )
    panel = SymbologyPanel()
    panel.set_layer(LayerSnapshot(layer=layer, visible=True, selected_feature_ids=()))
    requests: list[tuple[str, str, str, int]] = []
    panel.raster_classified_requested.connect(
        lambda layer_id, scheme, method, count: requests.append(
            (layer_id, scheme, method, count)
        )
    )

    graduated_index = panel._renderer.findData(SymbologyPanel._RASTER_GRADUATED)
    panel._renderer.setCurrentIndex(graduated_index)
    application.processEvents()

    assert requests
    assert requests[-1][0] == "dem"
    assert requests[-1][1] == "terrain"
    assert requests[-1][2] == "equal_interval"
    assert requests[-1][3] >= 3
    assert application is not None
