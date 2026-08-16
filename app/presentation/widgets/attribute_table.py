"""图层属性表面板 — 可停靠、与地图双向联动。"""

from PySide6.QtCore import (
    QAbstractTableModel,
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
    Signal,
)
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.application.results import LayerSnapshot
from app.domain.feature import Feature, FeatureId
from app.domain.raster_layer import RasterLayer
from app.domain.vector_layer import VectorLayer

# 估算列宽时采样的行数：足以代表字段取值宽度，避免全表测量。
_COLUMN_SAMPLE_ROWS: int = 200
_MIN_COLUMN_WIDTH: int = 60
_MAX_COLUMN_WIDTH: int = 320
_COLUMN_WIDTH_PADDING: int = 18
# 查型接口的默认父索引：Qt 惯例是 QModelIndex() 默认值，模块级单例
# 既满足不可变共享，又避免在参数默认值里执行函数调用。
_EMPTY_MODEL_INDEX: QModelIndex = QModelIndex()


class _SortableValue:
    """数值优先、回退字符串序的比较键。

    与旧版 QTableWidgetItem 子类保持一致：两侧文本都能解析为数值时按
    数值比较，否则按字符串比较，避免 "10" 排在 "2" 之前。
    """

    __slots__ = ("_text", "_number", "_is_numeric")

    def __init__(self, text: str) -> None:
        """缓存文本与可选数值，排序时不再重复解析。"""
        self._text: str = text
        try:
            self._number: float = float(text)
            self._is_numeric: bool = True
        except (TypeError, ValueError):
            self._number = 0.0
            self._is_numeric = False

    def __lt__(self, other: object) -> bool:
        """数值可比时用数值序，否则回退字符串字典序。"""
        if not isinstance(other, _SortableValue):
            return NotImplemented
        if self._is_numeric and other._is_numeric:
            return self._number < other._number
        return self._text < other._text


class AttributeTableModel(QAbstractTableModel):
    """矢量属性表与栅格元数据的按需取值模型。

    单元格文本在 data() 中按需从领域要素读取，不为任何单元格创建
    窗口部件；行序只是要素下标的一个可排序列表，切换图层或刷新时
    通过模型重置完成，成本与要素数量呈线性且无对象分配放大。
    """

    def __init__(self, parent: QObject | None = None) -> None:
        """创建空模型，等待外部注入矢量或文本内容。"""
        super().__init__(parent)
        self._is_vector: bool = False
        self._features: tuple[Feature, ...] = ()
        self._fields: tuple[str, ...] = ()
        self._headers: tuple[str, ...] = ()
        self._text_rows: tuple[tuple[str, ...], ...] = ()
        # 显示行 → 要素下标的映射；排序只调整该列表，不移动要素。
        self._order: list[int] = []
        self._fid_rows: dict[FeatureId, int] = {}

    # ── 内容注入 ────────────────────────────────────────────

    def set_vector_content(
        self,
        features: tuple[Feature, ...],
        fields: tuple[str, ...],
    ) -> None:
        """以矢量要素集合作为表格内容。

        状态变化:
            重置模型并恢复要素自然顺序，等待用户再次点击表头排序。
        """
        self.beginResetModel()
        self._is_vector = True
        self._features = features
        self._fields = fields
        self._headers = ("FID", *fields)
        self._text_rows = ()
        self._order = list(range(len(features)))
        self._fid_rows = {
            self._features[index].fid: position
            for position, index in enumerate(self._order)
        }
        self.endResetModel()

    def set_text_content(
        self,
        headers: tuple[str, ...],
        rows: tuple[tuple[str, ...], ...],
    ) -> None:
        """以静态文本行作为表格内容，用于栅格元数据等小表。"""
        self.beginResetModel()
        self._is_vector = False
        self._headers = headers
        self._text_rows = rows
        self._features = ()
        self._fields = ()
        self._order = []
        self._fid_rows = {}
        self.endResetModel()

    def clear_content(self) -> None:
        """清空全部内容。"""
        self.set_text_content((), ())

    # ── 查型接口实现 ────────────────────────────────────────

    def rowCount(
        self,
        parent: QModelIndex | QPersistentModelIndex = _EMPTY_MODEL_INDEX,
    ) -> int:
        """返回显示行数；矢量行数由排序后的行序决定。"""
        if parent.isValid():
            return 0
        if self._is_vector:
            return len(self._order)
        return len(self._text_rows)

    def columnCount(
        self,
        parent: QModelIndex | QPersistentModelIndex = _EMPTY_MODEL_INDEX,
    ) -> int:
        """返回列数（矢量内容含 FID 列）。"""
        if parent.isValid():
            return 0
        return len(self._headers)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """按需返回单元格文本；FID 列在 UserRole 携带原始要素编号。"""
        if not index.isValid():
            return None
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.UserRole):
            return None
        row: int = index.row()
        column: int = index.column()
        if self._is_vector:
            feature: Feature = self._features[self._order[row]]
            if column == 0:
                if role == Qt.ItemDataRole.UserRole:
                    return feature.fid
                return str(feature.fid)
            if role == Qt.ItemDataRole.UserRole:
                return None
            return str(feature.attributes.get(self._fields[column - 1], ""))
        if role == Qt.ItemDataRole.UserRole:
            return None
        return self._text_rows[row][column]

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """返回表头文本；垂直表头显示从 1 开始的行号。"""
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self._headers):
                return self._headers[section]
            return None
        return section + 1

    def sort(
        self,
        column: int,
        order: Qt.SortOrder = Qt.SortOrder.AscendingOrder,
    ) -> None:
        """按列对行序排序，保持数值优先的旧排序语义。

        用模型重置代替 layout 变更通知：重排后的持久索引无需逐个迁移，
        视图整体重建即可，代价与可见行数成正比。非矢量内容不支持排序。
        """
        if not self._is_vector or column < 0:
            return
        self.beginResetModel()
        self._order.sort(
            key=lambda feature_index: _SortableValue(
                self._cell_text(feature_index, column)
            ),
            reverse=order == Qt.SortOrder.DescendingOrder,
        )
        self._fid_rows = {
            self._features[feature_index].fid: position
            for position, feature_index in enumerate(self._order)
        }
        self.endResetModel()

    # ── 面板辅助 ────────────────────────────────────────────

    def feature_at_row(self, row: int) -> Feature | None:
        """返回显示行对应的要素；行号越界或非矢量内容返回 None。"""
        if not self._is_vector or not 0 <= row < len(self._order):
            return None
        return self._features[self._order[row]]

    def row_for_fid(self, fid: FeatureId) -> int | None:
        """返回要素编号当前所在的显示行；不存在返回 None。"""
        return self._fid_rows.get(fid)

    def _cell_text(self, feature_index: int, column: int) -> str:
        """返回排序用的单元格文本。"""
        feature: Feature = self._features[feature_index]
        if column == 0:
            return str(feature.fid)
        return str(feature.attributes.get(self._fields[column - 1], ""))


class AttributeTablePanel(QWidget):
    """在地图下方展示属性，并提供查询及要素增删改入口。"""

    # 表内选中行变化 → 请求地图同步高亮对应要素。
    selection_changed = Signal(str, tuple)  # (layer_id, feature_ids)
    # 双击行 → 请求地图缩放到该要素范围。
    feature_zoom_requested = Signal(str, object)  # (layer_id, fid)
    # 工具栏 → 请求主窗口调用应用层属性查询。
    query_requested = Signal(str)  # layer_id
    # 工具栏 → 请求主窗口启动当前图层的几何数字化新增。
    add_feature_requested = Signal(str)  # layer_id
    # 工具栏 → 请求主窗口编辑单个选中要素的属性。
    edit_feature_requested = Signal(str, object)  # (layer_id, fid)
    # 工具栏 → 请求主窗口删除当前选中的要素。
    delete_features_requested = Signal(str, tuple)  # (layer_id, fids)
    # 工具栏 → 请求主窗口关闭底部属性表。
    close_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建空属性表，等待外部传入图层快照。"""
        super().__init__(parent)
        self.setObjectName("attributeTablePanel")
        # 当前展示的图层快照；为空时面板处于空白状态。
        self._layer_snapshot: LayerSnapshot | None = None
        # 外部同步标记：为 True 时跳过 selection_changed 信号，防止反馈循环。
        self._syncing_selection: bool = False

        # 顶部工具栏：保持操作入口始终贴近属性表，避免用户必须回到功能区。
        self._layer_label: QLabel = QLabel("未选择图层")
        self._layer_label.setObjectName("attributeTableLayerLabel")
        self._query_button: QPushButton = QPushButton("按属性查询")
        self._query_button.setObjectName("attributeTableQueryButton")
        self._add_button: QPushButton = QPushButton("新增")
        self._add_button.setObjectName("attributeTableAddButton")
        self._edit_button: QPushButton = QPushButton("编辑")
        self._edit_button.setObjectName("attributeTableEditButton")
        self._delete_button: QPushButton = QPushButton("删除")
        self._delete_button.setObjectName("attributeTableDeleteButton")
        self._close_button: QPushButton = QPushButton("关闭")
        self._close_button.setObjectName("attributeTableCloseButton")

        toolbar_layout: QHBoxLayout = QHBoxLayout()
        toolbar_layout.setContentsMargins(10, 8, 10, 6)
        toolbar_layout.setSpacing(6)
        toolbar_layout.addWidget(self._layer_label, 1)
        toolbar_layout.addWidget(self._query_button)
        toolbar_layout.addWidget(self._add_button)
        toolbar_layout.addWidget(self._edit_button)
        toolbar_layout.addWidget(self._delete_button)
        toolbar_layout.addWidget(self._close_button)

        # 属性表控件：model/view 按需取值，几十万行也不创建单元格部件。
        self._model: AttributeTableModel = AttributeTableModel(self)
        self._table: QTableView = QTableView()
        self._table.setObjectName("attributeTable")
        self._table.setModel(self._model)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setSortingEnabled(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSortIndicatorShown(True)
        self._table.horizontalHeader().setStretchLastSection(False)
        # 保留垂直滚动条，方便识别并浏览记录。
        self._table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        # 字段超出面板宽度时显示可拖动的横向滚动条。
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        # 水平表头支持拖拽调整列宽。
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )

        # 表内选中行变化时通知外部。
        self._table.selectionModel().selectionChanged.connect(
            self._on_selection_changed
        )
        # 双击行触发缩放到要素。
        self._table.doubleClicked.connect(self._on_double_click)

        # 底部状态标签：显示行列统计。
        self._status_label: QLabel = QLabel("暂无属性数据")
        self._status_label.setObjectName("attributeTableStatus")

        layout: QVBoxLayout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(toolbar_layout)
        layout.addWidget(self._table, 1)
        layout.addWidget(self._status_label)

        self._query_button.clicked.connect(self._emit_query_request)
        self._add_button.clicked.connect(self._emit_add_request)
        self._edit_button.clicked.connect(self._emit_edit_request)
        self._delete_button.clicked.connect(self._emit_delete_request)
        self._close_button.clicked.connect(self.close_requested.emit)
        self._update_action_state()

    def set_layer(self, layer_snapshot: LayerSnapshot | None) -> None:
        """用指定图层快照重新填充属性表。

        参数:
            layer_snapshot: 待展示的图层快照；为空时清空表格。
        """
        self._layer_snapshot = layer_snapshot

        if layer_snapshot is None:
            self._model.clear_content()
            self._layer_label.setText("未选择图层")
            self._status_label.setText("暂无属性数据")
            self._update_action_state()
            return

        self._layer_label.setText(layer_snapshot.name)
        if not isinstance(layer_snapshot.layer, VectorLayer):
            # 栅格图层展示基础元数据。
            self._populate_raster(layer_snapshot)
            self._status_label.setText(f"栅格元数据 · {layer_snapshot.name}")
            self._update_action_state()
            return

        self._populate_vector(layer_snapshot)
        self._status_label.setText(
            f"共 {len(layer_snapshot.layer.features)} 行 · "
            f"{self._model.columnCount() - 1} 个字段 · "
            f"{layer_snapshot.name}"
        )
        self._update_action_state()

    @property
    def layer_id(self) -> str | None:
        """返回面板当前展示的图层编号；未设置时返回空值。"""
        return self._layer_snapshot.layer_id if self._layer_snapshot is not None else None

    def selected_feature_ids(self) -> tuple[FeatureId, ...]:
        """返回当前属性表选中的要素编号。"""
        selected_fids: list[FeatureId] = []
        for row_index in self._table.selectionModel().selectedRows():
            fid_value = row_index.siblingAtColumn(0).data(Qt.ItemDataRole.UserRole)
            if fid_value is None:
                fid_value = row_index.siblingAtColumn(0).data(
                    Qt.ItemDataRole.DisplayRole
                )
            selected_fids.append(fid_value)
        return tuple(selected_fids)

    def refresh_layer(self, layer_snapshot: LayerSnapshot | None) -> None:
        """刷新表格内容并保留当前选择，供属性编辑后更新显示。

        模型重置不创建任何单元格部件，大图层上的刷新成本可忽略。
        """
        selected_fids: tuple[FeatureId, ...] = self.selected_feature_ids()
        self._syncing_selection = True
        try:
            self.set_layer(layer_snapshot)
            self.highlight_features(set(selected_fids))
        finally:
            self._syncing_selection = False

    def highlight_features(self, feature_ids: set[FeatureId]) -> None:
        """从地图侧同步高亮指定要素对应的表格行。

        参数:
            feature_ids: 需要高亮的要素编号集合；为空时取消全部选中。
        """
        self._syncing_selection = True
        try:
            self._table.clearSelection()
            if not feature_ids or not self._model.rowCount():
                return
            # 行号直接来自模型的 fid 映射，避免逐行扫描和逐行 selectRow；
            # 按行号升序构建选择，保持选中编号随行序返回的旧语义。
            mapped_rows: list[int] = sorted(
                row
                for row in (
                    self._model.row_for_fid(fid) for fid in feature_ids
                )
                if row is not None
            )
            selection: QItemSelection = QItemSelection()
            last_column: int = max(self._model.columnCount() - 1, 0)
            for row in mapped_rows:
                selection.select(
                    self._model.index(row, 0),
                    self._model.index(row, last_column),
                )
            if not selection.isEmpty():
                self._table.selectionModel().select(
                    selection,
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Clear
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
        finally:
            self._syncing_selection = False
            self._update_action_state()

    # ── 内部方法 ────────────────────────────────────────────

    def _populate_vector(self, layer_snapshot: LayerSnapshot) -> None:
        """把矢量要素集合注入模型并按采样估算列宽。"""
        layer = layer_snapshot.layer
        if not isinstance(layer, VectorLayer):
            return
        # 字段合并用字典保序去重，避免大图层上的 O(行数×字段数) 查找。
        merged_fields: dict[str, None] = {}
        for feature in layer.features:
            merged_fields.update(dict.fromkeys(feature.attributes))
        self._model.set_vector_content(layer.features, tuple(merged_fields))
        # 切换图层后回到自然行序，与旧表格清空重建的行为一致。
        self._table.horizontalHeader().setSortIndicator(
            -1, Qt.SortOrder.AscendingOrder
        )
        self._size_columns_to_sample()

    def _populate_raster(self, layer_snapshot: LayerSnapshot) -> None:
        """为栅格图层展示基本影像元数据。"""
        raster_layer = layer_snapshot.layer
        if not isinstance(raster_layer, RasterLayer):
            return
        image_shape: tuple[int, ...] = raster_layer.image_data.shape
        crs_str: str = (
            raster_layer.crs.to_string() if raster_layer.crs is not None else "未定义"
        )
        self._model.set_text_content(
            ("属性", "值"),
            (
                ("波段数", str(raster_layer.band_count)),
                ("像素尺寸", f"{image_shape[1]} × {image_shape[0]}"),
                ("坐标系", crs_str),
            ),
        )
        self._size_columns_to_sample()

    def _size_columns_to_sample(self) -> None:
        """按表头和采样行估算列宽，避免全表测量阻塞大图层打开。"""
        metrics: QFontMetrics = QFontMetrics(self._table.font())
        row_count: int = self._model.rowCount()
        sample_rows: int = min(row_count, _COLUMN_SAMPLE_ROWS)
        for column in range(self._model.columnCount()):
            header_text = self._model.headerData(
                column, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole
            )
            width: int = metrics.horizontalAdvance(str(header_text))
            for row in range(sample_rows):
                cell_text = self._model.index(row, column).data(
                    Qt.ItemDataRole.DisplayRole
                )
                width = max(width, metrics.horizontalAdvance(str(cell_text)))
            self._table.setColumnWidth(
                column,
                max(
                    _MIN_COLUMN_WIDTH,
                    min(width + _COLUMN_WIDTH_PADDING, _MAX_COLUMN_WIDTH),
                ),
            )

    def _on_selection_changed(self) -> None:
        """表内选中行变化时通知外部同步地图选择。"""
        if self._syncing_selection or self._layer_snapshot is None:
            return
        if not isinstance(self._layer_snapshot.layer, VectorLayer):
            return
        self.selection_changed.emit(
            self._layer_snapshot.layer_id, self.selected_feature_ids()
        )
        self._update_action_state()

    def _on_double_click(self, index: QModelIndex) -> None:
        """双击行请求地图缩放到对应要素范围。"""
        if self._layer_snapshot is None:
            return
        if not isinstance(self._layer_snapshot.layer, VectorLayer):
            return
        fid_value = index.siblingAtColumn(0).data(Qt.ItemDataRole.UserRole)
        if fid_value is None:
            fid_value = index.siblingAtColumn(0).data(Qt.ItemDataRole.DisplayRole)
        self.feature_zoom_requested.emit(
            self._layer_snapshot.layer_id, fid_value
        )

    def _emit_query_request(self) -> None:
        """发出当前图层属性查询请求。"""
        if self._layer_snapshot is not None and isinstance(
            self._layer_snapshot.layer, VectorLayer
        ):
            self.query_requested.emit(self._layer_snapshot.layer_id)

    def _emit_add_request(self) -> None:
        """发出当前图层新增要素请求。"""
        if self._layer_snapshot is not None and isinstance(
            self._layer_snapshot.layer, VectorLayer
        ):
            self.add_feature_requested.emit(self._layer_snapshot.layer_id)

    def _emit_edit_request(self) -> None:
        """发出单个选中要素编辑请求。"""
        fids: tuple[FeatureId, ...] = self.selected_feature_ids()
        if self._layer_snapshot is None or len(fids) != 1:
            self._status_label.setText("请先选中一行，再点击“编辑”。")
            return
        self.edit_feature_requested.emit(self._layer_snapshot.layer_id, fids[0])

    def _emit_delete_request(self) -> None:
        """发出选中要素删除请求。"""
        fids: tuple[FeatureId, ...] = self.selected_feature_ids()
        if self._layer_snapshot is None or not fids:
            self._status_label.setText("请先选中至少一行，再点击“删除”。")
            return
        self.delete_features_requested.emit(self._layer_snapshot.layer_id, fids)

    def _update_action_state(self) -> None:
        """根据当前图层类型和选择数量更新工具栏按钮状态。"""
        is_vector: bool = self._layer_snapshot is not None and isinstance(
            self._layer_snapshot.layer, VectorLayer
        )
        selected_count: int = len(self.selected_feature_ids()) if is_vector else 0
        self._query_button.setEnabled(is_vector)
        self._add_button.setEnabled(is_vector)
        self._edit_button.setEnabled(selected_count == 1)
        self._delete_button.setEnabled(selected_count > 0)
