from __future__ import annotations

import getpass
import os
import re
from dataclasses import dataclass, field
from typing import Mapping

from .errors import HiveConfigurationError


@dataclass(frozen=True)
class HiveConfig:
    host: str
    port: int = 10000
    username: str = field(default_factory=getpass.getuser)
    database: str = "default"
    auth: str = "NONE"
    connect_timeout: int = 10
    query_timeout: int = 60
    max_rows: int = 10000
    cache_entries: int = 32

    def __post_init__(self):
        if not self.host.strip() or not self.username.strip():
            raise HiveConfigurationError("HIVE_HOST and HIVE_USERNAME must be nonempty")
        if not 1 <= self.port <= 65535:
            raise HiveConfigurationError("HIVE_PORT must be between 1 and 65535")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.database):
            raise HiveConfigurationError("Invalid HIVE_DATABASE")
        if self.auth not in ("NONE", "NOSASL"):
            raise HiveConfigurationError("HIVE_AUTH must be NONE or NOSASL")
        if min(self.connect_timeout, self.query_timeout, self.max_rows) <= 0:
            raise HiveConfigurationError("Hive timeouts and max_rows must be positive")
        if self.cache_entries < 0:
            raise HiveConfigurationError("HIVE_CACHE_ENTRIES must be nonnegative")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> HiveConfig:
        env = os.environ if env is None else env

        def number(name: str, default: int) -> int:
            try:
                return int(env.get(name, str(default)))
            except ValueError as exc:
                raise HiveConfigurationError(f"{name} must be an integer") from exc

        return cls(
            host=env.get("HIVE_HOST", "").strip(),
            port=number("HIVE_PORT", 10000),
            username=env.get("HIVE_USERNAME", getpass.getuser()).strip(),
            database=env.get("HIVE_DATABASE", "default").strip(),
            auth=env.get("HIVE_AUTH", "NONE").strip().upper(),
            connect_timeout=number("HIVE_CONNECT_TIMEOUT", 10),
            query_timeout=number("HIVE_QUERY_TIMEOUT", 60),
            max_rows=number("HIVE_MAX_ROWS", 10000),
            cache_entries=number("HIVE_CACHE_ENTRIES", 32),
        )
