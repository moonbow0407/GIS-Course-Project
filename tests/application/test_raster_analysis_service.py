"""栅格分析编排服务集成测试：真实临时 GeoTIFF 端到端验证。"""

from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from pyproj import CRS
from shapely.geometry import Polygon

import app.application.raster_analysis_service as service_module
from app.application.errors import (
    EmptyRasterResult,
    InvalidRasterAnalysisParameters,
    RasterAnalysisFailed,
    UnsupportedRasterAnalysisInput,
)
from app.application.raster_analysis import (
    DemAnalysisRequest,
    RasterClipRequest,
    RasterReclassifyRequest,
    ReclassRule,
)
from app.application.raster_analysis_service import RasterAnalysisService
from app.application.raster_calculator import BandMapping, RasterCalculatorRequest
from app.domain.feature import Feature
from app.domain.raster_layer import RasterLayer
from app.domain.symbology import RasterRendererType
from app.domain.vector_layer import VectorLayer
from app.infrastructure.file_io.rasterio_raster_reader import RasterioRasterReader

_CRS = CRS.from_epsg(32650)
_TRANSFORM = Affine.translation(500000.0, 3000000.0) * Affine.scale(10.0, -10.0)


def _write_tif(
    path: Path,
    data: np.ndarray,
    crs: CRS = _CRS,
    transform: Affine = _TRANSFORM,
    nodata: float | None = -9999.0,
) -> Path:
    """写出一个单波段临时 GeoTIFF。"""
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=data.shape[1],
        height=data.shape[0],
        count=1,
        dtype=data.dtype,
        crs=crs,
        transform=transform,
        nodata=nodata,
    ) as dst:
        dst.write(data, 1)
        mask = np.where(data != nodata, 255, 0).astype(np.uint8) if nodata is not None \
            else np.full(data.shape, 255, dtype=np.uint8)
        dst.write_mask(mask)
    return path


def _write_multiband_tif(
    path: Path,
    data: np.ndarray,
    crs: CRS = _CRS,
    transform: Affine = _TRANSFORM,
) -> Path:
    """鍐欏嚭涓€涓娉㈡涓存椂 GeoTIFF銆?"""
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=data.shape[2],
        height=data.shape[1],
        count=data.shape[0],
        dtype=data.dtype,
        crs=crs,
        transform=transform,
    ) as dst:
        dst.write(data)
        dst.write_mask(np.full(data.shape[1:], 255, dtype=np.uint8))
    return path


def _load(path: Path) -> RasterLayer:
    """使用项目读取器加载栅格图层。"""
    return RasterioRasterReader().read(path)


def _polygon_layer(polygon: Polygon, layer_id: str = "mask-layer") -> VectorLayer:
    """构建仅含一个面要素的矢量图层。"""
    return VectorLayer.create(
        layer_id=layer_id,
        name="掩膜",
        features=(Feature(fid=1, geometry=polygon, attributes={}),),
        crs=_CRS,
        geometry_family=None,
    )


class TestCalculatorService:
    """栅格计算器分块执行。"""

    def test_calculator_writes_result_and_reports_progress(self, tmp_path: Path) -> None:
        """计算器应按窗口写出结果，并报告从准备到完成的进度。"""
        data = np.stack(
            [
                np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32),
                np.full((2, 2), 10.0, dtype=np.float32),
            ],
            axis=0,
        )
        source = _write_multiband_tif(tmp_path / "bands.tif", data)
        layer = _load(source)
        progress: list[tuple[int, int]] = []
        request = RasterCalculatorRequest(
            expression='"a" + "b"',
            band_mappings=(
                BandMapping(alias="a", layer_id=layer.layer_id, band_index=1),
                BandMapping(alias="b", layer_id=layer.layer_id, band_index=2),
            ),
            output_layer_name="sum",
            output_path=tmp_path / "sum.tif",
        )

        result = RasterAnalysisService(
            progress_callback=lambda done, total: (
                progress.append((done, total)) or True
            )
        ).execute_calculator(request, {layer.layer_id: layer})

        assert result.name == "sum"
        assert progress[0][0] == 0
        assert progress[-1][0] == progress[-1][1]
        with rasterio.open(tmp_path / "sum.tif") as dst:
            np.testing.assert_allclose(dst.read(1), [[11.0, 12.0], [13.0, 14.0]])

    def test_calculator_rejects_mixed_crs_without_implicit_reprojection(
        self, tmp_path: Path
    ) -> None:
        """多输入栅格 CRS 不一致时必须先手动重投影。"""
        first_path = _write_tif(
            tmp_path / "first.tif", np.ones((2, 2), dtype=np.float32)
        )
        second_path = _write_tif(
            tmp_path / "second.tif",
            np.ones((2, 2), dtype=np.float32),
            crs=CRS.from_epsg(4326),
            transform=Affine.translation(120.0, 31.0) * Affine.scale(0.01, -0.01),
        )
        first = _load(first_path)
        second = _load(second_path)
        request = RasterCalculatorRequest(
            expression='"a" + "b"',
            band_mappings=(
                BandMapping(alias="a", layer_id=first.layer_id, band_index=1),
                BandMapping(alias="b", layer_id=second.layer_id, band_index=1),
            ),
            output_layer_name="mixed",
            output_path=tmp_path / "mixed.tif",
        )

        with pytest.raises(InvalidRasterAnalysisParameters, match="CRS 不一致"):
            RasterAnalysisService().execute_calculator(
                request,
                {first.layer_id: first, second.layer_id: second},
            )

    def test_calculator_uses_explicit_reference_grid_for_same_crs_inputs(
        self, tmp_path: Path
    ) -> None:
        """同 CRS 但网格不同的输入可按显式参考栅格临时对齐。"""
        first_path = _write_tif(
            tmp_path / "reference.tif", np.ones((2, 2), dtype=np.float32)
        )
        second_path = _write_tif(
            tmp_path / "shifted.tif",
            np.full((2, 2), 2.0, dtype=np.float32),
            transform=Affine.translation(500005.0, 3000000.0)
            * Affine.scale(10.0, -10.0),
        )
        first = _load(first_path)
        second = _load(second_path)
        request = RasterCalculatorRequest(
            expression='"a" + "b"',
            band_mappings=(
                BandMapping(alias="a", layer_id=first.layer_id, band_index=1),
                BandMapping(alias="b", layer_id=second.layer_id, band_index=1),
            ),
            output_layer_name="aligned",
            output_path=tmp_path / "aligned.tif",
            reference_layer_id=first.layer_id,
        )

        result = RasterAnalysisService().execute_calculator(
            request,
            {first.layer_id: first, second.layer_id: second},
        )

        assert result.raster_shape == first.raster_shape
        assert result.crs == first.crs


class TestReclassifyService:
    """重分类端到端。"""

    def test_reclassify_writes_expected_values(self, tmp_path: Path) -> None:
        """重分类结果值、NoData 和网格应正确。"""
        data = np.array(
            [[1.0, 5.0, 15.0], [25.0, -9999.0, 8.0]], dtype=np.float32
        )
        source = _write_tif(tmp_path / "input.tif", data)
        layer = _load(source)
        request = RasterReclassifyRequest(
            input_layer_id=layer.layer_id,
            band_index=1,
            rules=(
                ReclassRule(lower=0.0, upper=10.0, output_value=1.0),
                ReclassRule(lower=10.0, upper=20.0, output_value=2.0),
            ),
            unmatched_policy="nodata",
            output_dtype="float32",
            output_nodata=-9999.0,
            output_layer_name="重分类结果",
            output_path=tmp_path / "reclass.tif",
        )

        result = RasterAnalysisService().execute_reclassify(
            request, {layer.layer_id: layer}
        )

        assert result.name == "重分类结果"
        assert result.raster_shape == (2, 3)
        assert result.symbology.renderer_type is RasterRendererType.CLASSIFIED
        assert len(result.symbology.classes) == 2
        assert [category.label for category in result.symbology.classes] == [
            "[0, 10)",
            "[10, 20)",
        ]
        assert len(np.unique(result.image_data[result.valid_mask, :3], axis=0)) == 2
        with rasterio.open(tmp_path / "reclass.tif") as dst:
            values = dst.read(1)
            assert values[0, 0] == 1.0
            assert values[0, 1] == 1.0
            assert values[0, 2] == 2.0
            # 25 未匹配 → NoData；源 NoData 保持无效。
            assert dst.dataset_mask()[1, 0] == 0
            assert dst.dataset_mask()[1, 1] == 0

    def test_reclassify_overwrites_existing_output(self, tmp_path: Path) -> None:
        """用户确认覆盖后，已存在的输出文件应被新结果替换。"""
        source = _write_tif(
            tmp_path / "input.tif", np.ones((2, 2), dtype=np.float32)
        )
        layer = _load(source)
        occupied = tmp_path / "reclass.tif"
        occupied.write_bytes(b"existing")
        request = RasterReclassifyRequest(
            input_layer_id=layer.layer_id,
            band_index=1,
            rules=(ReclassRule(lower=0.0, upper=2.0, output_value=1.0),),
            unmatched_policy="nodata",
            output_layer_name="x",
            output_path=occupied,
        )

        result = RasterAnalysisService().execute_reclassify(
            request, {layer.layer_id: layer}
        )

        assert result.source_path == occupied.resolve()
        with rasterio.open(occupied) as dst:
            assert dst.read(1)[0, 0] == 1.0

    def test_reclassify_rejects_overwriting_input_source(self, tmp_path: Path) -> None:
        """分析结果不能覆盖当前输入栅格源文件。"""
        source = _write_tif(
            tmp_path / "input.tif", np.ones((2, 2), dtype=np.float32)
        )
        layer = _load(source)
        request = RasterReclassifyRequest(
            input_layer_id=layer.layer_id,
            band_index=1,
            rules=(ReclassRule(lower=0.0, upper=2.0, output_value=1.0),),
            unmatched_policy="nodata",
            output_layer_name="x",
            output_path=source,
        )
        with pytest.raises(InvalidRasterAnalysisParameters, match="输入栅格源文件"):
            RasterAnalysisService().execute_reclassify(
                request, {layer.layer_id: layer}
            )

    def test_reclassify_missing_band_rejected(self, tmp_path: Path) -> None:
        """波段编号超出范围应被拒绝。"""
        source = _write_tif(
            tmp_path / "input.tif", np.ones((2, 2), dtype=np.float32)
        )
        layer = _load(source)
        request = RasterReclassifyRequest(
            input_layer_id=layer.layer_id,
            band_index=5,
            rules=(ReclassRule(lower=0.0, upper=2.0, output_value=1.0),),
            unmatched_policy="nodata",
            output_layer_name="x",
            output_path=tmp_path / "out.tif",
        )
        with pytest.raises(InvalidRasterAnalysisParameters, match="波段"):
            RasterAnalysisService().execute_reclassify(
                request, {layer.layer_id: layer}
            )

    def test_non_raster_input_rejected(self) -> None:
        """输入不是栅格图层时应拒绝。"""
        vector = _polygon_layer(
            Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
        )
        request = RasterReclassifyRequest(
            input_layer_id=vector.layer_id,
            band_index=1,
            rules=(ReclassRule(lower=0.0, upper=2.0, output_value=1.0),),
            unmatched_policy="nodata",
            output_layer_name="x",
            output_path=Path("out.tif"),
        )
        with pytest.raises(UnsupportedRasterAnalysisInput):
            RasterAnalysisService().execute_reclassify(
                request, {vector.layer_id: vector}
            )


class TestDemService:
    """DEM 地形分析端到端。"""

    def test_slope_on_plane_dem(self, tmp_path: Path) -> None:
        """平面 DEM 的坡度应接近理论值。"""
        dem = np.tile(np.arange(20, dtype=np.float32) * 5.0, (20, 1))
        source = _write_tif(tmp_path / "dem.tif", dem, nodata=None)
        layer = _load(source)
        request = DemAnalysisRequest(
            input_layer_id=layer.layer_id,
            mode="slope",
            output_layer_name="坡度",
            output_path=tmp_path / "slope.tif",
        )

        result = RasterAnalysisService().execute_dem_analysis(
            request, {layer.layer_id: layer}
        )

        expected = np.degrees(np.arctan(0.5))
        with rasterio.open(tmp_path / "slope.tif") as dst:
            values = dst.read(1)
        np.testing.assert_allclose(values[10, 10], expected, atol=1e-2)
        assert result.symbology is not None
        assert result.symbology.renderer_type is RasterRendererType.CLASSIFIED
        assert any("陡坡" in category.label for category in result.symbology.classes)
        valid_rgb = result.image_data[result.valid_mask][:, :3]
        assert valid_rgb.size > 0
        # 分类色不是灰度拉伸：有效像元的 RGB 三通道不应全部相等。
        assert not np.all(valid_rgb[:, 0] == valid_rgb[:, 1])

    def test_hillshade_output_is_uint8(self, tmp_path: Path) -> None:
        """山体阴影应输出 uint8 且值在 0–255 范围。"""
        dem = np.tile(np.arange(10, dtype=np.float32) * 5.0, (10, 1))
        source = _write_tif(tmp_path / "dem.tif", dem, nodata=None)
        layer = _load(source)
        request = DemAnalysisRequest(
            input_layer_id=layer.layer_id,
            mode="hillshade",
            output_layer_name="阴影",
            output_path=tmp_path / "hillshade.tif",
        )
        result = RasterAnalysisService().execute_dem_analysis(
            request, {layer.layer_id: layer}
        )
        with rasterio.open(tmp_path / "hillshade.tif") as dst:
            assert dst.dtypes == ("uint8",)
            values = dst.read(1)
        assert values.min() >= 0
        assert values.max() <= 255
        assert result.symbology is not None
        assert result.symbology.renderer_type is RasterRendererType.STRETCH
        assert result.symbology.stretch_type.value == "min_max"
        assert result.symbology.color_scheme == "gray"

    def test_geographic_crs_dem_rejected(self, tmp_path: Path) -> None:
        """地理坐标系 DEM 应被拒绝并提示重投影。"""
        dem = np.ones((4, 4), dtype=np.float32)
        geo_transform = Affine.translation(116.0, 40.0) * Affine.scale(0.01, -0.01)
        source = _write_tif(
            tmp_path / "dem_geo.tif", dem, crs=CRS.from_epsg(4326),
            transform=geo_transform, nodata=None,
        )
        layer = _load(source)
        request = DemAnalysisRequest(
            input_layer_id=layer.layer_id,
            mode="slope",
            output_layer_name="坡度",
            output_path=tmp_path / "slope.tif",
        )
        with pytest.raises(InvalidRasterAnalysisParameters, match="米制"):
            RasterAnalysisService().execute_dem_analysis(
                request, {layer.layer_id: layer}
            )


class TestClipService:
    """矢量掩膜裁剪端到端。"""

    def _inputs(
        self, tmp_path: Path
    ) -> tuple[RasterLayer, VectorLayer, Path]:
        """构建 10×10 全 1 栅格和左上角 5×5 米制面掩膜。"""
        data = np.ones((10, 10), dtype=np.float32)
        source = _write_tif(tmp_path / "raster.tif", data, nodata=None)
        raster_layer = _load(source)
        # 栅格范围 x [500000, 500100]，y [2999900, 3000000]。
        polygon = Polygon(
            [
                (500000.0, 2999950.0),
                (500050.0, 2999950.0),
                (500050.0, 3000000.0),
                (500000.0, 3000000.0),
            ]
        )
        mask_layer = _polygon_layer(polygon)
        return raster_layer, mask_layer, source

    def test_clip_preserves_all_input_bands(self, tmp_path: Path) -> None:
        """多波段掩膜裁剪应保留输入的全部波段。"""
        data = np.stack(
            [
                np.full((4, 4), 1.0, dtype=np.float32),
                np.full((4, 4), 2.0, dtype=np.float32),
            ],
            axis=0,
        )
        source = _write_multiband_tif(tmp_path / "multiband.tif", data)
        raster_layer = _load(source)
        mask_layer = _polygon_layer(
            Polygon(
                [
                    (500000.0, 2999980.0),
                    (5000020.0, 2999980.0),
                    (5000020.0, 3000000.0),
                    (500000.0, 3000000.0),
                ]
            )
        )
        request = RasterClipRequest(
            raster_layer_id=raster_layer.layer_id,
            mask_layer_id=mask_layer.layer_id,
            crop=False,
            output_layer_name="多波段裁剪",
            output_path=tmp_path / "multiband_clip.tif",
        )

        RasterAnalysisService().execute_clip(
            request,
            {raster_layer.layer_id: raster_layer, mask_layer.layer_id: mask_layer},
        )

        with rasterio.open(tmp_path / "multiband_clip.tif") as dst:
            assert dst.count == 2
            mask = dst.dataset_mask() > 0
            assert mask.any()
            np.testing.assert_array_equal(dst.read(1)[mask], 1.0)
            np.testing.assert_array_equal(dst.read(2)[mask], 2.0)
            if dst.nodata is not None:
                np.testing.assert_array_equal(dst.read(1)[~mask], dst.nodata)

    def test_clip_without_crop_keeps_extent(self, tmp_path: Path) -> None:
        """crop=False 时应保持行列数，并把掩膜外写成 NoData 且预览透明。"""
        raster_layer, mask_layer, _ = self._inputs(tmp_path)
        request = RasterClipRequest(
            raster_layer_id=raster_layer.layer_id,
            mask_layer_id=mask_layer.layer_id,
            crop=False,
            output_layer_name="裁剪",
            output_path=tmp_path / "clip.tif",
        )
        result = RasterAnalysisService().execute_clip(
            request,
            {raster_layer.layer_id: raster_layer, mask_layer.layer_id: mask_layer},
        )
        with rasterio.open(tmp_path / "clip.tif") as dst:
            assert dst.width == 10 and dst.height == 10
            mask = dst.dataset_mask()
            values = dst.read(1)
        # 上半部分（掩膜内）有效，下半部分无效。
        assert mask[:5, :5].all()
        assert not mask[6:, :].any()
        assert dst.nodata is not None
        np.testing.assert_array_equal(values[6:, :], dst.nodata)
        # 预览 RGBA 必须把掩膜外像元画成透明，否则图面仍像未裁剪。
        assert result.image_data[7, 0, 3] == 0
        assert result.image_data[0, 0, 3] == 255

    def test_clip_with_crop_shrinks_extent(self, tmp_path: Path) -> None:
        """crop=True 时输出范围缩小至掩膜与栅格交集。"""
        raster_layer, mask_layer, _ = self._inputs(tmp_path)
        request = RasterClipRequest(
            raster_layer_id=raster_layer.layer_id,
            mask_layer_id=mask_layer.layer_id,
            crop=True,
            output_layer_name="裁剪",
            output_path=tmp_path / "clip_crop.tif",
        )
        result = RasterAnalysisService().execute_clip(
            request,
            {raster_layer.layer_id: raster_layer, mask_layer.layer_id: mask_layer},
        )
        # 掩膜覆盖 5 列 × 5 行。
        assert result.raster_shape == (5, 5)
        assert result.bounds[0] == 500000.0
        assert result.bounds[2] == 500050.0

    def test_clip_invert_keeps_outside(self, tmp_path: Path) -> None:
        """反转掩膜后应保留矢量范围外像元。"""
        raster_layer, mask_layer, _ = self._inputs(tmp_path)
        request = RasterClipRequest(
            raster_layer_id=raster_layer.layer_id,
            mask_layer_id=mask_layer.layer_id,
            crop=False,
            invert=True,
            output_layer_name="反转",
            output_path=tmp_path / "clip_invert.tif",
        )
        RasterAnalysisService().execute_clip(
            request,
            {raster_layer.layer_id: raster_layer, mask_layer.layer_id: mask_layer},
        )
        with rasterio.open(tmp_path / "clip_invert.tif") as dst:
            mask = dst.dataset_mask()
        assert not mask[:4, :5].any()
        assert mask[6:, :5].all()

    def test_clip_no_intersection_rejected(self, tmp_path: Path) -> None:
        """掩膜与栅格无交集时应终止。"""
        raster_layer, _, _ = self._inputs(tmp_path)
        far_polygon = Polygon(
            [(600000, 3100000), (600010, 3100000), (600010, 3100010)]
        )
        far_mask = _polygon_layer(far_polygon)
        request = RasterClipRequest(
            raster_layer_id=raster_layer.layer_id,
            mask_layer_id=far_mask.layer_id,
            output_layer_name="x",
            output_path=tmp_path / "clip.tif",
        )
        from app.application.errors import EmptyRasterResult

        with pytest.raises(EmptyRasterResult):
            RasterAnalysisService().execute_clip(
                request,
                {
                    raster_layer.layer_id: raster_layer,
                    far_mask.layer_id: far_mask,
                },
            )

    def test_clip_with_point_mask_rejected(self, tmp_path: Path) -> None:
        """非面几何掩膜应被拒绝。"""
        from shapely.geometry import Point

        raster_layer, _, _ = self._inputs(tmp_path)
        point_layer = VectorLayer.create(
            name="点",
            features=(Feature(fid=1, geometry=Point(500010, 2999990), attributes={}),),
            crs=_CRS,
        )
        request = RasterClipRequest(
            raster_layer_id=raster_layer.layer_id,
            mask_layer_id=point_layer.layer_id,
            output_layer_name="x",
            output_path=tmp_path / "clip.tif",
        )
        with pytest.raises(UnsupportedRasterAnalysisInput, match="面"):
            RasterAnalysisService().execute_clip(
                request,
                {
                    raster_layer.layer_id: raster_layer,
                    point_layer.layer_id: point_layer,
                },
            )

    def test_clip_rejects_geographic_raster_with_projected_coordinates(
        self, tmp_path: Path
    ) -> None:
        """把米制 DEM 误标成 WGS84 后再用中国省界裁剪，必须拒绝而不是写出空白图。"""
        # 与 dem_reprojected.tif 同类：仿射变换仍是 Albers 米，CRS 却写成经纬度。
        # 8×8 网格覆盖同一数值包络，使省界范围与栅格范围在数字上相交。
        transform = Affine.translation(-673983.37, 969734.48) * Affine.scale(
            166099.0, -125429.0
        )
        data = np.full((8, 8), 1200.0, dtype=np.float32)
        source = _write_tif(
            tmp_path / "dem_mislabelled.tif",
            data,
            crs=CRS.from_epsg(4326),
            transform=transform,
            nodata=-9999.0,
        )
        raster_layer = _load(source)
        china = Polygon(
            [(73.4, 18.0), (135.1, 18.0), (135.1, 53.6), (73.4, 53.6)]
        )
        mask_layer = VectorLayer.create(
            layer_id="china",
            name="省界",
            features=(Feature(fid=1, geometry=china, attributes={}),),
            crs=CRS.from_epsg(4326),
        )
        request = RasterClipRequest(
            raster_layer_id=raster_layer.layer_id,
            mask_layer_id=mask_layer.layer_id,
            crop=True,
            output_layer_name="clip_result1",
            output_path=tmp_path / "clip_result1.tif",
        )

        with pytest.raises(InvalidRasterAnalysisParameters, match="经纬度"):
            RasterAnalysisService().execute_clip(
                request,
                {
                    raster_layer.layer_id: raster_layer,
                    mask_layer.layer_id: mask_layer,
                },
            )
        assert not (tmp_path / "clip_result1.tif").exists()

    def test_clip_rejects_when_mask_hits_no_pixel_centers(
        self, tmp_path: Path
    ) -> None:
        """掩膜与范围相交但未覆盖任何像元中心时，不能留下全透明结果图层。"""
        transform = Affine.translation(100.0, 40.0) * Affine.scale(10.0, -10.0)
        data = np.ones((2, 2), dtype=np.float32)
        source = _write_tif(
            tmp_path / "coarse.tif",
            data,
            crs=CRS.from_epsg(4326),
            transform=transform,
            nodata=-9999.0,
        )
        raster_layer = _load(source)
        # 落在左上角像元内，但不包含像元中心 (105, 35)。
        sliver = Polygon(
            [(100.1, 39.9), (100.3, 39.9), (100.3, 39.7), (100.1, 39.7)]
        )
        mask_layer = VectorLayer.create(
            layer_id="sliver",
            name="碎部",
            features=(Feature(fid=1, geometry=sliver, attributes={}),),
            crs=CRS.from_epsg(4326),
        )
        request = RasterClipRequest(
            raster_layer_id=raster_layer.layer_id,
            mask_layer_id=mask_layer.layer_id,
            crop=True,
            output_layer_name="empty_clip",
            output_path=tmp_path / "empty_clip.tif",
        )

        with pytest.raises(EmptyRasterResult, match="有效像元"):
            RasterAnalysisService().execute_clip(
                request,
                {
                    raster_layer.layer_id: raster_layer,
                    mask_layer.layer_id: mask_layer,
                },
            )
        assert not (tmp_path / "empty_clip.tif").exists()


class TestBlockedExecution:
    """分块执行与整体执行一致性。"""

    def test_blocked_result_matches_single_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """分块（小窗口）重分类结果应与整体计算一致。"""
        data = np.arange(64, dtype=np.float32).reshape(8, 8)
        source = _write_tif(tmp_path / "input.tif", data, nodata=None)
        layer = _load(source)
        rules = (ReclassRule(lower=0.0, upper=32.0, output_value=1.0),
                 ReclassRule(lower=32.0, upper=64.0, output_value=2.0))

        def _run(output: Path) -> np.ndarray:
            request = RasterReclassifyRequest(
                input_layer_id=layer.layer_id,
                band_index=1,
                rules=rules,
                unmatched_policy="nodata",
                output_layer_name="x",
                output_path=output,
            )
            RasterAnalysisService().execute_reclassify(
                request, {layer.layer_id: layer}
            )
            with rasterio.open(output) as dst:
                return dst.read(1)

        single = _run(tmp_path / "single.tif")
        monkeypatch.setattr(service_module, "DEFAULT_BLOCK_SIZE", 3)
        blocked = _run(tmp_path / "blocked.tif")
        np.testing.assert_array_equal(single, blocked)

    def test_progress_callback_reports_windows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """进度回调应报告每个窗口的完成数量。"""
        data = np.ones((8, 8), dtype=np.float32)
        source = _write_tif(tmp_path / "input.tif", data, nodata=None)
        layer = _load(source)
        monkeypatch.setattr(service_module, "DEFAULT_BLOCK_SIZE", 4)
        reports: list[tuple[int, int]] = []

        def progress(done: int, total: int) -> bool:
            reports.append((done, total))
            return True

        request = RasterReclassifyRequest(
            input_layer_id=layer.layer_id,
            band_index=1,
            rules=(ReclassRule(lower=0.0, upper=2.0, output_value=1.0),),
            unmatched_policy="nodata",
            output_layer_name="x",
            output_path=tmp_path / "progress.tif",
        )
        RasterAnalysisService(progress_callback=progress).execute_reclassify(
            request, {layer.layer_id: layer}
        )
        # 8×8 按 4×4 分块应产生 4 个窗口。
        assert reports[-1] == (4, 4)

    def test_cancel_raises_and_removes_output(self, tmp_path: Path) -> None:
        """进度回调返回 False 时应取消并删除临时输出。"""
        data = np.ones((4, 4), dtype=np.float32)
        source = _write_tif(tmp_path / "input.tif", data, nodata=None)
        layer = _load(source)
        output = tmp_path / "cancel.tif"

        def progress(_done: int, _total: int) -> bool:
            return False

        request = RasterReclassifyRequest(
            input_layer_id=layer.layer_id,
            band_index=1,
            rules=(ReclassRule(lower=0.0, upper=2.0, output_value=1.0),),
            unmatched_policy="nodata",
            output_layer_name="x",
            output_path=output,
        )
        with pytest.raises(RasterAnalysisFailed, match="取消"):
            RasterAnalysisService(progress_callback=progress).execute_reclassify(
                request, {layer.layer_id: layer}
            )
        assert not output.exists()
