"""GIS 工作台主界面测试。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLineEdit, QTabWidget

from app.main_window import MainWindow
from app.widgets.map_canvas import MapCanvas


def test_main_window_uses_map_workbench_shell() -> None:
    """主窗口默认应显示地图 Ribbon、内容面板和地图标签工作区。"""
    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    assert app is not None
    assert window.quick_access_toolbar.objectName() == "quickAccessToolBar"
    assert window.ribbon_tabs.tabText(window.ribbon_tabs.currentIndex()) == "地图"
    assert window.map_tabs.tabText(0) == "地图"
    assert window.layer_panel.findChild(QLineEdit, "layerSearchInput") is not None
    assert window.right_panel.findChild(QTabWidget, "rightPanelTabs") is not None


def test_map_canvas_is_identified_as_the_map_workspace() -> None:
    """地图画布应有稳定对象名，供工作台主题定位。"""
    app = QApplication.instance() or QApplication([])
    canvas = MapCanvas()

    assert app is not None
    assert canvas.objectName() == "mapCanvas"
    assert canvas.scene().sceneRect().width() == 1000
