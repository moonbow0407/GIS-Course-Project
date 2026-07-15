"""GIS桌面通用平台入口。"""

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.presentation.main_window import MainWindow


def load_style(app: QApplication) -> None:
    """加载全局 QSS，样式文件缺失时保持默认样式运行。"""
    style_path = Path(__file__).resolve().parent / "app" / "resources" / "styles" / "main.qss"
    if style_path.exists():
        app.setStyleSheet(style_path.read_text(encoding="utf-8"))


def main() -> int:
    """创建主窗口并进入 Qt 事件循环。"""
    # QApplication 统一接收系统事件，整个进程只能创建一个实例。
    app = QApplication(sys.argv)
    app.setApplicationName("GIS桌面通用平台")
    load_style(app)

    window = MainWindow()
    window.show()

    # exec() 会持续处理鼠标、键盘和窗口事件，直到应用退出。
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
