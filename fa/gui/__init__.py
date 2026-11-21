# -*- coding: utf-8 -*-
"""Tkinter front end: the main window and the post-market monitor.

``main`` is exported lazily: ``fa.gui.app`` is imported on the cold-start path,
and an eager ``from fa.gui.launcher import main`` would pull in ``requests``
(via ``fa.utils.net``) before the first window appears (V2.4 E-02a).
"""
from __future__ import annotations

from typing import Any

__all__ = ["main"]


def __getattr__(name: str) -> Any:
    if name == "main":
        from fa.gui.launcher import main

        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(__all__) | set(globals()))
