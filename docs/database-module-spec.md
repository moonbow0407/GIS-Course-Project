# 数据库模块 Spec（v0.1）

状态：Draft / 第一轮后端实现

本文档依据以下资料整理：

- `README.md` 中的当前工程边界和分层约束；
- `docs/GIS原理课设设计文档-完善版.md` 第 4 节数据库连接模块；
- `docs/指导书-版本1.docx` 中“数据库连接”任务要求；
- `AGENTS.md` 中关于领域层、应用层和基础设施层的编码约束。

## 1. 目标

数据库模块为桌面 GIS 提供 PostgreSQL + PostGIS 的最小可用数据通道：

1. 使用主机、端口、数据库、用户名和密码建立连接，并验证 PostGIS 可用；
2. 将当前内存中的 `VectorLayer` 以事务方式导入数据库；
3. 查询数据库中的图层目录；
4. 将指定数据库图层加载回统一的 `VectorLayer`，供地图显示和后续空间业务使用。

模块必须保持现有分层：领域模型不依赖 SQLAlchemy、GeoAlchemy2 或 Qt；应用层只依赖数据库端口；PostgreSQL/PostGIS 细节留在基础设施适配器中。

## 2. v0.1 范围

### 包含

- PostgreSQL + PostGIS，驱动使用 `postgresql+psycopg`；
- SQLAlchemy Core 负责连接、事务和参数绑定；
- 固定使用 `public.gis_layers` 与 `public.gis_features` 两张共享表；
- 支持点、线、面和混合几何，几何统一存储为 PostGIS `geometry(Geometry)`；
- 属性统一存储为 JSONB；
- 导入时自动初始化 PostGIS 扩展、表和索引；
- 导入失败时整个导入事务回滚；
- 加载时通过 `ST_AsText` 转换几何，再由 Shapely 解析；
- 加载时可按目标 CRS 在应用边界转换几何，保持数据库中的原始数据不变；
- 在主窗口数据库功能区提供连接、断开、导入、加载和图层管理入口；
- 没有真实 PostGIS 服务时，连接配置、服务编排和数据转换仍可通过单元测试验证。

### 不包含

- 栅格数据写入 PostGIS（仍使用现有 RasterIO 文件通道）；
- 数据库内直接编辑、删除、更新单个要素；
- 任意 SQL 编辑器或用户自定义查询；
- 数据库图层在 `.gisproj` 中的持久化恢复；
- 连接密码持久化、连接池配置界面和多用户权限管理；
- 通过数据库端执行空间分析。

## 3. 分层设计

```text
MainWindow / 后续数据库对话框
            ↓
DatabaseService（应用层用例编排）
            ↓
DatabaseGateway（应用层端口）
            ↓
PostgisDatabaseGateway（基础设施适配器）
            ↓
SQLAlchemy Core + psycopg → PostgreSQL/PostGIS
```

### 3.1 应用层对象

`DatabaseConnectionConfig`：不可变连接参数对象，字段为 `host`、`port`、`database`、`username`、`password`。密码只在运行时内存中存在，不进入日志、异常文本或工程文件。

`DatabaseServerInfo`：连接测试结果，至少包含数据库名、当前用户、PostGIS 版本和 PostgreSQL 版本。

`DatabaseLayerInfo`：数据库图层目录项，至少包含数据库图层 ID、名称、几何类别、CRS 文本、SRID、要素数量和创建时间。

数据库加载后的 `VectorLayer` 额外保存 `database_layer_id`，用于在当前数据库连接仍有效时重新投影该图层；这比把数据库地址伪装成文件路径更安全。

`DatabaseGateway`：应用层端口，提供 `test_connection`、`list_layers`、`import_layer`、`load_layer`、`close` 和 `ensure_schema`。

`DatabaseService`：管理当前连接生命周期，并在未连接、连接失败和数据库操作失败时抛出项目应用异常。

## 4. 数据库模式

第一轮实现使用固定的 `public` schema，表名固定以 `gis_` 前缀开头，避免把用户输入拼接进 SQL。

```sql
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS public.gis_layers (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    geometry_type TEXT NOT NULL,
    crs TEXT,
    srid INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS public.gis_features (
    id BIGSERIAL PRIMARY KEY,
    layer_id BIGINT NOT NULL
        REFERENCES public.gis_layers(id) ON DELETE CASCADE,
    geom geometry(Geometry) NOT NULL,
    attrs_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_gis_features_layer_id
    ON public.gis_features(layer_id);

CREATE INDEX IF NOT EXISTS idx_gis_features_geom
    ON public.gis_features USING GIST (geom);
```

`gis_layers.id` 和 `gis_features.id` 是数据库侧稳定身份。v0.1 加载后的 `Feature.fid` 使用 `gis_features.id`，不承诺保留文件或内存图层原始 `Feature.fid` 类型和值；这样与设计文档的 `SERIAL PRIMARY KEY` 语义一致，也避免把任意字符串 ID 强行塞入自增主键。

每个导入图层生成一条 `gis_layers` 记录；v0.1 不覆盖同名图层，调用方通过数据库图层 ID 区分同名记录。

## 5. 关键行为

### 5.1 连接测试

连接配置通过 SQLAlchemy `URL.create` 构造，密码不手工拼接，避免特殊字符破坏连接串。测试查询同时读取当前数据库、当前用户、PostgreSQL 版本和 `PostGIS_Version()`；仅能连接 PostgreSQL 但没有 PostGIS 时，连接测试失败并返回可理解的应用错误。

连接建立不自动保存密码，也不在 `__repr__`、日志或用户提示中展示密码。

### 5.2 模式初始化

首次导入前调用 `ensure_schema`。该操作在一个事务中执行扩展、表和索引的幂等创建。连接测试本身不修改数据库；数据库账号若无创建扩展或表的权限，导入时给出权限错误。

### 5.3 图层导入

导入前校验图层至少有一个有效要素。单个事务内按以下顺序执行：

1. 初始化模式；
2. 插入图层元信息并取得 `gis_layers.id`；
3. 逐要素使用参数绑定写入 WKT 和 JSONB；
4. 任一要素失败则回滚图层元信息和此前已写入的所有要素；
5. 成功提交后返回 `DatabaseLayerInfo`。

`VectorLayer.crs` 存在时保存 `crs.to_string()`，并尽量保存 EPSG SRID；无法解析为 EPSG 的 CRS 使用 SRID `0`，但保留完整 CRS 文本。没有 CRS 的图层也使用 SRID `0`，不会伪造坐标系。

属性值按 JSON 规则编码；`date` / `datetime` 在 v0.1 以 ISO 字符串写入 JSONB。不可编码的值、非有限浮点数或数据库拒绝的几何都会导致整个事务失败。

### 5.4 图层目录

`list_layers` 按创建时间和数据库 ID 倒序返回图层目录，并统计每个图层的要素数量。数据库尚未初始化模式时返回空目录，不为了展示目录而自动创建扩展或表。

### 5.5 图层加载

加载先读取图层元信息，再按 `gis_features.id` 顺序读取全部要素。几何通过 `ST_AsText(geom)` 获取并由 Shapely 解析，属性 JSONB 转为属性映射。数据库图层没有要素、CRS 文本损坏或几何无法解析时，加载失败，不生成部分图层。

如果调用方提供目标 CRS，且源 CRS 已知且不同，则在内存中用 PyProj/Shapely 转换后再返回；数据库中的几何不被修改。返回的图层 ID 使用 `db-layer-{gis_layers.id}`，名称和 CRS 来自数据库。之后用户修改地图显示 CRS 时，应用层会通过 `database_layer_id` 重新读取并转换数据库图层。

## 6. 异常边界

基础设施适配器捕获 SQLAlchemy/驱动异常并转换为应用层异常，不把驱动堆栈和连接密码直接交给界面。第一轮异常分类包括：

- `DatabaseConnectionFailed`：无法连接、PostGIS 不可用；
- `DatabaseNotConnected`：调用操作时没有活动连接；
- `DatabaseSchemaFailed`：模式初始化失败；
- `DatabaseImportFailed`：图层或要素导入失败；
- `DatabaseLayerNotFound`：指定数据库图层不存在；
- `DatabaseLoadFailed`：图层读取、属性解码或 CRS/几何转换失败。

## 7. 验收标准

### 自动化验收

- 连接配置可以正确生成带特殊字符密码的 SQLAlchemy URL，且隐藏密码渲染不泄露密码；
- 未连接时 `DatabaseService` 的列表、导入和加载操作均抛出 `DatabaseNotConnected`；
- 导入 SQL 包含元数据、几何、JSONB 和事务边界；
- 数据库异常被转换为项目应用异常；
- 点图层、带属性图层和指定 CRS 图层的数据映射有单元测试；
- 真实 PostGIS 集成测试仅在显式提供测试连接串时运行，默认测试不依赖本地 PostgreSQL 服务。

### 手动验收（当前 UI 阶段）

- 输入连接参数后可以测试连接并看到 PostGIS 版本；
- 导入当前活动矢量图层后，数据库管理器能列出图层和要素数；
- 选择数据库图层后能加载到地图，图层 CRS 与当前地图 CRS 一致；
- 导入失败不会留下半个图层；
- 错误提示不显示密码，界面不崩溃。

## 8. 待确认技术决策

以下问题不会阻塞 v0.1 后端开发，但会影响 v0.2 的 schema、UI 和工程恢复：

1. 是否要保留原始 `Feature.fid`？若需要，建议增加 `source_fid` 和 `source_fid_type` 字段，而不是复用自增主键。
2. 是否允许导入同名图层时覆盖旧图层？当前默认不覆盖，优点是安全，代价是目录中可能出现同名项。
3. 数据库 schema 是否固定 `public`，还是连接配置中允许选择 schema？当前固定 `public` 以减少 SQL 注入和部署复杂度。
4. 数据库图层是否需要随 `.gisproj` 保存并自动恢复？若需要，需要给工程模型增加数据库数据源引用和连接配置标识，密码仍不应写入工程文件。
5. UI 是只支持一个当前数据库连接，还是维护多个命名连接？当前实现只保留一个活动连接，密码不持久化。
