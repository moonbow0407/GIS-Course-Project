"""分析历史面板的展示和交互测试。"""

import os

# 必须在导入 Qt 前启用无界面平台，测试才能在 CI 中创建原生控件。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from app.application.project_models import AnalysisOutputReference, AnalysisRun
from app.presentation.main_window import MainWindow
from app.presentation.widgets.analysis_history_panel import AnalysisHistoryPanel
from app.presentation.widgets.ribbon_bar import RibbonBar


def make_run(
    run_id: str,
    created_at: str,
    status: str = "completed",
) -> AnalysisRun:
    """创建一条包含缓冲距离和输出引用的测试记录。"""
    return AnalysisRun(
        run_id=run_id,
        algorithm_id="buffer",
        input_layer_ids=("roads",),
        parameters={
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


def test_main_window_toggles_analysis_history_dock() -> None:
    """连续点击分析记录入口应在显示和隐藏之间切换。"""
    qt_application: QApplication = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    qt_application.processEvents()

    assert not window._analysis_history_dock.isVisible()
    window._toggle_analysis_history()
    qt_application.processEvents()
    assert window._analysis_history_dock.isVisible()
    window._toggle_analysis_history()
    qt_application.processEvents()
    assert not window._analysis_history_dock.isVisible()
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
