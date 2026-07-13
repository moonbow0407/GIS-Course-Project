"""GIS桌面通用平台入口。"""

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow


def load_style(app: QApplication) -> None:
    """加载全局 QSS，样式文件缺失时保持默认样式运行。"""
    style_path = Path(__file__).resolve().parent / "app" / "resources" / "styles" / "main.qss"
    if style_path.exists():
        app.setStyleSheet(style_path.read_text(encoding="utf-8"))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("GIS桌面通用平台")
    load_style(app)

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
