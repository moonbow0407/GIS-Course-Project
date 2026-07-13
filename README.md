# GIS桌面通用平台

基于 Python 3.10 和 PySide6 的桌面端 GIS 通用平台课程设计项目。

当前阶段已实现主界面和基础交互骨架，后续可继续接入 GeoPandas、Shapely、Rasterio、PostGIS 等真实 GIS 业务能力。

## 环境准备

本项目使用 uv 管理依赖，并在项目目录内创建 `.venv` 虚拟环境。

```powershell
cd D:\workspace\gis_lab\gis_desktop
uv sync
```

## 启动程序

```powershell
uv run python main.py
```

## 常用开发命令

```powershell
uv run ruff check .
uv run mypy app
uv run pytest
```

## 项目结构

```text
gis_desktop/
├── main.py
├── pyproject.toml
├── requirements.txt
├── app/
│   ├── main_window.py
│   ├── widgets/
│   ├── models/
│   ├── services/
│   └── resources/
└── README.md
```

