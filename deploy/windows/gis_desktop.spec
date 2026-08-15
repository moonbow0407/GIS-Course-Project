"""GIS 桌面通用平台 Windows onedir 发布配置。"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


project_root = Path(SPECPATH).resolve().parents[1]
application_name = "GISDesktop"
resources = project_root / "app" / "resources"
version_file = project_root / "deploy" / "windows" / "version_info.txt"
icon_file = resources / "GISDesktop.ico"

datas = [(str(resources), "app/resources")]
datas += collect_data_files("certifi")

hiddenimports = [
    "PySide6.QtPrintSupport",
    "psycopg_binary",
    "psycopg_binary._uuid",
    "scipy.ndimage",
    "sqlalchemy.dialects.postgresql.psycopg",
]

analysis = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(project_root / "deploy" / "windows" / "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["docx", "mypy", "pytest", "ruff"],
    noarchive=False,
    optimize=0,
)

python_archive = PYZ(analysis.pure)

executable = EXE(
    python_archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=application_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_file) if icon_file.exists() else None,
    version=str(version_file),
)

bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=application_name,
)
