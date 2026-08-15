"""收集 Rasterio 的 GDAL/PROJ 数据、扩展模块和 delvewheel DLL。"""

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_delvewheel_libs_directory,
    collect_submodules,
)

datas = collect_data_files(
    "rasterio",
    includes=["gdal_data/**", "proj_data/**"],
)
binaries: list[tuple[str, str]] = []
datas, binaries = collect_delvewheel_libs_directory(
    "rasterio",
    datas=datas,
    binaries=binaries,
)
hiddenimports = collect_submodules(
    "rasterio",
    filter=lambda name: not name.startswith("rasterio.rio"),
)
