"""Stage E: line coverage for the core financial modules, using only the stdlib.

The project does not depend on the ``coverage`` package (no new dependencies are
allowed), so this script installs a ``sys.settrace`` tracer, runs the whole test
suite in-process, and reports executed / executable statement lines per module.

It is line coverage only, not branch coverage.  A module below 90 % fails the
run (V2.3.2 Stage E gate).

Run with::

    python scripts/coverage_core.py [--threshold 0.90] [--report PATH]
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import io
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REPORT = ROOT / "docs" / "development" / "stageE_coverage.md"

CORE = (
    "fa/analysis/returns/base.py",
    "fa/analysis/returns/equity.py",
    "fa/analysis/returns/etf.py",
    "fa/analysis/returns/bond_etf.py",
    "fa/analysis/returns/cash.py",
    "fa/analysis/returns/fx.py",
    "fa/analysis/valuation/portfolio_valuation.py",
    "fa/analysis/nav_estimator.py",
    "fa/analysis/valuation_dates.py",
    "fa/utils/data_quality.py",
)

#: Additional modules measured for information; the 90 % gate in
#: `06_testing_and_acceptance.md` names only the set in :data:`CORE`.
EXTRA = (
    "fa/backtesting/costs.py",
    "fa/backtesting/engine.py",
    "fa/backtesting/metrics.py",
    "fa/backtesting/runner.py",
    "fa/backtesting/benchmark.py",
    "fa/backtesting/data_access.py",
    "fa/backtesting/models.py",
    "fa/data/price_pipeline.py",
    "fa/data/fx_pipeline.py",
)


def executable_lines(path: Path) -> set[int]:
    """Line numbers of every executable statement in the module.

    Counts module-level statements, class-body statements and function-body
    statements alike: they all run at some point and the tracer is installed
    **before** ``unittest`` discovery, so the import-time ones are observable
    too (discovery imports ``fa`` after ``sys.settrace()``).  Only ``ast.stmt``
    nodes count — a function signature is one statement, its individual
    arguments are not separate executable lines — and docstrings are excluded
    because a bare string is documentation, not work.

    A line with several statements on it is counted once: the tracer reports
    line numbers, not statements.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt) and not _is_docstring(node):
            lines.add(node.lineno)
    return lines


def _is_docstring(node: ast.stmt) -> bool:
    """A bare string expression is documentation, not an executed statement."""
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


class Tracer:
    def __init__(self, targets: dict[str, str]) -> None:
        self.targets = targets
        self.hits: dict[str, set[int]] = {name: set() for name in targets.values()}

    def __call__(self, frame, event, arg):  # noqa: ANN001 - trace protocol
        filename = os.path.normcase(frame.f_code.co_filename)
        key = self.targets.get(filename)
        if key is None:
            return None
        if event == "line":
            self.hits[key].add(frame.f_lineno)
        return self


def run_suite(skip: tuple[str, ...] = ("test_gui",)) -> unittest.result.TestResult:
    """Run the discovered suite, dropping modules listed in ``skip``.

    The Tk tests are excluded from the measurement only: tearing Tk down under
    ``sys.settrace`` aborts the interpreter (``Tcl_AsyncDelete``), which would
    kill the run before the report is written.  They are still part of the
    normal suite, and no measured module lives in the GUI package.
    """
    loader = unittest.TestLoader()
    discovered = loader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    suite = unittest.TestSuite()
    skipped = 0
    for test in _flatten(discovered):
        if any(token in test.id() for token in skip):
            skipped += 1
            continue
        suite.addTest(test)
    if skipped:
        print(f"skipped from coverage run: {skipped} GUI test(s)")
    runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0)
    return runner.run(suite)


def _flatten(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=float, default=0.90)
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument(
        "--skip",
        default="test_gui",
        help="comma-separated substrings of test ids to exclude from the measurement",
    )
    args = parser.parse_args()
    skip = tuple(part.strip() for part in args.skip.split(",") if part.strip())

    targets: dict[str, str] = {}
    executable: dict[str, set[int]] = {}
    for relative in CORE + EXTRA:
        path = (ROOT / relative).resolve()
        if not path.exists():
            print(f"missing core module: {relative}")
            return 1
        targets[os.path.normcase(str(path))] = relative
        executable[relative] = executable_lines(path)

    tracer = Tracer(targets)
    sys.settrace(tracer)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            result = run_suite(skip)
    finally:
        sys.settrace(None)

    rows = []
    extra_rows = []
    failures = []
    missing: dict[str, list[int]] = {}
    for relative in CORE + EXTRA:
        hits = tracer.hits[relative]
        lines = executable[relative]
        executed = len(hits & lines)
        ratio = executed / len(lines) if lines else 1.0
        missing[relative] = sorted(lines - hits)
        row = (relative, len(lines), executed, ratio)
        if relative in EXTRA:
            extra_rows.append(row)
            continue
        rows.append(row)
        if ratio < args.threshold:
            failures.append((relative, ratio))

    total_lines = sum(row[1] for row in rows)
    total_executed = sum(row[2] for row in rows)
    total_ratio = total_executed / total_lines if total_lines else 1.0

    lines = [
        "# Stage E — core module coverage (stdlib tracer)",
        "",
        "Measured by `scripts/coverage_core.py` with `sys.settrace` while running the",
        "whole `unittest` suite in-process, because the project does not depend on the",
        "`coverage` package. **Line coverage only, not branch coverage.**",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- Python: {sys.version.split()[0]}",
        f"- Tests run: {result.testsRun}, failures: {len(result.failures)}, "
        f"errors: {len(result.errors)}",
        f"- GUI tests excluded from the measurement: {', '.join(skip) or 'none'} "
        "(Tk teardown aborts the interpreter under `sys.settrace`)",
        f"- Threshold: {args.threshold:.0%}",
        "",
        "| Module | Executable lines | Executed | Coverage |",
        "| --- | --- | --- | --- |",
    ]
    for relative, total, executed, ratio in rows:
        lines.append(f"| `{relative}` | {total} | {executed} | {ratio:.1%} |")
    lines += [
        "",
        f"- **Total (documented target set): {total_executed}/{total_lines} = {total_ratio:.1%}**",
        "",
    ]
    if failures:
        lines.append("Modules below the threshold: " + ", ".join(
            f"`{name}` ({ratio:.1%})" for name, ratio in failures
        ))
    else:
        lines.append(f"Every core module meets the {args.threshold:.0%} threshold.")

    lines += [
        "",
        "## Missing executable lines",
        "",
        "Line numbers reported by the tracer as executable but never executed.",
        "Use them to aim the new tests; the gate is per module, not aggregate.",
        "",
        "| Module | Missing | Line numbers |",
        "| --- | --- | --- |",
    ]
    for relative in CORE + EXTRA:
        gaps = missing[relative]
        if not gaps:
            continue
        numbers = ", ".join(str(number) for number in gaps)
        lines.append(f"| `{relative}` | {len(gaps)} | {numbers} |")
    if not any(missing[relative] for relative in CORE + EXTRA):
        lines.append("| — | 0 | — |")

    lines += [
        "",
        "## Additional modules (information only, not part of the documented gate)",
        "",
        "| Module | Executable lines | Executed | Coverage |",
        "| --- | --- | --- | --- |",
    ]
    for relative, total, executed, ratio in extra_rows:
        lines.append(f"| `{relative}` | {total} | {executed} | {ratio:.1%} |")
    extra_total = sum(row[1] for row in extra_rows)
    extra_executed = sum(row[2] for row in extra_rows)
    lines += [
        "",
        f"- Extra set: {extra_executed}/{extra_total} = "
        f"{(extra_executed / extra_total if extra_total else 1.0):.1%}",
        "",
    ]

    text = "\n".join(lines)
    if result.errors:
        text += "\n\n## Errors during the coverage run (first three)\n\n"
        for test, trace in result.errors[:3]:
            text += f"- `{test.id()}`: {trace.strip().splitlines()[-1]}\n"
    print(text)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(text, encoding="utf-8", newline="\r\n")
    print(f"written: {args.report}")
    return 0 if not failures and not result.failures and not result.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
