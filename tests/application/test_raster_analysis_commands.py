"""栅格分析应用用例测试：GisApplication 命令与历史记录。"""

from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from pyproj import CRS

from app.application.errors import InvalidRasterAnalysisParameters
from app.application.gis_application import GisApplication
from app.application.raster_analysis import (
    DemAnalysisRequest,
    RasterReclassifyRequest,
    ReclassRule,
)
from app.domain.raster_layer import RasterLayer
from app.infrastructure.file_io.auto_reader import AutoDataReader

_CRS = CRS.from_epsg(32650)
_TRANSFORM = Affine.translation(500000.0, 3000000.0) * Affine.scale(10.0, -10.0)


def _write_tif(path: Path, data: np.ndarray, nodata: float | None = -9999.0) -> Path:
    """写出单波段测试 GeoTIFF。"""
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=data.shape[1],
        height=data.shape[0],
        count=1,
        dtype=data.dtype,
        crs=_CRS,
        transform=_TRANSFORM,
        nodata=nodata,
    ) as dst:
        dst.write(data, 1)
    return path


def _app_with_raster(tmp_path: Path) -> tuple[GisApplication, str]:
    """创建加载了一个测试栅格的应用入口。"""
    data = np.arange(16, dtype=np.float32).reshape(4, 4)
    source = _write_tif(tmp_path / "input.tif", data, nodata=None)
    application = GisApplication(AutoDataReader())
    result = application.open_data(source)
    return application, result.layer_id


class TestRasterReclassifyCommand:
    """重分类应用命令。"""

    def test_success_adds_layer_and_history(self, tmp_path: Path) -> None:
        """成功的重分类应加入工作区并记录历史。"""
        application, layer_id = _app_with_raster(tmp_path)
        request = RasterReclassifyRequest(
            input_layer_id=layer_id,
            band_index=1,
            rules=(ReclassRule(lower=0.0, upper=8.0, output_value=1.0),),
            unmatched_policy="nodata",
            output_layer_name="重分类",
            output_path=tmp_path / "reclass.tif",
        )

        result = application.raster_reclassify(request)

        assert result.output_layer_name == "重分类"
        assert len(application.snapshot().layers) == 2
        assert application.snapshot().active_layer_id == result.output_layer_id
        assert len(application.analysis_runs) == 1
        run = application.analysis_runs[0]
        assert run.algorithm_id == "raster_reclassify"
        assert run.status == "completed"
        assert application.is_modified

    def test_failure_records_history_and_keeps_workspace(
        self, tmp_path: Path
    ) -> None:
        """失败的重分类不应加入图层但应记录失败历史。"""
        application, layer_id = _app_with_raster(tmp_path)
        request = RasterReclassifyRequest(
            input_layer_id=layer_id,
            band_index=1,
            rules=(ReclassRule(lower=0.0, upper=8.0, output_value=1.0),),
            unmatched_policy="nodata",
            output_layer_name="重分类",
            output_path=tmp_path / "不存在目录" / "reclass.tif",
        )

        with pytest.raises(InvalidRasterAnalysisParameters):
            application.raster_reclassify(request)

        assert len(application.snapshot().layers) == 1
        assert len(application.analysis_runs) == 1
        assert application.analysis_runs[0].status == "failed"


class TestDemAnalysisCommand:
    """DEM 分析应用命令。"""

    def test_success_records_mode_in_history(self, tmp_path: Path) -> None:
        """成功的 DEM 分析应在历史中记录分析模式。"""
        application, layer_id = _app_with_raster(tmp_path)
        request = DemAnalysisRequest(
            input_layer_id=layer_id,
            mode="slope",
            output_layer_name="坡度",
            output_path=tmp_path / "slope.tif",
        )

        result = application.dem_analysis(request)

        assert result.mode == "slope"
        run = application.analysis_runs[0]
        assert run.algorithm_id == "dem_analysis"
        assert run.parameters["mode"] == "slope"


class TestRasterResultRegistration:
    """结果注册与失败记录命令。"""

    def _result_layer(
        self,
        *,
        crs: CRS | None = _CRS,
        source_path: Path = Path("result.tif"),
    ) -> RasterLayer:
        """构建一个最小的结果栅格图层。"""
        data = np.ones((1, 2, 2), dtype=np.float32)
        image = np.full((2, 2, 4), 255, dtype=np.uint8)
        return RasterLayer.create(
            name="结果",
            raster_data=data,
            image_data=image,
            valid_mask=np.ones((2, 2), dtype=bool),
            transform=Affine.identity(),
            crs=crs,
            bounds=(0.0, 0.0, 2.0, 2.0),
            source_path=source_path,
        )

    def test_register_adds_layer_and_run(self) -> None:
        """注册结果图层应加入工作区并记录成功历史。"""
        application = GisApplication(AutoDataReader())
        layer = self._result_layer()

        application.register_raster_analysis_layer(
            layer,
            algorithm_id="raster_reclassify",
            input_layer_ids=("input",),
            parameters={"band_index": 1},
            output_layer_name="结果",
        )

        assert len(application.snapshot().layers) == 1
        assert application.snapshot().active_layer_id == layer.layer_id
        run = application.analysis_runs[0]
        assert run.status == "completed"
        assert run.output_layer_ids == (layer.layer_id,)

    def test_register_unknown_crs_failure_removes_new_output(self, tmp_path: Path) -> None:
        """结果图层因未定义 CRS 注册失败时，应回滚新写出的文件。"""
        application, input_layer_id = _app_with_raster(tmp_path)
        output_path = tmp_path / "result.tif"
        output_path.write_bytes(b"new analysis output")
        layer = self._result_layer(
            crs=None,
            source_path=output_path,
        )

        with pytest.raises(ValueError, match="未定义 CRS"):
            application.register_raster_analysis_layer(
                layer,
                algorithm_id="raster_reclassify",
                input_layer_ids=(input_layer_id,),
                parameters={"band_index": 1},
                output_layer_name="结果",
            )

        assert not output_path.exists()
        assert tuple(item.layer_id for item in application.snapshot().layers) == (
            input_layer_id,
        )
        assert application.analysis_runs == ()

    def test_record_failed_marks_history(self) -> None:
        """记录失败分析应只影响历史，不影响工作区。"""
        application = GisApplication(AutoDataReader())

        application.record_failed_raster_analysis(
            "dem_analysis", ("input",), {"mode": "slope"}, "测试失败"
        )

        assert len(application.snapshot().layers) == 0
        run = application.analysis_runs[0]
        assert run.status == "failed"
        assert run.message == "测试失败"
