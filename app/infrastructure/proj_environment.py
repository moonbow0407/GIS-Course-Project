"""PROJ/GDAL 数据目录环境隔离。

本机可能安装 PostgreSQL + PostGIS，其安装器会写入机器级 ``PROJ_LIB`` /
``GDAL_DATA`` 环境变量（指向旧版 proj.db，数据库布局版本不兼容）。rasterio 与
pyproj 各自捆绑了配套 PROJ 数据，但 libproj 在建立上下文时仍会读取进程继承的
``PROJ_LIB``，导致 EPSG 数据库查询失败。本模块在任何 GIS 库导入之前，把
``PROJ_DATA`` 指向虚拟环境内捆绑的数据目录并清理外部污染变量。

数据目录必须选 rasterio 捆绑的 ``rasterio/proj_data``：rasterio 内置的
PROJ 9.x 要求 proj.db 布局版本 >= 5，而 pyproj 捆绑的 proj.db 较旧（布局
1.4），反向使用会报同样的版本错误；新版 proj.db 可向下兼容 pyproj 的 PROJ。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _bundled_proj_data_dir() -> Path:
    """定位 venv 内 rasterio 捆绑的 PROJ 数据目录，不导入 rasterio 本体。

    rasterio/pyproj 的 C 扩展在导入时就会初始化 PROJ 上下文，因此只能通过
    包元数据定位目录，不能靠导入模块后再取路径。
    """
    frozen_root: object = getattr(sys, "_MEIPASS", None)
    if frozen_root is not None:
        # 冻结包中 rasterio 及其数据目录由 PyInstaller 钩子收集到根目录。
        return Path(str(frozen_root)) / "rasterio" / "proj_data"

    from importlib.metadata import distribution

    # locate_file("") 解析到分发目录（site-packages），避免依赖 files() 的条目顺序。
    distribution_root = Path(str(distribution("rasterio").locate_file("")))
    return distribution_root / "rasterio" / "proj_data"


def configure_proj_environment() -> None:
    """强制 PROJ 使用 venv 内捆绑的数据目录。

    应在 rasterio / pyproj / GDAL 首次导入之前调用：PROJ 上下文在建立时
    读取 ``PROJ_DATA`` / ``PROJ_LIB``，抢在导入前设置才能保证生效。

    状态变化：
        - 移除进程继承的 ``PROJ_LIB`` 与 ``GDAL_DATA``（不影响机器级设置）；
        - 设置 ``PROJ_DATA`` 指向 venv 内 rasterio 捆绑的 proj 数据目录。

    异常：
        RuntimeError: 捆绑数据目录缺失时抛出，避免带着错误数据目录静默运行。
    """
    # pyproj.datadir.get_data_dir() 等辅助函数会优先读取 PROJ_DATA/PROJ_LIB，
    # 先清理污染变量，避免后续任何代码误解析到 PostGIS 的旧目录。
    os.environ.pop("PROJ_LIB", None)
    os.environ.pop("GDAL_DATA", None)

    data_dir: Path = _bundled_proj_data_dir()
    if not (data_dir / "proj.db").is_file():
        raise RuntimeError(f"PROJ 数据目录不可用: {data_dir}")
    # PROJ >= 9.1 首选 PROJ_DATA；rasterio/pyproj 捆绑版本均满足。
    os.environ["PROJ_DATA"] = str(data_dir)
