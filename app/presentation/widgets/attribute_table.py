"""图层属性表对话框。"""

from PySide6.QtWidgets import QDialog, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from app.application.results import LayerSnapshot
from app.domain.feature import Feature
from app.domain.vector_layer import VectorLayer


class AttributeTableDialog(QDialog):
    """以只读表格展示一个矢量图层的全部要素属性。"""

    def __init__(self, layer_snapshot: LayerSnapshot, parent: QWidget | None = None) -> None:
        """使用图层快照创建属性表窗口。"""
        super().__init__(parent)
        self.setWindowTitle(f"属性表 - {layer_snapshot.name}")
        self.resize(720, 480)
        # 属性表控件：矢量时展示要素字段，栅格时展示基础元数据。
        self._table: QTableWidget = QTableWidget()
        self._populate(layer_snapshot)
        layout: QVBoxLayout = QVBoxLayout(self)
        layout.addWidget(self._table)

    def _populate(self, layer_snapshot: LayerSnapshot) -> None:
        """根据要素属性字段生成表头并填充表格行。"""
        if not isinstance(layer_snapshot.layer, VectorLayer):
            self._table.setColumnCount(2)
            self._table.setHorizontalHeaderLabels(["属性", "值"])
            self._table.setRowCount(2)
            self._table.setItem(0, 0, QTableWidgetItem("波段数"))
            self._table.setItem(0, 1, QTableWidgetItem(str(layer_snapshot.layer.band_count)))
            self._table.setItem(1, 0, QTableWidgetItem("像素尺寸"))
            image_shape: tuple[int, ...] = layer_snapshot.layer.image_data.shape
            self._table.setItem(1, 1, QTableWidgetItem(f"{image_shape[1]} × {image_shape[0]}"))
            self._table.resizeColumnsToContents()
            return
        fields: list[str] = []
        for feature in layer_snapshot.layer.features:
            for field_name in feature.attributes:
                if field_name not in fields:
                    fields.append(field_name)
        self._table.setColumnCount(len(fields) + 1)
        self._table.setHorizontalHeaderLabels(["FID", *fields])
        self._table.setRowCount(len(layer_snapshot.layer.features))
        row_index: int
        current_feature: Feature
        for row_index, current_feature in enumerate(layer_snapshot.layer.features):
            self._table.setItem(row_index, 0, QTableWidgetItem(str(current_feature.fid)))
            column_index: int
            current_field: str
            for column_index, current_field in enumerate(fields, start=1):
                value: object = current_feature.attributes.get(current_field, "")
                self._table.setItem(row_index, column_index, QTableWidgetItem(str(value)))
        self._table.resizeColumnsToContents()
