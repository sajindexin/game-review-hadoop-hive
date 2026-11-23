from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from pathlib import Path

LOG_FILE = "collection.log"


@contextmanager
def collection_logging(path: Path, *, as_json: bool = False, quiet: bool = False):
    """Send crawler logs to an UTF-8 file and one terminal stream for this run."""
    logger = logging.getLogger("steam_crawler")
    old_level, old_propagate = logger.level, logger.propagate
    http_loggers = [logging.getLogger(name) for name in ("httpx", "httpcore")]
    old_http_levels = [backend.level for backend in http_loggers]
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    disk = logging.FileHandler(path, mode="a", encoding="utf-8")
    console = logging.StreamHandler(sys.stderr if as_json else sys.stdout)
    console.setLevel(logging.WARNING if quiet else logging.INFO)
    for handler in (disk, console):
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    # Request URLs can contain Steam API keys or proxy credentials.
    for backend in http_loggers:
        backend.setLevel(logging.WARNING)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        yield
    finally:
        for handler in (console, disk):
            logger.removeHandler(handler)
            handler.close()
        logger.setLevel(old_level)
        logger.propagate = old_propagate
        for backend, level in zip(http_loggers, old_http_levels, strict=True):
            backend.setLevel(level)
