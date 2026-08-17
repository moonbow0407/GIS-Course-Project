"""将 Shapely 矢量几何转换为 Qt 图元。"""

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QStyleOptionGraphicsItem,
    QWidget,
)
from shapely.geometry import (
    GeometryCollection,
    LinearRing,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

from app.application.display_models import VectorDisplayPayload
from app.application.results import LayerSnapshot
from app.domain.feature import Feature
from app.domain.labeling import LabelClass, LabelPlacement
from app.domain.layer_style import LayerStyle
from app.domain.vector_layer import Bounds, VectorLayer
from app.presentation.global_display_settings import selection_color

# 标注避让网格的单元格边长（屏幕像素）：约等于单个标签高度，
# 使每个已占矩形平均落在一到四个单元格内。
_LABEL_GRID_CELL_SIZE: float = 48.0


def _cull_features(
    features: tuple[Feature, ...],
    visible_bounds: Bounds,
) -> tuple[Feature, ...]:
    """按包围盒粗筛掉完全位于渲染范围之外的要素。

    只比较包围盒，不做精确相交：边界恰好接触的要素必须保留给 Qt
    裁剪，且粗筛在 Shapely C 层完成，代价远低于逐要素几何运算。
    空几何的包围盒为 NaN，比较恒为 False，会在渲染循环中被跳过。
    """
    min_x, min_y, max_x, max_y = visible_bounds
    return tuple(
        feature
        for feature in features
        if not (
            feature.geometry.bounds[2] < min_x
            or feature.geometry.bounds[0] > max_x
            or feature.geometry.bounds[3] < min_y
            or feature.geometry.bounds[1] > max_y
        )
    )


class _LabelGrid:
    """标签避让网格：按屏幕像素单元格登记已占用矩形。

    标注数量大时线性扫描全部已占矩形是 O(n²)；标签尺寸相近，用均匀
    网格把碰撞检测缩小到邻近单元格，平均 O(n)。
    """

    def __init__(self, cell_size: float) -> None:
        """以近似单个标签高度的边长划分单元格。"""
        self._cell: float = max(cell_size, 1.0)
        self._cells: dict[tuple[int, int], list[QRectF]] = {}

    def _cells_for(self, rect: QRectF) -> tuple[tuple[int, int], ...]:
        """返回矩形覆盖的全部单元格坐标。"""
        x_start: int = math.floor(rect.left() / self._cell)
        x_end: int = math.floor(rect.right() / self._cell)
        y_start: int = math.floor(rect.top() / self._cell)
        y_end: int = math.floor(rect.bottom() / self._cell)
        return tuple(
            (x, y)
            for x in range(x_start, x_end + 1)
            for y in range(y_start, y_end + 1)
        )

    def collides(self, rect: QRectF) -> bool:
        """判断矩形与任一已占用矩形相交。"""
        for cell_key in self._cells_for(rect):
            for occupied in self._cells.get(cell_key, ()):
                if rect.intersects(occupied):
                    return True
        return False

    def add(self, rect: QRectF) -> None:
        """登记新占用的矩形。"""
        for cell_key in self._cells_for(rect):
            self._cells.setdefault(cell_key, []).append(rect)


_BLEND_MODE_MAP: dict[str, QPainter.CompositionMode] = {
    "normal": QPainter.CompositionMode.CompositionMode_SourceOver,
    "multiply": QPainter.CompositionMode.CompositionMode_Multiply,
    "darken": QPainter.CompositionMode.CompositionMode_Darken,
}


class _BlendPathItem(QGraphicsPathItem):
    """在绘制时应用指定合成模式的路径图元。"""

    def __init__(
        self,
        path: QPainterPath,
        composition_mode: QPainter.CompositionMode,
    ) -> None:
        super().__init__(path)
        self._composition_mode = composition_mode

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        painter.save()
        painter.setCompositionMode(self._composition_mode)
        super().paint(painter, option, widget)
        painter.restore()


def _readable_label_colors(
    text_name: str,
    halo_name: str,
    halo_enabled: bool,
) -> tuple[QColor, QColor]:
    """修复历史或手工配置中的低对比度颜色，保证标签在常见底图上可读。"""
    text_color: QColor = QColor(text_name)
    halo_color: QColor = QColor(halo_name)
    if not text_color.isValid():
        text_color = QColor("#20354A")
    if not halo_color.isValid():
        halo_color = QColor("#FFFFFF")
    text_color.setAlpha(255)
    halo_color.setAlpha(255)

    def relative_luminance(color: QColor) -> float:
        def linear_channel(channel: float) -> float:
            return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4

        return (
            0.2126 * linear_channel(color.redF())
            + 0.7152 * linear_channel(color.greenF())
            + 0.0722 * linear_channel(color.blueF())
        )

    text_luminance: float = relative_luminance(text_color)
    halo_luminance: float = relative_luminance(halo_color)
    contrast_ratio: float = (max(text_luminance, halo_luminance) + 0.05) / (
        min(text_luminance, halo_luminance) + 0.05
    )
    if contrast_ratio < 2.5:
        # 白字白光晕、黑字黑光晕等历史配置会把笔画完全淹没；
        # 优先恢复 GIS 常用的深色字白色光晕组合。
        if text_luminance > 0.45:
            text_color = QColor("#20354A")
        halo_color = QColor("#FFFFFF")
    elif not halo_enabled and text_luminance > 0.55:
        # 关闭晕染后没有底框托底，浅色文字在常见浅色地图上仍不可读。
        text_color = QColor("#20354A")
    return text_color, halo_color


class _LabelItem(QGraphicsItem):
    """绘制带高对比度光晕的屏幕像素标注文本。"""

    _ALIGNMENT: dict[LabelPlacement, tuple[int, int]] = {
        LabelPlacement.ABOVE_LEFT: (-1, -1),
        LabelPlacement.ABOVE: (0, -1),
        LabelPlacement.ABOVE_RIGHT: (1, -1),
        LabelPlacement.LEFT: (-1, 0),
        LabelPlacement.CENTER: (0, 0),
        LabelPlacement.RIGHT: (1, 0),
        LabelPlacement.BELOW_LEFT: (-1, 1),
        LabelPlacement.BELOW: (0, 1),
        LabelPlacement.BELOW_RIGHT: (1, 1),
    }

    def __init__(
        self,
        text: str,
        anchor: QPointF,
        label_class: LabelClass,
        map_units_per_pixel: float,
    ) -> None:
        super().__init__()
        font: QFont = QFont("Microsoft YaHei UI")
        font.setPixelSize(max(round(label_class.font_size), 1))
        font.setWeight(QFont.Weight.Medium)
        metrics: QFontMetricsF = QFontMetricsF(font)
        metrics_bounds: QRectF = metrics.tightBoundingRect(text)
        self._font: QFont = font
        self._text: str = text
        self._map_units_per_pixel: float = max(map_units_per_pixel, 1e-9)
        self._text_baseline: QPointF = QPointF(
            -metrics_bounds.left(),
            -metrics_bounds.top(),
        )
        self._text_bounds: QRectF = QRectF(
            0.0,
            0.0,
            max(metrics.horizontalAdvance(text), metrics_bounds.width()),
            max(metrics.height(), metrics_bounds.height()),
        )
        self._text_color, self._halo_color = _readable_label_colors(
            label_class.text_color,
            label_class.halo_color,
            label_class.halo_enabled,
        )
        self._halo_width: float = label_class.halo_width
        self._halo_enabled: bool = label_class.halo_enabled
        padding: float = max(self._halo_width, 1.0) if self._halo_enabled else 0.0
        self._bounds: QRectF = self._text_bounds.adjusted(
            -padding,
            -padding,
            padding,
            padding,
        )
        self._place(
            anchor,
            label_class.placement,
            label_class.offset_x,
            label_class.offset_y,
            map_units_per_pixel,
        )
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)

    def boundingRect(self) -> QRectF:
        """返回包含文本和光晕的局部包围盒。"""
        return self._bounds

    def screen_rect(self) -> QRectF:
        """返回用于标签避让的屏幕像素矩形，避免依赖 Qt 变换后的场景包围盒。"""
        position: QPointF = self.pos()
        scale: float = self._map_units_per_pixel
        return QRectF(
            position.x() / scale + self._bounds.left(),
            position.y() / scale + self._bounds.top(),
            self._bounds.width(),
            self._bounds.height(),
        )

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        """绘制不透明标签底和文本，确保不同底图与字体环境下均可读。"""
        del option, widget
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._halo_enabled:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(self._halo_color))
            corner_radius: float = max(self._halo_width, 3.0)
            painter.drawRoundedRect(self._bounds, corner_radius, corner_radius)
        painter.setFont(self._font)
        painter.setPen(self._text_color)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawText(self._text_baseline, self._text)
        painter.restore()

    def _place(
        self,
        anchor: QPointF,
        placement: LabelPlacement,
        offset_x: float,
        offset_y: float,
        map_units_per_pixel: float,
    ) -> None:
        """按九宫格位置把标签左上角放到要素锚点附近。"""
        horizontal, vertical = self._ALIGNMENT[placement]
        # 文本包围盒来自屏幕像素字号。ItemIgnoresTransformations 只保持绘制
        # 尺寸不受视图缩放影响，setPos 仍使用场景坐标，因此像素必须先换成
        # 地图单位。地理坐标系下 1 像素远小于 1 度，漏乘会把标注整块平移出图斑。
        text_width: float = self._text_bounds.width() * map_units_per_pixel
        text_height: float = self._text_bounds.height() * map_units_per_pixel
        gap_x: float = 4.0 * map_units_per_pixel
        gap_y: float = 4.0 * map_units_per_pixel
        if horizontal < 0:
            x = anchor.x() - text_width - gap_x
        elif horizontal > 0:
            x = anchor.x() + gap_x
        else:
            x = anchor.x() - text_width / 2.0
        if vertical < 0:
            y = anchor.y() - text_height - gap_y
        elif vertical > 0:
            y = anchor.y() + gap_y
        else:
            y = anchor.y() - text_height / 2.0
        # 显式偏移允许用户微调位置；scene Y 轴向下，与屏幕坐标一致。
        self.setPos(
            QPointF(
                x + offset_x * map_units_per_pixel,
                y + offset_y * map_units_per_pixel,
            )
        )


class QtVectorRenderer:
    """负责矢量领域模型的 Qt 路径构造、样式转换和选择高亮。"""

    def render_layer(
        self,
        scene: QGraphicsScene,
        snapshot: LayerSnapshot,
        z_value: float,
        map_units_per_pixel: float = 1.0,
        visible_bounds: Bounds | None = None,
    ) -> list[QGraphicsItem]:
        """将图层快照渲染到场景，并返回创建的图元。

        参数:
            scene: 接收图元的 Qt 地图场景。
            snapshot: 待绘制的矢量图层快照。
            z_value: 图层在场景中的堆叠顺序。
            map_units_per_pixel: 当前视图每个屏幕像素对应的地图单位。
            visible_bounds: 当前需要渲染的地图范围（含余量）；为空时渲染
                全部要素。视野外的要素不生成图元，缩小大图层的重建成本。

        说明:
            图层级透明度和显示比例范围在渲染时统一应用到全部要素图元。
        """
        if not isinstance(snapshot.layer, VectorLayer):
            raise TypeError("矢量渲染器只能绘制矢量图层。")
        if snapshot.display_payload is None:
            base_features: tuple[Feature, ...] = snapshot.layer.features
        elif isinstance(snapshot.display_payload, VectorDisplayPayload):
            base_features = snapshot.display_payload.features
        else:
            raise TypeError("矢量快照的显示载荷类型无效。")
        composition_mode = _BLEND_MODE_MAP.get(
            snapshot.blend_mode,
            QPainter.CompositionMode.CompositionMode_SourceOver,
        )
        _needs_blend = composition_mode != QPainter.CompositionMode.CompositionMode_SourceOver
        labeling = snapshot.layer.labeling
        generate_labels: bool = labeling is not None and labeling.enabled
        # 多级 LOD：仅在未发生显示投影时启用（载荷几何与领域层几何同坐标
        # 系）；投影场景下 LOD 的源坐标与显示坐标不一致，回退完整几何。
        fade: tuple[tuple[Feature, ...], tuple[Feature, ...], float] | None = None
        if snapshot.layer.lod is not None and (
            snapshot.display_payload is None
            or snapshot.display_payload.features is snapshot.layer.features
        ):
            fade = snapshot.layer.lod.select_fade(map_units_per_pixel)
        # 统一按当前视野裁剪；交叉淡化的两个级别几何需分别裁剪。
        if visible_bounds is not None:
            if fade is None:
                base_features = _cull_features(base_features, visible_bounds)
            else:
                fine: tuple[Feature, ...]
                coarse: tuple[Feature, ...]
                t: float
                fine, coarse, t = fade
                fade = (
                    _cull_features(fine, visible_bounds),
                    _cull_features(coarse, visible_bounds),
                    t,
                )

        if fade is None:
            return self._render_features(
                scene,
                snapshot,
                z_value,
                map_units_per_pixel,
                composition_mode,
                _needs_blend,
                base_features,
                1.0,
                generate_labels,
            )
        fine_features, coarse_features, t = fade
        if t <= 0.0:
            return self._render_features(
                scene, snapshot, z_value, map_units_per_pixel, composition_mode,
                _needs_blend, fine_features, 1.0, generate_labels,
            )
        if t >= 1.0:
            return self._render_features(
                scene, snapshot, z_value, map_units_per_pixel, composition_mode,
                _needs_blend, coarse_features, 1.0, generate_labels,
            )
        # 交叉淡化：细级别负责标注锚点，粗级别只补充淡化几何。
        items: list[QGraphicsItem] = self._render_features(
            scene, snapshot, z_value, map_units_per_pixel, composition_mode,
            _needs_blend, fine_features, 1.0 - t, generate_labels,
        )
        items += self._render_features(
            scene, snapshot, z_value, map_units_per_pixel, composition_mode,
            _needs_blend, coarse_features, t, False,
        )
        return items

    def _render_features(
        self,
        scene: QGraphicsScene,
        snapshot: LayerSnapshot,
        z_value: float,
        map_units_per_pixel: float,
        composition_mode: QPainter.CompositionMode,
        needs_blend: bool,
        features: tuple[Feature, ...],
        opacity_factor: float,
        generate_labels: bool,
    ) -> list[QGraphicsItem]:
        """把一组要素渲染为 Qt 图元，并按 ``opacity_factor`` 整体淡化。

        参数:
            opacity_factor: 额外乘到图元透明度上的淡化因子，交叉淡化时
                细级别为 ``1 - t``、粗级别为 ``t``，单层渲染时为 1。
            generate_labels: 是否为本组要素生成动态标注；交叉淡化只在
                细级别生成一次，避免重复标注。
        """
        # 标注锚点复用主循环已简化的显示几何；被符号规则隐藏的要素不渲染，
        # 也不再单独生成漂浮标注。
        label_anchors: list[tuple[Feature, BaseGeometry]] | None = (
            [] if generate_labels else None
        )
        items: list[QGraphicsItem] = []
        feature: Feature
        for feature in features:
            if feature.geometry.is_empty:
                continue
            if snapshot.layer.symbology is None:
                continue
            style: LayerStyle | None = snapshot.layer.symbology.symbol_for(feature)
            if style is None:
                continue
            path: QPainterPath = QPainterPath()
            point_size: float = style.point_size * map_units_per_pixel
            selected: bool = feature.fid in snapshot.selected_feature_ids
            # 选中点要素时适度放大符号。
            if selected:
                point_size *= 1.6
            display_geometry: BaseGeometry = feature.geometry
            if label_anchors is not None:
                label_anchors.append((feature, display_geometry))
            self._append_geometry(path, display_geometry, point_size)
            if path.isEmpty():
                continue
            # 选中要素先绘制光晕层（宽半透明描边），再绘制主体。
            if selected:
                halo: QGraphicsPathItem = (
                    _BlendPathItem(path, composition_mode)
                    if needs_blend
                    else QGraphicsPathItem(path)
                )
                self._apply_halo(halo, feature.geometry.geom_type)
                halo.setData(0, snapshot.layer_id)
                halo.setData(1, feature.fid)
                halo.setZValue(z_value - 0.1)
                halo.setVisible(snapshot.visible)
                halo.setOpacity(snapshot.opacity * opacity_factor)
                halo.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
                scene.addItem(halo)
                items.append(halo)
            item: QGraphicsPathItem = (
                _BlendPathItem(path, composition_mode)
                if needs_blend
                else QGraphicsPathItem(path)
            )
            self._apply_style(item, style, selected, feature.geometry.geom_type)
            # 图层级透明度乘以符号自身透明度与淡化因子，实现整体淡化。
            item.setOpacity(item.opacity() * snapshot.opacity * opacity_factor)
            # 自定义数据把 Qt 图元关联回领域图层和要素。
            item.setData(0, snapshot.layer_id)
            item.setData(1, feature.fid)
            item.setZValue(z_value)
            item.setVisible(snapshot.visible)
            scene.addItem(item)
            items.append(item)
        if label_anchors is not None:
            self._render_labels(
                scene,
                snapshot,
                label_anchors,
                z_value,
                map_units_per_pixel,
                items,
            )
        return items

    def _render_labels(
        self,
        scene: QGraphicsScene,
        snapshot: LayerSnapshot,
        label_anchors: list[tuple[Feature, BaseGeometry]] | None,
        z_value: float,
        map_units_per_pixel: float,
        items: list[QGraphicsItem],
    ) -> None:
        """按标注类为已渲染要素创建动态标签图元。

        参数:
            label_anchors: (要素, 显示几何) 列表；为空表示图层未启用标注。
        """
        if label_anchors is None or not isinstance(snapshot.layer, VectorLayer):
            return
        labeling = snapshot.layer.labeling
        if labeling is None or not labeling.enabled:
            return
        occupied = _LabelGrid(_LABEL_GRID_CELL_SIZE)
        for class_index, label_class in enumerate(labeling.classes):
            if not label_class.visible:
                continue
            for feature, display_geometry in label_anchors:
                text: str | None = label_class.text_for(feature)
                if text is None:
                    continue
                # 锚点取自简化后的显示几何，避免对原始几何逐要素求代表点。
                anchor = display_geometry.representative_point()
                label_item = _LabelItem(
                    text,
                    QPointF(float(anchor.x), -float(anchor.y)),
                    label_class,
                    map_units_per_pixel,
                )
                collision_rect: QRectF = label_item.screen_rect().adjusted(
                    -2.0,
                    -2.0,
                    2.0,
                    2.0,
                )
                if occupied.collides(collision_rect):
                    continue
                occupied.add(collision_rect)
                label_item.setData(0, snapshot.layer_id)
                label_item.setData(1, feature.fid)
                label_item.setData(2, "label")
                label_item.setZValue(z_value + 0.2 + class_index * 0.001)
                label_item.setVisible(snapshot.visible)
                label_item.setOpacity(snapshot.opacity)
                scene.addItem(label_item)
                items.append(label_item)

    def _append_geometry(
        self,
        path: QPainterPath,
        geometry: BaseGeometry,
        point_size: float,
    ) -> None:
        """递归把受支持的 Shapely 几何追加到同一个绘制路径。"""
        if isinstance(geometry, Point):
            radius: float = max(point_size, 1e-9) / 2.0
            # Qt 场景纵轴向下，地图纵轴向上，因此显示时反转 Y 坐标。
            path.addEllipse(QPointF(geometry.x, -geometry.y), radius, radius)
            return
        if isinstance(geometry, LineString):
            self._append_line(path, geometry)
            return
        if isinstance(geometry, Polygon):
            # 奇偶填充规则让多边形内环显示为空洞。
            path.setFillRule(Qt.FillRule.OddEvenFill)
            self._append_line(path, geometry.exterior, close=True)
            interior_ring: LinearRing
            for interior_ring in geometry.interiors:
                self._append_line(path, interior_ring, close=True)
            return
        if isinstance(
            geometry,
            (MultiPoint, MultiLineString, MultiPolygon, GeometryCollection),
        ):
            member: BaseGeometry
            for member in geometry.geoms:
                self._append_geometry(path, member, point_size)

    @staticmethod
    def _append_line(
        path: QPainterPath,
        geometry: LineString | LinearRing,
        close: bool = False,
    ) -> None:
        """把线或环坐标追加到路径，并统一反转地图纵轴。"""
        coordinates: list[tuple[float, float]] = [
            (float(coordinate[0]), float(coordinate[1])) for coordinate in geometry.coords
        ]
        if not coordinates:
            return
        path.moveTo(coordinates[0][0], -coordinates[0][1])
        coordinate: tuple[float, float]
        for coordinate in coordinates[1:]:
            path.lineTo(coordinate[0], -coordinate[1])
        if close:
            path.closeSubpath()

    @staticmethod
    def _apply_halo(item: QGraphicsPathItem, geom_type: str) -> None:
        """为选中要素绘制半透明宽描边光晕，确保在任何底图上可见。

        参数:
            item: 光晕层 Qt 图元。
            geom_type: Shapely 几何类型名称。
        """
        halo_color: QColor = QColor(selection_color())
        halo_color.setAlpha(120)
        halo_width: float = 10.0
        halo_pen: QPen = QPen(
            halo_color, halo_width,
            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin,
        )
        halo_pen.setCosmetic(True)
        item.setPen(halo_pen)
        item.setBrush(Qt.BrushStyle.NoBrush)

    @staticmethod
    def _apply_style(
        item: QGraphicsPathItem,
        style: LayerStyle,
        selected: bool,
        geom_type: str = "",
    ) -> None:
        """把领域样式转换为 Qt 画笔和画刷，选中要素按几何类型高亮。

        参数:
            item: 待应用样式的 Qt 图元。
            style: 领域图层样式配置。
            selected: 是否处于被选中状态。
            geom_type: Shapely 几何类型名称，用于区分面/线/点高亮策略。
        """
        if not selected:
            stroke_color: QColor = QColor(style.stroke_color)
            pen: QPen = QPen(stroke_color, style.line_width)
            pen.setCosmetic(True)
            fill_color: QColor = QColor(style.fill_color)
            brush: QBrush = QBrush(
                fill_color if fill_color.isValid() else Qt.BrushStyle.NoBrush
            )
            item.setPen(pen)
            item.setBrush(brush)
            item.setOpacity(style.opacity)
            return

        # ── 选中高亮策略：统一使用全局选择高亮色 ──
        highlight_color: QColor = selection_color()

        # 面要素：填充用选中色半透明覆盖，边界用选中色粗线。
        if geom_type in ("Polygon", "MultiPolygon"):
            fill = QColor(highlight_color)
            fill.setAlpha(80)
            pen = QPen(highlight_color, style.line_width + 3.0)
            pen.setCosmetic(True)
            item.setPen(pen)
            item.setBrush(QBrush(fill))
            item.setOpacity(1.0)

        # 线要素：选中色粗线。
        elif geom_type in ("LineString", "MultiLineString", "LinearRing"):
            pen = QPen(highlight_color, style.line_width + 3.0)
            pen.setCosmetic(True)
            item.setPen(pen)
            item.setOpacity(1.0)

        # 点要素：选中色填充 + 略微加深的选中色描边。
        else:
            pen = QPen(highlight_color.darker(150), style.line_width + 2.0)
            pen.setCosmetic(True)
            item.setPen(pen)
            item.setBrush(QBrush(highlight_color))
            item.setOpacity(1.0)
