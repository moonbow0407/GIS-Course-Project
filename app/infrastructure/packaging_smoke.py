"""冻结发布包的关键原生依赖自检。"""

import json
import traceback
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TypeAlias

import numpy as np
from affine import Affine
from pyproj import CRS, Transformer
from shapely.geometry import Point

from app.domain.feature import Feature
from app.domain.raster_layer import RasterLayer
from app.domain.vector_layer import VectorLayer
from app.infrastructure.file_io.geopandas_vector_reader import GeoPandasVectorReader
from app.infrastructure.file_io.geopandas_vector_writer import GeoPandasVectorWriter
from app.infrastructure.file_io.rasterio_raster_reader import RasterioRasterReader
from app.infrastructure.file_io.rasterio_raster_writer import RasterioRasterWriter

CheckDetails: TypeAlias = dict[str, object]
CheckFunction: TypeAlias = Callable[[Path], CheckDetails]

REQUIRED_PACKAGING_CHECKS: tuple[str, ...] = (
    "style_resource",
    "qt_print_support",
    "vector_geopackage",
    "raster_geotiff",
    "coordinate_transform",
    "scipy_ndimage",
    "postgis_driver",
)


def run_packaging_smoke(report_path: Path) -> int:
    """执行冻结发布包依赖自检，并把完整结果写入 JSON。

    参数:
        report_path: 自检报告输出位置。

    返回:
        全部检查通过时返回 0，否则返回 1。
    """
    resolved_report: Path = report_path.expanduser().resolve()
    resolved_report.parent.mkdir(parents=True, exist_ok=True)
    checks: dict[str, CheckDetails] = {}

    with TemporaryDirectory(prefix="gis-desktop-smoke-") as temporary_directory:
        workspace: Path = Path(temporary_directory)
        functions: dict[str, CheckFunction] = {
            "style_resource": _check_style_resource,
            "qt_print_support": _check_qt_print_support,
            "vector_geopackage": _check_vector_geopackage,
            "raster_geotiff": _check_raster_geotiff,
            "coordinate_transform": _check_coordinate_transform,
            "scipy_ndimage": _check_scipy_ndimage,
            "postgis_driver": _check_postgis_driver,
        }
        for name in REQUIRED_PACKAGING_CHECKS:
            try:
                details: CheckDetails = functions[name](workspace)
                checks[name] = {"status": "ok", **details}
            except Exception as error:  # noqa: BLE001 - 自检必须收集所有失败项
                checks[name] = {
                    "status": "error",
                    "error": f"{type(error).__name__}: {error}",
                    "traceback": traceback.format_exc(),
                }

    succeeded: bool = all(check["status"] == "ok" for check in checks.values())
    payload: dict[str, object] = {
        "status": "ok" if succeeded else "error",
        "checks": checks,
    }
    resolved_report.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0 if succeeded else 1


def _check_style_resource(_workspace: Path) -> CheckDetails:
    """确认全局 QSS 已按应用目录结构进入发布包。"""
    style_path: Path = Path(__file__).resolve().parents[1] / "resources" / "styles" / "main.qss"
    content: str = style_path.read_text(encoding="utf-8")
    if not content.strip():
        raise RuntimeError("main.qss 内容为空")
    return {"path": str(style_path), "characters": len(content)}


def _check_qt_print_support(_workspace: Path) -> CheckDetails:
    """确认延迟使用的 Qt 打印模块已被收集。"""
    from PySide6.QtPrintSupport import QPrinter

    return {"pdf_output_format": QPrinter.OutputFormat.PdfFormat.name}


def _check_vector_geopackage(workspace: Path) -> CheckDetails:
    """通过项目适配器往同一 GeoPackage 写入并读取两个图层。"""
    output_path: Path = workspace / "vector-smoke.gpkg"
    writer: GeoPandasVectorWriter = GeoPandasVectorWriter()
    for index, layer_name in enumerate(("first_layer", "second_layer"), start=1):
        layer: VectorLayer = VectorLayer.create(
            name=layer_name,
            features=(
                Feature(
                    fid=index,
                    geometry=Point(float(index), float(index)),
                    attributes={"name": layer_name},
                ),
            ),
            crs=CRS.from_epsg(4326),
        )
        writer.write(layer, output_path, layer_name=layer_name)

    loaded: VectorLayer = GeoPandasVectorReader().read(
        output_path,
        layer_name="second_layer",
    )
    if loaded.features[0].attributes["name"] != "second_layer":
        raise RuntimeError("GeoPackage 图层属性读取结果不一致")
    return {"layer": loaded.name, "feature_count": len(loaded.features)}


def _check_raster_geotiff(workspace: Path) -> CheckDetails:
    """通过项目适配器写出并读取带 CRS 的 GeoTIFF。"""
    output_path: Path = workspace / "raster-smoke.tif"
    raster_values = np.array([[[1, 2], [3, 4]]], dtype=np.int16)
    valid_mask = np.ones((2, 2), dtype=np.bool_)
    transform: Affine = Affine.translation(100, 200) * Affine.scale(10, -10)
    layer: RasterLayer = RasterLayer.create(
        name="raster-smoke",
        raster_data=raster_values,
        image_data=np.full((2, 2, 4), 255, dtype=np.uint8),
        valid_mask=valid_mask,
        transform=transform,
        crs=CRS.from_epsg(3857),
        bounds=(100, 180, 120, 200),
        nodata=-9999,
    )
    RasterioRasterWriter().write(layer, output_path)
    loaded: RasterLayer = RasterioRasterReader().read(output_path)
    if loaded.crs is None or loaded.crs.to_epsg() != 3857:
        raise RuntimeError("GeoTIFF CRS 读取结果不一致")
    return {"epsg": loaded.crs.to_epsg(), "shape": list(loaded.raster_data.shape)}


def _check_coordinate_transform(_workspace: Path) -> CheckDetails:
    """确认 PROJ 数据库和坐标转换流水线可用。"""
    transformer: Transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    x, y = transformer.transform(116.391, 39.907)
    if not (12_000_000 < x < 14_000_000 and 4_000_000 < y < 6_000_000):
        raise RuntimeError("坐标转换结果超出预期范围")
    return {"x": x, "y": y}


def _check_scipy_ndimage(_workspace: Path) -> CheckDetails:
    """确认栅格分析延迟导入的 SciPy 模块可用。"""
    from scipy.ndimage import minimum_filter

    values = np.array([[3, 2], [4, 1]], dtype=np.int16)
    filtered = minimum_filter(values, size=2)
    return {"minimum": int(filtered.min())}


def _check_postgis_driver(_workspace: Path) -> CheckDetails:
    """确认 SQLAlchemy 的 psycopg 方言和二进制实现可用。"""
    from psycopg import pq
    from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg

    dialect = PGDialect_psycopg()
    if dialect.driver != "psycopg":
        raise RuntimeError("SQLAlchemy 未加载 psycopg 方言")
    return {"dialect": dialect.driver, "pq_implementation": pq.__impl__}
