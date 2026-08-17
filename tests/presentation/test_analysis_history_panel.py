"""分析历史面板的展示和交互测试。"""

import os

# 必须在导入 Qt 前启用无界面平台，测试才能在 CI 中创建原生控件。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QDockWidget, QLabel, QToolTip

from app.application.project_models import AnalysisOutputReference, AnalysisRun
from app.presentation.main_window import MainWindow
from app.presentation.widgets.analysis_history_panel import AnalysisHistoryPanel
from app.presentation.widgets.ribbon_bar import RibbonBar


def make_run(
    run_id: str,
    created_at: str,
    status: str = "completed",
    algorithm_id: str = "buffer",
    parameters: dict[str, object] | None = None,
) -> AnalysisRun:
    """创建一条包含缓冲距离和输出引用的测试记录。"""
    return AnalysisRun(
        run_id=run_id,
        algorithm_id=algorithm_id,
        input_layer_ids=("roads",),
        parameters=parameters
        or {
            "distance": 500.0,
            "distance_unit": "meter",
            "dissolve": False,
        },
        output_layer_ids=(f"result-{run_id}",) if status == "completed" else (),
        outputs=(
            AnalysisOutputReference(
                layer_id=f"result-{run_id}",
                source_path="project_data/results.gpkg",
                source_layer_name=f"buffer_{run_id}",
            ),
        )
        if status == "completed"
        else (),
        parent_run_ids=(),
        status=status,
        created_at=created_at,
        completed_at=created_at,
        duration_seconds=1.25,
        message="测试失败" if status == "failed" else None,
    )


def test_history_panel_lists_latest_first_and_uses_hover_details() -> None:
    """面板应按时间倒序只展示名称和状态，详情通过悬停提示查看。"""
    qt_application: QApplication = QApplication.instance() or QApplication([])
    panel = AnalysisHistoryPanel()
    older = make_run("old", "2026-07-29T08:00:00+00:00")
    newer = make_run("new", "2026-07-29T09:00:00+00:00")

    panel.set_history(
        (older, newer),
        {"roads": "道路", "result-new": "道路缓冲区", "result-old": "旧缓冲区"},
    )
    qt_application.processEvents()

    assert panel._history_list.count() == 2
    assert panel._history_list.item(0).text() == ""
    row = panel._history_list.itemWidget(panel._history_list.item(0))
    assert row is not None
    operation = row.findChild(QLabel, "analysisHistoryOperation")
    assert operation is not None
    assert operation.text() == "缓冲区分析"
    assert "缓冲距离" in panel._history_list.item(0).toolTip()


def test_main_window_toggles_analysis_history_tab() -> None:
    """连续点击分析记录入口应在统一工作面板中显示和隐藏。"""
    qt_application: QApplication = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    qt_application.processEvents()

    assert not window._panel_dock.isVisible()
    window._toggle_analysis_history()
    qt_application.processEvents()
    assert window._panel_dock.isVisible()
    assert window._panel_tabs.currentIndex() == window._ANALYSIS_TAB_INDEX
    window._toggle_analysis_history()
    qt_application.processEvents()
    assert not window._panel_dock.isVisible()
    window.close()


def test_main_window_workspace_dock_has_only_analysis_tab() -> None:
    """右侧工作面板应仅包含分析记录标签（符号系统已集成到显示设置）。"""
    qt_application: QApplication = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()

    window._show_workspace_panel(window._ANALYSIS_TAB_INDEX)
    qt_application.processEvents()

    assert window._panel_dock.isVisible()
    assert window._panel_dock.widget() is window._panel_tabs
    assert window._panel_tabs.count() == 1
    assert window._panel_tabs.currentIndex() == window._ANALYSIS_TAB_INDEX
    assert len(window.findChildren(QDockWidget)) == 2
    window.close()


def test_history_panel_emits_clear_request_and_ribbon_has_no_result_export() -> None:
    """清除按钮应发出请求，分析功能区不再提供导出结果入口。"""
    qt_application: QApplication = QApplication.instance() or QApplication([])
    panel = AnalysisHistoryPanel()
    received: list[bool] = []
    panel.clear_requested.connect(lambda: received.append(True))

    panel._clear_button.click()
    qt_application.processEvents()

    assert received == [True]
    assert RibbonBar.action_title("analysis_history") == "分析记录"
    assert RibbonBar.action_title("export_result") is None


def test_history_panel_shows_chinese_names_for_raster_runs() -> None:
    """栅格分析记录应显示中文操作名和中文参数名，而不是算法编号。"""
    qt_application: QApplication = QApplication.instance() or QApplication([])
    panel = AnalysisHistoryPanel()
    run = make_run(
        "clip",
        "2026-07-29T10:00:00+00:00",
        algorithm_id="raster_clip",
        parameters={
            "crop": True,
            "all_touched": False,
            "invert": False,
            "band_index": 1,
        },
    )

    panel.set_history((run,), {"roads": "边界", "result-clip": "裁剪结果"})
    qt_application.processEvents()

    row = panel._history_list.itemWidget(panel._history_list.item(0))
    assert row is not None
    operation = row.findChild(QLabel, "analysisHistoryOperation")
    assert operation is not None
    assert operation.text() == "掩膜裁剪"
    tooltip = panel._history_list.item(0).toolTip()
    assert "波段序号" in tooltip
    assert "band_index" not in tooltip


def test_history_tooltip_wraps_long_details_with_a_limited_width() -> None:
    """超长路径和 CRS 应在窄提示框中换行，同时保留完整详情。"""
    qt_application: QApplication = QApplication.instance() or QApplication([])
    panel = AnalysisHistoryPanel()
    long_crs = (
        'PROJCS["NAD83 / Vermont",GEOGCS["NAD83"],DATUM["North_American_Datum_1983"],'
        'PROJECTION["Transverse_Mercator"],PARAMETER["central_meridian",-72.5]]'
    )
    output_path = r"D:\workspace\gis_lab\sample_data\缓冲区分析\road_impact.geojson"
    run = make_run(
        "long-detail",
        "2026-07-29T10:00:00+00:00",
        parameters={"calculation_crs": long_crs, "output_path": output_path},
    )

    panel.set_history((run,), {"roads": "道路", "result-long-detail": "道路缓冲区"})
    qt_application.processEvents()

    tooltip = panel._history_list.item(0).toolTip()
    assert f'width="{panel._DETAIL_TOOLTIP_WIDTH}"' in tooltip
    assert "<br>" in tooltip
    assert "\u200b" in tooltip
    tooltip_without_wrap_hints = tooltip.replace("\u200b", "")
    assert long_crs in tooltip_without_wrap_hints
    assert output_path in tooltip_without_wrap_hints
    assert ",\u200b" in tooltip
    assert "\\\u200b" in tooltip

    panel.show()
    QToolTip.showText(QPoint(20, 20), tooltip, panel)
    qt_application.processEvents()
    tooltip_widgets = [
        widget for widget in QApplication.topLevelWidgets() if widget.inherits("QTipLabel")
    ]
    assert tooltip_widgets
    assert tooltip_widgets[0].width() <= panel._DETAIL_TOOLTIP_WIDTH + 40
    QToolTip.hideText()
    panel.close()


def test_history_operation_names_match_ribbon_analysis_actions() -> None:
    """历史操作名应与分析功能区按钮的中文名一一对应，不残留英文编号。"""
    # buffer/overlay 的功能区编号带 _analysis 后缀，栅格四项两侧编号一致。
    pairs: tuple[tuple[str, str], ...] = (
        ("buffer", "buffer_analysis"),
        ("overlay", "overlay_analysis"),
        ("raster_calculator", "raster_calculator"),
        ("raster_reclassify", "raster_reclassify"),
        ("dem_analysis", "dem_analysis"),
        ("raster_clip", "raster_clip"),
    )

    for algorithm_id, action_id in pairs:
        title: str | None = RibbonBar.action_title(action_id)
        assert title is not None, f"功能区缺少操作 {action_id}"
        assert AnalysisHistoryPanel._OPERATION_NAMES.get(algorithm_id) == title

    # 图层重投影不对应功能区按钮，但也必须有中文显示名。
    assert AnalysisHistoryPanel._OPERATION_NAMES["reproject"] == "图层重投影"
    for name in AnalysisHistoryPanel._OPERATION_NAMES.values():
        assert name and not name.isascii(), f"操作名 {name} 不是中文"


def test_ribbon_analysis_tab_has_single_merged_group() -> None:
    """分析标签页应只保留合并后的空间分析分组，不再有栅格分析分组。"""
    analysis_groups = dict(RibbonBar._tab_specs())["分析"]

    assert tuple(group.title for group in analysis_groups) == ("空间分析", "结果")
    spatial_actions = analysis_groups[0].actions
    assert tuple(action.action_id for action in spatial_actions) == (
        "buffer_analysis",
        "overlay_analysis",
        "raster_calculator",
        "raster_reclassify",
        "dem_analysis",
        "raster_clip",
    )
