# Stage A — legacy price backfill evidence

The frozen baseline (`docs/development/v2.2.3_baseline/database_backup.db`) holds the 93 V2.2 rows whose `availability_source` is `unknown` and `available_at` is NULL, all under `price_type='adjusted_close'`. A copy is upgraded to schema 3 and `update_prices()` runs against the live Yahoo provider, so a row can only become PIT-visible if the vendor returned that session in this run (Q1).

Run instant: `2026-11-18T12:35:20.034187+00:00`

| Ticker | Legacy keys | Provider calls | Returned by vendor | Row has availability | Revisions | PIT at run instant | PIT after run | Still unknown | Dates with two keys |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ABBV | 9 | 1 | 9 | 9 | 0 | 9 | 9 | 0 | 0 |
| AMGN | 9 | 1 | 9 | 9 | 0 | 9 | 9 | 0 | 0 |
| AZN | 10 | 1 | 10 | 10 | 1 | 9 | 10 | 0 | 0 |
| JNJ | 9 | 1 | 9 | 9 | 0 | 9 | 9 | 0 | 0 |
| LLY | 9 | 1 | 9 | 9 | 0 | 9 | 9 | 0 | 0 |
| MRK | 9 | 1 | 9 | 9 | 0 | 9 | 9 | 0 | 0 |
| NOVN | 10 | 1 | 10 | 10 | 1 | 9 | 10 | 0 | 0 |
| ROP SW | 10 | 1 | 10 | 10 | 1 | 9 | 10 | 0 | 0 |
| TMO | 9 | 1 | 9 | 9 | 0 | 9 | 9 | 0 | 0 |
| UNH | 9 | 1 | 9 | 9 | 0 | 9 | 9 | 0 | 0 |
| **Total** | **93** | **10** | **93** | **93** | **3** | **90** | **93** | **0** | **0** |

`prices_as_of(run instant)` returned 90 rows across the 10 tickers, and 93 rows immediately afterwards.

Reading the columns: *Returned by vendor* counts legacy keys whose stored row was rewritten by this run (`fetched_at` equals the run instant). *Revisions* counts numbers the vendor changed; a revised row's `available_at` is pushed to the revision instant, which is a fraction of a second later than the run's `now`, so the revision is correctly invisible at `now` and visible in the next query — that is why the two PIT columns differ, and the difference must equal the revision count. *Still unknown* counts legacy keys the vendor did not return; they keep their old `fetched_at` and stay PIT-invisible. *Dates with two keys* must stay at zero — a re-download that invents a second `price_type` for an existing session would split the history in two.

## Working database — disposition after review m08830

The working database was enriched before the type-preservation fix, so it still
carries legacy `adjusted_close` keys next to the canonical rows. The decision from
the review is to **keep** them (deleting or re-keying is a data-migration change
this stage does not take): they remain `unknown`, remain PIT-invisible, and must
not make a session look incomplete.

Read-only reconciliation, run instant `2026-11-18T13:29:13Z`:

| Check | Result |
| --- | ---: |
| `market_prices` rows | 5 107 (unchanged; nothing deleted) |
| `available_at IS NULL` | 73, all `price_type='adjusted_close'`, all `unknown` |
| Sessions with two keys | 93, every one exactly 2 rows |
| Canonical sessions with evidence | ABBV/AMGN/JNJ/LLY/MRK/TMO/UNH 501, AZN 507, NOVN/ROP SW 500 — `unusable=0` for all ten |
| Repeat-run plan | `cache_hit=True`, `fetch_start=None` for 10/10 tickers |
| `prices_as_of()` sources | `inferred` only |

`prices_as_of()` returns 501/506/499 rows against 501/507/500 canonical sessions:
the newest bar of AZN, NOVN and ROP SW is today's running session
(2026-11-18), stamped available at `2026-11-18T16:30Z`, which is still in the
future at the probe instant — the point-in-time query excludes it, as it must.
