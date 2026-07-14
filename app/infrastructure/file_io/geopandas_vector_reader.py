"""基于 GeoPandas 的矢量文件读取适配器。"""

from datetime import date, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from app.application.errors import (
    EmptyVectorDataset,
    IncompatibleCoordinateReferenceSystem,
    NoUsableGeometry,
    UnsupportedVectorFormat,
    VectorFileNotFound,
    VectorReadFailed,
)
from app.domain.feature import AttributeValue, Feature, FeatureId
from app.domain.vector_layer import VectorLayer


class GeoPandasVectorReader:
    """读取常见矢量文件并转换为应用统一领域模型。"""

    # 支持扩展名：限定当前经过测试且允许用户选择的矢量格式。
    SUPPORTED_SUFFIXES: frozenset[str] = frozenset({".shp", ".geojson", ".json"})

    def read(self, path: Path, target_crs: CRS | None = None) -> VectorLayer:
        """读取矢量文件，规范化字段，并按需转换坐标参考系统。"""
        resolved_path: Path = path.expanduser().resolve()
        if not resolved_path.is_file():
            raise VectorFileNotFound(f"矢量文件不存在：{resolved_path}")
        suffix: str = resolved_path.suffix.lower()
        if suffix not in self.SUPPORTED_SUFFIXES:
            raise UnsupportedVectorFormat(f"暂不支持该矢量文件格式：{suffix or '无扩展名'}")

        try:
            dataframe: gpd.GeoDataFrame = gpd.read_file(resolved_path)
        except Exception as error:
            raise VectorReadFailed(f"矢量文件读取失败：{resolved_path.name}") from error

        if dataframe.empty:
            raise EmptyVectorDataset(f"矢量数据集不包含任何记录：{resolved_path.name}")

        source_crs: CRS | None = (
            CRS.from_user_input(dataframe.crs) if dataframe.crs is not None else None
        )
        if target_crs is not None:
            if source_crs is None:
                raise IncompatibleCoordinateReferenceSystem(
                    "源数据未声明坐标参考系统，无法转换到地图显示坐标系。"
                )
            if source_crs != target_crs:
                try:
                    dataframe = dataframe.to_crs(target_crs)
                except Exception as error:
                    raise IncompatibleCoordinateReferenceSystem(
                        "矢量图层无法转换到地图显示坐标系。"
                    ) from error
                source_crs = target_crs

        features: list[Feature] = []
        # GeoPandas 行对象缺少可稳定使用的精确静态类型，仅在适配器边缘使用 Any。
        row: Any
        index: Any
        for index, row in dataframe.iterrows():
            # GeoPandas geometry 访问器的静态类型不完整，在此处收窄为 Shapely 几何。
            geometry_value: Any = row.geometry
            if geometry_value is None:
                continue
            geometry: BaseGeometry = geometry_value
            if geometry.is_empty:
                continue
            raw_attributes: dict[str, Any] = row.drop(labels=[dataframe.geometry.name]).to_dict()
            attributes: dict[str, AttributeValue] = {
                str(field_name): self._normalize_attribute(value)
                for field_name, value in raw_attributes.items()
            }
            feature_id: FeatureId = self._normalize_feature_id(index)
            features.append(
                Feature(fid=feature_id, geometry=geometry, attributes=attributes)
            )

        if not features:
            raise NoUsableGeometry(f"矢量数据集不包含可用几何：{resolved_path.name}")

        return VectorLayer.create(
            name=resolved_path.stem,
            features=tuple(features),
            crs=source_crs,
            source_path=resolved_path,
        )

    @staticmethod
    def _normalize_feature_id(value: Any) -> FeatureId:
        """将 GeoPandas 索引规范化为领域模型支持的要素编号。"""
        if isinstance(value, np.generic):
            normalized_value: object = value.item()
        else:
            normalized_value = value
        if isinstance(normalized_value, (str, int)) and not isinstance(normalized_value, bool):
            return normalized_value
        return str(normalized_value)

    @staticmethod
    def _normalize_attribute(value: Any) -> AttributeValue:
        """将 Pandas 和 NumPy 标量转换为稳定的领域属性值。"""
        if value is None:
            return None
        if isinstance(value, pd.Timestamp):
            return value.to_pydatetime()
        if isinstance(value, np.generic):
            normalized_value: object = value.item()
            return GeoPandasVectorReader._normalize_attribute(normalized_value)
        if isinstance(value, (str, int, float, bool, date, datetime)):
            if isinstance(value, float) and pd.isna(value):
                return None
            return value
        try:
            is_missing: bool = bool(pd.isna(value))
        except (TypeError, ValueError):
            is_missing = False
        if is_missing:
            return None
        return str(value)
