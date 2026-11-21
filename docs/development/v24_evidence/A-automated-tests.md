# Stage A — automated test evidence

Actual commands and actual result lines from the Stage A delivery, recorded per
the acceptance matrix evidence rules (method, command, result).  Nothing below
is an "expected" output; each line was copied from a real run.

## Environment

- Python: 3.10.9 (`C:\Users\user\AppData\Local\Programs\Python\Python310\python.exe`)
- Platform: Windows (Asia/Macau locale), Tk 8.6 available
- Framework: stdlib `unittest`
- Working directory: `C:\Users\user\Desktop\Finance\codes\alipay_funds\fund_analyzer`

## A-01 — main window opens and closes

```powershell
python -m unittest tests.test_gui_lifecycle.MainWindowStageATests.test_main_window_opens_and_closes
```

```text
Ran 1 test in 2.636s
OK
```

## A-02 — four notebook areas

```powershell
python -m unittest tests.test_gui_lifecycle.MainWindowStageATests.test_notebook_has_four_areas
```

```text
Ran 1 test in 2.730s
OK
```

## A-03 — tab switching

```powershell
python -m unittest tests.test_gui_lifecycle.MainWindowStageATests.test_tabs_switch
```

```text
Ran 1 test in 2.813s
OK
```

## A-04 — worker event contract and close lifecycle

```powershell
python -m unittest tests.test_gui_worker
```

```text
Ran 15 tests in 12.623s
OK
```

(The same module also exercises the late-message drop after close, the
single-terminal-result rule, the refusal of a second submit, and the two
`submit()` guard tests.)

## A-05 — registered child windows close with the main window

```powershell
python -m unittest tests.test_gui_lifecycle.WindowRegistryTests
```

```text
Ran 4 tests in 0.000s
OK
```

`test_shutdown_closes_registered_child_windows` in `MainWindowStageATests`
covers the main-window path (part of the A-01/A-04 runs above).

## A-06 — worker holds no widget reference

```powershell
python -m unittest tests.test_gui_worker.WorkerThreadIsolationTests tests.test_gui_lifecycle.LegacyWorkerIsolationTests tests.test_gui_lifecycle.RegistryBudgetTests
```

```text
Ran 10 tests in 7.717s
OK
```

These inspect the live `threading.Thread` objects (`_target`, `__self__`,
`__closure__`, `_args`) and the `ProgressReporter` slots — see `A-06.md`.

## A-07 — post-market scheduling on a fake scheduler

```powershell
python -m unittest tests.test_gui_lifecycle.PostMarketSchedulerTests
```

```text
Ran 3 tests in 0.842s
OK
```

## A-08 — shared formatting

```powershell
python -m unittest tests.test_gui_formatting
```

```text
Ran 23 tests in 0.001s
OK
```

## A-09 — existing analysis GUI regression

```powershell
python -m unittest tests.test_gui
```

```text
Ran 11 tests in 4.164s
OK
```

## Full suite, compile and whitespace

```powershell
python -m unittest discover -s tests -t .
python -m compileall -q fa tests scripts fund_analyzer.py fund_analyzer_gui.py
git diff --check
```

```text
Ran 901 tests in 47.690s
OK
compileall exit code: 0
git diff --check exit code: 0
```

## A-10

Not automated.  Procedure and required evidence are in `A-10.md`; status stays
`pending` until the lead runs it on the Windows desktop.
