"""Windows 冻结程序依赖自检测试。"""

import json
from pathlib import Path

from app.infrastructure.packaging_smoke import REQUIRED_PACKAGING_CHECKS, run_packaging_smoke
from main import packaging_smoke_report_path


def test_packaging_smoke_exercises_all_required_native_dependencies(tmp_path: Path) -> None:
    """自检应真实读写 GIS 数据，并覆盖发布包中的全部关键原生依赖。"""
    report_path: Path = tmp_path / "packaging-smoke.json"

    exit_code: int = run_packaging_smoke(report_path)

    payload: dict[str, object] = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["status"] == "ok"
    assert set(payload["checks"]) == set(REQUIRED_PACKAGING_CHECKS)
    assert all(check["status"] == "ok" for check in payload["checks"].values())


def test_packaging_smoke_command_requires_explicit_report_path(tmp_path: Path) -> None:
    """冻结自检入口只在传入完整命令和报告路径时启用。"""
    report_path: Path = tmp_path / "frozen-report.json"

    assert packaging_smoke_report_path(["GISDesktop.exe"]) is None
    assert packaging_smoke_report_path(
        ["GISDesktop.exe", "--packaging-smoke-test", str(report_path)]
    ) == report_path
