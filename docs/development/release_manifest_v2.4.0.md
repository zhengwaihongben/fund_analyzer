# V2.4.0 — release manifest

Single traceable record for the V2.4 release: what was tagged, what was tested,
where the artefact came from, and what is knowingly not covered.

## 1. Source identity

| Item | Value |
| --- | --- |
| Tag | `v2.4.0` (annotated) |
| Source of truth | `git rev-list -n1 'v2.4.0^{commit}'` — the peeled commit is the one that contains this file. The tag object and commit hash are verifiable with git; a ZIP cannot carry `.git`, so this link is recorded here instead of inside the archive. |
| Commit chain before the tag | `9579805` (V2.4 stages 0/A/B/C/D/E accepted) → `fa8c8b5` (matrix, V2.3.2 Stage E record, overview, backlog) → `9eb9f32` (coverage regenerated on the frozen tree) → `d3bf1d7` (baseline fixtures force-added, `data/cache/.gitkeep` negation) → the documentation-sync commit that contains this manifest |
| Working tree at build | clean (`git status --porcelain` empty) |

The release artefact is built from `git archive v2.4.0` inside a **clean
checkout**, never from a live working directory.

## 2. Release artefact

| Item | Value |
| --- | --- |
| File | `dist/fund_analyzer_review.zip` |
| SHA-256 | recorded in `dist/SHA256SUMS` **and in the delivery message** — a ZIP cannot contain its own digest, and this manifest is inside the ZIP |
| Integrity | `python -c "import zipfile; print(zipfile.ZipFile(path).testzip())"` → `None` |
| Contents | `fa/`, `tests/`, `scripts/`, `docs/`, `config/`, launchers, README, requirements |
| Excluded by design | runtime state: `data/database/*.db`, `data/cache/*`, `output/`, `__pycache__/`, `*.log`, `dist/` |
| ZIP/tag comparison | every file in the archive is byte-identical to the tagged tree; the only tagged files not in the archive are the five 2-byte CRLF placeholders `data/{cache,database,exports/backtests,exports/estimations,exports/holdings}/.gitkeep`, because the packager treats `data/` as runtime |

## 3. Verification environment and results

| Item | Value |
| --- | --- |
| OS | Windows 10 Home 25H2, build 26200.9974 |
| Python | 3.10.9 (`C:\Users\user\AppData\Local\Programs\Python\Python310\python.exe`) |
| Tk | 8.6.12 |
| Full suite | `python -m unittest discover -s tests -t .` → `Ran 1131 tests in 130.230s` — `OK` |
| Compile | `python -m compileall -q fa tests scripts fund_analyzer.py fund_analyzer_gui.py` → exit 0 |
| Whitespace | `git add -N` for new files, then `git diff --check` → exit 0 |
| Coverage | `python scripts/coverage_core.py --report docs/development/stageE_coverage.md` → **704/708 = 99.4 %**, 10/10 gate modules ≥ 90 %, generated `2026-11-21T11:13:29+00:00` on the frozen tree |
| V2.3.2 Stage E matrix | BT-01…BT-14, 423 tests `OK` (`docs/development/stageE_bt_matrix.md`) |
| GUI acceptance | `docs/development/v24_evidence/E-01.md` … `E-13.md` (E-02 median startup **1.547 s**, close during a run 0.203 s, no orphan process) |

## 4. Acceptance summary

| Scope | Status |
| --- | --- |
| V2.3.2 backtesting (Stages A–E) | **Accepted** — Stage E accepted 2026-11-21 (`docs/technical_specification/V2/10_v232_backtest_engine.md` §19.7) |
| V2.4 GUI integration (Stages 0, A–E) | **Accepted 2026-11-21** — matrix 79 rows: 78 `pass`, 1 waived (D-10) |
| D-10 (double-click an exported report) | **Waived** (lead-approved 2026-11-21): this VM has no `.md` file association, so the OS cannot open the file by double-click. The same flow (history list → detail → export) has real GUI evidence in E-01/E-10. |
| Non-blocking ideas | `docs/development/backlog.md`; they are not part of any accepted item and do not block the V2.4 close |

## 5. Known limitations carried into the release

- D-10 waiver above.
- The archive omits the five `data/*/.gitkeep` placeholders (see §2).
- Coverage leaves four documented defensive lines uncovered (`docs/development/stageE_coverage.md`).
- FX has no version ledger (Q7 option (a)): an in-place FX correction becomes
  visible to earlier instants; this is documented and pinned by a test.
- Review of this release should use `dist/fund_analyzer_review.zip`, not a copy of
  the working directory (a full checkout also contains `__pycache__`, logs and
  local databases).

## 6. Reproduction

```powershell
git rev-list -n1 'v2.4.0^{commit}'                 # the commit this manifest ships from
git archive v2.4.0 -o dist/v2.4.0.tar              # or the packaging script
python -m unittest discover -s tests -t .
python scripts/coverage_core.py --report docs/development/stageE_coverage.md
python -c "import zipfile,hashlib,sys; p='dist/fund_analyzer_review.zip'; print(zipfile.ZipFile(p).testzip()); print(hashlib.sha256(open(p,'rb').read()).hexdigest())"
```
