"""坐标参考系统等价判断工具。

地图显示缓存和重投影等场景需要判断两个 CRS 是否描述同一坐标系，
不能只比较 EPSG 字符串：同一坐标系可以有多个权威编号、WKT 或
Proj4 表达形式。项目内所有转换统一使用 ``always_xy=True`` 轴顺序，
因此比较时忽略 CRS 声明的轴顺序，避免坐标语义相同的坐标系被误判。
"""

from pyproj import CRS


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
