"""空间分析所需的坐标参考系统环境。"""

from dataclasses import dataclass

from pyproj import CRS


@dataclass(frozen=True, slots=True)
class AnalysisEnvironment:
    """表示一次分析任务统一使用的目标坐标系。"""

    # 分析坐标系：所有输入应在此坐标系的临时副本上参与几何计算。
    analysis_crs: CRS
