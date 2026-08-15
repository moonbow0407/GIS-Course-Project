"""栅格分析对话框测试：输入校验、无栅格提示和请求构建。"""

import os

# 必须在导入 Qt 前启用无界面平台，测试才能在没有显示器的环境运行。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import patch

import numpy as np
import pytest
from affine import Affine
from pyproj import CRS
from PySide6.QtWidgets import QApplication
from shapely.geometry import Point, Polygon

from app.application.results import LayerSnapshot
from app.domain.feature import Feature
from app.domain.raster_layer import RasterLayer
from app.domain.vector_layer import VectorLayer
from app.presentation.widgets.dem_analysis_dialog import DemAnalysisDialog
from app.presentation.widgets.raster_clip_dialog import RasterClipDialog
from app.presentation.widgets.raster_reclassify_dialog import RasterReclassifyDialog


def _raster_snapshot() -> LayerSnapshot:
    """构建一个 4 波段测试栅格图层快照。"""
    data = np.ones((4, 4, 4), dtype=np.float32)
    image = np.full((4, 4, 4), 255, dtype=np.uint8)
    layer = RasterLayer.create(
        name="遥感影像",
        raster_data=data,
        image_data=image,
        valid_mask=np.ones((4, 4), dtype=bool),
        transform=Affine.identity(),
        crs=CRS.from_epsg(32650),
        bounds=(0.0, 0.0, 4.0, 4.0),
    )
    return LayerSnapshot(layer=layer, visible=True, selected_feature_ids=())


def _polygon_snapshot() -> LayerSnapshot:
    """构建一个面矢量图层快照。"""
    polygon = Polygon([(0, 0), (4, 0), (4, 4), (0, 4)])
    layer = VectorLayer.create(
        name="行政区",
        features=(Feature(fid=1, geometry=polygon, attributes={}),),
        crs=CRS.from_epsg(32650),
    )
    return LayerSnapshot(layer=layer, visible=True, selected_feature_ids=())


def _point_snapshot() -> LayerSnapshot:
    """构建一个点矢量图层快照。"""
    layer = VectorLayer.create(
        name="监测点",
        features=(Feature(fid=1, geometry=Point(1, 1), attributes={}),),
        crs=CRS.from_epsg(32650),
    )
    return LayerSnapshot(layer=layer, visible=True, selected_feature_ids=())


def _ensure_app() -> QApplication:
    """确保存在测试用 QApplication。"""
    return QApplication.instance() or QApplication([])


class TestDialogAvailability:
    """没有可用输入图层时各入口应给出明确错误。"""

    def test_reclassify_requires_raster(self) -> None:
        """没有栅格时重分类对话框应抛出明确异常。"""
        _ensure_app()
        with pytest.raises(ValueError, match="栅格"):
            RasterReclassifyDialog(())

    def test_dem_requires_raster(self) -> None:
        """没有栅格时 DEM 对话框应抛出明确异常。"""
        _ensure_app()
        with pytest.raises(ValueError, match="栅格"):
            DemAnalysisDialog(())

    def test_clip_requires_raster_and_polygon(self) -> None:
        """缺少栅格或面图层时掩膜裁剪对话框应抛出明确异常。"""
        _ensure_app()
        with pytest.raises(ValueError, match="栅格"):
            RasterClipDialog((_polygon_snapshot(),))
        with pytest.raises(ValueError, match="面"):
            RasterClipDialog((_raster_snapshot(), _point_snapshot()))


class TestReclassifyDialog:
    """重分类对话框。"""

    def test_request_builds_rules_from_table(self) -> None:
        """规则表格内容应完整进入请求对象。"""
        _ensure_app()
        snapshot = _raster_snapshot()
        dialog = RasterReclassifyDialog((snapshot,))
        dialog._add_rule_row()
        dialog._table.item(0, 0).setText("0")
        dialog._table.item(0, 1).setText("10")
        dialog._table.item(0, 2).setText("1")

        request = dialog.request()

        assert len(request.rules) == 1
        rule = request.rules[0]
        assert rule.lower == 0.0
        assert rule.upper == 10.0
        assert rule.output_value == 1.0
        assert request.input_layer_id == snapshot.layer_id
        dialog.close()

    def test_empty_rules_rejected_on_accept(self) -> None:
        """没有规则时确认应被阻止（对话框保持打开）。"""
        _ensure_app()
        dialog = RasterReclassifyDialog((_raster_snapshot(),))
        # 拦截模态弹框避免测试阻塞，然后验证校验未通过。
        with patch(
            "app.presentation.widgets.raster_reclassify_dialog.QMessageBox"
        ) as box:
            dialog._accept_request()
        box.warning.assert_called_once()
        assert dialog.result() != dialog.DialogCode.Accepted
        dialog.close()

    def test_default_policy_and_auto_generation_match_gis_workflow(self) -> None:
        """默认保留未匹配值，并支持按唯一值自动填充规则。"""
        _ensure_app()
        dialog = RasterReclassifyDialog((_raster_snapshot(),))

        assert dialog._policy_combo.currentText() == "保留原值"
        assert dialog._dtype_combo.currentText() == "int16"
        dialog._mode_combo.setCurrentText("按唯一值分类")
        dialog._generate_rules()

        assert dialog._table.rowCount() == 1
        assert dialog._table.item(0, 0).text() == "1"
        assert dialog._table.item(0, 1).text() == "1"
        assert dialog._table.item(0, 3).text() == "是"
        assert dialog._table.item(0, 4).text() == "是"
        dialog.close()


class TestDemDialog:
    """DEM 分析对话框。"""

    def test_request_mode_and_path(self) -> None:
        """请求应携带分析模式和 GeoTIFF 输出路径。"""
        _ensure_app()
        dialog = DemAnalysisDialog((_raster_snapshot(),))
        dialog._mode_combo.setCurrentText("坡向（度，北为0顺时针）")
        request = dialog.request()
        assert request.mode == "aspect"
        assert request.output_path.suffix == ".tif"
        dialog.close()

    def test_hillshade_parameters_visible_only_for_hillshade(self) -> None:
        """太阳角度参数仅在山体阴影模式下显示。"""
        _ensure_app()
        dialog = DemAnalysisDialog((_raster_snapshot(),))
        dialog._mode_combo.setCurrentText("坡度（度）")
        assert not dialog._shade_group.isVisibleTo(dialog)
        dialog._mode_combo.setCurrentText("山体阴影（0-255）")
        assert dialog._shade_group.isVisibleTo(dialog)
        dialog.close()


class TestClipDialog:
    """掩膜裁剪对话框。"""

    def test_request_defaults(self) -> None:
        """默认选项应为 crop 开启、all_touched 和 invert 关闭。"""
        _ensure_app()
        dialog = RasterClipDialog((_raster_snapshot(), _polygon_snapshot()))
        request = dialog.request()
        assert request.crop is True
        assert request.all_touched is False
        assert request.invert is False
        assert request.output_path.suffix == ".tif"
        dialog.close()

    def test_polygon_combo_only_lists_polygon_layers(self) -> None:
        """掩膜下拉框应只列出面图层。"""
        _ensure_app()
        dialog = RasterClipDialog(
            (_raster_snapshot(), _polygon_snapshot(), _point_snapshot())
        )
        assert dialog._mask_combo.count() == 1
        dialog.close()
