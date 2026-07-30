"""主窗口数据库功能区动作路由测试。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.presentation.main_window import MainWindow


def test_database_ribbon_actions_are_routed_to_real_handlers(monkeypatch) -> None:
    """数据库四个入口不应再落入“接口已预留”分支。"""
    application: QApplication = QApplication.instance() or QApplication([])
    window: MainWindow = MainWindow()
    actions: list[str] = []
    monkeypatch.setattr(window, "_connect_database", lambda: actions.append("connect"))
    monkeypatch.setattr(window, "_disconnect_database", lambda: actions.append("disconnect"))
    monkeypatch.setattr(window, "_import_database", lambda: actions.append("import"))
    monkeypatch.setattr(window, "_load_database", lambda: actions.append("load"))
    monkeypatch.setattr(window, "_database_manager", lambda: actions.append("manager"))

    for action_id in (
        "connect_database",
        "disconnect_database",
        "import_database",
        "load_database",
        "database_manager",
    ):
        window._handle_action(action_id)

    assert actions == ["connect", "disconnect", "import", "load", "manager"]
    assert window._application.database_service is not None
    window.close()
    assert application is not None
