# -*- coding: utf-8 -*-
"""Unified logging setup: one console format, one rotating file, one getter.

Before Phase 3 the CLI configured ``logging.basicConfig`` with no file
handler, and the GUI reconfigured the root logger ad hoc. Now every entry
point calls :func:`setup_logging` once and every module obtains its logger
through :func:`get_logger`, so log lines are traceable to their module.
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fa.config import LOG_FILE

LOG_FORMAT = "%(asctime)s  %(levelname)-7s [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(verbose: bool = False, log_file: str | os.PathLike | None = None,
                  force: bool = False) -> None:
    """Configure the root logger (console + rotating file).

    Idempotent: repeated calls are no-ops unless ``force`` is set, so the GUI
    can call it again after attaching its queue handler if it wants to.
    """
    global _configured
    if _configured and not force:
        return
    _configured = True

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    console = logging.StreamHandler(stream=sys.stderr)
    console.setFormatter(formatter)
    root.addHandler(console)

    target = LOG_FILE if log_file is None else log_file
    if target:
        try:
            path = Path(target)
            path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError as exc:
            log = logging.getLogger("fa.logging")
            log.debug("無法建立日誌文件 %s: %s", target, exc)


def get_logger(name: str) -> logging.Logger:
    """Module logger. Handlers are owned by :func:`setup_logging`."""
    return logging.getLogger(name)
