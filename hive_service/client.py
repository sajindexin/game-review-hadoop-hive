from __future__ import annotations

import logging
import math
from collections import OrderedDict
from copy import deepcopy
from functools import lru_cache
from threading import Lock
from time import monotonic, sleep
from typing import Any

from pyhive import hive
from TCLIService.ttypes import TOperationState
from thrift.Thrift import TException
from thrift.transport import TSocket, TTransport
from thrift_sasl import TSaslClientTransport

from .config import HiveConfig
from .errors import HiveError, HiveQueryError, HiveResultTooLarge, HiveTimeout, HiveUnavailable

logger = logging.getLogger(__name__)


class HiveClient:
    """每次查询独立建立会话，可在线程间共享客户端实例。"""

    def __init__(self, config: HiveConfig):
        self.config = config
        self._cache = OrderedDict()
        self._cache_lock = Lock()

    def query(
        self,
        sql: str,
        params: dict | tuple | list | None = None,
        *,
        max_rows: int | None = None,
        cache_ttl: float = 0,
    ) -> list[dict[str, Any]]:
        """执行模块内定义的只读查询，以列名为键返回原生 Python 值。"""
        limit = self.config.max_rows if max_rows is None else max_rows
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise HiveQueryError("max_rows must be a positive integer")
        if not math.isfinite(cache_ttl) or cache_ttl < 0:
            raise HiveQueryError("cache_ttl must be finite and nonnegative")
        if not sql.strip() or sql.lstrip().split()[0].upper() not in {"SELECT", "WITH", "SHOW", "DESCRIBE", "DESC"}:
            raise HiveQueryError("Expected a read query")

        # 缓存小型维表和聚合结果，调用方显式选择有效期。
        if not cache_ttl or not self.config.cache_entries:
            return self._query(sql, params, limit)
        key = (sql, repr(params), limit, cache_ttl)
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry and entry[0] > monotonic():
                self._cache.move_to_end(key)
                return deepcopy(entry[1])
            self._cache.pop(key, None)

        rows = self._query(sql, params, limit)
        if len(rows) <= 1000:
            with self._cache_lock:
                self._cache[key] = (monotonic() + cache_ttl, deepcopy(rows))
                self._cache.move_to_end(key)
                while len(self._cache) > self.config.cache_entries:
                    self._cache.popitem(last=False)
        return rows

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()

    def _query(self, sql: str, params, limit: int) -> list[dict[str, Any]]:
        config = self.config
        sock = TSocket.TSocket(config.host, config.port)
        sock.setTimeout(config.connect_timeout * 1000)
        if config.auth == "NOSASL":
            transport = TTransport.TBufferedTransport(sock)
        else:
            transport = TSaslClientTransport(
                lambda: hive.get_pure_sasl_client(config.host, "PLAIN", username=config.username, password="x"),
                "PLAIN", sock,
            )

        connection = cursor = None
        transport_failed = False
        try:
            connection = hive.connect(
                thrift_transport=transport, username=config.username, database=config.database,
                configuration={"hive.query.timeout.seconds": str(config.query_timeout)},
            )
            cursor = connection.cursor(arraysize=min(1000, limit + 1))
            deadline = monotonic() + config.query_timeout

            def remaining() -> float:
                seconds = deadline - monotonic()
                if seconds <= 0:
                    raise HiveTimeout("Hive query exceeded its time limit")
                sock.setTimeout(max(1, math.ceil(seconds * 1000)))
                return seconds

            remaining()
            cursor.execute(sql, params, async_=True)
            while True:
                remaining()
                status = cursor.poll(get_progress_update=False)
                state = status.operationState
                if state == TOperationState.FINISHED_STATE:
                    break
                if state == TOperationState.TIMEDOUT_STATE:
                    raise HiveTimeout("Hive query timed out on the server")
                if state not in (TOperationState.INITIALIZED_STATE, TOperationState.RUNNING_STATE,
                                 TOperationState.PENDING_STATE):
                    raise HiveQueryError("Hive query failed") from RuntimeError(status.errorMessage or str(state))
                sleep(min(0.2, remaining()))

            remaining()
            description = cursor.description
            if not description:
                raise HiveQueryError("Query did not return columns")
            columns = [column[0] for column in description]
            if len(set(columns)) != len(columns):
                raise HiveQueryError("Duplicate result column names; use distinct SQL aliases")
            rows = []
            while True:
                remaining()
                batch = cursor.fetchmany(min(1000, limit + 1 - len(rows)))
                if not batch:
                    return rows
                rows.extend(dict(zip(columns, row)) for row in batch)
                if len(rows) > limit:
                    raise HiveResultTooLarge(f"Hive query exceeded {limit} rows")
        except (TTransport.TTransportException, OSError, EOFError) as exc:
            transport_failed = True
            if (isinstance(exc, TimeoutError)
                    or getattr(exc, "type", None) == TTransport.TTransportException.TIMED_OUT
                    or isinstance(getattr(exc, "inner", None), TimeoutError)):
                raise HiveTimeout("Hive connection or transport timed out") from exc
            raise HiveUnavailable("Hive connection unavailable") from exc
        except HiveTimeout:
            if cursor is not None:
                sock.setTimeout(config.connect_timeout * 1000)
                try:
                    cursor.cancel()
                except (TException, OSError, EOFError):
                    transport_failed = True
                except Exception:
                    logger.warning("Failed to cancel Hive query")
            raise
        except HiveError:
            raise
        except Exception as exc:
            if connection is None:
                raise HiveUnavailable("Could not open Hive session") from exc
            raise HiveQueryError("Hive query failed") from exc
        finally:
            # 网络错误后 Thrift 协议流可能已失步，直接关闭底层连接。
            sock.setTimeout(config.connect_timeout * 1000)
            try:
                if not transport_failed:
                    for resource in (cursor, connection):
                        if resource is None:
                            continue
                        try:
                            resource.close()
                        except (TException, OSError, EOFError):
                            logger.warning("Hive transport failed during cleanup")
                            break
                        except Exception:
                            logger.warning("Failed to close Hive resource cleanly")
            finally:
                sock.close()


@lru_cache(maxsize=1)
def get_hive_client() -> HiveClient:
    """按当前进程的环境变量懒加载共享客户端。"""
    return HiveClient(HiveConfig.from_env())
