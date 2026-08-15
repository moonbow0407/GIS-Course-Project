"""PROJ 数据目录环境隔离测试。"""

import os
import subprocess
import sys
from pathlib import Path


def test_conftest_has_isolated_proj_data_directory_for_test_session() -> None:
    """测试会话的环境变量应已被 conftest 指向 venv 捆绑的 PROJ 数据目录。"""
    proj_data: str | None = os.environ.get("PROJ_DATA")
    assert proj_data is not None
    # 必须指向 rasterio 捆绑目录：其 proj.db 布局版本是 venv 内最高的，
    # 能同时满足 rasterio（要求 >= 5）与 pyproj 的 PROJ。
    assert Path(proj_data).name == "proj_data"
    assert Path(proj_data, "proj.db").is_file()
    assert "PROJ_LIB" not in os.environ
    assert "GDAL_DATA" not in os.environ


def test_configure_cleans_polluted_environment_in_fresh_process() -> None:
    """新进程中模拟 PostGIS 污染，配置后 rasterio 应能正常解析 EPSG。

    必须在干净的子进程验证：PROJ 数据目录在 libproj 初始化时一次性读取，
    当前测试会话早已初始化过 PROJ，无法复现"导入前修复"的时序。
    """
    script = "\n".join(
        [
            "import os",
            "os.environ['PROJ_LIB'] = r'D:\\polluted\\proj'",
            "os.environ['GDAL_DATA'] = r'D:\\polluted\\gdal-data'",
            "from app.infrastructure.proj_environment import configure_proj_environment",
            "configure_proj_environment()",
            "assert 'PROJ_LIB' not in os.environ",
            "assert 'GDAL_DATA' not in os.environ",
            "assert os.path.isfile(os.path.join(os.environ['PROJ_DATA'], 'proj.db'))",
            "import rasterio.crs",
            "assert rasterio.crs.CRS.from_epsg(3857).to_epsg() == 3857",
            "import pyproj",
            "assert pyproj.CRS.from_epsg(3857).to_epsg() == 3857",
            "print('ISOLATED_OK', os.environ['PROJ_DATA'])",
        ]
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ISOLATED_OK" in result.stdout
