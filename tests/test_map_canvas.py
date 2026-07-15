"""地图画布初始状态测试。"""

import os

# 必须在导入 Qt 前启用无界面平台，测试才能在没有显示器的环境运行。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.presentation.widgets.map_canvas import MapCanvas


def test_canvas_starts_without_mock_map_items() -> None:
    """未加载数据时，画布不应包含演示底图要素。"""
    application = QApplication.instance() or QApplication([])
    canvas = MapCanvas()

    assert application is not None
    assert canvas.scene().items() == []
    assert canvas.scene().items() == []
