"""栅格分析输出名称与 GeoTIFF 路径的联动控件辅助。"""

import re
from pathlib import Path

from PySide6.QtWidgets import QLineEdit

_SUPPORTED_SUFFIXES: tuple[str, ...] = (".tif", ".tiff")
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def normalize_output_path(path: Path | str) -> Path:
    """规范化栅格输出路径，缺少扩展名时补充 ``.tif``。"""
    output_path = Path(path).expanduser()
    if output_path.suffix.lower() not in _SUPPORTED_SUFFIXES:
        return output_path.with_suffix(".tif")
    return output_path


def normalize_output_name(name: str) -> str:
    """规范化图层名称，避免用户重复输入 GeoTIFF 扩展名。"""
    normalized = name.strip()
    lower_name = normalized.lower()
    for suffix in _SUPPORTED_SUFFIXES:
        if lower_name.endswith(suffix):
            return normalized[: -len(suffix)].rstrip()
    return normalized


def output_name_error(name: str) -> str | None:
    """返回输出文件名不可用时的中文错误；合法时返回空值。"""
    normalized = normalize_output_name(name)
    if not normalized:
        return "请输入输出文件名。"
    if normalized in {".", ".."}:
        return "输出文件名不能是 . 或 ..。"
    if _INVALID_FILENAME_CHARS.search(normalized):
        return "输出文件名不能包含 \\: / * ? \" < > | 等特殊字符。"
    return None


class RasterOutputNameBinder:
    """维护输出图层名称与 GeoTIFF 文件名的一致性。"""

    def __init__(
        self,
        name_edit: QLineEdit,
        path_edit: QLineEdit,
        initial_path: Path | str,
    ) -> None:
        """绑定名称和路径输入框，并使用初始路径初始化两者。"""
        self._name_edit = name_edit
        self._path_edit = path_edit
        self._updating = False
        self._name_edit.textChanged.connect(self._on_name_changed)
        self.set_path(initial_path)

    @property
    def output_path(self) -> Path:
        """返回当前规范化的 GeoTIFF 输出路径。"""
        return normalize_output_path(self._path_edit.text().strip())

    @property
    def output_name(self) -> str:
        """返回当前不含扩展名的输出图层名称。"""
        return normalize_output_name(self._name_edit.text())

    def set_path(self, path: Path | str) -> None:
        """设置输出路径，并将图层名称同步为文件名主干。"""
        output_path = normalize_output_path(path)
        self._updating = True
        try:
            self._path_edit.setText(str(output_path))
            self._name_edit.setText(output_path.stem)
        finally:
            self._updating = False

    def validation_error(self) -> str | None:
        """返回当前输出名称的校验错误。"""
        return output_name_error(self._name_edit.text())

    def _on_name_changed(self, name: str) -> None:
        """用户修改图层名称时同步输出路径的文件名。"""
        if self._updating:
            return
        normalized_name = normalize_output_name(name)
        if output_name_error(normalized_name) is not None:
            return
        if normalized_name != name:
            self._updating = True
            try:
                self._name_edit.setText(normalized_name)
            finally:
                self._updating = False
        current_path = self.output_path
        self._path_edit.setText(
            str(current_path.with_name(f"{normalized_name}{current_path.suffix}"))
        )
