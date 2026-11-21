# -*- coding: utf-8 -*-
"""Stage A: WorkerBridge event contract and thread isolation (A-04, A-06)."""
from __future__ import annotations

import gc
import threading
import time
import unittest

from fa.gui import worker as worker_module
from fa.gui.worker import (
    PROGRESS,
    RESULT,
    ProgressReporter,
    WorkerBridge,
    WorkerEvent,
)


class FakeWidget:
    """Records after() calls without needing a Tk display."""

    def __init__(self):
        self.scheduled = []
        self.cancelled = []
        self._next = 1

    def after(self, ms, func=None, *args):
        self.scheduled.append((ms, func))
        self._next += 1
        return f"after-{self._next}"

    def after_cancel(self, identifier):
        self.cancelled.append(identifier)


def _wait_for_result(bridge, recorder, timeout=5.0):
    deadline = time.time() + timeout
    while not recorder["done"] and time.time() < deadline:
        bridge._poll()
        time.sleep(0.01)
    bridge._poll()


def _blocking_work(reporter, release):
    """Module-level so the thread can prove it captured no GUI object."""
    release.wait(5.0)
    return "released"


def _return_payload(reporter):
    return "payload"


def _raise_boom(reporter):
    raise ValueError("boom")


class WorkerBridgeTests(unittest.TestCase):
    def setUp(self):
        self.widget = FakeWidget()
        self.bridge = WorkerBridge(self.widget, interval_ms=10)
        self.recorder = {"progress": [], "result": [], "done": False}

    def tearDown(self):
        self.bridge.close(timeout=2.0)
        # Keep Tk-object collection on the main thread; a worker thread must
        # never finalise a Tcl async handler (Tcl_AsyncDelete is fatal).
        gc.collect()

    def _callbacks(self):
        def on_progress(payload):
            self.recorder["progress"].append(payload)

        def on_result(ok, payload):
            self.recorder["result"].append((ok, payload))
            self.recorder["done"] = True

        return on_progress, on_result

    def test_progress_events_then_exactly_one_terminal_result(self):
        def work(reporter, message, factor):
            reporter.report(message + "-1")
            reporter.report(message + "-2")
            return factor * 2

        on_progress, on_result = self._callbacks()
        self.bridge.submit(work, "step", 21, on_progress=on_progress,
                           on_result=on_result)
        _wait_for_result(self.bridge, self.recorder)

        self.assertEqual(self.recorder["progress"], ["step-1", "step-2"])
        self.assertEqual(len(self.recorder["result"]), 1)
        self.assertEqual(self.recorder["result"][0], (True, 42))
        self.assertFalse(self.bridge.is_running)

    def test_a_failure_still_produces_a_terminal_result(self):
        on_progress, on_result = self._callbacks()
        self.bridge.submit(_raise_boom, on_progress=on_progress,
                           on_result=on_result)
        _wait_for_result(self.bridge, self.recorder)

        self.assertEqual(len(self.recorder["result"]), 1)
        ok, payload = self.recorder["result"][0]
        self.assertFalse(ok)
        self.assertIsInstance(payload, ValueError)

    def test_callbacks_run_on_the_polling_thread(self):
        seen = []

        def on_result(ok, payload):
            seen.append(threading.current_thread().ident)
            self.recorder["done"] = True

        self.bridge.submit(_return_payload, on_result=on_result)
        _wait_for_result(self.bridge, self.recorder)

        self.assertEqual(seen, [threading.current_thread().ident])

    def test_worker_inputs_are_plain_data(self):
        received = {}

        def work(reporter, fund_code, options, numbers):
            received["fund_code"] = fund_code
            received["options"] = options
            received["numbers"] = numbers
            return "ok"

        self.bridge.submit(work, "000369", {"use_cache": False}, [1, 2, 3])
        _wait_for_result(self.bridge, self.recorder)

        self.assertEqual(received["fund_code"], "000369")
        self.assertEqual(received["options"], {"use_cache": False})
        self.assertEqual(received["numbers"], [1, 2, 3])

    def test_second_submit_is_refused_while_running(self):
        release = threading.Event()

        def slow(reporter):
            release.wait(2.0)
            return "done"

        self.bridge.submit(slow)
        with self.assertRaises(RuntimeError):
            self.bridge.submit(_return_payload)
        release.set()
        _wait_for_result(self.bridge, self.recorder)

    def test_late_message_after_close_is_dropped(self):
        started = threading.Event()
        release = threading.Event()

        def slow(reporter):
            started.set()
            release.wait(2.0)
            return "late"

        self.bridge.submit(slow, on_result=lambda ok, p: self.recorder.update(done=True))
        self.assertTrue(started.wait(1.0))
        self.bridge.close(timeout=0.0)
        release.set()
        time.sleep(0.1)

        self.assertTrue(self.bridge.closed)
        self.assertEqual(self.bridge._queue.qsize(), 0)
        self.assertFalse(self.recorder.get("done", False))

    def test_close_cancels_the_poll_timer(self):
        self.bridge.close()
        self.assertTrue(self.widget.cancelled)

    def test_submit_after_close_is_refused(self):
        self.bridge.close()
        with self.assertRaises(RuntimeError):
            self.bridge.submit(_return_payload)


class WorkerThreadIsolationTests(unittest.TestCase):
    """A-06: the running thread holds no bridge, widget or bound method."""

    def setUp(self):
        self.widget = FakeWidget()
        self.bridge = WorkerBridge(self.widget, interval_ms=10)
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        self.bridge.close(timeout=2.0)
        gc.collect()

    def _start(self):
        self.bridge.submit(_blocking_work, self.release)
        thread = self.bridge.thread
        self.assertIsNotNone(thread)
        return thread

    def test_thread_target_is_module_level_and_holds_no_widget(self):
        thread = self._start()
        target = thread._target
        self.assertIs(target, worker_module._execute_worker)
        self.assertIsNone(getattr(target, "__self__", None))
        self.assertIsNone(getattr(target, "__closure__", None))
        for argument in thread._args:
            self.assertIsNot(argument, self.widget)
            self.assertIsNot(argument, self.bridge)
            self.assertNotIsInstance(argument, FakeWidget)
            self.assertNotIsInstance(argument, WorkerBridge)

    def test_worker_callable_is_not_a_bound_method_and_captures_no_window(self):
        thread = self._start()
        work = thread._args[0]
        self.assertIs(work, _blocking_work)
        self.assertIsNone(getattr(work, "__self__", None))
        self.assertIsNone(getattr(work, "__closure__", None))
        self.assertEqual(thread._args[1], (self.release,))
        self.assertEqual(thread._args[2], {})

    def test_progress_reporter_does_not_retain_widget(self):
        thread = self._start()
        reporter = thread._args[5]
        self.assertIsInstance(reporter, ProgressReporter)
        self.assertEqual(set(ProgressReporter.__slots__), {"_queue", "_closed_event"})
        self.assertIs(reporter._queue, self.bridge._queue)
        self.assertIs(reporter._closed_event, self.bridge._closed_event)
        self.assertFalse(hasattr(reporter, "__dict__"))
        self.assertIsNone(getattr(reporter, "_widget", None))
        self.assertIsNone(getattr(reporter, "_bridge", None))

    def test_a_bound_method_is_refused_before_a_thread_starts(self):
        with self.assertRaises(TypeError) as caught:
            self.bridge.submit(self.widget.after, 10)
        self.assertIn("bound method", str(caught.exception))
        self.assertIsNone(self.bridge.thread)

    def test_a_closure_over_the_widget_is_refused(self):
        widget = self.widget

        def work(reporter):
            return widget

        with self.assertRaises(TypeError) as caught:
            self.bridge.submit(work)
        self.assertIn("closure", str(caught.exception))
        self.assertIsNone(self.bridge.thread)

    def test_close_timeout_is_capped_at_two_seconds(self):
        self._start()
        started = time.monotonic()
        self.bridge.close(timeout=30.0)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, worker_module.MAX_CLOSE_SECONDS + 0.5)
        self.assertTrue(self.bridge.closed)
        self.assertEqual(worker_module._bounded_timeout(30.0), 2.0)
        self.assertEqual(worker_module._bounded_timeout(-5.0), 0.0)
        self.assertEqual(worker_module._bounded_timeout(float("nan")), 2.0)


class WorkerEventTests(unittest.TestCase):
    def test_event_kinds(self):
        self.assertEqual((PROGRESS, RESULT), ("progress", "result"))
        event = WorkerEvent(RESULT, (True, 1))
        self.assertEqual(event.payload, (True, 1))


if __name__ == "__main__":
    unittest.main()
