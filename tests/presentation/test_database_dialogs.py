"""数据库连接和图层目录对话框测试。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.application.database_models import DatabaseConnectionConfig, DatabaseLayerInfo
from app.presentation.widgets.database_dialogs import (
    DatabaseConnectionDialog,
    DatabaseLayerDialog,
)


def test_database_connection_dialog_builds_runtime_only_config() -> None:
    """连接对话框应将字段转换为配置对象，密码只存在配置内存对象中。"""
    application: QApplication = QApplication.instance() or QApplication([])
    dialog: DatabaseConnectionDialog = DatabaseConnectionDialog()
    dialog._database_edit.setText("gis")
    dialog._username_edit.setText("tester")
    dialog._password_edit.setText("secret")
    dialog._accept_config()

    assert dialog.result() == dialog.DialogCode.Accepted
    config: DatabaseConnectionConfig = dialog.config()
    assert config.database == "gis"
    assert config.password == "secret"
    dialog.close()
    assert application is not None


def test_database_layer_dialog_returns_selected_database_id() -> None:
    """图层目录对话框应以数据库 ID 而不是名称作为选择结果。"""
    application: QApplication = QApplication.instance() or QApplication([])
    dialog: DatabaseLayerDialog = DatabaseLayerDialog(
        (
            DatabaseLayerInfo(3, "道路", "line", "EPSG:4326", 4326, 12, None),
            DatabaseLayerInfo(9, "道路", "line", "EPSG:3857", 3857, 4, None),
        )
    )
    dialog._table.selectRow(1)

    assert dialog.selected_layer_id() == 9
    dialog.close()
    assert application is not None
