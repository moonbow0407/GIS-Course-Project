"""应用层可预期错误。"""


class ApplicationError(Exception):
    """表示可以转换为中文用户提示的应用层基础错误。"""


class VectorFileNotFound(ApplicationError):
    """表示用户选择的矢量文件不存在。"""


class UnsupportedVectorFormat(ApplicationError):
    """表示文件扩展名不属于当前支持的矢量格式。"""


class VectorReadFailed(ApplicationError):
    """表示底层矢量数据源无法正常读取。"""


class EmptyVectorDataset(ApplicationError):
    """表示矢量数据源不包含任何记录。"""


class NoUsableGeometry(ApplicationError):
    """表示矢量数据源不包含可显示和查询的有效几何。"""


class IncompatibleCoordinateReferenceSystem(ApplicationError):
    """表示待加载图层无法安全转换到地图显示坐标系。"""


class CoordinateReferenceSystemRequired(ApplicationError):
    """表示数据没有 CRS，必须先由用户定义或修正。"""


class LayerNotFound(ApplicationError):
    """表示业务操作引用了不存在的图层。"""


class RasterFileNotFound(ApplicationError):
    """表示用户选择的栅格文件不存在。"""


class UnsupportedRasterFormat(ApplicationError):
    """表示文件扩展名不属于当前支持的栅格格式。"""


class RasterReadFailed(ApplicationError):
    """表示底层栅格数据源无法正常读取。"""


class NoActiveLayer(ApplicationError):
    """表示导出等操作缺少活动图层。"""


class UnsupportedExportFormat(ApplicationError):
    """表示输出文件扩展名不属于当前支持的导出格式。"""


class DataWriteFailed(ApplicationError):
    """表示空间图层无法写入指定输出位置。"""


class InvalidBufferParameters(ApplicationError):
    """表示缓冲区分析参数不满足业务约束。"""


class UnsupportedBufferInput(ApplicationError):
    """表示缓冲区分析输入不是可处理的矢量图层。"""


class BufferAnalysisFailed(ApplicationError):
    """表示缓冲区几何计算失败。"""


class EmptyBufferResult(ApplicationError):
    """表示缓冲区计算后没有产生可用几何。"""


class InvalidOverlayParameters(ApplicationError):
    """表示叠加分析参数不满足业务约束。"""


class UnsupportedOverlayInput(ApplicationError):
    """表示叠加分析输入不是可处理的矢量图层。"""


class OverlayAnalysisFailed(ApplicationError):
    """表示叠加几何计算失败。"""


class EmptyOverlayResult(ApplicationError):
    """表示叠加计算后没有产生可用几何。"""


class LayerReprojectionFailed(ApplicationError):
    """表示图层无法根据原始数据源转换到目标坐标系。"""


class WorkspaceOperationCancelled(ApplicationError):
    """表示用户取消了仍未提交的工作区耗时操作。"""


class ProjectReadFailed(ApplicationError):
    """表示工程文件无法读取或格式校验失败。"""


class ProjectWriteFailed(ApplicationError):
    """表示工程快照无法写入目标路径。"""


class ProjectSourceMissing(ApplicationError):
    """表示工程引用的外部数据源不存在。"""


class ProjectNotSaved(ApplicationError):
    """表示需要先为未命名工程选择保存位置。"""


class ProjectStoreNotConfigured(ApplicationError):
    """表示应用入口没有注入工程存储适配器。"""


class DatabaseConnectionFailed(ApplicationError):
    """表示 PostgreSQL 连接或 PostGIS 能力检查失败。"""


class DatabaseNotConnected(ApplicationError):
    """表示数据库操作缺少当前活动连接。"""


class DatabaseNotConfigured(ApplicationError):
    """表示应用入口没有注入数据库服务。"""


class DatabaseSchemaFailed(ApplicationError):
    """表示 PostGIS 扩展、表或索引初始化失败。"""


class DatabaseImportFailed(ApplicationError):
    """表示矢量图层导入数据库失败。"""


class DatabaseLayerNotFound(ApplicationError):
    """表示请求的数据库图层不存在。"""


class DatabaseListFailed(ApplicationError):
    """表示数据库图层目录读取失败。"""


class DatabaseLoadFailed(ApplicationError):
    """表示数据库图层加载或空间数据转换失败。"""


class InvalidRasterCalculatorParameters(ApplicationError):
    """表示栅格计算器参数不满足业务约束。"""


class RasterCalculatorFailed(ApplicationError):
    """表示栅格像素表达式求值失败。"""


class RasterBandAlignmentError(ApplicationError):
    """表示输入栅格波段空间对齐不一致（CRS/分辨率/范围）。"""


class InvalidRasterAnalysisParameters(ApplicationError):
    """表示栅格分析参数不满足业务约束。"""


class UnsupportedRasterAnalysisInput(ApplicationError):
    """表示栅格分析输入图层类型或状态不满足要求。"""


class RasterAnalysisFailed(ApplicationError):
    """表示栅格分析算法内核执行失败。"""


class EmptyRasterResult(ApplicationError):
    """表示栅格分析计算后没有产生任何有效像元。"""


class RasterWindowReadFailed(ApplicationError):
    """表示按窗口读取栅格分析数据失败。"""


class RasterReclassFailed(ApplicationError):
    """表示栅格重分类规则应用失败。"""


class DemAnalysisFailed(ApplicationError):
    """表示 DEM 地形分析计算失败。"""


class RasterClipFailed(ApplicationError):
    """表示矢量掩膜裁剪栅格失败。"""
