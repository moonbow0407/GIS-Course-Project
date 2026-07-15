## GIS原理课设设计文档

### 一、总体技术栈

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
AI Coding：Codex/Claude Code
```
### 二、各模块实现重点与相关技术栈

#### 1.文件数据读取与存储

模块功能：允许用户打开常见矢量/栅格数据文件，并将当前地图数据导出保存为通用格式。

- 读取

根据扩展名判断格式，如果是 `.shp`、`.geojson`、`.kml` 等矢量格式，就调用 GDAL/OGR，打开数据源、获取图层和元信息、遍历要素、提取 FID、几何和属性字段；栅格则读取宽、高、波段数、投影、地理变换参数和像素数组

- 存储

用户编辑数据、做缓冲区分析、生成新图层之后，需要把结果保存出去。

矢量存储要创建数据源、图层、字段，再遍历要素写入几何和属性；栅格存储要创建数据集、设置投影和地理变换，再逐波段写入数组。

| 子功能     | 推荐技术栈                    | 说明                            |
| ---------- | ----------------------------- | ------------------------------- |
| 矢量读取   | GeoPandas / GDAL OGR / Fiona  | 读取 `.shp`、`.geojson`、`.kml` |
| 矢量保存   | GeoPandas / GDAL OGR / Fiona  | 导出 `.shp`、`.geojson`         |
| 栅格读取   | Rasterio / GDAL / NumPy       | 读取 `.tif`、`.img`、`.dem`     |
| 栅格保存   | Rasterio / GDAL               | 保存处理后的栅格                |
| 坐标系处理 | PyProj                        | 读取 CRS、投影转换              |
| 数据封装   | 自定义 Layer / RasterLayer 类 | 统一管理图层                    |

对应接口

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



#### 2.数据查询与编辑模块

模块功能：让用户能对地图数据进行**查询、选择、编辑和维护**。指导书要求这一部分实现：空间查询、属性查询、要素新增、删除、修改，以及线矢量的平滑和简化操作。



- 查询

  ```python
  BaseQueryer
  ├── PointQueryer
  ├── RectQueryer
  └── AttributeQueryer
  ```

**点选查询**

用户在地图上点一下，程序判断点到了哪个要素。

流程：

```
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

指导书里的点选查询就是这个思路：计算点击点到各要素的距离，返回距离最小且在容差范围内的要素。

相关技术栈：

```
PySide6 鼠标事件
Shapely Point / distance
GeoPandas GeoDataFrame
Pandas 属性表
```

------

**框选查询**

用户拖拽一个矩形，程序查出矩形范围内的所有要素。

流程：

```
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
```

相关技术栈：

```
PySide6 鼠标拖拽事件
Shapely box / intersects
GeoPandas 空间筛选
```

------

**属性查询**

用户根据属性字段筛选要素。

比如：

```
name 包含 “学校”
type = “道路”
area > 1000
```

流程：

```
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

```
Pandas
GeoPandas
PySide6 QDialog
QComboBox
QLineEdit
QTableView / QTableWidget
```

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

