"""收集 GeoPandas 默认 I/O 引擎 Pyogrio 的 GDAL/PROJ 数据和 DLL。"""

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_delvewheel_libs_directory,
    collect_submodules,
)

datas = collect_data_files(
    "pyogrio",
    includes=["gdal_data/**", "proj_data/**"],
)
binaries: list[tuple[str, str]] = []
datas, binaries = collect_delvewheel_libs_directory(
    "pyogrio",
    datas=datas,
    binaries=binaries,
)
hiddenimports = collect_submodules(
    "pyogrio",
    filter=lambda name: not name.startswith("pyogrio.tests"),
)
