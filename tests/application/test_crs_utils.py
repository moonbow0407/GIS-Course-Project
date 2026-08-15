"""CRS 等价判断工具测试。"""

import re

from pyproj import CRS

from app.application.crs_utils import (
    crs_coordinate_domain_error,
    crs_equivalent,
    geographic_bounds_are_plausible,
)


def _swap_axis_order(crs: CRS) -> CRS:
    """返回轴顺序与权威定义相反但坐标系等价的 WKT 版本。

    WKT2 中轴顺序由 ORDER 编号声明，交换两个 AXIS 块时必须同步
    交换 ORDER 值，否则 PROJ 会拒绝非连续的顺序编号。
    """
    wkt: str = crs.to_wkt("WKT2_2019")
    blocks: list[str] = _axis_blocks(wkt)
    if len(blocks) < 2:
        raise AssertionError("WKT 未包含两个轴顺序声明。")
    first_swapped: str = blocks[0].replace("ORDER[1]", "ORDER[2]")
    second_swapped: str = blocks[1].replace("ORDER[2]", "ORDER[1]")
    swapped_wkt: str = (
        wkt.replace(blocks[0], "@@A@@")
        .replace(blocks[1], first_swapped)
        .replace("@@A@@", second_swapped)
    )
    return CRS.from_wkt(swapped_wkt)


def _axis_blocks(wkt: str) -> list[str]:
    """提取 WKT 中完整的 AXIS[...] 块（支持嵌套方括号）。"""
    blocks: list[str] = []
    for match in re.finditer(r"AXIS\[", wkt):
        depth: int = 0
        end: int = match.start()
        for index, character in enumerate(wkt[match.start() :]):
            if character == "[":
                depth += 1
            elif character == "]":
                depth -= 1
                if depth == 0:
                    end = match.start() + index + 1
                    break
        blocks.append(wkt[match.start() : end])
    return blocks


def test_same_epsg_is_equivalent() -> None:
    """相同 EPSG 编码的坐标系应判为等价。"""
    assert crs_equivalent(CRS.from_epsg(4326), CRS.from_epsg(4326)) is True


def test_equivalent_wkt_is_equivalent() -> None:
    """EPSG 编码与其等价 WKT 表达应判为等价。"""
    crs: CRS = CRS.from_epsg(4326)
    assert crs_equivalent(crs, CRS.from_wkt(crs.to_wkt())) is True


def test_different_crs_is_not_equivalent() -> None:
    """不同坐标系不应判为等价。"""
    assert crs_equivalent(CRS.from_epsg(4326), CRS.from_epsg(3857)) is False
    assert crs_equivalent(CRS.from_epsg(4326), CRS.from_epsg(32650)) is False


def test_axis_order_is_ignored() -> None:
    """轴顺序不同的等价坐标系应判为等价。

    项目内部统一使用 always_xy 轴顺序转换坐标，因此 EPSG:4326
    与其声明经纬顺序的 WKT 版本应视为同一坐标系；而 pyproj 的
    严格比较会看到轴顺序差异。
    """
    crs: CRS = CRS.from_epsg(4326)
    swapped: CRS = _swap_axis_order(crs)
    assert crs != swapped
    assert crs_equivalent(crs, swapped) is True


def test_none_is_rejected() -> None:
    """未知坐标系与任何值（包括另一个未知）都不应判为等价。"""
    assert crs_equivalent(None, None) is False
    assert crs_equivalent(None, CRS.from_epsg(4326)) is False
    assert crs_equivalent(CRS.from_epsg(4326), None) is False


def test_geographic_bounds_accept_china_and_world() -> None:
    """真实经纬度范围应视为与地理坐标系一致。"""
    wgs84 = CRS.from_epsg(4326)
    assert geographic_bounds_are_plausible(wgs84, (73.4, 6.3, 135.1, 53.6)) is True
    assert geographic_bounds_are_plausible(wgs84, (-180.0, -90.0, 180.0, 90.0)) is True
    assert geographic_bounds_are_plausible(wgs84, (0.0, -90.0, 360.0, 90.0)) is True


def test_geographic_bounds_reject_metre_values() -> None:
    """把 Albers/UTM 米制范围标成经纬度时应被识别。"""
    wgs84 = CRS.from_epsg(4326)
    albers_like = (-673983.37, -33697.32, 654808.97, 969734.48)
    assert geographic_bounds_are_plausible(wgs84, albers_like) is False
    message = crs_coordinate_domain_error(wgs84, albers_like, "dem_reprojected")
    assert message is not None
    assert "dem_reprojected" in message
    assert "经纬度" in message
    assert "重投影" in message


def test_projected_crs_skips_geographic_domain_check() -> None:
    """投影坐标系的米制范围不应被经纬度规则误伤。"""
    utm = CRS.from_epsg(32650)
    bounds = (500000.0, 2999900.0, 500100.0, 3000000.0)
    assert geographic_bounds_are_plausible(utm, bounds) is True
    assert crs_coordinate_domain_error(utm, bounds, "dem") is None
