# GIS原理课设设计文档

## 一、项目任务与总体目标

本课程设计的题目为 **AI Coding: GIS桌面通用平台的构建**。系统要求以小组协作方式，借助 AI 辅助编码工具，从零构建一个 C/S 架构的桌面端 GIS 通用平台。平台需要能够完成空间数据的读取、显示、查询、编辑、分析和数据库管理等基础 GIS 功能，并最终形成可运行的软件、项目源码、技术文档和演示汇报材料。

本项目的核心目标可以概括为以下三点：

1. 深入理解 GIS 核心原理：将课堂中学习到的空间数据结构、空间查询、空间分析、地图投影、数据库管理等知识转化为可运行的程序。
2. 掌握工程化开发能力：通过模块化设计、版本控制、团队协作、AI 辅助编码等方式，模拟真实软件开发流程。
3. 建立系统思维：设计并实现一个能够管理、查询、编辑和分析空间数据的综合 GIS 平台，理解 GIS 软件内部的整体构造。

系统整体采用桌面客户端 + 数据管理层的 C/S 思路进行设计。用户主要通过桌面端界面完成地图浏览、图层管理、查询编辑和空间分析；系统内部通过文件读写模块和数据库连接模块提供数据服务能力，使平台既能处理本地文件数据，也能将空间数据持久化到数据库中。

---

## 二、功能需求概述

根据指导书要求，系统共分为四大核心功能模块，所有模块需要集成到统一的主界面中。

| 模块 | 模块说明 | 核心目标 |
| --- | --- | --- |
| 文件数据读取与存储 | 允许用户打开常见矢量、栅格数据文件，并将当前地图数据导出保存为通用格式。 | 使平台具备基本的数据输入与输出能力。 |
| 数据的查询与编辑 | 支持空间查询、属性查询、要素新增、删除、修改，以及线矢量的平滑和简化操作。 | 使用户能像编辑文档一样直接维护和更新地图数据。 |
| 空间分析 | 实现缓冲区分析，并自由设计一项空间分析功能，将分析结果可视化为新图层。 | 将平台从看图工具升级为能够解决实际地理问题的分析工具。 |
| 数据库连接 | 支持连接 PostgreSQL + PostGIS 空间数据库，能将空间数据导入数据库，也能从数据库加载数据至地图显示。 | 将文件式管理转变为结构化空间数据库管理，便于数据持久化、空间查询与共享。 |

系统最终需要满足以下交付要求：

- 可运行的桌面端 GIS 软件。
- 完整项目源码。
- 技术设计文档。
- 演示汇报材料。

---

## 三、总体技术栈

- 使用语言：Python 3.10

```python
桌面界面：PySide6
地图绘制：QGraphicsView / QGraphicsScene / QPainter
矢量数据：GeoPandas + Fiona + Shapely
栅格数据：Rasterio + NumPy
坐标转换：PyProj
空间分析：Shapely + GeoPandas
数据库：PostgreSQL + PostGIS + SQLAlchemy + GeoAlchemy2 + psycopg2 / psycopg
数据处理：Pandas
打包部署：PyInstaller
AI Coding：Codex / Claude Code / Trae
协作管理：Git + Gitee
```

技术选型说明：

1. Python 生态中 GIS 库较为成熟，GeoPandas、Shapely、Rasterio、PyProj 等库能够覆盖矢量处理、栅格处理、空间分析和坐标转换等核心需求。
2. PySide6 适合开发桌面端图形界面，并且与 Python 数据处理库结合方便。
3. QGraphicsView / QGraphicsScene / QPainter 能够支持地图图层绘制、缩放、平移、要素高亮和交互编辑。
4. 数据库层采用 PostgreSQL + PostGIS。PostgreSQL 负责关系型数据管理，PostGIS 提供 geometry 空间字段、空间索引和空间函数，便于后续实现更规范的空间数据存储、查询与分析。
5. 使用 PyInstaller 打包，便于将课程设计成果交付为可运行程序。

---

## 四、系统总体架构设计

### 1.总体架构

系统采用分层架构设计，将界面交互、业务功能、数据模型和数据持久化分开，降低模块之间的耦合度。

```text
用户界面层
├── 主窗口 MainWindow
├── 图层管理面板 LayerPanel
├── 地图画布 MapCanvas
├── 属性表 AttributeTable
└── 参数对话框 Dialogs

业务功能层
├── 文件读写模块 FileIO
├── 查询编辑模块 QueryEdit
├── 空间分析模块 SpatialAnalysis
└── 数据库连接模块 Database

数据模型层
├── Layer
├── Feature
├── Geometry
├── RasterLayer
└── AnalysisResult

数据访问层
├── VectorReader / VectorWriter
├── RasterReader / RasterWriter
├── DBConnection
└── LayerRepository
```

### 2.核心数据流

系统运行时的数据流如下：

```text
文件或数据库
↓
读取器 / 数据库连接器
↓
Layer / RasterLayer 内存图层对象
↓
地图画布显示与图层面板管理
↓
查询、编辑、分析等业务操作
↓
生成新图层或更新原图层
↓
导出为文件或写入数据库
```

这种设计可以保证不同数据来源最终都被统一转换为内存中的图层对象。后续的查询、编辑和空间分析模块不需要关心数据最初来自 Shapefile、GeoJSON、GeoTIFF 还是数据库，只需要面向统一的数据模型进行处理。

### 3.核心数据结构设计

```python
class Feature:
    def __init__(self, fid, geometry, attributes):
        self.fid = fid
        self.geometry = geometry
        self.attributes = attributes


class Layer:
    def __init__(self, name, crs, geometry_type, features, srid=None):
        self.name = name
        self.crs = crs
        self.srid = srid
        self.geometry_type = geometry_type
        self.features = features
        self.visible = True
        self.style = {}


class RasterLayer:
    def __init__(self, name, crs, transform, bands, width, height):
        self.name = name
        self.crs = crs
        self.transform = transform
        self.bands = bands
        self.width = width
        self.height = height
        self.visible = True
```

---

## 五、各模块实现重点与相关技术栈

## 1.文件数据读取与存储

模块功能：允许用户打开常见矢量/栅格数据文件，并将当前地图数据导出保存为通用格式。

### 1.1 模块目标

该模块是整个系统的数据入口和出口。没有文件读取功能，平台无法加载外部地图数据；没有文件保存功能，用户编辑和分析后的结果也无法长期保存。因此，该模块的目标是让系统具备基本的数据输入、输出和格式转换能力。

### 1.2 设计思路

设计时主要从以下三个维度考虑：

1. 数据抽象：将不同格式的数据统一抽象为 Layer、Feature、RasterLayer 等对象，使后续业务逻辑不需要关心具体文件格式。
2. 读写分离：读取器负责把外部文件转换为内存图层对象，写入器负责把内存图层保存为外部文件，两者相互独立。
3. 格式识别：通过文件扩展名自动判断数据类型，再调用对应的读取或写入逻辑。

### 1.3 读取实现

根据扩展名判断格式，如果是 `.shp`、`.geojson`、`.kml` 等矢量格式，就调用 GeoPandas / Fiona / GDAL OGR 打开数据源，读取图层元信息、坐标系、几何类型、FID、几何对象和属性字段。

如果是 `.tif`、`.img`、`.dem` 等栅格格式，就调用 Rasterio / GDAL 读取栅格宽度、高度、波段数、投影、地理变换参数和像素数组。

读取流程：

```text
选择文件
↓
获取文件扩展名
↓
判断矢量或栅格
↓
调用对应 Reader
↓
转换为 Layer / RasterLayer
↓
加入图层管理器
↓
刷新地图画布
```

### 1.4 存储实现

用户编辑数据、做缓冲区分析、生成新图层之后，需要把结果保存出去。

矢量存储要创建数据源、图层、字段，再遍历要素写入几何和属性。栅格存储要创建数据集、设置投影和地理变换，再逐波段写入数组。

存储流程：

```text
选择要导出的图层
↓
选择输出路径和格式
↓
判断矢量或栅格
↓
调用对应 Writer
↓
写入几何、属性或像素数据
↓
提示导出成功或失败
```

| 子功能 | 推荐技术栈 | 说明 |
| --- | --- | --- |
| 矢量读取 | GeoPandas / GDAL OGR / Fiona | 读取 `.shp`、`.geojson`、`.kml` |
| 矢量保存 | GeoPandas / GDAL OGR / Fiona | 导出 `.shp`、`.geojson` |
| 栅格读取 | Rasterio / GDAL / NumPy | 读取 `.tif`、`.img`、`.dem` |
| 栅格保存 | Rasterio / GDAL | 保存处理后的栅格 |
| 坐标系处理 | PyProj | 读取 CRS、投影转换 |
| 数据封装 | 自定义 Layer / RasterLayer 类 | 统一管理图层 |

对应接口：

```python
def read_vector(path: str) -> Layer:
    """读取矢量数据，返回图层对象。"""


def read_raster(path: str) -> RasterLayer:
    """读取栅格数据，返回栅格图层对象。"""


def save_vector(layer: Layer, path: str) -> None:
    """保存矢量图层。"""


def save_raster(layer: RasterLayer, path: str) -> None:
    """保存栅格图层。"""
```

### 1.5 异常处理

文件读写过程中可能出现格式不支持、文件损坏、坐标系缺失、字段类型不兼容、输出路径无权限等问题。系统应通过弹窗或状态栏提示用户，并避免程序直接崩溃。

```python
try:
    layer = read_vector(path)
except UnsupportedFormatError:
    show_error("暂不支持该文件格式")
except Exception as e:
    show_error(f"文件读取失败：{e}")
```

---

## 2.数据查询与编辑模块

模块功能：让用户能对地图数据进行查询、选择、编辑和维护。指导书要求这一部分实现空间查询、属性查询、要素新增、删除、修改，以及线矢量的平滑和简化操作。

### 2.1 模块目标

该模块的核心目标是使用户能像编辑文档一样直接维护和更新地图数据。用户不仅可以浏览地图，还可以选中要素、查看属性、修改属性、增加或删除要素，并对线要素进行平滑和简化处理。

### 2.2 查询模块设计

查询方式统一抽象为 Queryer 接口，不同查询方式实现不同子类。

```python
BaseQueryer
├── PointQueryer
├── RectQueryer
└── AttributeQueryer
```

#### 点选查询

用户在地图上点一下，程序判断点到了哪个要素。

流程：

```text
鼠标点击屏幕坐标
↓
转换成地图坐标
↓
构造 Shapely Point
↓
计算点击点到各要素的距离
↓
返回容差范围内最近的要素
↓
地图高亮 + 属性表显示
```

指导书中的点选查询就是这个思路：计算点击点到各要素的距离，返回距离最小且在容差范围内的要素。

相关技术栈：

```text
PySide6 鼠标事件
Shapely Point / distance
GeoPandas GeoDataFrame
Pandas 属性表
```

#### 框选查询

用户拖拽一个矩形，程序查出矩形范围内的所有要素。

流程：

```text
鼠标按下
↓
拖拽形成矩形
↓
屏幕矩形转地图范围
↓
构造 Shapely box
↓
判断图层要素是否 intersects 矩形
↓
返回多个要素
↓
地图批量高亮 + 属性表显示
```

相关技术栈：

```text
PySide6 鼠标拖拽事件
Shapely box / intersects
GeoPandas 空间筛选
```

#### 属性查询

用户根据属性字段筛选要素。

例如：

```text
name 包含 “学校”
type = “道路”
area > 1000
```

流程：

```text
打开属性查询对话框
↓
选择字段、运算符、查询值
↓
在 GeoDataFrame 中筛选属性
↓
返回查询结果
↓
地图高亮 + 属性表显示
```

相关技术栈：

```text
Pandas
GeoPandas
PySide6 QDialog
QComboBox
QLineEdit
QTableView / QTableWidget
```

对应接口：

```python
class BaseQueryer:
    def query(self, layer, condition):
        raise NotImplementedError


class PointQueryer(BaseQueryer):
    def query(self, layer, condition):
        pass


class RectQueryer(BaseQueryer):
    def query(self, layer, condition):
        pass


class AttributeQueryer(BaseQueryer):
    def query(self, layer, condition):
        pass
```

### 2.3 编辑模块设计

编辑操作包括新增、删除、修改。为了便于后续扩展撤销/重做功能，可以将每一次编辑封装为一个独立操作对象。

```python
class FeatureEditor:
    def add_feature(self, layer: Layer, geometry, attributes) -> Feature:
        pass

    def delete_feature(self, layer: Layer, fid: int) -> None:
        pass

    def update_feature(self, layer: Layer, fid: int, geometry=None, attributes=None) -> None:
        pass
```

#### 新增要素

新增要素时，用户先选择编辑图层和绘制工具，再在地图画布上绘制点、线或面。绘制完成后弹出属性编辑窗口，用户填写属性字段，系统将几何和属性封装为 Feature 后加入图层。

```text
选择编辑图层
↓
选择新增点 / 线 / 面工具
↓
在地图上绘制几何
↓
填写属性信息
↓
生成 Feature
↓
加入 Layer
↓
刷新地图
```

#### 删除要素

删除要素时，用户先通过点选或框选选中要素，再点击删除按钮。系统根据 FID 从图层中移除该要素，并刷新属性表和地图显示。

#### 修改要素

修改包括属性修改和几何修改。属性修改可以通过属性表或属性编辑对话框完成；几何修改可以通过拖动节点、增加节点、删除节点等方式完成。

### 2.4 线矢量平滑与简化

指导书要求实现线矢量的平滑和简化操作。

#### 线简化

线简化可以采用 Douglas-Peucker 算法。该算法通过递归计算点到线段的最大垂直距离，保留关键点，删除冗余点，从而在尽量保持线形特征的同时减少节点数量。

流程：

```text
输入线要素坐标序列
↓
连接起点和终点
↓
寻找距离线段最远的点
↓
如果最大距离大于阈值，则递归保留该点
↓
如果最大距离小于阈值，则删除中间点
↓
输出简化后的线要素
```

#### 线平滑

线平滑可以采用三次样条插值或贝塞尔曲线拟合。平滑处理的目标是让折线变得更加自然连续，适合道路、河流等线状地物的表达。

流程：

```text
输入线要素坐标序列
↓
提取控制点
↓
进行三次样条插值或贝塞尔曲线拟合
↓
生成更密集且平滑的新坐标序列
↓
输出平滑后的线要素
```

对应接口：

```python
class LineProcessor:
    def simplify(self, line_geometry, tolerance: float):
        """使用 Douglas-Peucker 算法简化线要素。"""

    def smooth(self, line_geometry, method: str = "bezier"):
        """使用贝塞尔曲线或样条插值平滑线要素。"""
```

---

## 3.空间分析模块

模块功能：实现缓冲区生成，并自由设计一项空间分析功能，将分析结果可视化为新图层。

### 3.1 模块目标

空间分析模块是 GIS 平台区别于普通地图浏览器的重要部分。通过空间分析，用户可以基于已有空间数据生成新的地理信息结果，例如道路缓冲区、影响范围、空间叠加结果等。

### 3.2 设计思路

设计时主要从以下三个维度考虑：

1. 分析算法抽象：将缓冲区分析、叠加分析等功能统一抽象为 Analyzer 接口，业务层通过统一方式调用。
2. 分析结果表示：定义统一的 AnalysisResult 数据结构，包含输入图层、分析参数、输出图层和统计信息。
3. 分析流程管理：对于耗时较长的分析任务，可以设计进度提示和结果回调，避免界面卡顿。

```python
class BaseAnalyzer:
    def run(self, input_layer, params):
        raise NotImplementedError


class BufferAnalyzer(BaseAnalyzer):
    def run(self, input_layer, params):
        pass


class OverlayAnalyzer(BaseAnalyzer):
    def run(self, input_layer, params):
        pass
```

### 3.3 缓冲区分析

缓冲区分析根据输入几何对象和缓冲距离，生成缓冲多边形。对于点要素，生成圆形缓冲区；对于线要素，生成带状缓冲区；对于面要素，生成向外扩展或向内收缩的多边形。

流程：

```text
选择输入图层
↓
输入缓冲距离
↓
选择缓冲单位和输出图层名称
↓
对每个要素调用 geometry.buffer(distance)
↓
合并或保留独立缓冲结果
↓
生成新的面图层
↓
添加到地图显示
```

伪代码：

```python
def buffer_analysis(layer: Layer, distance: float) -> Layer:
    result_features = []
    for feature in layer.features:
        buffer_geom = feature.geometry.buffer(distance)
        result_features.append(
            Feature(
                fid=feature.fid,
                geometry=buffer_geom,
                attributes=feature.attributes.copy()
            )
        )
    return Layer(
        name=f"{layer.name}_buffer",
        crs=layer.crs,
        geometry_type="Polygon",
        features=result_features
    )
```

注意事项：

- 缓冲距离应与图层坐标单位保持一致。如果图层是经纬度坐标，应提醒用户先进行投影转换。
- 对多个缓冲结果可以提供“合并缓冲区”和“保留单独缓冲区”两种方式。
- 分析结果应作为新图层加入图层面板，而不是直接覆盖原始图层。

### 3.4  TODO 自定义空间分析功能

---

## 4.数据库连接模块

模块功能：允许用户连接 PostgreSQL + PostGIS 空间数据库，将空间数据导入数据库存储，或从数据库加载数据至地图显示。

### 4.1 模块目标

该模块的核心目标是将文件式管理转变为结构化的空间数据库管理，便于空间数据的持久化、查询和共享。相比普通关系型数据库，PostGIS 能直接保存 geometry 空间字段，并提供空间索引、坐标系标识和 `ST_Intersects`、`ST_Buffer`、`ST_AsText` 等空间函数，更适合作为 GIS 桌面平台的数据管理后端。

### 4.2 设计思路

设计时主要从以下三个维度考虑：

1. 数据库抽象：将 PostgreSQL/PostGIS 连接封装为 DBConnection 接口，界面层只负责填写连接参数和触发操作，业务层通过统一接口执行建表、导入、加载和查询。
2. 数据映射：定义内存图层与数据库表之间的映射规则。图层元信息保存到 `gis_layers` 表，要素几何保存为 PostGIS `geometry` 字段，要素属性保存为 `JSONB` 字段，坐标系通过 SRID 和 CRS 文本共同记录。
3. 事务管理：导入图层时使用事务机制，保证要么全部写入成功，要么全部回滚。

### 4.3 数据库表结构设计

PostGIS 数据库中可以设计两张核心表：图层表和要素表。图层表保存图层级元信息，要素表保存每个空间要素的几何和属性。

#### 图层表 gis_layers

| 字段名 | 类型 | 说明 |
| --- | --- | --- |
| id | SERIAL PRIMARY KEY | 图层编号 |
| name | TEXT | 图层名称 |
| geometry_type | TEXT | 几何类型 |
| crs | TEXT | 坐标参考系统 |
| srid | INTEGER | 空间参考编号 |
| created_at | TIMESTAMP | 创建时间 |

#### 要素表 gis_features

| 字段名 | 类型 | 说明 |
| --- | --- | --- |
| id | SERIAL PRIMARY KEY | 要素编号 |
| layer_id | INTEGER | 所属图层编号 |
| geom | geometry(Geometry) | PostGIS 几何字段 |
| attrs_json | JSONB | JSON 格式属性 |

建表语句：

```sql
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS gis_layers (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    geometry_type TEXT,
    crs TEXT,
    srid INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS gis_features (
    id SERIAL PRIMARY KEY,
    layer_id INTEGER NOT NULL,
    geom geometry(Geometry) NOT NULL,
    attrs_json JSONB,
    FOREIGN KEY(layer_id) REFERENCES gis_layers(id)
);

CREATE INDEX IF NOT EXISTS idx_gis_features_geom
ON gis_features USING GIST (geom);
```

这里使用通用的 `geometry(Geometry)` 类型，方便在同一张要素表中保存点、线、面等不同几何类型。每个要素实际的几何类型和 SRID 由图层表记录，并在导入和加载时由程序进行校验。

### 4.4 数据导入至数据库

将内存中的图层数据写入数据库。系统先在 `gis_layers` 表中写入图层元信息，再遍历每个 Feature，将几何对象转换为 WKT，并通过 `ST_GeomFromText(wkt, srid)` 写入 PostGIS geometry 字段，将属性字典转换为 JSON 后写入 `JSONB` 字段。

流程：

```text
选择当前图层
↓
连接数据库
↓
启用 PostGIS 扩展并创建数据表
↓
创建图层记录
↓
遍历 Feature
↓
geometry 转 WKT 后写入 geometry 字段
↓
attributes 转 JSONB
↓
写入 gis_features
↓
提交事务
```

### 4.5 从数据库加载图层

从数据库加载图层时，系统先读取 `gis_layers` 表获取图层元信息，再读取 `gis_features` 表中属于该图层的所有记录。读取时通过 `ST_AsText(geom)` 将 PostGIS geometry 转换为 WKT，再由 Shapely 转换为几何对象；`attrs_json` 转换为属性字典，最后重新构造 Layer 对象并加入地图显示。

流程：

```text
选择数据库连接
↓
查询图层列表
↓
选择要加载的图层
↓
读取图层元信息
↓
读取所有要素记录
↓
ST_AsText(geom) 转 WKT，再转 Geometry
↓
JSONB 转 attributes
↓
构造 Layer 并显示
```

---

## 六、系统界面设计

### 1.系统主界面设计

系统主界面可参考经典的 GIS 软件布局，采用顶部菜单栏和工具栏、左侧图层面板、中间地图画布、右侧属性/分析面板、底部状态栏的结构。

```text
┌────────────────────────────────────────────┐
│ 菜单栏：文件  编辑  查询  分析  数据库  帮助 │
├────────────────────────────────────────────┤
│ 工具栏：打开 保存 平移 缩放 点选 框选 编辑 分析 │
├──────────────┬───────────────────┬─────────┤
│ 图层管理面板 │      地图画布       │ 属性面板 │
│              │                   │ 分析参数 │
├──────────────┴───────────────────┴─────────┤
│ 状态栏：坐标  比例尺  当前图层  选中要素数量 │
└────────────────────────────────────────────┘
```

各区域说明：

- 顶部菜单栏：提供文件打开、保存、导出、查询、编辑、空间分析、数据库连接等入口。
- 工具栏：放置常用工具按钮，如打开文件、平移、缩放、点选、框选、新增要素、删除要素、缓冲区分析等。
- 左侧图层管理面板：以树形列表展示当前加载的所有图层，支持显示/隐藏、图层排序、删除图层和查看图层属性。
- 中部地图画布：用于绘制矢量和栅格图层，并响应鼠标交互。
- 右侧属性/分析面板：显示选中要素属性，或显示空间分析参数。
- 底部状态栏：实时显示鼠标所在地图坐标、比例尺、当前图层、选中要素数量等信息。

从开发顺序上看，主界面应优先搭建。第一阶段先完成菜单栏、工具栏、图层面板、地图画布、属性表、状态栏等界面骨架，并为各功能模块预留按钮和菜单入口。按钮初期可以只弹出“功能开发中”或打开空参数对话框，后续再逐步接入文件读写、查询编辑、空间分析和数据库连接等具体逻辑。这样可以在开发早期就形成统一的测试入口，方便每个模块边开发边在真实界面中验证。

### 2.系统对话框设计

系统中需要设计多个对话框，包括：

| 对话框 | 功能 |
| --- | --- |
| 打开文件对话框 | 选择矢量或栅格数据文件 |
| 图层属性对话框 | 查看图层名称、坐标系、几何类型、要素数量 |
| 属性查询对话框 | 选择字段、运算符和查询值 |
| 要素属性编辑对话框 | 新增或修改要素属性 |
| 缓冲区分析对话框 | 设置输入图层、缓冲距离、输出图层名称 |
| 叠加分析对话框 | 设置输入图层 A 和叠加图层 B |
| 数据库连接对话框 | 输入 PostgreSQL 主机、端口、数据库名、用户名和密码，并测试 PostGIS 连接 |

对话框设计原则：

1. 标题清晰，能直接表达用途，例如“缓冲区分析参数设置”。
2. 参数按功能分组，例如输入设置、输出设置、执行按钮。
3. 主按钮放在右侧，取消按钮放在左侧或主按钮旁边。
4. 输入参数需要进行校验，例如缓冲距离必须为数字，输出图层名称不能为空。

### 3.地图交互设计

地图画布需要支持以下交互操作：

- 平移：按住鼠标左键拖动地图。
- 缩放：通过鼠标滚轮或工具按钮缩放地图。
- 点选：点击地图中的要素并显示属性。
- 框选：拖拽矩形范围并批量选择要素。
- 高亮：选中要素以醒目颜色显示。
- 编辑：在编辑模式下新增、移动、删除或修改要素。

地图坐标转换是交互设计中的关键点。鼠标点击得到的是屏幕坐标，需要通过地图画布的变换矩阵转换为地图坐标，再用于空间查询和编辑。





## 七、打包部署设计

系统开发完成后，使用 PyInstaller 打包为可执行程序。

打包命令示例：

```bash
pyinstaller -F -w main.py --name GISDesktopPlatform
```

如果项目包含图标、样式文件或测试数据，可以使用 `--add-data` 参数一起打包。

```bash
pyinstaller -F -w main.py ^
  --name GISDesktopPlatform ^
  --add-data "resources;resources" ^
  --add-data "sample_data;sample_data"
```

打包后需要在无开发环境的电脑上进行运行测试，检查界面能否启动、依赖库是否完整、示例数据能否正常打开。
