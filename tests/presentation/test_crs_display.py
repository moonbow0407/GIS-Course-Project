"""坐标系状态栏展示测试。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pyproj import CRS
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from shapely.geometry import Point

from app.domain.feature import Feature
from app.domain.vector_layer import VectorLayer
from app.presentation.main_window import MainWindow
from app.presentation.widgets.display_crs_confirmation_dialog import (
    DisplayCrsConfirmationDialog,
)


def test_format_crs_keeps_esri_authority_distinct_from_epsg() -> None:
    """ESRI 自定义投影应显示 ESRI 编码和可读名称，而不是伪造 EPSG 编码。"""
    formatted: str = MainWindow._format_crs(CRS.from_user_input("ESRI:102026"))

    assert formatted.startswith("ESRI:102026 · ")
    assert "Asia_North_Equidistant_Conic" in formatted


def test_format_crs_displays_epsg_authority_when_available() -> None:
    """标准 EPSG 坐标系应保留 EPSG 权威编号。"""
    formatted: str = MainWindow._format_crs(CRS.from_epsg(4326))

    assert formatted.startswith("EPSG:4326 · ")


def test_dialog_offers_source_recommendations_and_custom_input() -> None:
    """确认框应同时提供源 CRS、三个固定推荐项和自定义输入。"""
    QApplication.instance() or QApplication([])
    dialog = DisplayCrsConfirmationDialog(
        CRS.from_epsg(4549),
        "当前 CRS 适用范围较窄，可能不适合后续图层显示。",
    )

    assert dialog.option_codes() == (
        "SOURCE",
        "EPSG:4490",
        "EPSG:4326",
        "EPSG:3857",
        "CUSTOM",
    )
    assert dialog.selected_crs() == CRS.from_epsg(4549)
    assert "不会修改图层自身 CRS" in dialog.explanation_text()
    dialog.close()


def test_dialog_parses_custom_display_crs() -> None:
    """自定义项应支持任意有效 EPSG 输入。"""
    QApplication.instance() or QApplication([])
    dialog = DisplayCrsConfirmationDialog(CRS.from_epsg(4549), "需要确认")

    dialog.set_custom_text("EPSG:32650")

    assert dialog.selected_crs() == CRS.from_epsg(32650)
    dialog.close()


def test_suitable_first_layer_does_not_open_confirmation(monkeypatch) -> None:
    """常见 CRS 在低纬适用范围内不应打扰用户。"""
    QApplication.instance() or QApplication([])
    window = MainWindow()
    monkeypatch.setattr(window, "_confirm_project_switch", lambda: True)
    monkeypatch.setattr(
        "app.presentation.main_window.DisplayCrsConfirmationDialog",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("适用 CRS 不应打开确认框")
        ),
    )
    layer = VectorLayer.create(
        name="equator",
        features=(Feature(1, Point(110.0, 15.0), {}),),
        crs=CRS.from_epsg(4326),
    )

    assert window._choose_initial_display_crs(layer) == (True, None)
    window.close()


def test_unsuitable_choice_can_return_to_dialog_after_secondary_warning(
    monkeypatch,
) -> None:
    """用户拒绝不适配候选 CRS 后应回到选择框，而不是取消导入。"""
    QApplication.instance() or QApplication([])
    window = MainWindow()
    monkeypatch.setattr(window, "_confirm_project_switch", lambda: True)
    source_crs = CRS.from_epsg(4326)
    candidates = iter((CRS.from_epsg(3857), source_crs))
    dialog_count: list[int] = []
    question_messages: list[str] = []

    class FakeDialog:
        """依次返回高变形候选和源 CRS 的测试确认框。"""

        def __init__(self, *_args, **_kwargs) -> None:
            dialog_count.append(1)

        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Accepted

        def selected_crs(self) -> CRS:
            return next(candidates)

    monkeypatch.setattr(
        "app.presentation.main_window.DisplayCrsConfirmationDialog",
        FakeDialog,
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda _parent, _title, message, *_args: (
            question_messages.append(message) or QMessageBox.StandardButton.No
        ),
    )
    layer = VectorLayer.create(
        name="arctic",
        features=(Feature(1, Point(15.0, 78.0), {}),),
        crs=source_crs,
    )

    assert window._choose_initial_display_crs(layer) == (True, None)
    assert len(dialog_count) == 2
    assert question_messages == [
        "当前 CRS 在该区域变形较明显，显示效果可能不理想。\n是否仍要使用该显示 CRS？"
    ]
    window.close()
