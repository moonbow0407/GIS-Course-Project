"""GIS桌面通用平台入口。"""

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

# 必须抢在任何 GIS 库导入之前隔离 PROJ 数据目录：本机 PostGIS 写入的机器级
# PROJ_LIB 指向旧版 proj.db，会让 rasterio/pyproj 内置 PROJ 的 EPSG 查询失败。
from app.infrastructure.proj_environment import configure_proj_environment

configure_proj_environment()

from app.presentation.main_window import MainWindow  # noqa: E402
from app.presentation.widgets.startup_dialog import (  # noqa: E402
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
        style_text: str = style_path.read_text(encoding="utf-8")
        # QSS 中的相对 url() 按进程工作目录解析，file:/// 形式又不会被
        # background-image 采纳；替换为样式目录的绝对正斜杠路径，保证从
        # 任意目录启动（含冻结包）都能找到指示块图片。
        style_text = style_text.replace("__STYLE_DIR__", style_path.parent.as_posix())
        app.setStyleSheet(style_text)


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
