"""基于 GeoPandas 的矢量文件写入适配器。"""

from pathlib import Path

import fiona
import geopandas as gpd

from app.application.errors import DataWriteFailed, UnsupportedExportFormat
from app.domain.feature import Feature, FeatureId
from app.domain.vector_layer import VectorLayer


class GeoPandasVectorWriter:
    """把内存矢量图层写出为 Shapefile、GeoJSON 或 GeoPackage。"""

    _DRIVERS: dict[str, str] = {
        ".shp": "ESRI Shapefile",
        ".geojson": "GeoJSON",
        ".gpkg": "GPKG",
    }

    def write(
        self,
        layer: VectorLayer,
        path: Path,
        selected_feature_ids: tuple[FeatureId, ...] = (),
        layer_name: str | None = None,
    ) -> None:
        """写出全部要素或非空选择集，并保留图层当前坐标系。"""
        resolved_path: Path = path.expanduser().resolve()
        suffix: str = resolved_path.suffix.lower()
        driver: str | None = self._DRIVERS.get(suffix)
        if driver is None:
            raise UnsupportedExportFormat(
                f"暂不支持该矢量导出格式：{suffix or '无扩展名'}"
            )
        if not resolved_path.parent.is_dir():
            raise DataWriteFailed(f"输出目录不存在：{resolved_path.parent}")

        selected_ids: set[FeatureId] = set(selected_feature_ids)
        features: tuple[Feature, ...] = (
            tuple(feature for feature in layer.features if feature.fid in selected_ids)
            if selected_ids
            else layer.features
        )
        if not features:
            raise DataWriteFailed("选择集中没有可导出的矢量要素。")

        resolved_layer_name: str | None = layer_name
        write_mode: str = "w"
        if suffix == ".gpkg":
            resolved_layer_name = resolved_layer_name or layer.name
            if not resolved_layer_name.strip():
                raise DataWriteFailed("GeoPackage 图层名称不能为空。")
            if resolved_path.exists():
                try:
                    existing_layers: list[str] = list(fiona.listlayers(resolved_path))
                except Exception as error:
                    raise DataWriteFailed(
                        f"无法读取 GeoPackage 图层列表：{resolved_path.name}"
                    ) from error
                if resolved_layer_name in existing_layers:
                    raise DataWriteFailed(
                        f"GeoPackage 图层已存在，不能覆盖旧结果：{resolved_layer_name}"
                    )
                write_mode = "a"

        dataframe: gpd.GeoDataFrame = gpd.GeoDataFrame(
            [dict(feature.attributes) for feature in features],
            geometry=[feature.geometry for feature in features],
            crs=layer.crs,
        )
        if suffix == ".geojson":
            if layer.crs is None:
                raise DataWriteFailed(
                    "GeoJSON 导出需要已知坐标系，无法安全写出坐标系未知的图层。"
                )
            try:
                # RFC 7946 GeoJSON 使用 WGS84 经纬度。若直接写入无 EPSG 编号的
                # 投影坐标，GDAL 会省略 CRS，重新读取时就会被误判为 EPSG:4326。
                dataframe = dataframe.to_crs(epsg=4326)
            except Exception as error:
                raise DataWriteFailed(
                    f"图层无法转换为 GeoJSON 所需的 WGS84 坐标：{layer.name}"
                ) from error
        # 对于 .shp 覆写，先删除旧文件防止 fiona 写入失败。
        if suffix == ".shp" and write_mode == "w" and resolved_path.exists():
            for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
                companion = resolved_path.with_suffix(ext)
                if companion.exists():
                    companion.unlink()

        # Shapefile 的 DBF 字段名限制为 10 字节（非字符）。
        # 预截断中文等宽字符字段名，避免 pyogrio 写入时产生 RuntimeWarning。
        if suffix == ".shp":
            dataframe = self._truncate_shp_field_names(dataframe)

        try:
            dataframe.to_file(
                resolved_path,
                driver=driver,
                layer=resolved_layer_name,
                mode=write_mode,
                encoding="UTF-8",
                index=False,
            )
        except Exception as error:
            raise DataWriteFailed(f"矢量数据导出失败：{resolved_path.name}") from error

    @staticmethod
    def _truncate_shp_field_names(df: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """将 GeoDataFrame 列名截断为 Shapefile 兼容的 10 字节以内。

        中文等宽字符按 UTF-8 编码后截断；冲突时追加 "_2" "_3" 等后缀。
        """
        renamed: dict[str, str] = {}
        used: set[str] = set()
        for col in df.columns:
            if col == "geometry":
                continue
            encoded: bytes = col.encode("utf-8")
            if len(encoded) <= 10:
                safe: str = col
            else:
                # 按字节截断并解码，舍弃可能被截断的不完整字节。
                truncated: bytes = encoded[:10]
                safe = truncated.decode("utf-8", errors="ignore")
            # 冲突去重。
            base: str = safe
            counter: int = 1
            while safe in used or safe == "":
                counter += 1
                suffix: str = f"_{counter}"
                suffix_bytes: bytes = suffix.encode("utf-8")
                safe = (base.encode("utf-8")[:10 - len(suffix_bytes)]).decode("utf-8", errors="ignore") + suffix
            renamed[col] = safe
            used.add(safe)
        if renamed:
            df = df.rename(columns=renamed)
        return df
