# -*- coding: utf-8 -*-
"""Stage D — estimation report renderer (D-05)."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fa.application.estimation import EstimationView
from fa.reporting import estimation_report


def _view(**overrides):
    record = {
        "fund_code": "000369",
        "run_id": "run-abc",
        "model_version": "2.2.0",
        "created_at": "2026-11-16T13:05:00",
        "mode": "qdii",
        "base_nav": 2.815,
        "estimated_nav": 2.8205,
        "estimated_return": 0.00195,
        "target_nav_date": "2026-11-16",
        "base_nav_date": "2026-11-13",
        "coverage_ratio": 0.461,
        "uncovered_weight": 0.539,
        "local_contribution": 0.0012,
        "fx_contribution": -0.0004,
        "holdings_quarter": "2026Q3",
        "holdings_scope": "top10",
        "disclosure_date": "2026-10-26",
        "disclosure_date_estimated": True,
        "holdings_source": "eastmoney-page",
        "look_ahead_status": "estimated-disclosure",
        "warnings": ["2026Q3 持倉揭露日為推算值", "未覆蓋權重 53.9% 未計入估算"],
    }
    record.update(overrides.pop("record", {}))
    inputs = {
        "holdings": {
            "quarter": "2026Q3",
            "scope": "top10",
            "disclosure_date": "2026-10-26",
            "disclosure_date_estimated": True,
            "source": "eastmoney-page",
        },
        "assets": [{"ticker": "LLY", "used_price_type": "close+adj_close"}],
    }
    inputs.update(overrides.pop("inputs", {}))
    rows = [
        {"ticker": "LLY", "name": "禮來", "weight": 0.1043,
         "contribution": 0.0013, "status": "ok"},
        {"ticker": "AZN", "name": "阿斯利康", "weight": None,
         "contribution": None, "status": "missing-fx"},
    ]
    rows = overrides.pop("rows", rows)
    return EstimationView(
        record=record, mode="stored", run_id="run-abc", persisted=True,
        asset_rows=rows, asset_source="SQLite 輸入快照",
        warnings=list(record["warnings"]),
        message=estimation_report.NO_ASSET_SOURCE_TEXT,
        inputs=inputs,
    )


class EstimationReportTests(unittest.TestCase):
    def test_d05_nested_inputs_supply_metadata_when_the_record_lacks_it(self):
        """F5: isolate the nested reading path, not the record fallback."""
        import dataclasses

        view = _view()
        record = {key: value for key, value in view.record.items()
                  if key not in {"holdings_quarter", "holdings_scope",
                                 "disclosure_date", "disclosure_date_estimated",
                                 "holdings_source", "look_ahead_status"}}
        isolated = dataclasses.replace(view, record=record)
        text = estimation_report.render(isolated)
        self.assertIn("2026Q3", text)
        self.assertIn("前十大（揭露口徑）", text)
        self.assertIn("2026-10-26", text)
        self.assertIn("eastmoney-page", text)
        self.assertIn("推算", text)

    def test_d05_header_and_nav_sections_are_present(self):
        text = estimation_report.render(_view())
        self.assertIn("# Estimation report — `run-abc`", text)
        self.assertIn("## NAV", text)
        self.assertIn("| Estimated return | +0.19% |", text)
        self.assertIn("| Coverage ratio | 46.10% |", text)
        self.assertIn("| Target NAV date | 2026-11-16 |", text)

    def test_d05_asset_table_never_turns_unknown_into_zero(self):
        text = estimation_report.render(_view())
        self.assertIn("| `LLY` | 禮來 | 10.43% | +0.13% | ok |", text)
        self.assertIn("| `AZN` | 阿斯利康 | — | — | missing-fx |", text)
        self.assertNotIn("| `AZN` | 阿斯利康 | 0.00%", text)

    def test_d05_quality_block_reads_nested_inputs_and_marks_estimates(self):
        text = estimation_report.render(_view())
        self.assertIn("Holdings scope: 前十大（揭露口徑）", text)
        self.assertIn("Disclosure date: 2026-10-26（推算）", text)
        self.assertIn("Price types: close+adj_close", text)

    def test_d05_warnings_are_verbatim(self):
        text = estimation_report.render(_view())
        for warning in ("2026Q3 持倉揭露日為推算值", "未覆蓋權重 53.9% 未計入估算"):
            self.assertIn(f"- {warning}", text)

    def test_d05_missing_assets_use_the_contract_message(self):
        text = estimation_report.render(_view(rows=[]))
        self.assertIn(estimation_report.NO_ASSET_SOURCE_TEXT, text)
        self.assertNotIn("| Ticker | Name | Weight", text)

    def test_d05_missing_values_render_as_a_dash(self):
        view = _view(record={"estimated_nav": None, "estimated_return": None,
                             "coverage_ratio": None})
        text = estimation_report.render(view)
        self.assertIn("| Estimated NAV | — |", text)
        self.assertIn("| Estimated return | — |", text)
        self.assertIn("| Coverage ratio | — |", text)

    def test_d05_attribution_and_provenance(self):
        text = estimation_report.render(_view())
        self.assertIn("| Contribution | +0.12% | -0.04% | — |", text)
        self.assertIn("Asset detail source: SQLite 輸入快照", text)
        self.assertIn("Inputs snapshot present: 是", text)
        self.assertNotIn("Snapshot run id matches: 是", text)


if __name__ == "__main__":
    unittest.main()
