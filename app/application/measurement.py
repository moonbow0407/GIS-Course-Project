"""按图层 CRS 选择平面或椭球测地测量。"""

from dataclasses import dataclass

from pyproj import CRS, Geod
from shapely.geometry.base import BaseGeometry


@dataclass(frozen=True, slots=True)
class MeasurementResult:
    """表示一次测量值及其计算方式。"""

    value: float
    method: str
    unit: str


def _geod_for_crs(crs: CRS) -> Geod:
    """按 CRS 椭球参数构造测地计算器。"""
    ellipsoid = (crs.geodetic_crs or crs).ellipsoid
    if ellipsoid is None:
        raise ValueError("地理 CRS 没有可用的椭球参数。")
    return Geod(
        a=ellipsoid.semi_major_metre,
        b=ellipsoid.semi_minor_metre,
    )


def measure_length(geometry: BaseGeometry, crs: CRS) -> MeasurementResult:
    """测量线几何长度；地理 CRS 使用椭球测地长度。"""
    if crs.is_geographic:
        geod = _geod_for_crs(crs)
        value = float(geod.geometry_length(geometry))
        return MeasurementResult(value=abs(value), method="ellipsoidal", unit="m")
    return MeasurementResult(value=float(geometry.length), method="planar", unit="CRS units")


def measure_area(geometry: BaseGeometry, crs: CRS) -> MeasurementResult:
    """测量面几何面积；地理 CRS 使用椭球测地面积。"""
    if crs.is_geographic:
        geod = _geod_for_crs(crs)
        area, _perimeter = geod.geometry_area_perimeter(geometry)
        return MeasurementResult(value=abs(float(area)), method="ellipsoidal", unit="m²")
    return MeasurementResult(value=float(geometry.area), method="planar", unit="CRS units²")
