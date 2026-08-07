"""工程分析历史面板。"""

from collections.abc import Mapping, Sequence
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.application.project_models import AnalysisRun


class AnalysisHistoryPanel(QWidget):
    """展示空间分析执行历史，并提供记录详情和清除入口。"""

    clear_requested = Signal()

    _OPERATION_NAMES: dict[str, str] = {
        "buffer": "缓冲区分析",
        "overlay": "叠加分析",
    }
    _PARAMETER_NAMES: dict[str, str] = {
        "geometry_family": "几何类型",
        "distance": "缓冲距离",
        "distance_unit": "距离单位",
        "distance_meters": "距离（米）",
        "side_type": "缓冲侧类型",
        "segments": "圆弧分段数",
        "cap_style": "端点样式",
        "join_style": "连接样式",
        "mitre_limit": "斜接比",
        "dissolve": "融合结果",
        "analysis_crs": "指定分析 CRS",
        "calculation_crs": "计算 CRS",
        "output_path": "输出位置",
        "output_layer_name": "输出图层名",
        "operation": "叠加类型",
        "operation_label": "叠加类型",
        "input_geometry_family": "输入几何类型",
        "overlay_geometry_family": "叠加几何类型",
        "keep_geom_type": "保持几何类型",
        "make_valid": "自动修复几何",
        "sjoin_predicate": "空间连接谓词",
        "sjoin_how": "连接方式",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建空的分析历史列表。"""
        super().__init__(parent)
        self.setObjectName("analysisHistoryPanel")
        self._layer_names: dict[str, str] = {}
        self._runs: dict[str, AnalysisRun] = {}

        hint: QLabel = QLabel("空间分析历史")
        hint.setObjectName("analysisHistorySubtitle")
        self._clear_button: QPushButton = QPushButton("清除")
        self._clear_button.setObjectName("analysisHistoryClear")
        self._clear_button.setToolTip("清除历史记录，保留分析结果图层和文件")

        header_layout: QHBoxLayout = QHBoxLayout()
        header_layout.setContentsMargins(12, 10, 12, 6)
        header_layout.addWidget(hint)
        header_layout.addStretch(1)
        header_layout.addWidget(self._clear_button)

        self._history_list: QListWidget = QListWidget()
        self._history_list.setObjectName("analysisHistoryList")
        self._history_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._history_list.setMouseTracking(True)
        self._history_list.setToolTip("悬停查看操作详情")

        layout: QVBoxLayout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(header_layout)
        layout.addWidget(self._history_list)

        self._clear_button.clicked.connect(self.clear_requested.emit)

    def set_history(
        self,
        runs: Sequence[AnalysisRun],
        layer_names: Mapping[str, str],
    ) -> None:
        """刷新历史列表，最新执行记录显示在最上方。"""
        selected_run_id: str | None = self._selected_run_id()
        self._layer_names = dict(layer_names)
        ordered_runs: tuple[AnalysisRun, ...] = tuple(
            sorted(runs, key=lambda run: run.created_at, reverse=True)
        )
        self._runs = {run.run_id: run for run in ordered_runs}

        self._history_list.blockSignals(True)
        self._history_list.clear()
        for run in ordered_runs:
            # 列表项使用自定义行承载状态图标和名称，清空原生文本避免 Qt
            # 同时绘制 item 文本与自定义控件而产生重影。
            item: QListWidgetItem = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, run.run_id)
            item.setToolTip(self._format_detail(run))
            row: QWidget = self._create_history_row(run)
            row.setToolTip(self._format_detail(run))
            self._history_list.addItem(item)
            self._history_list.setItemWidget(item, row)
            item.setSizeHint(row.sizeHint())
        self._history_list.blockSignals(False)

        if not ordered_runs:
            return
        restored_item: QListWidgetItem | None = self._find_item(selected_run_id)
        self._history_list.setCurrentItem(restored_item or self._history_list.item(0))

    def _create_history_row(self, run: AnalysisRun) -> QWidget:
        """创建只显示状态和操作名称的历史列表项。"""
        row: QWidget = QWidget()
        row.setObjectName("analysisHistoryRow")
        row_layout: QHBoxLayout = QHBoxLayout(row)
        row_layout.setContentsMargins(8, 0, 8, 0)
        row_layout.setSpacing(8)

        status: QLabel = QLabel("✓" if run.status == "completed" else "!")
        status.setObjectName("analysisHistoryStatus")
        status.setProperty("status", run.status)
        status.setAlignment(Qt.AlignmentFlag.AlignCenter)

        operation: QLabel = QLabel(self._operation_name(run))
        operation.setObjectName("analysisHistoryOperation")
        operation.setToolTip(self._format_detail(run))

        row_layout.addWidget(status)
        row_layout.addWidget(operation)
        row_layout.addStretch(1)
        return row

    def _selected_run_id(self) -> str | None:
        """返回刷新前当前选中的记录编号。"""
        item: QListWidgetItem | None = self._history_list.currentItem()
        if item is None:
            return None
        run_id: object = item.data(Qt.ItemDataRole.UserRole)
        return run_id if isinstance(run_id, str) else None

    def _find_item(self, run_id: str | None) -> QListWidgetItem | None:
        """按记录编号查找列表项。"""
        if run_id is None:
            return None
        for index in range(self._history_list.count()):
            item: QListWidgetItem | None = self._history_list.item(index)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == run_id:
                return item
        return None

    def _format_detail(self, run: AnalysisRun) -> str:
        """生成悬停时显示的操作详情。"""
        input_names: str = self._format_layer_names(run.input_layer_ids)
        output_names: str = self._format_output_names(run)
        duration: str = (
            f"{run.duration_seconds:.3f} 秒"
            if run.duration_seconds is not None
            else "未记录"
        )
        lines: list[str] = [
            f"操作名称：{self._operation_name(run)}",
            f"状态：{self._status_name(run.status)}",
            f"开始时间：{self._format_time(run.created_at)}",
            f"完成时间：{self._format_time(run.completed_at) if run.completed_at else '未完成'}",
            f"耗时：{duration}",
            f"输入图层：{input_names}",
            f"输出结果：{output_names}",
        ]
        if run.message:
            lines.extend(("", f"消息：{run.message}"))
        if run.parameters:
            lines.append("")
            lines.append("参数：")
            lines.extend(
                f"  {self._PARAMETER_NAMES.get(key, key)}：{self._format_value(value)}"
                for key, value in run.parameters.items()
            )
        return "\n".join(lines)

    def _format_layer_names(self, layer_ids: Sequence[str]) -> str:
        """把图层编号转换为用户可读名称。"""
        if not layer_ids:
            return "无"
        return "、".join(self._layer_names.get(layer_id, layer_id) for layer_id in layer_ids)

    def _format_output_names(self, run: AnalysisRun) -> str:
        """格式化分析结果图层和结果引用。"""
        if not run.outputs:
            return "无"
        output_names: list[str] = []
        for output in run.outputs:
            name: str = self._layer_names.get(
                output.layer_id,
                output.source_layer_name or output.layer_id,
            )
            output_names.append(f"{name}（{output.source_path}）")
        return "、".join(output_names)

    def _operation_name(self, run: AnalysisRun) -> str:
        """将稳定算法编号转换为中文操作名称。"""
        return self._OPERATION_NAMES.get(run.algorithm_id, run.algorithm_id)

    @staticmethod
    def _status_name(status: str) -> str:
        """将历史状态转换为界面文字。"""
        return {
            "completed": "成功",
            "failed": "失败",
            "stale": "可能过期",
        }.get(status, status)

    @staticmethod
    def _format_time(value: str | None) -> str:
        """将 ISO 时间转换为本地短时间，无法解析时保留原值。"""
        if value is None:
            return "未记录"
        try:
            return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return value

    @staticmethod
    def _format_value(value: object) -> str:
        """格式化历史参数中的布尔值、空值和普通值。"""
        if value is None:
            return "未设置"
        if isinstance(value, bool):
            return "是" if value else "否"
        return str(value)
