"""坐标参考系统等价判断与坐标语义校验。

地图显示缓存和重投影等场景需要判断两个 CRS 是否描述同一坐标系，
不能只比较 EPSG 字符串：同一坐标系可以有多个权威编号、WKT 或
Proj4 表达形式。项目内所有转换统一使用 ``always_xy=True`` 轴顺序，
因此比较时忽略 CRS 声明的轴顺序，避免坐标语义相同的坐标系被误判。

另外，经纬度 CRS 的数值必须落在合理经纬度范围内；把 Albers/UTM
米制坐标误标成 EPSG:4326 会让掩膜裁剪得到全透明空白图。
"""

from pyproj import CRS

# 允许 0–360 经度以及轻微越界；米制数量级必然超出该窗口。
_MAX_GEOGRAPHIC_X: float = 360.0
_MAX_GEOGRAPHIC_Y: float = 90.5


def crs_equivalent(left: CRS | None, right: CRS | None) -> bool:
    """判断两个 CRS 在忽略轴顺序后是否等价。

    未知坐标系（None）与任何值都不等价，包括另一个 None：
    不能静默把未声明 CRS 的数据当作与地图坐标系一致。

    参数:
        left: 参与比较的第一个坐标系，可以为空。
        right: 参与比较的第二个坐标系，可以为空。

    返回:
        True 表示两个坐标系在忽略轴顺序后描述同一坐标系。
    """
    if left is None or right is None:
        return False
    # 忽略轴顺序：内部转换统一使用 always_xy，EPSG:4326 与
    # 声明为经纬顺序的等价 WKT 应视为同一坐标系。
    return left.equals(right, ignore_axis_order=True)


def geographic_bounds_are_plausible(
    crs: CRS,
    bounds: tuple[float, float, float, float],
) -> bool:
    """判断图层范围是否符合 CRS 的坐标语义。

    投影坐标系不做数值窗口检查。地理坐标系则要求 X/Y 落在经纬度
    合理区间内；超出时通常是把投影坐标误标成了 WGS84。
    """
    if not crs.is_geographic:
        return True
    min_x, min_y, max_x, max_y = bounds
    return (
        -_MAX_GEOGRAPHIC_X <= min_x <= _MAX_GEOGRAPHIC_X
        and -_MAX_GEOGRAPHIC_X <= max_x <= _MAX_GEOGRAPHIC_X
        and -_MAX_GEOGRAPHIC_Y <= min_y <= _MAX_GEOGRAPHIC_Y
        and -_MAX_GEOGRAPHIC_Y <= max_y <= _MAX_GEOGRAPHIC_Y
    )


def crs_coordinate_domain_error(
    crs: CRS,
    bounds: tuple[float, float, float, float],
    layer_name: str,
) -> str | None:
    """范围与 CRS 语义不一致时返回中文说明，否则返回空。"""
    if geographic_bounds_are_plausible(crs, bounds):
        return None
    min_x, min_y, max_x, max_y = bounds
    return (
        f"图层「{layer_name}」的坐标系是经纬度，但坐标范围明显不是经纬度"
        f"（当前约为 x={min_x:.0f}～{max_x:.0f}，y={min_y:.0f}～{max_y:.0f}）。"
        "这通常是把投影坐标误标成了 WGS84。"
        "请对原始数据使用「重投影为新图层」转换坐标，不要使用「定义/修正 CRS」。"
    )
