class HiveError(Exception):
    """公共层异常基类。底层异常保留在 __cause__ 中。"""


class HiveConfigurationError(HiveError):
    """连接配置缺失或无效。"""


class HiveUnavailable(HiveError):
    """无法建立连接或连接在查询期间中断。"""


class HiveTimeout(HiveError):
    """连接或查询超时。"""


class HiveQueryError(HiveError):
    """查询、参数或结果字段无效。"""


class HiveResultTooLarge(HiveQueryError):
    """查询结果超过调用方允许的行数。"""
