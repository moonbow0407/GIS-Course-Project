"""基于 GeoPandas 的矢量文件读取适配器。"""

from datetime import date, datetime
from pathlib import Path
from typing import Any

import fiona
import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from app.application.crs_utils import crs_equivalent
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
    SUPPORTED_SUFFIXES: frozenset[str] = frozenset({".shp", ".geojson", ".json", ".gpkg"})

    @staticmethod
    def list_layers(path: Path) -> tuple[str, ...]:
        """返回 GeoPackage 中的矢量图层名称。"""
        resolved_path: Path = path.expanduser().resolve()
        if resolved_path.suffix.lower() != ".gpkg":
            return ()
        if not resolved_path.is_file():
            raise VectorFileNotFound(f"矢量文件不存在：{resolved_path}")
        try:
            return tuple(fiona.listlayers(resolved_path))
        except Exception as error:
            raise VectorReadFailed(f"GeoPackage 图层列表读取失败：{resolved_path.name}") from error

    def read(
        self,
        path: Path,
        target_crs: CRS | None = None,
        layer_name: str | None = None,
        source_crs_override: CRS | None = None,
    ) -> VectorLayer:
        """读取矢量文件，规范化字段，并按需转换坐标参考系统。

        ``source_crs_override`` 只修正坐标的解释，不会修改几何坐标值；
        ``target_crs`` 仅保留给需要输出到指定坐标系的底层调用方。
        """
        resolved_path: Path = path.expanduser().resolve()
        if not resolved_path.is_file():
            raise VectorFileNotFound(f"矢量文件不存在：{resolved_path}")
        suffix: str = resolved_path.suffix.lower()
        if suffix not in self.SUPPORTED_SUFFIXES:
            raise UnsupportedVectorFormat(f"暂不支持该矢量文件格式：{suffix or '无扩展名'}")

        try:
            resolved_layer_name: str | None = layer_name
            if resolved_path.suffix.lower() == ".gpkg" and resolved_layer_name is None:
                layer_names: list[str] = list(fiona.listlayers(resolved_path))
                if len(layer_names) != 1:
                    raise ValueError(
                        "GeoPackage 包含多个图层，请在工程或调用方中指定图层名称。"
                    )
                resolved_layer_name = layer_names[0]
            dataframe: gpd.GeoDataFrame = gpd.read_file(
                resolved_path,
                layer=resolved_layer_name,
            )
            # 即使未抛出 UnicodeDecodeError，GBK 字节也可能恰好是合法 UTF-8 序列，
            # 导致静默乱码。对 shapefile 检测字符串列是否包含典型乱码特征。
            if (
                suffix == ".shp"
                and not dataframe.empty
                and self._looks_garbled(dataframe)
            ):
                dataframe = self._read_with_fallback_encoding(
                    resolved_path, resolved_layer_name,
                )
        except UnicodeDecodeError:
            dataframe = self._read_with_fallback_encoding(
                resolved_path, resolved_layer_name,
            )
        except Exception as error:
            raise VectorReadFailed(f"矢量文件读取失败：{resolved_path.name}") from error

        if dataframe.empty:
            raise EmptyVectorDataset(f"矢量数据集不包含任何记录：{resolved_path.name}")

        declared_crs: CRS | None = (
            CRS.from_user_input(dataframe.crs) if dataframe.crs is not None else None
        )
        source_crs: CRS | None = source_crs_override or declared_crs
        self._validate_coordinate_bounds(dataframe, source_crs, resolved_path.name)
        if target_crs is not None:
            if source_crs is None:
                raise IncompatibleCoordinateReferenceSystem(
                    "源数据未声明坐标参考系统，无法转换到地图显示坐标系。"
                )
            if source_crs_override is not None and not crs_equivalent(
                declared_crs, source_crs_override
            ):
                # GeoPandas 的 to_crs 使用 GeoDataFrame 自身的 CRS；先覆盖
                # 该解释，才能保证“定义 CRS”后的分析投影按新 CRS 计算。
                dataframe = dataframe.set_crs(source_crs_override, allow_override=True)
            if not crs_equivalent(source_crs, target_crs):
                try:
                    # to_crs 会转换全部几何坐标，原始属性保持不变。
                    dataframe = dataframe.to_crs(target_crs)
                except Exception as error:
                    raise IncompatibleCoordinateReferenceSystem(
                        "矢量图层无法转换到地图显示坐标系。"
                    ) from error
                source_crs = target_crs
                self._validate_coordinate_bounds(
                    dataframe,
                    source_crs,
                    resolved_path.name,
                )

        features = self._features_from_dataframe(dataframe)

        if not features:
            raise NoUsableGeometry(f"矢量数据集不包含可用几何：{resolved_path.name}")

        return VectorLayer.create(
            name=resolved_layer_name or resolved_path.stem,
            features=tuple(features),
            crs=source_crs,
            source_path=resolved_path,
            source_layer_name=resolved_layer_name,
            crs_override=source_crs_override is not None,
        )

    @classmethod
    def _features_from_dataframe(cls, dataframe: gpd.GeoDataFrame) -> list[Feature]:
        """按列批量转换要素，避免 iterrows 在全国级矢量上逐行装箱。"""
        geometry_column = dataframe.geometry.name
        attribute_columns = [
            column for column in dataframe.columns if column != geometry_column
        ]
        geometries = dataframe.geometry.to_numpy()
        records: list[dict[str, Any]]
        if attribute_columns:
            records = dataframe.loc[:, attribute_columns].to_dict("records")
        else:
            records = [{} for _ in range(len(dataframe))]
        features: list[Feature] = []
        index: Any
        geometry_value: Any
        raw_attributes: dict[str, Any]
        for index, geometry_value, raw_attributes in zip(
            dataframe.index, geometries, records, strict=True
        ):
            if geometry_value is None:
                continue
            geometry: BaseGeometry = geometry_value
            if geometry.is_empty:
                continue
            attributes: dict[str, AttributeValue] = {
                str(field_name): cls._normalize_attribute(value)
                for field_name, value in raw_attributes.items()
            }
            features.append(
                Feature(
                    fid=cls._normalize_feature_id(index),
                    geometry=geometry,
                    attributes=attributes,
                )
            )
        return features

    @staticmethod
    def _validate_coordinate_bounds(
        dataframe: gpd.GeoDataFrame,
        crs: CRS | None,
        file_name: str,
    ) -> None:
        """拒绝非有限坐标和被误报为经纬度的投影坐标。"""
        bounds = np.asarray(dataframe.total_bounds, dtype=np.float64)
        if np.isnan(bounds).all():
            # 全空几何由后续 NoUsableGeometry 分支提供更准确的错误信息。
            return
        if not np.isfinite(bounds).all():
            raise IncompatibleCoordinateReferenceSystem(
                f"矢量数据坐标无效，无法显示：{file_name}"
            )
        if crs is None or not crs.is_geographic:
            return
        minimum_x, minimum_y, maximum_x, maximum_y = bounds
        if (
            minimum_x < -180.0
            or maximum_x > 180.0
            or minimum_y < -90.0
            or maximum_y > 90.0
        ):
            raise IncompatibleCoordinateReferenceSystem(
                f"GeoJSON 坐标超出经纬度范围，文件可能缺失或声明了错误的坐标系："
                f"{file_name}"
            )

    @staticmethod
    def _read_with_fallback_encoding(
        resolved_path: Path,
        resolved_layer_name: str | None,
    ) -> gpd.GeoDataFrame:
        """依次尝试 GBK / GB18030 / 系统默认编码读取矢量文件。"""
        import locale

        for encoding in ("gbk", "gb18030"):
            try:
                return gpd.read_file(
                    resolved_path,
                    layer=resolved_layer_name,
                    encoding=encoding,
                )
            except Exception:
                continue
        sys_enc = locale.getpreferredencoding()
        try:
            return gpd.read_file(
                resolved_path,
                layer=resolved_layer_name,
                encoding=sys_enc,
            )
        except Exception as fallback_error:
            raise VectorReadFailed(
                f"矢量文件读取失败（已尝试 UTF-8 / GBK / GB18030 / {sys_enc} 编码）："
                f"{resolved_path.name}"
            ) from fallback_error

    @staticmethod
    def _looks_garbled(dataframe: gpd.GeoDataFrame) -> bool:
        """检测字符串列是否包含典型乱码特征（GBK 被误读为 UTF-8）。

        乱码特征：包含大量 CJK 兼容区字符（U+F900-U+FAFF）或
        带有异常组合标记的字符，这些在正常中文文本中极少出现。
        """
        string_columns = dataframe.select_dtypes(include=["object"]).columns
        if len(string_columns) == 0:
            return False
        garbled_chars = 0
        total_chars = 0
        for col in string_columns:
            for value in dataframe[col].dropna():
                text = str(value)
                total_chars += len(text)
                for ch in text:
                    code = ord(ch)
                    # CJK 兼容区（乱码常见区域）
                    if 0xF900 <= code <= 0xFAFF:
                        garbled_chars += 1
                    # 带有异常组合标记的字符
                    elif 0x0300 <= code <= 0x036F:
                        garbled_chars += 1
                if total_chars > 500:
                    break
            if total_chars > 500:
                break
        if total_chars == 0:
            return False
        return garbled_chars / total_chars > 0.05

    @staticmethod
    def _normalize_feature_id(value: Any) -> FeatureId:
        """将 GeoPandas 索引规范化为领域模型支持的要素编号。"""
        if isinstance(value, np.generic):
            # NumPy 标量先转成 Python 标量，避免领域层依赖 NumPy 类型。
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
