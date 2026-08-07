"""基于 JSON 的工程清单存储适配器。"""

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import cast
from uuid import uuid4

from app.application.errors import ProjectReadFailed, ProjectWriteFailed
from app.application.project_models import (
    AnalysisOutputReference,
    AnalysisRun,
    LayerReference,
    MapViewState,
    ProjectManifest,
    SourceFingerprint,
)
from app.domain.feature import FeatureId


class JsonProjectStore:
    """将工程快照保存为可校验、可迁移的 UTF-8 JSON 文件。"""

    FORMAT: str = "gis-desktop-project"
    CURRENT_SCHEMA_VERSION: int = 1

    def load(self, path: Path) -> ProjectManifest:
        """读取工程文件并校验其格式版本和字段类型。"""
        resolved_path: Path = path.expanduser().resolve()
        if not resolved_path.is_file():
            raise ProjectReadFailed(f"工程文件不存在：{resolved_path}")
        try:
            payload: object = json.loads(resolved_path.read_text(encoding="utf-8"))
            return self._decode_manifest(payload)
        except ProjectReadFailed:
            raise
        except Exception as error:
            raise ProjectReadFailed(f"工程文件读取失败：{resolved_path.name}") from error

    def save(self, path: Path, manifest: ProjectManifest) -> None:
        """将工程清单先写入临时文件，再原子替换正式文件。"""
        resolved_path: Path = path.expanduser().resolve()
        temporary_path: Path = resolved_path.with_name(
            f".{resolved_path.name}.{uuid4().hex}.tmp"
        )
        try:
            resolved_path.parent.mkdir(parents=True, exist_ok=True)
            payload: dict[str, object] = self._encode_manifest(manifest)
            serialized: str = json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            temporary_path.write_text(serialized + "\n", encoding="utf-8")
            os.replace(temporary_path, resolved_path)
        except Exception as error:
            if temporary_path.exists():
                temporary_path.unlink()
            if isinstance(error, ProjectWriteFailed):
                raise
            raise ProjectWriteFailed(f"工程文件保存失败：{resolved_path}") from error

    def _decode_manifest(self, payload: object) -> ProjectManifest:
        """将未经信任的 JSON 对象转换为不可变工程模型。"""
        root: Mapping[str, object] = self._mapping(payload, "工程根对象")
        if root.get("format") != self.FORMAT:
            raise ProjectReadFailed("不是受支持的 GIS 工程文件。")
        schema_version: int = self._integer(root.get("schema_version"), "schema_version")
        if schema_version != self.CURRENT_SCHEMA_VERSION:
            raise ProjectReadFailed(f"不支持的工程文件版本：{schema_version}")

        project: Mapping[str, object] = self._mapping(root.get("project"), "project")
        workspace: Mapping[str, object] = self._mapping(root.get("workspace"), "workspace")
        raw_layers: object = workspace.get("layers")
        if not isinstance(raw_layers, list):
            raise ProjectReadFailed("工程 workspace.layers 必须是数组。")

        layers: tuple[LayerReference, ...] = tuple(
            self._decode_layer(item, index) for index, item in enumerate(raw_layers)
        )
        analysis_history: object = root.get("analysis_history", [])
        if not isinstance(analysis_history, list):
            raise ProjectReadFailed("工程 analysis_history 必须是数组。")

        return ProjectManifest(
            schema_version=schema_version,
            name=self._string(project.get("name"), "project.name"),
            created_at=self._string(project.get("created_at"), "project.created_at"),
            modified_at=self._string(project.get("modified_at"), "project.modified_at"),
            display_crs=self._optional_string(workspace.get("display_crs")),
            active_layer_id=self._optional_string(workspace.get("active_layer_id")),
            layers=layers,
            view_state=self._decode_view_state(workspace.get("view")),
            analysis_runs=tuple(
                self._decode_analysis_run(item, index)
                for index, item in enumerate(analysis_history)
            ),
        )

    def _decode_layer(self, value: object, index: int) -> LayerReference:
        """解析单个工程图层引用。"""
        layer: Mapping[str, object] = self._mapping(value, f"workspace.layers[{index}]")
        raw_ids: object = layer.get("selected_feature_ids", [])
        if not isinstance(raw_ids, list):
            raise ProjectReadFailed(f"图层 {index} 的选择集必须是数组。")
        selected_feature_ids: tuple[FeatureId, ...] = tuple(
            self._feature_id(item, f"workspace.layers[{index}].selected_feature_ids")
            for item in raw_ids
        )
        return LayerReference(
            layer_id=self._string(layer.get("layer_id"), f"图层 {index}.layer_id"),
            name=self._string(layer.get("name"), f"图层 {index}.name"),
            source_path=self._string(layer.get("source_path"), f"图层 {index}.source_path"),
            source_layer_name=self._optional_string(layer.get("source_layer_name")),
            layer_kind=self._string(layer.get("layer_kind"), f"图层 {index}.layer_kind"),
            visible=self._boolean(layer.get("visible"), f"图层 {index}.visible"),
            selected_feature_ids=selected_feature_ids,
            fingerprint=self._decode_fingerprint(layer.get("fingerprint")),
            symbology=(
                self._mapping(layer.get("symbology"), f"图层 {index}.symbology")
                if layer.get("symbology") is not None
                else None
            ),
            opacity=self._number(
                layer.get("opacity", 1.0), f"图层 {index}.opacity"
            ),
            min_scale_percent=self._optional_number(
                layer.get("min_scale_percent"),
                f"图层 {index}.min_scale_percent",
            ),
            max_scale_percent=self._optional_number(
                layer.get("max_scale_percent"),
                f"图层 {index}.max_scale_percent",
            ),
        )

    def _decode_analysis_run(self, value: object, index: int) -> AnalysisRun:
        """解析一条分析历史记录。"""
        run: Mapping[str, object] = self._mapping(value, f"analysis_history[{index}]")
        input_layer_ids: tuple[str, ...] = self._string_array(
            run.get("input_layer_ids"), f"analysis_history[{index}].input_layer_ids"
        )
        output_layer_ids: tuple[str, ...] = self._string_array(
            run.get("output_layer_ids"), f"analysis_history[{index}].output_layer_ids"
        )
        parent_run_ids: tuple[str, ...] = self._string_array(
            run.get("parent_run_ids"), f"analysis_history[{index}].parent_run_ids"
        )
        raw_outputs: object = run.get("outputs")
        if not isinstance(raw_outputs, list):
            raise ProjectReadFailed(f"分析历史 {index} 的 outputs 必须是数组。")
        outputs: tuple[AnalysisOutputReference, ...] = tuple(
            self._decode_output(item, index, output_index)
            for output_index, item in enumerate(raw_outputs)
        )
        parameters: Mapping[str, object] = self._mapping(
            run.get("parameters"), f"analysis_history[{index}].parameters"
        )
        return AnalysisRun(
            run_id=self._string(run.get("run_id"), f"分析历史 {index}.run_id"),
            algorithm_id=self._string(
                run.get("algorithm_id"), f"分析历史 {index}.algorithm_id"
            ),
            input_layer_ids=input_layer_ids,
            parameters=parameters,
            output_layer_ids=output_layer_ids,
            outputs=outputs,
            parent_run_ids=parent_run_ids,
            status=self._string(run.get("status"), f"分析历史 {index}.status"),
            created_at=self._string(run.get("created_at"), f"分析历史 {index}.created_at"),
            supersedes_run_id=self._optional_string(run.get("supersedes_run_id")),
            completed_at=self._optional_string(run.get("completed_at")),
            duration_seconds=(
                self._number(run.get("duration_seconds"), f"分析历史 {index}.duration_seconds")
                if run.get("duration_seconds") is not None
                else None
            ),
            message=self._optional_string(run.get("message")),
        )

    def _decode_output(
        self,
        value: object,
        run_index: int,
        output_index: int,
    ) -> AnalysisOutputReference:
        """解析分析结果图层引用。"""
        output: Mapping[str, object] = self._mapping(
            value, f"analysis_history[{run_index}].outputs[{output_index}]"
        )
        return AnalysisOutputReference(
            layer_id=self._string(output.get("layer_id"), "分析结果.layer_id"),
            source_path=self._string(output.get("source_path"), "分析结果.source_path"),
            source_layer_name=self._optional_string(output.get("source_layer_name")),
        )

    def _decode_view_state(self, value: object) -> MapViewState | None:
        """解析可选地图视图状态。"""
        if value is None:
            return None
        view: Mapping[str, object] = self._mapping(value, "workspace.view")
        return MapViewState(
            center_x=self._number(view.get("center_x"), "workspace.view.center_x"),
            center_y=self._number(view.get("center_y"), "workspace.view.center_y"),
            zoom_percent=self._number(
                view.get("zoom_percent"), "workspace.view.zoom_percent"
            ),
        )

    def _decode_fingerprint(self, value: object) -> SourceFingerprint | None:
        """解析可选数据源文件指纹。"""
        if value is None:
            return None
        fingerprint: Mapping[str, object] = self._mapping(value, "图层 fingerprint")
        return SourceFingerprint(
            size=self._integer(fingerprint.get("size"), "fingerprint.size"),
            mtime_ns=self._integer(fingerprint.get("mtime_ns"), "fingerprint.mtime_ns"),
        )

    def _encode_manifest(self, manifest: ProjectManifest) -> dict[str, object]:
        """将工程模型转换为稳定的 JSON 对象。"""
        return {
            "format": self.FORMAT,
            "schema_version": manifest.schema_version,
            "project": {
                "name": manifest.name,
                "created_at": manifest.created_at,
                "modified_at": manifest.modified_at,
            },
            "workspace": {
                "display_crs": manifest.display_crs,
                "active_layer_id": manifest.active_layer_id,
                "layers": [self._encode_layer(layer) for layer in manifest.layers],
                "view": self._encode_view_state(manifest.view_state),
            },
            "analysis_history": [
                self._encode_analysis_run(run) for run in manifest.analysis_runs
            ],
        }

    @staticmethod
    def _encode_layer(layer: LayerReference) -> dict[str, object]:
        """编码单个图层引用。"""
        fingerprint: dict[str, int] | None = None
        if layer.fingerprint is not None:
            fingerprint = {
                "size": layer.fingerprint.size,
                "mtime_ns": layer.fingerprint.mtime_ns,
            }
        return {
            "layer_id": layer.layer_id,
            "name": layer.name,
            "source_path": layer.source_path,
            "source_layer_name": layer.source_layer_name,
            "layer_kind": layer.layer_kind,
            "visible": layer.visible,
            "selected_feature_ids": list(layer.selected_feature_ids),
            "fingerprint": fingerprint,
            "symbology": dict(layer.symbology) if layer.symbology is not None else None,
            "opacity": layer.opacity,
            "min_scale_percent": layer.min_scale_percent,
            "max_scale_percent": layer.max_scale_percent,
        }

    @staticmethod
    def _encode_view_state(view_state: MapViewState | None) -> dict[str, float] | None:
        """编码可选地图视图状态。"""
        if view_state is None:
            return None
        return {
            "center_x": view_state.center_x,
            "center_y": view_state.center_y,
            "zoom_percent": view_state.zoom_percent,
        }

    @staticmethod
    def _encode_analysis_run(run: AnalysisRun) -> dict[str, object]:
        """编码一条分析历史记录。"""
        return {
            "run_id": run.run_id,
            "algorithm_id": run.algorithm_id,
            "input_layer_ids": list(run.input_layer_ids),
            "parameters": dict(run.parameters),
            "output_layer_ids": list(run.output_layer_ids),
            "outputs": [
                {
                    "layer_id": output.layer_id,
                    "source_path": output.source_path,
                    "source_layer_name": output.source_layer_name,
                }
                for output in run.outputs
            ],
            "parent_run_ids": list(run.parent_run_ids),
            "status": run.status,
            "created_at": run.created_at,
            "supersedes_run_id": run.supersedes_run_id,
            "completed_at": run.completed_at,
            "duration_seconds": run.duration_seconds,
            "message": run.message,
        }

    @staticmethod
    def _mapping(value: object, field_name: str) -> Mapping[str, object]:
        """验证 JSON 字段是对象，并提供统一错误信息。"""
        if not isinstance(value, Mapping):
            raise ProjectReadFailed(f"工程字段 {field_name} 必须是对象。")
        return cast(Mapping[str, object], value)

    @staticmethod
    def _string(value: object, field_name: str) -> str:
        """读取必填字符串字段。"""
        if not isinstance(value, str) or not value.strip():
            raise ProjectReadFailed(f"工程字段 {field_name} 必须是非空字符串。")
        return value

    @staticmethod
    def _optional_string(value: object) -> str | None:
        """读取可选字符串字段。"""
        if value is None:
            return None
        if not isinstance(value, str):
            raise ProjectReadFailed("工程可选字符串字段类型错误。")
        return value

    @staticmethod
    def _optional_number(value: object, field_name: str) -> float | None:
        """读取可选数值字段。"""
        if value is None:
            return None
        return JsonProjectStore._number(value, field_name)

    @staticmethod
    def _boolean(value: object, field_name: str) -> bool:
        """读取布尔字段。"""
        if not isinstance(value, bool):
            raise ProjectReadFailed(f"工程字段 {field_name} 必须是布尔值。")
        return value

    @staticmethod
    def _integer(value: object, field_name: str) -> int:
        """读取整数值字段。"""
        if not isinstance(value, int) or isinstance(value, bool):
            raise ProjectReadFailed(f"工程字段 {field_name} 必须是整数。")
        return value

    @staticmethod
    def _number(value: object, field_name: str) -> float:
        """读取数值字段。"""
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ProjectReadFailed(f"工程字段 {field_name} 必须是数值。")
        return float(value)

    @classmethod
    def _string_array(cls, value: object, field_name: str) -> tuple[str, ...]:
        """读取字符串数组字段。"""
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ProjectReadFailed(f"工程字段 {field_name} 必须是字符串数组。")
        return tuple(value)

    @staticmethod
    def _feature_id(value: object, field_name: str) -> FeatureId:
        """读取领域支持的要素编号。"""
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise ProjectReadFailed(f"工程字段 {field_name} 包含非法要素编号。")
        return value
