# Backlog — non-blocking review notes (V2.4 hand-off)

Recorded 2026-11-21 while closing V2.4. Nothing here blocks acceptance; each item
is a known limitation or a deliberate scope cut, kept so the next developer sees
it instead of rediscovering it.

## Environment limits

- **D-10** — opening an exported Markdown report by double-click could not be
  verified: this VM has no `.md` file association. Export itself is proven
  (`v24_evidence/E-10.md`: 3,381 B backtest + 3,140 B estimate report).

## Stage B — estimation GUI

- History ordering and the 1…200 limit are owned by the service
  (`list_estimates`), not the widget; the GUI does not re-sort.
- Stored look-ahead is rendered from the persisted boolean when the record has
  no `look_ahead_status` column value (four-state text only for new runs).
- The `estimate=` handover record is intentionally never marked persisted.

## Stage C — backtest GUI

- The canvas chart scales total and benchmark independently (two normalised
  series), so the two lines are comparable in shape but not in absolute level.
- No intermediate progress text during a run — only Running → Completed/Failed.
- `tests/test_gui_backtest_view.py` keeps a `if __name__ == "__main__"` block
  in the middle of the file (harmless, slightly odd).

## Stage D — reports & history

- The report centre renders Markdown only; HTML reports stay a CLI concern.
- The CLI `list` keeps ascending `run_id` order (D-02 contract) while the GUI
  history uses `created_at DESC`.
- A reused/historical run shows 「成本來源未持久化」 because the cost-source
  mode is request metadata and is never stored.

## Stage E — acceptance

- Coverage is line coverage measured by the stdlib tracer, not branch coverage;
  GUI tests are excluded from the measurement (Tk teardown aborts the traced
  interpreter).
- `fa/backtesting/{engine,data_access}.py` and `fa/data/price_pipeline.py` sit
  just under 90 % but are in the informational EXTRA set, not the CORE gate.
