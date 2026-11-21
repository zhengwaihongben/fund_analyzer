"""Background work with one terminal result per task.

Every Tk update stays on the main thread.  A worker thread only runs plain
Python and writes messages into a queue; the owner polls that queue with
``after()`` and applies the messages.  This is the contract the V2.4 spec
§4.1/§4.3 calls ``WorkerBridge``.

Thread isolation (spec T-9)
---------------------------
The worker thread must not be able to reach a Tk widget — not even indirectly
through a bound method, a closure or a callback:

* the thread target is the module-level :func:`_execute_worker`, never a bound
  method of ``WorkerBridge``;
* the bridge keeps its widget reference for ``after()``/``after_cancel()`` on
  the **main thread only** and never passes it to the worker;
* progress is reported through :class:`ProgressReporter`, which holds nothing
  but the queue and the closed event;
* the work callable and every argument it receives must be plain data or a
  module-level function — ``submit()`` enforces the obvious violations (a bound
  method, or a closure that captures a widget/bridge) and the Stage A tests
  assert the rest by inspecting the live thread.

Event ordering: zero or more ``progress`` events, then exactly one ``result``
(success or failure).  A task never emits two terminal results, and a
``progress`` message never replaces the terminal result.
"""
from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

import tkinter as tk

log = logging.getLogger(__name__)

__all__ = [
    "PROGRESS",
    "RESULT",
    "ProgressReporter",
    "WorkerBridge",
    "WorkerEvent",
]

PROGRESS = "progress"
RESULT = "result"

#: T-7: a close never waits longer than this for a Python thread.
MAX_CLOSE_SECONDS = 2.0


def _bounded_timeout(timeout: float, *, limit: float = MAX_CLOSE_SECONDS) -> float:
    """Clamp a caller-supplied close timeout into ``[0, limit]``."""
    try:
        value = float(timeout)
    except (TypeError, ValueError):
        return limit
    if value != value:  # NaN
        return limit
    return min(max(value, 0.0), limit)


@dataclass(frozen=True)
class WorkerEvent:
    """One message from a worker thread to the Tk main thread."""

    kind: str
    payload: Any = field(default=None)


def _looks_like_gui(value: Any) -> bool:
    """True for a Tk widget/bridge or anything with Tk's scheduling interface.

    ``tk.Misc`` covers widgets and toplevels; the ``after``/``after_cancel``
    pair catches test doubles and other display handles that would still let a
    worker reschedule work on the UI thread.
    """
    if isinstance(value, (tk.Misc, WorkerBridge)):
        return True
    return hasattr(value, "after") and hasattr(value, "after_cancel")


def _validate_work(work: Callable[..., Any]) -> None:
    """Reject callables that could hand a widget to a worker thread (T-9).

    A bound method carries its instance through ``__self__``; a closure can
    capture a widget or the bridge itself.  Either would let the background
    thread reach Tk objects even though the thread target is module level — the
    exact defect the Stage A review found.  Plain values remain allowed.
    """
    owner = getattr(work, "__self__", None)
    if owner is not None:
        raise TypeError(
            "submit() 不接受 bound method（會把 GUI 物件帶進背景執行緒）；"
            "請改傳模組層級函式，並以參數傳入純資料"
        )
    cells = getattr(work, "__closure__", None)
    for cell in cells or ():
        try:
            value = cell.cell_contents
        except ValueError:  # the cell is empty
            continue
        if _looks_like_gui(value):
            raise TypeError(
                "submit() 不接受捕獲 GUI 物件（widget 或 WorkerBridge）的 closure；"
                "請改傳模組層級函式，並以參數傳入純資料"
            )


class ProgressReporter:
    """The only channel a worker may use to report progress.

    It holds the queue and the closed event and nothing else — no bridge, no
    widget, no window.  A reporter is created per task and passed to the work
    callable as its first argument.
    """

    __slots__ = ("_queue", "_closed_event")

    def __init__(
        self,
        event_queue: "queue.Queue[WorkerEvent]",
        closed_event: threading.Event,
    ):
        self._queue = event_queue
        self._closed_event = closed_event

    def report(self, message: Any) -> None:
        if self._closed_event.is_set():
            return
        self._queue.put(WorkerEvent(kind=PROGRESS, payload=message))


def _execute_worker(
    work: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    event_queue: "queue.Queue[WorkerEvent]",
    closed_event: threading.Event,
    reporter: ProgressReporter,
) -> None:
    """Thread entry point.  Module level so the thread owns no GUI object."""
    try:
        result = work(reporter, *args, **kwargs)
    except BaseException as exc:  # noqa: BLE001 - always report a terminal
        log.exception("背景任務失敗")
        if not closed_event.is_set():
            event_queue.put(WorkerEvent(kind=RESULT, payload=(False, exc)))
    else:
        if not closed_event.is_set():
            event_queue.put(WorkerEvent(kind=RESULT, payload=(True, result)))


class WorkerBridge:
    """Run one callable on a background thread and marshal its events back.

    Parameters
    ----------
    widget:
        Tk widget used for ``after``/``after_cancel``.  Kept on the main
        thread only; it is never handed to the worker.
    interval_ms:
        Polling interval for the main-thread queue drain.
    """

    def __init__(self, widget: tk.Misc, *, interval_ms: int = 50):
        self._widget = widget
        self._interval_ms = int(interval_ms)
        self._queue: "queue.Queue[WorkerEvent]" = queue.Queue()
        self._closed_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._poll_id: str | None = None
        self._closed = False
        self._on_progress: Callable[[Any], None] | None = None
        self._on_result: Callable[[bool, Any], None] | None = None
        self._schedule()

    # -- lifecycle -------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def thread(self) -> threading.Thread | None:
        return self._thread

    def _schedule(self) -> None:
        if self._closed:
            return
        try:
            self._poll_id = self._widget.after(self._interval_ms, self._poll)
        except tk.TclError:  # the widget is already gone
            self._poll_id = None
            self._closed = True

    def submit(
        self,
        work: Callable[..., Any],
        *args: Any,
        on_progress: Callable[[Any], None] | None = None,
        on_result: Callable[[bool, Any], None] | None = None,
        **kwargs: Any,
    ) -> None:
        """Run ``work(reporter, *args, **kwargs)`` on a worker thread.

        ``work`` must be a module-level function (or another callable that
        captures no GUI object); ``args``/``kwargs`` must be plain data.  Its
        first parameter receives a :class:`ProgressReporter`.  The thread
        target is a module-level function, so the running thread holds no
        reference to this bridge or its widget.
        """
        if self._closed:
            raise RuntimeError("WorkerBridge 已關閉，不能再提交任務")
        if self.is_running:
            raise RuntimeError("WorkerBridge 已有任務在執行中")
        _validate_work(work)
        self._on_progress = on_progress
        self._on_result = on_result
        self._closed_event = threading.Event()
        reporter = ProgressReporter(self._queue, self._closed_event)
        self._thread = threading.Thread(
            target=_execute_worker,
            args=(work, args, kwargs, self._queue, self._closed_event, reporter),
            daemon=True,
            name="gui-worker",
        )
        self._thread.start()

    # -- main-thread side ------------------------------------------------
    def _poll(self) -> None:
        self._poll_id = None
        if self._closed:
            return
        while True:
            try:
                event = self._queue.get_nowait()
            except queue.Empty:
                break
            if event.kind == PROGRESS:
                if self._on_progress is not None:
                    self._on_progress(event.payload)
            elif event.kind == RESULT:
                ok, payload = event.payload
                self._on_progress = None
                self._thread = None
                if self._on_result is not None:
                    callback, self._on_result = self._on_result, None
                    callback(ok, payload)
        self._schedule()

    def close(self, *, timeout: float = MAX_CLOSE_SECONDS) -> None:
        """Stop polling, drop late messages, and wait at most two seconds.

        A Python thread cannot be force-killed; if the work does not support
        cooperative cancellation it may keep running until its current call
        returns.  Nothing it sends after close reaches a widget.  The wait is
        always clamped to :data:`MAX_CLOSE_SECONDS`, whatever the caller asks
        for (spec T-7).
        """
        wait = _bounded_timeout(timeout)
        self._closed = True
        self._closed_event.set()
        if self._poll_id is not None:
            try:
                self._widget.after_cancel(self._poll_id)
            except Exception:  # noqa: BLE001 - closing must not raise
                log.debug("取消 WorkerBridge 輪詢失敗", exc_info=True)
            self._poll_id = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=wait)
        self._on_progress = None
        self._on_result = None
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
