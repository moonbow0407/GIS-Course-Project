"""基于 GeoPandas 的矢量文件写入适配器。"""

import os
import sqlite3
import uuid
from pathlib import Path

import fiona
import geopandas as gpd

from app.application.errors import DataWriteFailed, UnsupportedExportFormat
from app.domain.feature import Feature, FeatureId
from app.domain.vector_layer import VectorLayer


class GeoPandasVectorWriter:
    """把内存矢量图层写出为 Shapefile、GeoJSON 或 GeoPackage。

    写入语义：``write`` 以传入图层的全部要素（或非空选择集）替换目标。
    GeoPackage 同名图层通过"同目录临时副本 + os.replace"整层原子替换，
    文件中的其他图层保持不变；Shapefile 和 GeoJSON 维持整文件重写。
    """

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
        """写出全部要素或非空选择集，并保留图层当前坐标系。

        GeoPackage 目标已存在同名图层时执行整层原子替换，不影响其他图层。
        """
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
        write_engine: str | None = None
        # 非 None 表示目标 GPKG 存在同名图层，需要走整层原子替换流程；
        # 值为 (图层名, 其他图层名集合)。
        gpkg_replacement: tuple[str, frozenset[str]] | None = None
        if suffix == ".gpkg":
            resolved_layer_name = resolved_layer_name or layer.name
            if not resolved_layer_name.strip():
                raise DataWriteFailed("GeoPackage 图层名称不能为空。")
            # GPKG 写入显式使用 pyogrio 引擎：fiona 引擎在重建同名层时
            # 会抛出驱动错误，且本项目已把 pyogrio 声明为直接依赖。
            write_engine = "pyogrio"
            if resolved_path.exists():
                try:
                    existing_layers: list[str] = list(fiona.listlayers(resolved_path))
                except Exception as error:
                    raise DataWriteFailed(
                        f"无法读取 GeoPackage 图层列表：{resolved_path.name}"
                    ) from error
                if resolved_layer_name in existing_layers:
                    gpkg_replacement = (
                        resolved_layer_name,
                        frozenset(existing_layers) - {resolved_layer_name},
                    )
                else:
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

        if gpkg_replacement is not None:
            replace_name, other_layers = gpkg_replacement
            self._replace_gpkg_layer(
                dataframe, resolved_path, replace_name, other_layers
            )
            return

        try:
            dataframe.to_file(
                resolved_path,
                driver=driver,
                layer=resolved_layer_name,
                mode=write_mode,
                engine=write_engine,
                encoding="UTF-8",
                index=False,
            )
        except Exception as error:
            raise DataWriteFailed(f"矢量数据导出失败：{resolved_path.name}") from error

    def _replace_gpkg_layer(
        self,
        dataframe: gpd.GeoDataFrame,
        target_path: Path,
        layer_name: str,
        other_layers: frozenset[str],
    ) -> None:
        """整层原子替换 GeoPackage 中的同名图层。

        通过"同目录临时副本 + os.replace"保证：替换流程任一步失败时，
        原文件的字节内容不变，其他图层不受影响。

        参数:
            dataframe: 已按目标图层要素构造的 GeoDataFrame。
            target_path: 目标 GeoPackage 路径。
            layer_name: 待替换的图层名。
            other_layers: 原文件中除目标图层外的图层名集合，用于副本校验。
        """
        temp_path: Path = self._make_temp_gpkg_path(target_path)
        try:
            self._backup_sqlite_database(target_path, temp_path)
            # pyogrio mode="w" 的语义是删除同名层后重建，其他图层保留；
            # 被替换层在文件内的顺序会移到末尾，本项目一律按层名读取，
            # 层序变化无影响。
            dataframe.to_file(
                temp_path,
                driver="GPKG",
                layer=layer_name,
                mode="w",
                engine="pyogrio",
                encoding="UTF-8",
                index=False,
            )
            self._verify_gpkg_copy(temp_path, layer_name, other_layers)
            # 全部数据源连接已关闭，os.replace 在同卷内原子替换目标文件。
            os.replace(temp_path, target_path)
        except DataWriteFailed:
            self._discard_temp_file(temp_path)
            raise
        except (PermissionError, sqlite3.OperationalError) as error:
            self._discard_temp_file(temp_path)
            raise DataWriteFailed(
                f"GeoPackage 写入失败：{target_path.name} 可能被其他程序占用，"
                "请关闭占用该文件的程序后重试。"
            ) from error
        except Exception as error:
            self._discard_temp_file(temp_path)
            raise DataWriteFailed(
                f"GeoPackage 图层替换失败：{layer_name}（{target_path.name}）"
            ) from error

    @staticmethod
    def _make_temp_gpkg_path(target_path: Path) -> Path:
        """在目标文件同目录生成带随机后缀的临时 GPKG 路径。

        临时文件必须与目标同目录（同卷），os.replace 才具有原子性；
        随机后缀保证不会覆盖任何既有文件。
        """
        while True:
            candidate: Path = target_path.with_name(
                f".{target_path.stem}.{uuid.uuid4().hex[:12]}.tmp.gpkg"
            )
            if not candidate.exists():
                return candidate

    @staticmethod
    def _backup_sqlite_database(source: Path, destination: Path) -> None:
        """使用 SQLite 在线备份 API 把 GeoPackage 一致性复制到临时文件。"""
        source_connection = sqlite3.connect(source)
        destination_connection = sqlite3.connect(destination)
        try:
            source_connection.backup(destination_connection)
        finally:
            destination_connection.close()
            source_connection.close()

    @staticmethod
    def _verify_gpkg_copy(
        temp_path: Path, layer_name: str, other_layers: frozenset[str]
    ) -> None:
        """在原子替换前验证临时副本完整可用。

        校验目标图层存在且可按名称读取、其他图层名集合不变（顺序允许变化）、
        SQLite 完整性和外键检查通过。
        """
        try:
            copied_layers: frozenset[str] = frozenset(fiona.listlayers(temp_path))
        except Exception as error:
            raise DataWriteFailed(
                f"临时 GeoPackage 副本校验失败：{temp_path.name}"
            ) from error
        if layer_name not in copied_layers:
            raise DataWriteFailed(
                f"临时 GeoPackage 副本缺少目标图层：{layer_name}"
            )
        if copied_layers - {layer_name} != other_layers:
            raise DataWriteFailed(
                f"临时 GeoPackage 副本中其他图层发生变化：{temp_path.name}"
            )
        try:
            with fiona.open(temp_path, layer=layer_name) as collection:
                _ = len(collection)
        except Exception as error:
            raise DataWriteFailed(
                f"临时 GeoPackage 副本无法读取目标图层：{layer_name}"
            ) from error
        connection = sqlite3.connect(temp_path)
        try:
            integrity: tuple | None = connection.execute(
                "PRAGMA integrity_check"
            ).fetchone()
            if integrity != ("ok",):
                raise DataWriteFailed(
                    f"临时 GeoPackage 副本完整性检查未通过：{temp_path.name}"
                )
            foreign_key_violations: list = connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
            if foreign_key_violations:
                raise DataWriteFailed(
                    f"临时 GeoPackage 副本外键检查未通过：{temp_path.name}"
                )
        finally:
            connection.close()

    @staticmethod
    def _discard_temp_file(temp_path: Path) -> None:
        """尽力删除失败流程遗留的临时文件；清理失败不得覆盖原始异常。"""
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass

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
