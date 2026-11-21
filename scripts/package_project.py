# -*- coding: utf-8 -*-
"""Build one of the two project archives.

* ``--mode full``   — 完整專案包, for backup, hand-over or deployment. Contains
  the source, tests, docs, scripts, the data skeleton and the generated output;
  excludes caches, scratch directories, the local SQLite database and logs.
* ``--mode review`` — 程式碼審查包. Contains only what a reviewer reads:
  ``fa/``, ``tests/``, ``docs/``, ``scripts/``, ``pytest.ini``, ``README.md``
  and ``.gitignore``.

Both archives are written to ``dist/`` (git-ignored) and are reproducible: the
file order is sorted and every entry carries the same fixed timestamp.

Usage::

    python scripts/package_project.py --mode review
    python scripts/package_project.py --mode full
"""

from __future__ import annotations

import argparse
import os
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = PROJECT_ROOT / "dist"

# Never packed in either mode.
EXCLUDED_DIRS = {".git", "__pycache__", ".pytest_cache", ".vscode", ".idea",
                 "_tmp", "_pytmp", "dist"}
EXCLUDED_DIR_PATTERNS = ("pytest-cache-files-*",)
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".log", ".zip", ".tmp")
# Runtime data: the live database must never leave the machine.
EXCLUDED_DB_SUFFIXES = (".db", ".db-wal", ".db-shm")
# …except the frozen baseline, which is a test fixture and must stay.
BASELINE_KEEP = "docs/development/v2.2.3_baseline/database_backup.db"

REVIEW_ENTRIES = ("fa", "tests", "docs", "scripts", "config", "pytest.ini", "README.md",
                  ".gitignore", "fund_analyzer.py", "fund_analyzer_gui.py",
                  "requirements.txt", "yf_helper.py", "install.bat", "install.sh",
                  "launch.vbs", "LICENSE")

FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _is_excluded(rel: Path, *, review: bool) -> bool:
    parts = rel.parts
    if any(part in EXCLUDED_DIRS for part in parts[:-1]):
        return True
    if any(part.startswith("pytest-cache-files-") for part in parts[:-1]):
        return True
    name = rel.name
    if name.endswith(EXCLUDED_SUFFIXES):
        return True
    if name.endswith(EXCLUDED_DB_SUFFIXES) and rel.as_posix() != BASELINE_KEEP:
        return True
    if review:
        return parts[0] not in REVIEW_ENTRIES
    return False


def _collect(review: bool) -> list[Path]:
    files: list[Path] = []
    for base, dirs, names in os.walk(PROJECT_ROOT):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS)
        base_path = Path(base)
        for name in sorted(names):
            path = base_path / name
            rel = path.relative_to(PROJECT_ROOT)
            if not _is_excluded(rel, review=review):
                files.append(rel)
    return sorted(files)


def _write_zip(files: list[Path], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for rel in files:
            info = zipfile.ZipInfo(rel.as_posix(), date_time=FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, (PROJECT_ROOT / rel).read_bytes())


def _audit(files: list[Path], review: bool) -> list[str]:
    """Return the problems the reviewer's cleanliness rules would flag."""
    problems: list[str] = []
    for rel in files:
        text = rel.as_posix()
        if "__pycache__" in text or text.endswith((".pyc", ".log", ".zip")):
            problems.append(f"cache/log: {text}")
        if text.endswith(EXCLUDED_DB_SUFFIXES) and text != BASELINE_KEEP:
            problems.append(f"database: {text}")
        if text.startswith(("_tmp/", "_pytmp/")):
            problems.append(f"scratch: {text}")
    if review:
        top = {rel.parts[0] for rel in files}
        unexpected = top - set(REVIEW_ENTRIES)
        problems.extend(f"unexpected top-level entry: {name}" for name in sorted(unexpected))
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("full", "review"), required=True)
    args = parser.parse_args()

    review = args.mode == "review"
    files = _collect(review=review)
    target = DIST_DIR / f"fund_analyzer_{args.mode}.zip"
    if target.exists():
        target.unlink()
    _write_zip(files, target)

    problems = _audit(files, review)
    print(f"模式: {args.mode}")
    print(f"檔案數: {len(files)}")
    print(f"輸出: {target.relative_to(PROJECT_ROOT)} ({target.stat().st_size:,} bytes)")
    print(f"頂層項目: {', '.join(sorted({rel.parts[0] for rel in files}))}")
    if problems:
        print(f"警告 {len(problems)} 項:")
        for problem in problems[:20]:
            print(f"  {problem}")
        return 1
    print("清潔度檢查: 通過（無快取、無暫存目錄、無本機資料庫、無 log）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
