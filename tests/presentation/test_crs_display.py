"""坐标系状态栏展示测试。"""

from pyproj import CRS

from app.presentation.main_window import MainWindow


def test_format_crs_keeps_esri_authority_distinct_from_epsg() -> None:
    """ESRI 自定义投影应显示 ESRI 编码和可读名称，而不是伪造 EPSG 编码。"""
    formatted: str = MainWindow._format_crs(CRS.from_user_input("ESRI:102026"))

    assert formatted.startswith("ESRI:102026 · ")
    assert "Asia_North_Equidistant_Conic" in formatted


def test_format_crs_displays_epsg_authority_when_available() -> None:
    """标准 EPSG 坐标系应保留 EPSG 权威编号。"""
    formatted: str = MainWindow._format_crs(CRS.from_epsg(4326))

    assert formatted.startswith("EPSG:4326 · ")
