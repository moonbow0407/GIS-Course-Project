"""目标图层选择对话框测试。"""

import os

# 必须在导入 Qt 前启用无界面平台，测试才能稳定复现原生控件事件。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QListWidget

from app.presentation.widgets.target_layer_dialog import (
    TargetLayerDialog,
    TargetLayerOption,
)


def _make_options() -> tuple[TargetLayerOption, ...]:
    """构造三个候选图层选项。"""
    return (
        TargetLayerOption(
            layer_id="a",
            name="监测点",
            description="点 · 5 个要素 · shp",
        ),
        TargetLayerOption(
            layer_id="b",
            name="巡查路线",
            description="线 · 3 个要素 · geojson",
        ),
        TargetLayerOption(
            layer_id="c",
            name="管理分区",
            description="面 · 2 个要素 · geojson",
        ),
    )


def test_dialog_lists_all_options_with_descriptions() -> None:
    """对话框应列出全部候选图层的名称和描述。"""
    _ = QApplication.instance() or QApplication([])
    dialog: TargetLayerDialog = TargetLayerDialog(
        _make_options(), "点", parent=None
    )

    listing: QListWidget = dialog._list
    assert listing.count() == 3
    assert "监测点（点 · 5 个要素 · shp）" in listing.item(0).text()
    assert "巡查路线（线 · 3 个要素 · geojson）" in listing.item(1).text()
    dialog.close()


def test_dialog_selects_default_layer_when_provided() -> None:
    """提供默认图层编号时应对应项成为当前选中项。"""
    _ = QApplication.instance() or QApplication([])
    dialog: TargetLayerDialog = TargetLayerDialog(
        _make_options(), "点", default_layer_id="b", parent=None
    )

    listing: QListWidget = dialog._list
    assert listing.currentItem().text().startswith("巡查路线")
    assert dialog.selected_layer_id() == "b"
    dialog.close()


def test_dialog_defaults_to_first_option_without_default() -> None:
    """未提供默认图层时默认选中第一项。"""
    _ = QApplication.instance() or QApplication([])
    dialog: TargetLayerDialog = TargetLayerDialog(
        _make_options(), "点", parent=None
    )

    assert dialog.selected_layer_id() == "a"
    dialog.close()


def test_dialog_returns_selected_layer_after_user_choice() -> None:
    """用户切换选中项后应返回对应的图层编号。"""
    _ = QApplication.instance() or QApplication([])
    dialog: TargetLayerDialog = TargetLayerDialog(
        _make_options(), "点", parent=None
    )
    dialog._list.setCurrentRow(2)

    assert dialog.selected_layer_id() == "c"
    dialog.close()
