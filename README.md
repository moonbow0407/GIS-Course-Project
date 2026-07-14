# GIS 桌面通用平台

基于 Python、PySide6、GeoPandas 和 Rasterio 构建的桌面 GIS 课程设计项目。平台采用经典 GIS 工作台布局，已经支持矢量与栅格数据的统一读取、显示和图层管理，并为查询编辑、空间分析及 PostGIS 数据管理集成了完整界面入口。

## 功能状态

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| 矢量数据读取 | 已实现 | 支持 Shapefile、GeoJSON 和 JSON |
| 栅格数据读取 | 已实现 | 支持 GeoTIFF、IMG 和 DEM |
| 地图显示 | 已实现 | 支持矢量、栅格叠加与不同 CRS 自动统一 |
| 图层管理 | 已实现 | 支持显隐、激活、排序、删除、搜索和属性查看 |
| 地图导航 | 已实现 | 支持平移、滚轮缩放、按钮缩放和全图显示 |
| 查询与编辑 | 接口已集成 | 点选、框选、属性查询及要素编辑入口已就绪 |
| 空间分析 | 接口已集成 | 缓冲区、叠加分析和结果管理入口已就绪 |
| PostGIS | 接口已集成 | 连接、导入、加载和数据库管理入口已就绪 |
| 工程与导出 | 接口已集成 | 工程管理、图层保存和结果导出入口已就绪 |

未接入业务逻辑的按钮会明确提示“接口已预留”，不会生成演示结果或测试数据。程序启动时保持空工作区，用户需要自行打开本地空间数据。

## 主界面

- 顶部功能区：文件、地图、编辑、分析、数据库和视图六个标签页，共 37 个入口。
- 左侧图层面板：显示真实图层，支持搜索、显隐切换和图层顺序管理。
- 中央地图画布：占据其余全部空间，不设置右侧预留面板。
- 底部状态栏：显示坐标、视图比例、当前图层、选择数量和坐标系。

## 支持格式

### 矢量

- `.shp`
- `.geojson`
- `.json`

### 栅格

- `.tif` / `.tiff`
- `.img`
- `.dem`

多波段遥感影像会自动选择显示波段、执行百分位拉伸，并将无数据背景设为透明。后续加载的图层会自动转换到当前地图工作区的显示坐标系。

## 环境要求

- Windows 10/11
- Python 3.10–3.12
- [uv](https://docs.astral.sh/uv/)（推荐）

## 安装与启动

```powershell
uv sync
uv run python main.py
```

启动后进入“文件”标签页，点击“打开数据”选择本地矢量或栅格文件。仓库不包含测试数据，运行程序不要求 PostgreSQL/PostGIS 服务。

不使用 uv 时可以通过 requirements 文件安装：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

## 开发检查

```powershell
uv run ruff check .
uv run mypy app
uv run pytest
```

当前基线为 31 项测试通过，覆盖领域图层、地图文档、应用服务、矢量读取器、图层面板和 Qt 矢量渲染等已有能力。

## 项目结构

```text
gis_desktop/
├── app/
│   ├── application/          # 应用服务、端口和结果对象
│   ├── domain/               # 矢量、栅格、要素和地图文档模型
│   ├── infrastructure/       # GeoPandas、Rasterio 文件读取适配器
│   ├── presentation/         # 主窗口、功能区、面板、画布和渲染器
│   └── resources/styles/     # 全局 QSS 主题
├── tests/                    # 自动化测试
├── main.py                   # 应用入口
├── pyproject.toml            # 项目与工具配置
├── requirements.txt          # pip 依赖列表
└── uv.lock                   # 可复现依赖锁文件
```

## 架构

```text
用户界面层 → 应用服务层 → 领域模型层 ← 基础设施层
```

- 界面层只处理用户交互和 Qt 渲染。
- 应用服务统一编排图层管理、选择和外部数据读取。
- 领域模型不依赖 Qt，保存矢量、栅格及地图文档状态。
- 基础设施层通过 GeoPandas 和 Rasterio 适配具体文件格式。

## 后续计划

1. 接入点选、框选和属性查询交互。
2. 实现要素新增、修改、删除以及线简化、线平滑。
3. 实现缓冲区分析和叠加分析，结果作为新图层加入地图。
4. 接入 PostgreSQL/PostGIS 连接、图层导入与加载。
5. 完成矢量/栅格导出、工程保存和 PyInstaller 打包。
