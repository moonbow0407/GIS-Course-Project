"""PostgreSQL/PostGIS 连接和图层目录对话框。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.application.database_models import DatabaseConnectionConfig, DatabaseLayerInfo
from app.application.errors import ApplicationError


class DatabaseConnectionDialog(QDialog):
    """收集 PostgreSQL/PostGIS 连接参数。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        """创建连接参数表单；密码不会写入持久化控件状态。"""
        super().__init__(parent)
        self.setWindowTitle("连接 PostgreSQL / PostGIS")
        self.setMinimumWidth(460)
        self._config: DatabaseConnectionConfig | None = None

        self._host_edit: QLineEdit = QLineEdit("localhost")
        self._port_spin: QSpinBox = QSpinBox()
        self._port_spin.setRange(1, 65535)
        self._port_spin.setValue(5432)
        self._database_edit: QLineEdit = QLineEdit()
        self._database_edit.setPlaceholderText("数据库名称")
        self._username_edit: QLineEdit = QLineEdit()
        self._username_edit.setPlaceholderText("用户名")
        self._password_edit: QLineEdit = QLineEdit()
        self._password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._password_edit.setPlaceholderText("密码仅保存在当前进程")

        form_layout: QFormLayout = QFormLayout()
        form_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form_layout.addRow("主机", self._host_edit)
        form_layout.addRow("端口", self._port_spin)
        form_layout.addRow("数据库", self._database_edit)
        form_layout.addRow("用户名", self._username_edit)
        form_layout.addRow("密码", self._password_edit)

        hint_label: QLabel = QLabel(
            "点击“连接”会同时验证 PostgreSQL 和 PostGIS。连接密码不会写入工程文件。"
        )
        hint_label.setWordWrap(True)
        hint_label.setObjectName("databaseConnectionHint")

        button_box: QDialogButtonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.button(QDialogButtonBox.StandardButton.Ok).setText("连接")
        button_box.accepted.connect(self._accept_config)
        button_box.rejected.connect(self.reject)

        layout: QVBoxLayout = QVBoxLayout(self)
        layout.addLayout(form_layout)
        layout.addWidget(hint_label)
        layout.addWidget(button_box)

    def config(self) -> DatabaseConnectionConfig:
        """返回已通过本地字段校验的连接配置。"""
        if self._config is None:
            raise ApplicationError("连接参数尚未提交。")
        return self._config

    def _accept_config(self) -> None:
        """校验表单并保存当前对话框内的临时配置。"""
        try:
            config: DatabaseConnectionConfig = DatabaseConnectionConfig(
                host=self._host_edit.text().strip(),
                port=self._port_spin.value(),
                database=self._database_edit.text().strip(),
                username=self._username_edit.text().strip(),
                password=self._password_edit.text(),
            )
        except ValueError as error:
            QMessageBox.warning(self, "连接参数无效", str(error))
            return
        self._config = config
        self.accept()


class DatabaseLayerDialog(QDialog):
    """显示数据库图层目录，并可选择一个图层加载。"""

    _HEADERS: tuple[str, ...] = ("ID", "图层名称", "几何", "要素数", "CRS", "创建时间")

    def __init__(
        self,
        layers: tuple[DatabaseLayerInfo, ...],
        title: str = "数据库图层",
        selection_required: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        """使用数据库图层目录创建选择窗口。"""
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(760, 420)
        self._layers: tuple[DatabaseLayerInfo, ...] = layers
        self._selection_required: bool = selection_required

        self._table: QTableWidget = QTableWidget(len(layers), len(self._HEADERS))
        self._table.setHorizontalHeaderLabels(list(self._HEADERS))
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            1,
            QHeaderView.ResizeMode.Stretch,
        )
        self._table.horizontalHeader().setSectionResizeMode(
            4,
            QHeaderView.ResizeMode.Stretch,
        )
        self._populate_table()
        if layers:
            self._table.selectRow(0)

        hint_text: str = (
            "选择一个图层后点击“加载”。数据库图层 ID 用于区分同名图层。"
            if selection_required
            else "当前数据库中的图层目录；同名图层可通过 ID 区分。"
        )
        hint_label: QLabel = QLabel(hint_text)
        hint_label.setWordWrap(True)
        hint_label.setObjectName("databaseLayerHint")

        if selection_required:
            button_box = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            button_box.button(QDialogButtonBox.StandardButton.Ok).setText("加载")
            button_box.accepted.connect(self._accept_selection)
            button_box.rejected.connect(self.reject)
        else:
            button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            close_button = button_box.button(QDialogButtonBox.StandardButton.Close)
            if close_button is not None:
                close_button.clicked.connect(self.close)

        if not layers and selection_required:
            button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

        layout: QVBoxLayout = QVBoxLayout(self)
        layout.addWidget(hint_label)
        layout.addWidget(self._table, 1)
        layout.addWidget(button_box)

    def selected_layer_id(self) -> int:
        """返回用户选中的数据库图层 ID。"""
        row: int = self._table.currentRow()
        if row < 0 or row >= len(self._layers):
            raise ApplicationError("请选择一个数据库图层。")
        return self._layers[row].layer_id

    def _populate_table(self) -> None:
        """将图层目录映射为只读表格。"""
        for row_index, layer in enumerate(self._layers):
            values: tuple[str, ...] = (
                str(layer.layer_id),
                layer.name,
                layer.geometry_type,
                str(layer.feature_count),
                layer.crs or "未设置",
                layer.created_at.isoformat(sep=" ", timespec="seconds")
                if layer.created_at is not None
                else "未记录",
            )
            for column_index, value in enumerate(values):
                item: QTableWidgetItem = QTableWidgetItem(value)
                if column_index in {0, 3}:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self._table.setItem(row_index, column_index, item)

    def _accept_selection(self) -> None:
        """校验有选中项后关闭窗口。"""
        try:
            self.selected_layer_id()
        except ApplicationError as error:
            QMessageBox.warning(self, "未选择图层", str(error))
            return
        self.accept()
