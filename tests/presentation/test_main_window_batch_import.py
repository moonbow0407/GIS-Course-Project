"""主窗口批量导入空间数据测试。"""

import os
from pathlib import Path
from types import SimpleNamespace

# 必须在导入 Qt 前启用无界面平台，测试才能在没有显示器的环境运行。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from app.application.errors import UnsupportedVectorFormat
from app.presentation.main_window import MainWindow


def test_open_data_accepts_multiple_files_and_reports_partial_failures(monkeypatch) -> None:
    """多选文件应逐个加载，单项失败不能阻止其余文件加入工作区。"""
    application: QApplication = QApplication.instance() or QApplication([])
    window: MainWindow = MainWindow()
    selected_paths: list[str] = [
        str(Path("roads.geojson")),
        str(Path("broken.xyz")),
        str(Path("elevation.tif")),
    ]
    opened_paths: list[Path] = []
    refresh_count: list[int] = []
    warning_messages: list[tuple[str, str]] = []

    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileNames",
        lambda *args, **kwargs: (selected_paths, "空间数据"),
    )
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("批量导入应使用支持 Ctrl/Shift 多选的文件对话框")
        ),
    )

    def open_data(path: Path, layer_name: str | None = None) -> SimpleNamespace:
        opened_paths.append(path)
        if path.suffix == ".xyz":
            raise UnsupportedVectorFormat(f"不支持的数据格式：{path.suffix}")
        return SimpleNamespace(layer_id=f"fake-{len(opened_paths)}", warning=None)

    monkeypatch.setattr(window._application, "open_data", open_data)
    monkeypatch.setattr(
        window,
        "_refresh_workspace",
        lambda *args, **kwargs: refresh_count.append(1),
    )
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, title, message: warning_messages.append((title, message)),
    )

    window._open_data()
    application.processEvents()

    assert opened_paths == [Path(path) for path in selected_paths]
    assert refresh_count == [1]
    assert window._ready_label.text() == "已加载  2 个数据"
    assert warning_messages == [
        ("部分数据打开失败", "broken.xyz：不支持的数据格式：.xyz")
    ]
    window.close()
