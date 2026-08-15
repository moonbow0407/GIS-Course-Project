"""栅格显示金字塔的自动决策与安全缓存服务。"""

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.io import DatasetReader
from rasterio.shutil import copy as copy_raster

from app.application.errors import RasterReadFailed


@dataclass(frozen=True, slots=True)
class RasterOverviewPolicy:
    """定义自动构建显示金字塔的尺寸和资源阈值。"""

    target_dimension: int = 2048
    conditional_dimension: int = 4096
    automatic_dimension: int = 8192
    minimum_overview_dimension: int = 256
    conditional_decoded_bytes: int = 64 * 1024 * 1024
    conditional_file_bytes: int = 64 * 1024 * 1024
    automatic_pixels: int = 50_000_000
    automatic_file_bytes: int = 256 * 1024 * 1024

    def __post_init__(self) -> None:
        """校验阈值顺序，避免生成无效或无限的层级。"""
        if self.minimum_overview_dimension <= 0 or self.target_dimension <= 0:
            raise ValueError("Overview 尺寸阈值必须大于零。")
        if not (
            self.minimum_overview_dimension
            < self.target_dimension
            <= self.conditional_dimension
            <= self.automatic_dimension
        ):
            raise ValueError("Overview 尺寸阈值顺序无效。")


@dataclass(frozen=True, slots=True)
class RasterOverviewPlan:
    """表示一次自动金字塔决策及其证据。"""

    source_path: Path
    should_build: bool
    factors: tuple[int, ...]
    resampling: Resampling
    reason: str
    width: int
    height: int
    pixel_count: int
    decoded_bytes: int
    file_bytes: int


@dataclass(frozen=True, slots=True)
class RasterOverviewResult:
    """表示金字塔构建或复用后的显示数据源。"""

    source_path: Path
    display_path: Path
    factors: tuple[int, ...]
    built: bool
    reason: str


class RasterOverviewService:
    """按栅格规模自动创建 VRT 与外部 Overview 缓存。"""

    _MANIFEST_VERSION = 1

    def __init__(
        self,
        cache_root: Path | None = None,
        policy: RasterOverviewPolicy | None = None,
    ) -> None:
        """配置缓存目录和自动构建策略，不在初始化时写入磁盘。"""
        self._cache_root = (cache_root or self._default_cache_root()).expanduser().resolve()
        self._policy = policy or RasterOverviewPolicy()

    @staticmethod
    def _default_cache_root() -> Path:
        """返回当前平台的用户级显示缓存目录。"""
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "GISDesktopPlatform" / "overview-cache"
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        if xdg_cache:
            return Path(xdg_cache) / "gis-desktop-platform" / "overview-cache"
        return Path.home() / ".cache" / "gis-desktop-platform" / "overview-cache"

    def plan(
        self,
        source_path: Path,
        *,
        resampling: Resampling = Resampling.average,
    ) -> RasterOverviewPlan:
        """检查元数据、已有层级和缓存状态，返回可解释的构建决策。"""
        source = source_path.expanduser().resolve()
        if not source.is_file():
            raise RasterReadFailed(f"栅格文件不存在：{source}")
        cached = self.display_path(source)
        _vrt, _overview, manifest_path = self._entry_paths(source)
        cached_manifest = self._read_manifest(manifest_path)
        if (
            cached != source
            and cached_manifest is not None
            and cached_manifest.get("resampling") == resampling.name
        ):
            return RasterOverviewPlan(
                source_path=source,
                should_build=False,
                factors=self._cached_factors(source),
                resampling=resampling,
                reason="cache_valid",
                width=0,
                height=0,
                pixel_count=0,
                decoded_bytes=0,
                file_bytes=source.stat().st_size,
            )
        try:
            with rasterio.open(source) as dataset:
                width = dataset.width
                height = dataset.height
                pixel_count = width * height
                decoded_bytes = pixel_count * sum(
                    np.dtype(dtype).itemsize for dtype in dataset.dtypes
                )
                common_overviews = self._common_overviews(dataset)
        except Exception as error:
            raise RasterReadFailed(f"无法检查栅格金字塔：{source.name}") from error
        file_bytes = source.stat().st_size
        longest = max(width, height)
        factors = self._overview_factors(longest)
        if longest <= self._policy.target_dimension or not factors:
            reason = "small_raster"
            should_build = False
        elif self._has_sufficient_overview(longest, common_overviews):
            reason = "source_overviews"
            should_build = False
        elif (
            longest >= self._policy.automatic_dimension
            or pixel_count >= self._policy.automatic_pixels
            or file_bytes >= self._policy.automatic_file_bytes
        ):
            reason = "automatic_threshold"
            should_build = True
        elif longest >= self._policy.conditional_dimension and (
            decoded_bytes >= self._policy.conditional_decoded_bytes
            or file_bytes >= self._policy.conditional_file_bytes
        ):
            reason = "conditional_threshold"
            should_build = True
        else:
            reason = "threshold_not_met"
            should_build = False
        return RasterOverviewPlan(
            source_path=source,
            should_build=should_build,
            factors=factors if should_build else (),
            resampling=resampling,
            reason=reason,
            width=width,
            height=height,
            pixel_count=pixel_count,
            decoded_bytes=decoded_bytes,
            file_bytes=file_bytes,
        )

    def optimize(
        self,
        source_path: Path,
        *,
        resampling: Resampling = Resampling.average,
    ) -> RasterOverviewResult:
        """按决策构建缓存；无需构建时返回原始或已有缓存路径。"""
        plan = self.plan(source_path, resampling=resampling)
        if not plan.should_build:
            return RasterOverviewResult(
                source_path=plan.source_path,
                display_path=self.display_path(plan.source_path),
                factors=self._cached_factors(plan.source_path),
                built=False,
                reason=plan.reason,
            )
        entry = self._entry_directory(plan.source_path)
        entry.mkdir(parents=True, exist_ok=True)
        self._ensure_disk_space(entry, plan)
        final_vrt, final_overview, final_manifest = self._entry_paths(plan.source_path)
        token = uuid4().hex
        temporary_vrt = entry / f"display-{token}.vrt"
        temporary_overview = Path(f"{temporary_vrt}.ovr")
        temporary_manifest = entry / f"manifest-{token}.json"
        try:
            with rasterio.Env(GDAL_NUM_THREADS="ALL_CPUS"):
                copy_raster(plan.source_path, temporary_vrt, driver="VRT")
                with rasterio.open(temporary_vrt, "r+") as dataset:
                    dataset.build_overviews(list(plan.factors), plan.resampling)
                    dataset.update_tags(ns="rio_overview", resampling=plan.resampling.name)
            manifest = self._manifest_payload(plan)
            temporary_manifest.write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            if not temporary_overview.is_file():
                raise RasterReadFailed("GDAL 未生成外部 Overview 文件。")
            temporary_overview.replace(final_overview)
            temporary_vrt.replace(final_vrt)
            temporary_manifest.replace(final_manifest)
        except Exception as error:
            self._remove_paths(temporary_vrt, temporary_overview, temporary_manifest)
            if isinstance(error, RasterReadFailed):
                raise
            raise RasterReadFailed(f"构建栅格显示金字塔失败：{plan.source_path.name}") from error
        return RasterOverviewResult(
            source_path=plan.source_path,
            display_path=final_vrt,
            factors=plan.factors,
            built=True,
            reason=plan.reason,
        )

    def display_path(self, source_path: Path) -> Path:
        """缓存与源文件版本一致时返回 VRT，否则返回原始路径。"""
        source = source_path.expanduser().resolve()
        if not source.is_file():
            return source
        vrt_path, overview_path, manifest_path = self._entry_paths(source)
        if not (vrt_path.is_file() and overview_path.is_file() and manifest_path.is_file()):
            return source
        manifest = self._read_manifest(manifest_path)
        if manifest is None:
            return source
        stat = source.stat()
        if (
            manifest.get("version") != self._MANIFEST_VERSION
            or manifest.get("source_path") != str(source)
            or manifest.get("source_size") != stat.st_size
            or manifest.get("source_mtime_ns") != stat.st_mtime_ns
        ):
            return source
        return vrt_path

    def _overview_factors(self, longest_dimension: int) -> tuple[int, ...]:
        """生成二倍层级，直到最小层最长边接近策略下限。"""
        factors: list[int] = []
        factor = 2
        while longest_dimension // factor >= self._policy.minimum_overview_dimension:
            factors.append(factor)
            factor *= 2
        return tuple(factors)

    def _has_sufficient_overview(
        self,
        longest_dimension: int,
        existing_factors: tuple[int, ...],
    ) -> bool:
        """判断源文件层级是否已经覆盖当前 2048 预览需求。"""
        required = 2
        ratio = longest_dimension / self._policy.target_dimension
        while required * 2 <= ratio:
            required *= 2
        return bool(existing_factors and max(existing_factors) >= required)

    @staticmethod
    def _common_overviews(dataset: DatasetReader) -> tuple[int, ...]:
        """返回全部波段共同具备的 Overview 因子。"""
        count = dataset.count
        if count <= 0:
            return ()
        factors = set(dataset.overviews(1))
        for band_index in range(2, count + 1):
            factors.intersection_update(dataset.overviews(band_index))
        return tuple(sorted(int(factor) for factor in factors))

    def _entry_directory(self, source_path: Path) -> Path:
        """按源文件绝对路径生成稳定且不泄露文件名的缓存目录。"""
        digest = hashlib.sha256(str(source_path).encode("utf-8")).hexdigest()[:24]
        return self._cache_root / digest

    def _entry_paths(self, source_path: Path) -> tuple[Path, Path, Path]:
        entry = self._entry_directory(source_path)
        vrt_path = entry / "display.vrt"
        return vrt_path, Path(f"{vrt_path}.ovr"), entry / "manifest.json"

    def _manifest_payload(self, plan: RasterOverviewPlan) -> dict[str, object]:
        stat = plan.source_path.stat()
        return {
            "version": self._MANIFEST_VERSION,
            "source_path": str(plan.source_path),
            "source_size": stat.st_size,
            "source_mtime_ns": stat.st_mtime_ns,
            "factors": list(plan.factors),
            "resampling": plan.resampling.name,
        }

    @staticmethod
    def _read_manifest(path: Path) -> dict[str, object] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _cached_factors(self, source_path: Path) -> tuple[int, ...]:
        _vrt, _overview, manifest_path = self._entry_paths(source_path)
        manifest = self._read_manifest(manifest_path)
        raw_factors = manifest.get("factors") if manifest is not None else None
        if not isinstance(raw_factors, list):
            return ()
        return tuple(
            int(factor)
            for factor in raw_factors
            if isinstance(factor, int) and factor > 1
        )

    @staticmethod
    def _ensure_disk_space(entry: Path, plan: RasterOverviewPlan) -> None:
        """按未压缩层级估算缓存空间，并保留固定安全余量。"""
        ratio = sum(1.0 / (factor * factor) for factor in plan.factors)
        estimated_bytes = int(plan.decoded_bytes * ratio)
        required_bytes = int(estimated_bytes * 1.2) + 16 * 1024 * 1024
        if shutil.disk_usage(entry).free < required_bytes:
            raise RasterReadFailed(
                f"磁盘空间不足，无法为 {plan.source_path.name} 构建显示金字塔。"
            )

    @staticmethod
    def _remove_paths(*paths: Path) -> None:
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
