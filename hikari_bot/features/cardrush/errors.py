class CardrushError(Exception):
    """Cardrush 功能的基础异常。"""


class CardrushClientError(CardrushError):
    """Cardrush 请求或页面解析失败。"""


class CardrushRepositoryError(CardrushError):
    """Cardrush 持久化失败。"""


class CardrushRenderError(CardrushError):
    """Cardrush 报表渲染失败。"""
