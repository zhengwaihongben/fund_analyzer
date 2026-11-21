# -*- coding: utf-8 -*-
"""Remove files that Python, pytest and this project's test runs generate.

These are the "runtime artefacts" category of the project layout:

* ``__pycache__/`` and ``*.pyc`` — Python bytecode.
* ``.pytest_cache/`` and ``pytest-cache-files-*/`` — pytest cache.
* ``_tmp/`` and ``_pytmp/`` — scratch directories used by the test suite.

Nothing here is versioned, and nothing here is needed to run the project: the
database (``data/database/fund_analyzer.db``) and the frozen V2.2.3 baseline
(``docs/development/v2.2.3_baseline/``) are deliberately *not* touched.

Some directories left behind by an earlier sandboxed test run are ACL-locked
and cannot be deleted without elevation. Those are reported and skipped; they
are covered by ``.gitignore``.

Usage::

    python scripts/clean_runtime_files.py            # dry run, lists targets
    python scripts/clean_runtime_files.py --apply    # delete them
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Directories removed wholesale wherever they appear.
CACHE_DIR_NAMES = {"__pycache__", ".pytest_cache"}
# Directories removed only at the project root.
ROOT_SCRATCH_NAMES = {"_tmp", "_pytmp"}
ROOT_SCRATCH_PATTERNS = ("pytest-cache-files-*",)


def _iter_cache_dirs() -> list[Path]:
    found: list[Path] = []
    for base, dirs, _files in os.walk(PROJECT_ROOT):
        for name in list(dirs):
            if name in CACHE_DIR_NAMES:
                found.append(Path(base) / name)
                dirs.remove(name)          # do not descend into a cache
    return found


def _iter_root_scratch() -> list[Path]:
    found: list[Path] = []
    for entry in PROJECT_ROOT.iterdir():
        if not entry.is_dir():
            continue
        if entry.name in ROOT_SCRATCH_NAMES:
            found.append(entry)
        elif any(entry.match(pattern) for pattern in ROOT_SCRATCH_PATTERNS):
            found.append(entry)
    return found


def _iter_stray_bytecode() -> list[Path]:
    found: list[Path] = []
    for base, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "data", "docs")]
        found.extend(Path(base) / name for name in files if name.endswith((".pyc", ".pyo")))
    return found


def _remove(path: Path) -> str | None:
    """Delete ``path``; return an error message when that is not possible."""
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    except FileNotFoundError:
        return None                      # 已被上層目錄的刪除帶走
    except PermissionError as exc:
        return f"權限不足，保留（{exc.strerror}）"
    except OSError as exc:
        return f"刪除失敗（{exc}）"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="實際刪除；未指定時只列出目標")
    args = parser.parse_args()

    targets = _iter_root_scratch() + _iter_cache_dirs() + _iter_stray_bytecode()
    if not targets:
        print("沒有需要清理的目錄或檔案。")
        return 0

    print(f"{'刪除' if args.apply else '將刪除'} {len(targets)} 項：")
    failures = 0
    for path in sorted(targets):
        rel = path.relative_to(PROJECT_ROOT)
        if not args.apply:
            print(f"  {rel}")
            continue
        error = _remove(path)
        if error:
            failures += 1
            print(f"  [跳過] {rel} — {error}")
        else:
            print(f"  [完成] {rel}")

    if args.apply and failures:
        print(f"\n{failures} 項無法刪除。它們已被 .gitignore 忽略；"
              "要徹底清除請在用管理員身分開啟的 PowerShell 內對這些路徑執行 Remove-Item。")
    if not args.apply:
        print("\n加上 --apply 才會真的刪除。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
