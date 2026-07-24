"""基于 GeoPandas 的矢量文件写入适配器。"""

from pathlib import Path

import geopandas as gpd

from app.application.errors import DataWriteFailed, UnsupportedExportFormat
from app.domain.feature import Feature, FeatureId
from app.domain.vector_layer import VectorLayer


class GeoPandasVectorWriter:
    """把内存矢量图层写出为 Shapefile 或 GeoJSON。"""

    _DRIVERS: dict[str, str] = {
        ".shp": "ESRI Shapefile",
        ".geojson": "GeoJSON",
    }

    def write(
        self,
        layer: VectorLayer,
        path: Path,
        selected_feature_ids: tuple[FeatureId, ...] = (),
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

        dataframe: gpd.GeoDataFrame = gpd.GeoDataFrame(
            [dict(feature.attributes) for feature in features],
            geometry=[feature.geometry for feature in features],
            crs=layer.crs,
        )
        try:
            dataframe.to_file(
                resolved_path,
                driver=driver,
                encoding="UTF-8",
                index=False,
            )
        except Exception as error:
            raise DataWriteFailed(f"矢量数据导出失败：{resolved_path.name}") from error
