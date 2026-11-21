"""Notebook areas and the child-window registry.

The main window owns one :class:`WindowRegistry`; every child window (QDII
estimate, post-market monitor, and later backtesting/history) is opened
through it, so "open the same window twice" lifts the existing one and
shutdown has one place to close them all.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from tkinter import ttk

log = logging.getLogger(__name__)

__all__ = ["AREA_TITLES", "MainNotebook", "WindowRegistry"]


AREA_TITLES = {
    "analysis": "基金分析",
    "estimation": "QDII 估算",
    "backtest": "策略回測",
    "reports": "報告與歷史",
}


class MainNotebook(ttk.Notebook):
    """Notebook with the four V2.4 areas and a small tab helper."""

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self._frames: dict[str, ttk.Frame] = {}

    def add_area(self, key: str, title: str | None = None) -> ttk.Frame:
        frame = ttk.Frame(self)
        self.add(frame, text=title or AREA_TITLES[key])
        self._frames[key] = frame
        return frame

    def frame_for(self, key: str) -> ttk.Frame:
        return self._frames[key]

    def area_keys(self) -> list[str]:
        return list(self._frames)

    def register_window(self, key: str, factory: Callable[[Any], Any]) -> None:
        """Declare how a child window is created (delegates to the registry)."""
        registry = getattr(self, "registry", None)
        if registry is None:
            raise RuntimeError("MainNotebook 尚未綁定 WindowRegistry")
        registry.register(key, factory)


class WindowRegistry:
    """Own child window instances and their open/close lifecycle."""

    def __init__(self, master):
        self._master = master
        self._factories: dict[str, Callable[[Any], Any]] = {}
        self._windows: dict[str, Any] = {}
        self.closed = False

    def register(self, key: str, factory: Callable[[Any], Any]) -> None:
        if not key:
            raise ValueError("視窗 key 不可為空")
        self._factories[key] = factory

    def keys(self) -> list[str]:
        return list(self._factories)

    def get(self, key: str) -> Any:
        return self._windows.get(key)

    def is_open(self, key: str) -> bool:
        window = self._windows.get(key)
        return window is not None and self._exists(window)

    def open(self, key: str, **kwargs: Any) -> Any:
        """Return the live window, or create one through the factory."""
        if self.closed:
            raise RuntimeError("視窗已關閉，不能再開啟子視窗")
        existing = self._windows.get(key)
        if existing is not None:
            if self._exists(existing):
                try:
                    existing.lift()
                    existing.focus_force()
                except Exception:  # noqa: BLE001 - focus is best effort
                    log.debug("提升子視窗失敗: %s", key, exc_info=True)
                return existing
            self._windows.pop(key, None)

        factory = self._factories.get(key)
        if factory is None:
            raise KeyError(f"未註冊的視窗: {key}")
        window = factory(kwargs.pop("parent", self._master), **kwargs)
        self._windows[key] = window
        return window

    def _exists(self, window: Any) -> bool:
        exists = getattr(window, "winfo_exists", None)
        if exists is None:
            return True
        try:
            return bool(exists())
        except Exception:  # noqa: BLE001 - a destroyed Tk object raises
            return False

    def remove(self, key: str) -> None:
        self._windows.pop(key, None)

    def close_all(self, *, timeout: float = 2.0) -> None:
        """Close every child, sharing one wait budget across all of them.

        ``timeout`` is the **total** budget for joining the child windows'
        background workers (clamped to two seconds, spec T-7).  Each window's
        ``close(timeout=remaining)`` gets only the time left before the
        deadline, so N windows cannot each wait two seconds.  Tk ``destroy()``
        and user close callbacks are not forcibly bounded by it.
        """
        self.closed = True
        budget = min(max(float(timeout), 0.0), 2.0)
        deadline = time.monotonic() + budget
        for key, window in list(self._windows.items()):
            closer = getattr(window, "close", None)
            try:
                if closer is not None:
                    remaining = max(0.0, deadline - time.monotonic())
                    try:
                        closer(timeout=remaining)
                    except TypeError:  # a closer that takes no arguments
                        closer()
                elif self._exists(window):
                    window.destroy()
            except Exception:  # noqa: BLE001 - shutdown must finish
                log.debug("關閉子視窗失敗: %s", key, exc_info=True)
        self._windows.clear()
