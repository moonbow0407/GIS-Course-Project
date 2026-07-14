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


class LayerNotFound(ApplicationError):
    """表示业务操作引用了不存在的图层。"""
