"""布局视图快捷键一览对话框 —— 突出 Alt / Shift 功能，并列全部快捷键与鼠标操作。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class LayoutShortcutDialog(QDialog):
    """列出布局视图的快捷键。

    把 Alt / Shift 的组合功能放在最顶部、以高亮色块单独成栏重点展示；
    其余键盘快捷键与鼠标操作列在其后。快捷键文本运行时通过
    QKeySequence 解析（StandardKey 因平台而异，例如 Windows 上重做为
    Ctrl+Y），保证与实际绑定一致。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("布局视图快捷键")
        self.setMinimumWidth(460)
        self._create_ui()

    def _create_ui(self) -> None:
        layout: QVBoxLayout = QVBoxLayout(self)
        layout.setSpacing(6)

        # Alt / Shift 功能 —— 最显眼的高亮栏
        highlight: QFrame = QFrame()
        highlight.setStyleSheet(
            "QFrame { background: #eff6ff; border: 1px solid #bfdbfe;"
            " border-radius: 6px; }"
        )
        hl_layout: QVBoxLayout = QVBoxLayout(highlight)
        hl_layout.setContentsMargins(12, 8, 12, 8)
        hl_layout.setSpacing(4)

        title: QLabel = QLabel("Alt / Shift 快捷键")
        title.setStyleSheet(
            "font-weight: bold; color: #1e40af; font-size: 13px;"
        )
        hl_layout.addWidget(title)

        hl_layout.addLayout(self._row(
            "Alt / Ctrl + 点击",
            "在叠压元素间循环选中",
        ))
        hl_layout.addLayout(self._row(
            "Shift + 拖拽",
            "平移地图框内容（点击已选中的地图框后拖拽）",
        ))
        layout.addWidget(highlight)

        layout.addWidget(self._section_title("键盘快捷键"))
        layout.addLayout(self._grid([
            (self._std_key(QKeySequence.StandardKey.Delete), "删除选中的元素"),
            (self._std_key(QKeySequence.StandardKey.Undo), "撤销上一步操作"),
            (self._std_key(QKeySequence.StandardKey.Redo), "重做上一步操作"),
        ]))

        layout.addWidget(self._section_title("鼠标操作"))
        layout.addLayout(self._grid([
            ("左键单击", "选中元素，空白处取消选中"),
            ("左键拖拽", "拖动选中的元素"),
            ("拖拽缩放手柄", "调整选中元素的大小"),
            ("双击元素", "打开属性编辑"),
            ("Ctrl + 滚轮", "缩放整个布局视图"),
            ("滚轮", "缩放地图框内容（选中地图框且指针在框内）"),
            ("中键拖拽", "平移布局视图"),
        ]))

        layout.addStretch()

        button_box: QDialogButtonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Close
        )
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    @staticmethod
    def _std_key(standard_key: QKeySequence.StandardKey) -> str:
        """把标准快捷键解析为平台实际按键文本。"""
        return QKeySequence(standard_key).toString(
            QKeySequence.SequenceFormat.NativeText
        )

    @staticmethod
    def _section_title(text: str) -> QLabel:
        """分组小标题。"""
        label = QLabel(text)
        label.setStyleSheet(
            "font-weight: bold; color: #475569; margin-top: 8px;"
        )
        return label

    @staticmethod
    def _row(key: str, desc: str) -> QGridLayout:
        """单行：按键（高亮小标签）→ 说明。"""
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setColumnStretch(1, 1)
        key_label = QLabel(key)
        key_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        key_label.setStyleSheet(
            "color: #1e40af; background: #ffffff; border: 1px solid #bfdbfe;"
            "border-radius: 4px; padding: 2px 8px;"
            "font-family: Consolas, monospace; font-size: 12px;"
        )
        desc_label = QLabel(desc)
        grid.addWidget(key_label, 0, 0)
        grid.addWidget(desc_label, 0, 1)
        return grid

    @staticmethod
    def _grid(rows: list[tuple[str, str]]) -> QGridLayout:
        """快捷键→说明两列网格，按键用浅蓝圆角标签突出。"""
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        for i, (key, desc) in enumerate(rows):
            key_label = QLabel(key)
            key_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            key_label.setStyleSheet(
                "color: #1e40af; background: #eff6ff; border: 1px solid #bfdbfe;"
                "border-radius: 4px; padding: 2px 8px;"
                "font-family: Consolas, monospace; font-size: 12px;"
            )
            desc_label = QLabel(desc)
            grid.addWidget(key_label, i, 0)
            grid.addWidget(desc_label, i, 1)
        grid.setColumnStretch(1, 1)
        return grid
