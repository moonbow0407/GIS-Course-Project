"""打开数据准备阶段的进度与显示优化测试。"""

from pathlib import Path

import numpy as np
from affine import Affine
from pyproj import CRS
from shapely.geometry import Point

from app.application.gis_application import GisApplication
from app.domain.feature import Feature
from app.domain.raster_layer import RasterLayer
from app.domain.symbology import RasterRendererType
from app.domain.vector_layer import VectorLayer


def _raster_layer(path: Path) -> RasterLayer:
    """构造带占位灰度符号的测试栅格。"""
    values = np.array([[[100.0, 200.0], [800.0, 1500.0]]], dtype=np.float32)
    image = np.zeros((2, 2, 4), dtype=np.uint8)
    return RasterLayer.create(
        name=path.stem,
        raster_data=values,
        image_data=image,
        valid_mask=np.ones((2, 2), dtype=np.bool_),
        transform=Affine.identity(),
        crs=CRS.from_epsg(4326),
        bounds=(0.0, 0.0, 2.0, 2.0),
        source_path=path,
    )


def test_prepare_open_data_builds_raster_display_before_read_and_reports_progress(
    tmp_path: Path,
) -> None:
    """大栅格应先准备显示金字塔，再读取预览，并把阶段进度回传给界面。"""
    source = tmp_path / "dem.tif"
    calls: list[str] = []
    reports: list[tuple[int, int, str]] = []

    class RecordingReader:
        """记录准备与读取顺序的测试读取器。"""

        def prepare_raster_display(self, path: Path) -> None:
            calls.append(f"prepare:{path.name}")

        def read(
            self,
            path: Path,
            target_crs: object | None = None,
            layer_name: str | None = None,
            source_crs_override: object | None = None,
        ) -> RasterLayer:
            del target_crs, layer_name, source_crs_override
            calls.append(f"read:{path.name}")
            return _raster_layer(path)

    application = GisApplication(RecordingReader())
    layer = application.prepare_open_data(
        source,
        progress_callback=lambda current, total, message: reports.append(
            (current, total, message)
        ),
    )

    assert calls == ["prepare:dem.tif", "read:dem.tif"]
    assert reports
    assert reports[0][0] == 0
    assert reports[-1][0] == reports[-1][1]
    assert any("金字塔" in message or "预览" in message for _, _, message in reports)
    assert isinstance(layer, RasterLayer)
    assert layer.symbology is not None
    assert layer.symbology.renderer_type is RasterRendererType.STRETCH
    assert layer.symbology.color_scheme == "terrain"


def test_prepare_open_data_reports_vector_progress_without_raster_prepare() -> None:
    """矢量打开应报告读取进度，且不要求读取器提供栅格金字塔接口。"""
    vector = VectorLayer.create(
        name="roads",
        features=(Feature(fid=1, geometry=Point(0, 0), attributes={}),),
        crs=CRS.from_epsg(4326),
        source_path=Path("roads.shp"),
    )
    reports: list[str] = []

    class VectorOnlyReader:
        def read(
            self,
            path: Path,
            target_crs: object | None = None,
            layer_name: str | None = None,
            source_crs_override: object | None = None,
        ) -> VectorLayer:
            del path, target_crs, layer_name, source_crs_override
            return vector

    application = GisApplication(VectorOnlyReader())
    layer = application.prepare_open_data(
        Path("roads.shp"),
        progress_callback=lambda _current, _total, message: reports.append(message),
    )

    assert layer is vector
    assert reports
    assert any("矢量" in message or "读取" in message for message in reports)
