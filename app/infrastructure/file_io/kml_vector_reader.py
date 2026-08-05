"""基于标准库 XML 解析的 KML 矢量读取适配器。"""

import math
import re
from pathlib import Path
from xml.etree import ElementTree

from pyproj import CRS
from pyproj.transformer import Transformer
from shapely.geometry import GeometryCollection, LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from app.application.errors import (
    EmptyVectorDataset,
    IncompatibleCoordinateReferenceSystem,
    NoUsableGeometry,
    UnsupportedVectorFormat,
    VectorFileNotFound,
    VectorReadFailed,
)
from app.domain.feature import AttributeValue, Feature
from app.domain.vector_layer import VectorLayer

# KML 坐标组：逗号分隔的“经度,纬度”，允许逗号后空白，忽略可选的高度值。
_COORDINATE_PAIR: re.Pattern[str] = re.compile(
    r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
    r"\s*,\s*"
    r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
)


class KmlVectorReader:
    """读取 KML 矢量文件并转换为应用统一领域模型。

    KML 是 XML 格式，规范规定坐标始终为 WGS 84 经纬度（经度在前），
    因此本读取器不依赖 GDAL 的 KML 驱动，直接用标准库解析，
    几何与属性构造完成后可与 GeoPandas 读取器共享相同的领域模型。
    """

    # 支持扩展名：.kml 为纯 XML；.kmz 是压缩包，暂不支持。
    SUPPORTED_SUFFIXES: frozenset[str] = frozenset({".kml"})

    def read(
        self,
        path: Path,
        target_crs: CRS | None = None,
        layer_name: str | None = None,
    ) -> VectorLayer:
        """读取 KML 文件，并按需转换坐标参考系统。

        参数:
            path: KML 文件路径。
            target_crs: 地图显示坐标系；为空时保留 WGS 84 源坐标系。
            layer_name: KML 没有多图层概念，该参数仅用于保持统一签名。

        返回:
            由 KML 要素构造的矢量领域图层。

        异常:
            VectorFileNotFound: 文件不存在时抛出。
            UnsupportedVectorFormat: 扩展名不是 .kml 时抛出。
            VectorReadFailed: XML 结构损坏或坐标文本无法解析时抛出。
            EmptyVectorDataset: 文档不包含任何要素时抛出。
            NoUsableGeometry: 要素全部缺少可解析几何时抛出。
            IncompatibleCoordinateReferenceSystem: 坐标非有限或超出经纬度范围时抛出。
        """
        resolved_path: Path = path.expanduser().resolve()
        if not resolved_path.is_file():
            raise VectorFileNotFound(f"矢量文件不存在：{resolved_path}")
        suffix: str = resolved_path.suffix.lower()
        if suffix not in self.SUPPORTED_SUFFIXES:
            raise UnsupportedVectorFormat(f"暂不支持该矢量文件格式：{suffix or '无扩展名'}")

        try:
            root: ElementTree.Element = ElementTree.parse(resolved_path).getroot()
        except ElementTree.ParseError as error:
            raise VectorReadFailed(f"KML 文件解析失败：{resolved_path.name}") from error
        except OSError as error:
            raise VectorReadFailed(f"KML 文件读取失败：{resolved_path.name}") from error

        features: list[Feature] = []
        for fid, placemark in enumerate(
            self._iter_placemarks(root),
            start=1,
        ):
            geometry: BaseGeometry | None = self._find_geometry(placemark)
            if geometry is None:
                # KML 中允许 Placemark 只有名称没有几何，跳过而不是整体失败。
                continue
            if geometry.is_empty:
                continue
            features.append(
                Feature(
                    fid=fid,
                    geometry=geometry,
                    attributes=self._parse_attributes(placemark),
                )
            )

        if not features:
            if not any(self._iter_placemarks(root)):
                raise EmptyVectorDataset(f"KML 文档不包含任何要素：{resolved_path.name}")
            raise NoUsableGeometry(f"KML 文档不包含可用几何：{resolved_path.name}")

        resolved_features: tuple[Feature, ...] = tuple(features)
        resolved_crs: CRS = CRS.from_epsg(4326)
        if target_crs is not None and target_crs != resolved_crs:
            resolved_features = self._reproject_features(
                resolved_features,
                resolved_crs,
                target_crs,
            )
            resolved_crs = target_crs

        return VectorLayer.create(
            name=resolved_path.stem,
            features=resolved_features,
            crs=resolved_crs,
            source_path=resolved_path,
        )

    # ── XML 遍历 ──────────────────────────────────────────────

    @staticmethod
    def _local_name(tag: str) -> str:
        """剥离 XML 命名空间前缀，返回本地标签名。

        KML 文档可能使用 2.0/2.1/2.2 等不同命名空间甚至没有命名空间，
        统一按本地名识别可以兼容各种生成器。
        """
        return tag.rsplit("}", 1)[-1]

    @classmethod
    def _iter_placemarks(cls, root: ElementTree.Element) -> list[ElementTree.Element]:
        """返回文档中全部 Placemark 元素（含 Document/Folder 嵌套）。"""
        return [
            element
            for element in root.iter()
            if cls._local_name(element.tag) == "Placemark"
        ]

    @classmethod
    def _first_text(
        cls,
        element: ElementTree.Element,
        local_name: str,
    ) -> str | None:
        """返回第一个匹配子元素的文本内容，不存在时返回空值。"""
        for child in element:
            if cls._local_name(child.tag) == local_name:
                return child.text or ""
        return None

    @classmethod
    def _parse_attributes(
        cls,
        placemark: ElementTree.Element,
    ) -> dict[str, AttributeValue]:
        """提取 Placemark 的常用属性字段。

        支持 name、description，以及 ExtendedData 下带 name 属性的
        Data 标签；重复键保留先出现的值。
        """
        attributes: dict[str, AttributeValue] = {}
        for child in placemark:
            child_name: str = cls._local_name(child.tag)
            if child_name == "name":
                attributes.setdefault("name", child.text or "")
            elif child_name == "description":
                attributes.setdefault("description", child.text or "")
            elif child_name == "ExtendedData":
                for data in child.iter():
                    if cls._local_name(data.tag) != "Data":
                        continue
                    key: str | None = data.get("name")
                    value: str | None = cls._first_text(data, "value")
                    if key and value is not None:
                        attributes.setdefault(key, value)
        return attributes

    # ── 几何解析 ──────────────────────────────────────────────

    @classmethod
    def _find_geometry(
        cls,
        placemark: ElementTree.Element,
    ) -> BaseGeometry | None:
        """在 Placemark 内寻找第一个可用的几何对象。

        使用深度优先遍历：MultiGeometry 会在其内部成员之前出现，
        因此优先命中集合标签，再递归收集成员。
        """
        for child in placemark.iter():
            name: str = cls._local_name(child.tag)
            if name == "Point":
                return cls._parse_point(child)
            if name == "LineString":
                return cls._parse_line_string(child)
            if name == "Polygon":
                return cls._parse_polygon(child)
            if name == "MultiGeometry":
                return cls._parse_multi_geometry(child)
        return None

    @classmethod
    def _parse_point(cls, element: ElementTree.Element) -> Point | None:
        """解析 Point 坐标；缺少坐标时返回空值。"""
        coordinates_text: str | None = cls._first_text(element, "coordinates")
        if not coordinates_text:
            return None
        coordinates: list[tuple[float, float]] = cls._parse_coordinates(
            coordinates_text
        )
        if not coordinates:
            return None
        return Point(coordinates[0])

    @classmethod
    def _parse_line_string(
        cls,
        element: ElementTree.Element,
    ) -> LineString | None:
        """解析 LineString 坐标；顶点不足两个时返回空值。"""
        coordinates_text: str | None = cls._first_text(element, "coordinates")
        if not coordinates_text:
            return None
        coordinates: list[tuple[float, float]] = cls._parse_coordinates(
            coordinates_text
        )
        if len(coordinates) < 2:
            return None
        return LineString(coordinates)

    @classmethod
    def _parse_polygon(cls, element: ElementTree.Element) -> Polygon | None:
        """解析 Polygon 的外环和内环；缺少有效外环时返回空值。"""
        exterior: list[tuple[float, float]] | None = None
        interiors: list[list[tuple[float, float]]] = []
        for boundary in element:
            boundary_name: str = cls._local_name(boundary.tag)
            if boundary_name not in {"outerBoundaryIs", "innerBoundaryIs"}:
                continue
            ring: list[tuple[float, float]] | None = cls._parse_linear_ring(
                boundary
            )
            if ring is None:
                continue
            if boundary_name == "outerBoundaryIs":
                exterior = ring
            else:
                interiors.append(ring)
        if exterior is None or len(exterior) < 3:
            return None
        return Polygon(exterior, interiors)

    @classmethod
    def _parse_linear_ring(
        cls,
        boundary: ElementTree.Element,
    ) -> list[tuple[float, float]] | None:
        """解析 LinearRing 坐标，必要时补上闭合点。"""
        for child in boundary:
            if cls._local_name(child.tag) != "LinearRing":
                continue
            coordinates_text: str | None = cls._first_text(child, "coordinates")
            if not coordinates_text:
                return None
            coordinates: list[tuple[float, float]] = cls._parse_coordinates(
                coordinates_text
            )
            if not coordinates:
                return None
            # KML 规范要求环闭合，但部分生成器省略重复终点，这里自动补全。
            if coordinates[0] != coordinates[-1]:
                coordinates.append(coordinates[0])
            return coordinates
        return None

    @classmethod
    def _parse_multi_geometry(
        cls,
        element: ElementTree.Element,
    ) -> BaseGeometry | None:
        """递归解析 MultiGeometry 的全部成员。

        返回:
            单个成员时直接返回该成员；多个成员时返回 GeometryCollection；
            没有任何可用成员时返回空值。
        """
        members: list[BaseGeometry] = []
        for child in element.iter():
            if child is element:
                # iter 包含集合元素自身，跳过以避免嵌套 MultiGeometry 无限递归。
                continue
            name: str = cls._local_name(child.tag)
            member: BaseGeometry | None = None
            if name == "Point":
                member = cls._parse_point(child)
            elif name == "LineString":
                member = cls._parse_line_string(child)
            elif name == "Polygon":
                member = cls._parse_polygon(child)
            elif name == "MultiGeometry":
                member = cls._parse_multi_geometry(child)
            if member is not None and not member.is_empty:
                members.append(member)
        if not members:
            return None
        if len(members) == 1:
            return members[0]
        return GeometryCollection(members)

    @classmethod
    def _parse_coordinates(
        cls,
        text: str,
    ) -> list[tuple[float, float]]:
        """解析 KML 坐标文本为经纬度对，并校验数值合法性。

        KML 坐标顺序为“经度,纬度[,高度]”，与常见的“纬度,经度”习惯相反，
        解析时必须保持经度在前，否则所有要素都会发生镜像错位。

        异常:
            VectorReadFailed: 文本不是合法数字时抛出。
            IncompatibleCoordinateReferenceSystem: 数值非有限或超出经纬度范围时抛出。
        """
        coordinates: list[tuple[float, float]] = []
        for match in _COORDINATE_PAIR.finditer(text):
            longitude_text, latitude_text = match.group().split(",")
            try:
                longitude: float = float(longitude_text)
                latitude: float = float(latitude_text)
            except ValueError as error:
                raise VectorReadFailed("KML 坐标文本无法解析。") from error
            if not math.isfinite(longitude) or not math.isfinite(latitude):
                raise IncompatibleCoordinateReferenceSystem(
                    "KML 坐标数值无效，无法显示。"
                )
            if (
                longitude < -180.0
                or longitude > 180.0
                or latitude < -90.0
                or latitude > 90.0
            ):
                raise IncompatibleCoordinateReferenceSystem(
                    "KML 坐标超出经纬度范围，文件可能声明了错误的坐标系。"
                )
            coordinates.append((longitude, latitude))
        return coordinates

    # ── 坐标转换 ──────────────────────────────────────────────

    @staticmethod
    def _reproject_features(
        features: tuple[Feature, ...],
        source_crs: CRS,
        target_crs: CRS,
    ) -> tuple[Feature, ...]:
        """在读取边界完成坐标转换，保持属性和要素编号不变。"""
        try:
            transformer: Transformer = Transformer.from_crs(
                source_crs,
                target_crs,
                always_xy=True,
            )
            return tuple(
                Feature(
                    fid=feature.fid,
                    geometry=transform(transformer.transform, feature.geometry),
                    attributes=feature.attributes,
                )
                for feature in features
            )
        except Exception as error:
            raise IncompatibleCoordinateReferenceSystem(
                "KML 图层无法转换到地图显示坐标系。"
            ) from error
