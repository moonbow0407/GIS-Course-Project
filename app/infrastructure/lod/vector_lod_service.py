"""基于 mapshaper 的矢量多级 LOD 生成服务。

mapshaper 内部使用弧段拓扑（TopoJSON 机制）做简化，共享边界只简化
一次、两侧要素共用同一结果，因此相邻面要素之间不会因独立简化产生
缝隙或重叠。本服务只替换几何，fid 与属性完全沿用原始要素，避免
序列化往返造成类型漂移。
"""

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

import geopandas as gpd

from app.domain.feature import Feature
from app.domain.lod import LodLevel, LodPyramid
from app.domain.vector_layer import Bounds, VectorLayer

# 默认 LOD 级别数：0（原始）+ 7 档 2 倍递增简化，覆盖到 64 倍基准容差。
# 用 2 倍而非 4 倍步进，减小相邻级别几何差异，缩放切换更平滑不突兀。
_DEFAULT_LEVEL_COUNT: int = 8


class VectorLodService:
    """调用 mapshaper 为矢量图层生成多级简化金字塔。"""

    def __init__(self, cache_dir: Path | None = None) -> None:
        """创建服务并定位 mapshaper 可执行入口。

        参数:
            cache_dir: LOD 中间文件的缓存目录；为空时使用当前目录下的
                .lod_cache。
        """
        self._cache_dir: Path = cache_dir or Path(".lod_cache")
        self._mapshaper_cmd: list[str] | None = self._locate_mapshaper()

    @staticmethod
    def default_tolerances(bounds: Bounds, level_count: int = _DEFAULT_LEVEL_COUNT) -> tuple[float, ...]:
        """按图层范围生成默认简化容差表。

        参数:
            bounds: 图层在源坐标系下的范围。
            level_count: 级别总数，至少为 1。

        返回:
            以 0（原始几何）开头的升序容差序列，后续级别按 2 倍递增，
            基准取图层宽高的 1/1024（约等于首屏全图时一个屏幕像素）。
            2 倍步进让相邻级别几何差异较小，缩放时级别切换更平滑。
        """
        width: float = bounds[2] - bounds[0]
        height: float = bounds[3] - bounds[1]
        base: float = max(width, height) / 1024.0
        if level_count <= 1 or base <= 0.0:
            return (0.0,)
        return tuple([0.0] + [base * (2 ** i) for i in range(level_count - 1)])

    def build_pyramid(
        self,
        layer: VectorLayer,
        tolerances: tuple[float, ...] | None = None,
    ) -> LodPyramid:
        """为图层生成多级 LOD 金字塔。

        参数:
            layer: 待简化的矢量图层。
            tolerances: 升序简化容差表，含 0（原始级别）；为空时按图层
                范围生成默认容差表。

        返回:
            与容差表一一对应的 LOD 金字塔；mapshaper 不可用时仅含原始
            级别，此时无简化收益但接口保持一致。
        """
        if tolerances is None or len(tolerances) == 0:
            tolerances = self.default_tolerances(layer.bounds)
        if self._mapshaper_cmd is None:
            return LodPyramid((LodLevel(0.0, layer.features),))
        source_path: Path = self._export_source(layer)
        levels: list[LodLevel] = []
        tolerance: float
        for tolerance in tolerances:
            if tolerance <= 0.0:
                levels.append(LodLevel(0.0, layer.features))
            else:
                levels.append(
                    LodLevel(tolerance, self._simplify(source_path, tolerance, layer))
                )
        return LodPyramid(tuple(levels))

    def _export_source(self, layer: VectorLayer) -> Path:
        """把图层要素连同索引列导出为 GeoJSON，供 mapshaper 简化。

        导出附带 ``_fid`` 索引列（0..n-1），简化后按该索引对齐回原始
        要素。基于源文件的图层会写入磁盘缓存，内存图层使用临时目录。
        """
        source_hash: str = self._source_hash(layer)
        if layer.source_path is not None and layer.source_path.is_file():
            export_path: Path = self._cache_dir / source_hash / "source.geojson"
            if export_path.is_file():
                return export_path
            export_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            temp_dir: Path = Path(tempfile.mkdtemp(prefix="lod_"))
            export_path = temp_dir / "source.geojson"
        geometries = [feature.geometry for feature in layer.features]
        dataframe: gpd.GeoDataFrame = gpd.GeoDataFrame(
            {"_fid": list(range(len(layer.features)))},
            geometry=geometries,
            crs=layer.crs,
        )
        dataframe.to_file(export_path, driver="GeoJSON")
        return export_path

    def _simplify(
        self,
        source_path: Path,
        tolerance: float,
        layer: VectorLayer,
    ) -> tuple[Feature, ...]:
        """对导出文件按指定容差简化并恢复为要素集合（带磁盘缓存）。"""
        out_path: Path = source_path.parent / f"lod_{tolerance:.12g}.geojson"
        if not out_path.is_file():
            self._run_mapshaper(source_path, tolerance, out_path)
        return self._read_simplified(out_path, layer)

    def _run_mapshaper(self, source_path: Path, tolerance: float, out_path: Path) -> None:
        """调用 mapshaper 执行拓扑保持简化。"""
        command: list[str] = [
            *self._mapshaper_cmd,
            str(source_path),
            "-simplify",
            "keep-shapes",
            f"{tolerance:.12g}",
            "-o",
            str(out_path),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            detail: str = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"mapshaper 简化失败：{detail}")

    def _read_simplified(self, path: Path, layer: VectorLayer) -> tuple[Feature, ...]:
        """重读简化结果，按 _fid 索引对齐回原始要素。

        只替换几何；fid 与属性沿用原始要素。简化导致几何退化时回退到
        原始几何，保证级别内要素与原图层一一对应。
        """
        dataframe: gpd.GeoDataFrame = gpd.read_file(path)
        original: tuple[Feature, ...] = layer.features
        indices = dataframe["_fid"].to_numpy().astype(int)
        geometries = dataframe.geometry.to_numpy()
        features: list[Feature] = []
        for index, geometry in zip(indices, geometries):
            original_feature: Feature = original[int(index)]
            if geometry is None or geometry.is_empty:
                geometry = original_feature.geometry
            features.append(
                Feature(
                    fid=original_feature.fid,
                    geometry=geometry,
                    attributes=original_feature.attributes,
                )
            )
        return tuple(features)

    @staticmethod
    def _source_hash(layer: VectorLayer) -> str:
        """生成源图层的缓存键：源文件内容哈希或图层编号。"""
        if layer.source_path is not None and layer.source_path.is_file():
            digest: str = hashlib.sha256(layer.source_path.read_bytes()).hexdigest()
            return digest[:16]
        return layer.layer_id

    @staticmethod
    def _locate_mapshaper() -> list[str] | None:
        """定位 mapshaper 并返回可直接执行的命令前缀。

        Windows 上 npm 全局安装生成的是 mapshaper.cmd 脚本，Python 子
        进程无法直接执行，改为从 .cmd 位置推导 node 入口；Unix 上回退
        到 PATH 中可直接调用的 mapshaper 命令。
        """
        cmd: str | None = shutil.which("mapshaper.cmd")
        if cmd is not None:
            entry: Path = (
                Path(cmd).parent / "node_modules" / "mapshaper" / "bin" / "mapshaper"
            )
            if entry.is_file():
                return ["node", str(entry)]
        if shutil.which("mapshaper"):
            return ["mapshaper"]
        return None
