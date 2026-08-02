"""图层属性表面板 — 可停靠、与地图双向联动。"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.application.results import LayerSnapshot
from app.domain.feature import FeatureId
from app.domain.raster_layer import RasterLayer
from app.domain.vector_layer import VectorLayer


class _SortableTableItem(QTableWidgetItem):
    """支持自动识别数值并按数值排序的表单项。

    Qt 默认对 QTableWidgetItem 使用字符串字典序比较，
    导致 "10" 排在 "2" 前面。本子类覆写 __lt__，
    当两边文本均可解析为数值时使用数值比较，否则回退到字符串比较。
    """

    def __lt__(self, other: QTableWidgetItem) -> bool:
        try:
            return float(self.text()) < float(other.text())
        except (ValueError, TypeError):
            return self.text() < other.text()


class AttributeTablePanel(QWidget):
    """以只读表格展示矢量图层属性，支持双向选择联动和双击缩放。"""

    # 表内选中行变化 → 请求地图同步高亮对应要素。
    selection_changed = Signal(str, tuple)  # (layer_id, feature_ids)
    # 双击行 → 请求地图缩放到该要素范围。
    feature_zoom_requested = Signal(str, object)  # (layer_id, fid)

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建空属性表，等待外部传入图层快照。"""
        super().__init__(parent)
        # 当前展示的图层快照；为空时面板处于空白状态。
        self._layer_snapshot: LayerSnapshot | None = None
        # 外部同步标记：为 True 时跳过 selection_changed 信号，防止反馈循环。
        self._syncing_selection: bool = False

        # 属性表控件。
        self._table: QTableWidget = QTableWidget()
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSortIndicatorShown(True)
        self._table.horizontalHeader().setStretchLastSection(True)
        # 保留垂直滚动条，方便识别并浏览记录。
        self._table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        # 水平表头支持拖拽调整列宽。
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )

        # 表内选中行变化时通知外部。
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        # 双击行触发缩放到要素。
        self._table.cellDoubleClicked.connect(self._on_double_click)

        # 底部状态标签：显示行列统计。
        self._status_label: QLabel = QLabel("暂无属性数据")
        self._status_label.setObjectName("attributeTableStatus")

        layout: QVBoxLayout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._table, 1)
        layout.addWidget(self._status_label)

    def set_layer(self, layer_snapshot: LayerSnapshot | None) -> None:
        """用指定图层快照重新填充属性表。

        参数:
            layer_snapshot: 待展示的图层快照；为空时清空表格。
        """
        self._layer_snapshot = layer_snapshot
        self._table.setSortingEnabled(False)
        self._table.clear()
        self._table.setRowCount(0)
        self._table.setColumnCount(0)

        if layer_snapshot is None:
            self._status_label.setText("暂无属性数据")
            return

        if not isinstance(layer_snapshot.layer, VectorLayer):
            # 栅格图层展示基础元数据。
            self._populate_raster(layer_snapshot)
            self._status_label.setText(f"栅格元数据 · {layer_snapshot.name}")
            return

        self._populate_vector(layer_snapshot)
        self._table.setSortingEnabled(True)
        self._status_label.setText(
            f"共 {len(layer_snapshot.layer.features)} 行 · "
            f"{self._table.columnCount() - 1} 个字段 · "
            f"{layer_snapshot.name}"
        )

    @property
    def layer_id(self) -> str | None:
        """返回面板当前展示的图层编号；未设置时返回空值。"""
        return self._layer_snapshot.layer_id if self._layer_snapshot is not None else None

    def highlight_features(self, feature_ids: set[FeatureId]) -> None:
        """从地图侧同步高亮指定要素对应的表格行。

        参数:
            feature_ids: 需要高亮的要素编号集合；为空时取消全部选中。
        """
        self._syncing_selection = True
        try:
            self._table.clearSelection()
            if not feature_ids:
                return
            row: int
            for row in range(self._table.rowCount()):
                fid_item: QTableWidgetItem | None = self._table.item(row, 0)
                if fid_item is None:
                    continue
                fid_value = fid_item.data(Qt.ItemDataRole.UserRole)
                if fid_value is None:
                    fid_value = fid_item.text()
                if fid_value in feature_ids:
                    self._table.selectRow(row)
        finally:
            self._syncing_selection = False

    # ── 内部方法 ────────────────────────────────────────────────

    def _populate_vector(self, layer_snapshot: LayerSnapshot) -> None:
        """根据矢量要素的字段填充表格。"""
        layer = layer_snapshot.layer
        if not isinstance(layer, VectorLayer):
            return
        # 合并全部要素的字段名，保留首次出现顺序。
        fields: list[str] = []
        for feature in layer.features:
            for field_name in feature.attributes:
                if field_name not in fields:
                    fields.append(field_name)

        self._table.setColumnCount(len(fields) + 1)
        self._table.setHorizontalHeaderLabels(["FID", *fields])
        self._table.setRowCount(len(layer.features))

        for row_index, current_feature in enumerate(layer.features):
            # FID 列：存储原始要素编号至 UserRole，保持排序后的追踪能力。
            fid_item: _SortableTableItem = _SortableTableItem(
                str(current_feature.fid)
            )
            fid_item.setData(Qt.ItemDataRole.UserRole, current_feature.fid)
            fid_item.setFlags(fid_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row_index, 0, fid_item)

            for column_index, current_field in enumerate(fields, start=1):
                value: object = current_feature.attributes.get(current_field, "")
                item: _SortableTableItem = _SortableTableItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row_index, column_index, item)

        self._table.resizeColumnsToContents()

    def _populate_raster(self, layer_snapshot: LayerSnapshot) -> None:
        """为栅格图层展示基本影像元数据。"""
        raster_layer = layer_snapshot.layer
        if not isinstance(raster_layer, RasterLayer):
            return
        self._table.setColumnCount(2)
        self._table.setHorizontalHeaderLabels(["属性", "值"])
        self._table.setRowCount(3)

        def _add_row(row: int, label: str, value: str) -> None:
            label_item: _SortableTableItem = _SortableTableItem(label)
            label_item.setFlags(label_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            value_item: _SortableTableItem = _SortableTableItem(value)
            value_item.setFlags(value_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(row, 0, label_item)
            self._table.setItem(row, 1, value_item)

        _add_row(0, "波段数", str(raster_layer.band_count))
        image_shape: tuple[int, ...] = raster_layer.image_data.shape
        _add_row(1, "像素尺寸", f"{image_shape[1]} × {image_shape[0]}")
        crs_str: str = (
            raster_layer.crs.to_string() if raster_layer.crs is not None else "未定义"
        )
        _add_row(2, "坐标系", crs_str)
        self._table.resizeColumnsToContents()

    def _on_selection_changed(self) -> None:
        """表内选中行变化时通知外部同步地图选择。"""
        if self._syncing_selection or self._layer_snapshot is None:
            return
        if not isinstance(self._layer_snapshot.layer, VectorLayer):
            return
        selected_fids: list[FeatureId] = []
        for row in self._table.selectionModel().selectedRows():
            fid_item: QTableWidgetItem | None = self._table.item(row.row(), 0)
            if fid_item is None:
                continue
            fid_value = fid_item.data(Qt.ItemDataRole.UserRole)
            if fid_value is None:
                fid_value = fid_item.text()
            selected_fids.append(fid_value)
        self.selection_changed.emit(
            self._layer_snapshot.layer_id, tuple(selected_fids)
        )

    def _on_double_click(self, row: int, column: int) -> None:
        """双击行请求地图缩放到对应要素范围。"""
        if self._layer_snapshot is None:
            return
        if not isinstance(self._layer_snapshot.layer, VectorLayer):
            return
        fid_item: QTableWidgetItem | None = self._table.item(row, 0)
        if fid_item is None:
            return
        fid_value = fid_item.data(Qt.ItemDataRole.UserRole)
        if fid_value is None:
            fid_value = fid_item.text()
        self.feature_zoom_requested.emit(
            self._layer_snapshot.layer_id, fid_value
        )
