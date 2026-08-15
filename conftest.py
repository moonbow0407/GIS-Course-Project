"""全局 pytest 配置。

必须在任何 rasterio / pyproj / GDAL 导入之前隔离 PROJ 数据目录：本机可能安装
PostgreSQL + PostGIS，其机器级 ``PROJ_LIB`` 指向旧版 proj.db，会使新版 PROJ
的 EPSG 数据库查询全部失败。PROJ 数据目录在 libproj 初始化时一次性读取，
conftest 在测试模块导入前执行，是最早且对克隆仓库即生效的拦截点。
"""

from app.infrastructure.proj_environment import configure_proj_environment

configure_proj_environment()
