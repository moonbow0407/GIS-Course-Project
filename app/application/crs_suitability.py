"""评估坐标系是否适合作为首图层的地图显示坐标系。"""

from dataclasses import dataclass
from enum import Enum
from math import isfinite

from pyproj import CRS, Proj, Transformer

from app.domain.spatial_layer import SpatialLayer


class CrsSuitabilityReason(str, Enum):
    """地图显示 CRS 需要用户确认的原因。"""

    NARROW_AREA = "narrow_area"
    OUTSIDE_AREA = "outside_area"
    UNKNOWN_AUTHORITY = "unknown_authority"
    STRONG_DISTORTION = "strong_distortion"


_REASON_MESSAGES: dict[CrsSuitabilityReason, str] = {
    CrsSuitabilityReason.OUTSIDE_AREA: "当前图层部分超出 CRS 适用范围，可能出现变形或缺失。",
    CrsSuitabilityReason.STRONG_DISTORTION: "当前 CRS 在该区域变形较明显，显示效果可能不理想。",
    CrsSuitabilityReason.NARROW_AREA: "当前 CRS 适用范围较窄，可能不适合后续图层显示。",
    CrsSuitabilityReason.UNKNOWN_AUTHORITY: "当前 CRS 无法识别适用范围，建议确认地图显示 CRS。",
}

_REASON_PRIORITY: tuple[CrsSuitabilityReason, ...] = (
    CrsSuitabilityReason.OUTSIDE_AREA,
    CrsSuitabilityReason.STRONG_DISTORTION,
    CrsSuitabilityReason.NARROW_AREA,
    CrsSuitabilityReason.UNKNOWN_AUTHORITY,
)


@dataclass(frozen=True, slots=True)
class CrsSuitabilityAssessment:
    """描述候选地图 CRS 对当前首图层的适用性。"""

    reasons: tuple[CrsSuitabilityReason, ...] = ()

    @property
    def requires_confirmation(self) -> bool:
        """返回导入前是否需要用户确认地图显示 CRS。"""
        return bool(self.reasons)

    @property
    def message(self) -> str:
        """只返回最严重的一条简短用户提示。"""
        for reason in _REASON_PRIORITY:
            if reason in self.reasons:
                return _REASON_MESSAGES[reason]
        return ""


class CrsSuitabilityService:
    """按适用范围和投影变形评估候选地图显示 CRS。"""

    # 三度带、六度带等局部投影会触发提示，但仍允许用户继续使用。
    NARROW_LONGITUDE_SPAN_DEGREES: float = 6.0

    # 仅提示特别明显的视觉变形，避免普通经纬度或低纬墨卡托频繁打扰用户。
    MAX_LINEAR_SCALE_FACTOR: float = 1.5
    MIN_LINEAR_SCALE_FACTOR: float = 1.0 / MAX_LINEAR_SCALE_FACTOR
    MAX_ANISOTROPY_RATIO: float = 1.3
    MAX_AREA_SCALE_FACTOR: float = 2.0
    MIN_AREA_SCALE_FACTOR: float = 1.0 / MAX_AREA_SCALE_FACTOR
    MAX_ANGULAR_DISTORTION_DEGREES: float = 10.0
    MAX_SCALE_VARIATION_RATIO: float = 1.5

    def assess(
        self,
        layer: SpatialLayer,
        candidate_crs: CRS | None,
    ) -> CrsSuitabilityAssessment:
        """评估候选 CRS，并按固定顺序返回需要确认的原因。"""
        if layer.crs is None or candidate_crs is None:
            return CrsSuitabilityAssessment(
                (CrsSuitabilityReason.UNKNOWN_AUTHORITY,)
            )

        reasons: list[CrsSuitabilityReason] = []
        authority = candidate_crs.to_authority()
        area = candidate_crs.area_of_use
        if authority is None or area is None:
            reasons.append(CrsSuitabilityReason.UNKNOWN_AUTHORITY)

        geographic_points = self._geographic_sample_points(layer)
        if area is not None:
            if self._longitude_span(area.west, area.east) <= (
                self.NARROW_LONGITUDE_SPAN_DEGREES
            ):
                reasons.append(CrsSuitabilityReason.NARROW_AREA)
            if geographic_points and not all(
                self._area_contains(area.west, area.south, area.east, area.north, lon, lat)
                for lon, lat in geographic_points
            ):
                reasons.append(CrsSuitabilityReason.OUTSIDE_AREA)

        if geographic_points and self._has_strong_distortion(
            candidate_crs, geographic_points
        ):
            reasons.append(CrsSuitabilityReason.STRONG_DISTORTION)

        ordered_reasons = tuple(
            reason for reason in _REASON_PRIORITY if reason in reasons
        )
        return CrsSuitabilityAssessment(ordered_reasons)

    @staticmethod
    def _geographic_sample_points(layer: SpatialLayer) -> tuple[tuple[float, float], ...]:
        """将图层范围的规则采样点转换为经纬度，避免只检查四角。"""
        assert layer.crs is not None
        minimum_x, minimum_y, maximum_x, maximum_y = layer.bounds
        xs = tuple(
            minimum_x + (maximum_x - minimum_x) * index / 4.0
            for index in range(5)
        )
        ys = tuple(
            minimum_y + (maximum_y - minimum_y) * index / 4.0
            for index in range(5)
        )
        try:
            transformer = Transformer.from_crs(
                layer.crs,
                CRS.from_epsg(4326),
                always_xy=True,
            )
            points = tuple(
                transformer.transform(x, y)
                for x in xs
                for y in ys
            )
        except Exception:  # PROJ 错误类型会随输入 CRS 和运行环境变化。
            return ()
        return tuple(
            (CrsSuitabilityService._normalize_longitude(float(lon)), float(lat))
            for lon, lat in points
            if isfinite(lon) and isfinite(lat) and -90.0 <= lat <= 90.0
        )

    def _has_strong_distortion(
        self,
        candidate_crs: CRS,
        geographic_points: tuple[tuple[float, float], ...],
    ) -> bool:
        """通过局部比例、面积和角度因子识别明显视觉变形。"""
        try:
            projection = Proj(candidate_crs)
        except Exception:
            return True

        scales: list[float] = []
        for longitude, latitude in geographic_points:
            try:
                factors = projection.get_factors(longitude, latitude)
            except Exception:
                return True
            meridional = float(factors.meridional_scale)
            parallel = float(factors.parallel_scale)
            areal = float(factors.areal_scale)
            angular = abs(float(factors.angular_distortion))
            if not all(isfinite(value) for value in (meridional, parallel, areal, angular)):
                return True
            if meridional <= 0.0 or parallel <= 0.0 or areal <= 0.0:
                return True
            anisotropy = max(meridional, parallel) / min(meridional, parallel)
            if (
                meridional > self.MAX_LINEAR_SCALE_FACTOR
                or parallel > self.MAX_LINEAR_SCALE_FACTOR
                or meridional < self.MIN_LINEAR_SCALE_FACTOR
                or parallel < self.MIN_LINEAR_SCALE_FACTOR
                or anisotropy > self.MAX_ANISOTROPY_RATIO
                or areal > self.MAX_AREA_SCALE_FACTOR
                or areal < self.MIN_AREA_SCALE_FACTOR
                or angular > self.MAX_ANGULAR_DISTORTION_DEGREES
            ):
                return True
            scales.extend((meridional, parallel))

        return bool(scales) and max(scales) / min(scales) > self.MAX_SCALE_VARIATION_RATIO

    @staticmethod
    def _longitude_span(west: float, east: float) -> float:
        """返回适用区经度跨度，兼容跨越反经线的范围。"""
        if east >= west:
            return east - west
        return 360.0 - west + east

    @staticmethod
    def _area_contains(
        west: float,
        south: float,
        east: float,
        north: float,
        longitude: float,
        latitude: float,
    ) -> bool:
        """判断经纬度是否位于适用区，兼容反经线包络。"""
        if latitude < south or latitude > north:
            return False
        if east >= west:
            return west <= longitude <= east
        return longitude >= west or longitude <= east

    @staticmethod
    def _normalize_longitude(longitude: float) -> float:
        """将经度归一化到 [-180, 180]，保留正 180 度边界。"""
        normalized = (longitude + 180.0) % 360.0 - 180.0
        return 180.0 if normalized == -180.0 and longitude > 0.0 else normalized
