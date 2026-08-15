"""GIS桌面通用平台入口。"""

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.presentation.main_window import MainWindow
from app.presentation.widgets.startup_dialog import (
    StartupDialog,
    save_recent_project,
)


def packaging_smoke_report_path(arguments: list[str]) -> Path | None:
    """解析冻结程序自检命令，普通启动返回空值。"""
    if len(arguments) >= 3 and arguments[1] == "--packaging-smoke-test":
        return Path(arguments[2])
    return None


def load_style(app: QApplication) -> None:
    """加载全局 QSS，样式文件缺失时保持默认样式运行。"""
    style_path = Path(__file__).resolve().parent / "app" / "resources" / "styles" / "main.qss"
    if style_path.exists():
        app.setStyleSheet(style_path.read_text(encoding="utf-8"))


def main() -> int:
    """显示启动对话框，然后创建主窗口并进入 Qt 事件循环。"""
    smoke_report_path: Path | None = packaging_smoke_report_path(sys.argv)
    qt_arguments: list[str] = [sys.argv[0]] if smoke_report_path is not None else sys.argv
    app = QApplication(qt_arguments)
    app.setOrganizationName("GISPlatform")
    app.setApplicationName("GIS桌面通用平台")
    load_style(app)

    if smoke_report_path is not None:
        from app.infrastructure.packaging_smoke import run_packaging_smoke

        # 构造完整主窗口以验证冻结包中的所有界面模块和服务装配；自检不显示窗口。
        smoke_window = MainWindow()
        try:
            return run_packaging_smoke(smoke_report_path)
        finally:
            smoke_window.close()

    # 启动对话框：可选择历史工程 / 浏览 / 新建空白工程。
    startup = StartupDialog()
    if startup.exec() != StartupDialog.DialogCode.Accepted:
        return 0

    if startup.action == "new":
        window = MainWindow()
    elif startup.action == "open" and startup.selected_path is not None:
        if startup.selected_path.exists():
            save_recent_project(startup.selected_path)
        window = MainWindow(project_path=startup.selected_path)
    else:
        return 0

    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
