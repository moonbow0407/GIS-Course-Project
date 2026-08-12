"""分块流式栅格重投影端口。

大栅格无法整幅载入内存，重投影工具依赖该端口把源栅格文件按窗口
流式转换到目标坐标系并直接写入输出文件；具体实现由基础设施层的
Rasterio 适配器提供，应用层不直接依赖重投影库。
"""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from pyproj import CRS

from app.domain.raster_grid import RasterGrid

# 进度回调：接收（已完成窗口数, 总窗口数），返回 False 表示请求取消。
ProjectionProgressCallback = Callable[[int, int], bool]


class WindowedRasterProjector(Protocol):
    """定义把源栅格文件按窗口流式重投影到输出文件的能力。"""

    def project_to_file(
        self,
        source_path: Path,
        target_crs: CRS,
        output_path: Path,
        *,
        source_crs_override: CRS | None = None,
        resampling: str = "bilinear",
        progress_callback: ProjectionProgressCallback | None = None,
    ) -> RasterGrid:
        """把源栅格文件重投影并流式写入输出 GeoTIFF。

        参数:
            source_path: 源栅格文件路径。
            target_crs: 重投影目标坐标系。
            output_path: 输出文件路径（不能与源文件相同且不能已存在）。
            source_crs_override: 工程内覆盖的源坐标系；为空时使用文件声明。
            resampling: 重采样方法名称（nearest/bilinear/cubic 等）。
            progress_callback: 每完成一个窗口回调；返回 False 时取消。

        返回:
            输出栅格的目标空间网格（CRS、仿射变换、行列数）。

        异常:
            LayerReprojectionFailed: 源文件缺失、源 CRS 不可知或输出失败。
            WorkspaceOperationCancelled: 进度回调返回 False 表示用户取消。
        """
        ...
