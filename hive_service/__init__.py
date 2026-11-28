"""公共 Hive 读取接口，适用于 Web、版本分析、NLP 和 Agent。"""

from .client import HiveClient, get_hive_client
from .config import HiveConfig
from .errors import (
    HiveConfigurationError,
    HiveError,
    HiveQueryError,
    HiveResultTooLarge,
    HiveTimeout,
    HiveUnavailable,
)

__all__ = [
    "HiveClient", "HiveConfig", "get_hive_client", "HiveError",
    "HiveConfigurationError", "HiveUnavailable", "HiveTimeout",
    "HiveQueryError", "HiveResultTooLarge",
]
