# -*- coding: utf-8 -*-
"""Tests for the V2 data models and data-quality grading (V2.0)."""
from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from fa.models import (
    Asset,
    AssetType,
    AssetValidationError,
    EstimationResult,
    EstimationRun,
    FxSeries,
    HoldingSnapshot,
    HoldingValidationError,
    PortfolioStatus,
    PricePoint,
    PriceSeries,
    ReturnStatus,
    shapley_contributions,
    snapshot_from_rows,
)
from fa.utils.data_quality import QualityGrade, assess_quality


def make_asset(**overrides) -> Asset:
    payload = dict(
        asset_id="LLY",
        name="礼来",
        asset_type=AssetType.EQUITY,
        ticker="LLY",
        market="US",
        currency="USD",
        weight=0.1043,
        holding_date=date(2026, 9, 30),
        source="eastmoney-page",
    )
    payload.update(overrides)
    return Asset(**payload)


class AssetTypeTests(unittest.TestCase):
    def test_parse_aliases(self):
        self.assertIs(AssetType.parse("Stock"), AssetType.EQUITY)
        self.assertIs(AssetType.parse(" bond-etf "), AssetType.BOND_ETF)
        self.assertIs(AssetType.parse("ETF"), AssetType.ETF)
        self.assertIs(AssetType.parse("cash"), AssetType.CASH)

    def test_parse_unknown_is_explicit(self):
        for value in (None, "", "warrant", "derivative"):
            self.assertIs(AssetType.parse(value), AssetType.UNKNOWN)
        self.assertNotIn(AssetType.UNKNOWN, {AssetType.EQUITY})


class AssetTests(unittest.TestCase):
    def test_defaults_and_capabilities(self):
        asset = make_asset()
        self.assertTrue(asset.is_supported)
        self.assertTrue(asset.needs_price)
        self.assertFalse(asset.needs_interest_rate)
        self.assertTrue(asset.uses_fx)

    def test_cash_uses_rate_model(self):
        asset = make_asset(
            asset_id="CASH",
            name="银行存款",
            asset_type=AssetType.CASH,
            ticker=None,
            market=None,
            currency="CNY",
            weight=0.05,
            interest_rate_source="policy_rate",
            interest_rate=0.015,
        )
        self.assertTrue(asset.needs_interest_rate)
        self.assertFalse(asset.needs_price)
        self.assertFalse(asset.uses_fx)

    def test_unknown_asset_is_unsupported(self):
        asset = make_asset(asset_type="warrant")
        self.assertIs(asset.asset_type, AssetType.UNKNOWN)
        self.assertFalse(asset.is_supported)

    def test_currency_normalisation(self):
        self.assertEqual(make_asset(currency="rmb").currency, "CNY")
        self.assertEqual(make_asset(currency=" usd ").currency, "USD")
        self.assertEqual(make_asset(currency="").currency, "")

    def test_percent_weight_rejected(self):
        with self.assertRaises(AssetValidationError):
            make_asset(weight=10.43)

    def test_bad_weights_rejected(self):
        for weight in (-0.1, float("nan"), float("inf"), "abc"):
            with self.assertRaises(AssetValidationError):
                make_asset(weight=weight)

    def test_missing_identity_rejected(self):
        with self.assertRaises(AssetValidationError):
            make_asset(asset_id="")
        with self.assertRaises(AssetValidationError):
            make_asset(name="   ")

    def test_ticker_blank_becomes_none(self):
        self.assertIsNone(make_asset(ticker="  ").ticker)

    def test_dict_roundtrip(self):
        asset = make_asset(price_type="adjusted_close", extra={"currency_raw": "USD"})
        clone = Asset.from_dict(asset.to_dict())
        self.assertEqual(clone.to_dict(), asset.to_dict())


class HoldingSnapshotTests(unittest.TestCase):
    def make_snapshot(self, **overrides) -> HoldingSnapshot:
        assets = [
            make_asset(asset_id="LLY", weight=0.50),
            make_asset(asset_id="JNJ", name="强生", weight=0.20),
        ]
        payload = dict(
            fund_code="000369",
            assets=assets,
            quarter="2026Q3",
            holding_date=date(2026, 9, 30),
            disclosure_date=date(2026, 10, 21),
            disclosure_date_estimated=True,
            source="eastmoney-page",
        )
        payload.update(overrides)
        return HoldingSnapshot(**payload)

    def test_totals(self):
        snapshot = self.make_snapshot()
        self.assertAlmostEqual(snapshot.total_weight, 0.70)
        self.assertAlmostEqual(snapshot.uncovered_weight, 0.30)
        self.assertEqual(snapshot.to_dict()["assets"][0]["asset_id"], "LLY")

    def test_point_in_time_helper(self):
        snapshot = self.make_snapshot()
        self.assertTrue(snapshot.is_disclosed_by(date(2026, 11, 13)))
        self.assertFalse(snapshot.is_disclosed_by(date(2026, 10, 20)))
        self.assertFalse(snapshot.is_disclosed_by(None))

    def test_validate_rejects_duplicates(self):
        snapshot = self.make_snapshot(
            assets=[make_asset(asset_id="LLY"), make_asset(asset_id="LLY")]
        )
        with self.assertRaises(HoldingValidationError):
            snapshot.validate()

    def test_validate_rejects_missing_dates(self):
        snapshot = self.make_snapshot(disclosure_date=None)
        with self.assertRaises(HoldingValidationError):
            snapshot.validate()

    def test_validate_rejects_overweight(self):
        snapshot = self.make_snapshot(
            assets=[make_asset(asset_id="A", weight=0.7), make_asset(asset_id="B", weight=0.6)]
        )
        with self.assertRaises(HoldingValidationError):
            snapshot.validate()

    def test_disclosure_before_holding_rejected(self):
        with self.assertRaises(HoldingValidationError):
            self.make_snapshot(disclosure_date=date(2026, 9, 1))

    def test_by_type_and_filters(self):
        snapshot = self.make_snapshot(
            assets=[
                make_asset(asset_id="LLY", weight=0.4),
                make_asset(asset_id="CASH", name="现金", asset_type="cash", weight=0.1),
            ]
        )
        self.assertEqual(snapshot.by_type(), {"equity": 0.4, "cash": 0.1})
        self.assertEqual([a.asset_id for a in snapshot.assets_of(AssetType.CASH)], ["CASH"])

    def test_snapshot_from_rows(self):
        rows = [
            {
                "ticker": "LLY",
                "name": "礼来",
                "asset_type": "equity",
                "currency": "USD",
                "weight": 0.1,
            }
        ]
        snapshot = snapshot_from_rows(
            "000369",
            rows,
            quarter="2026Q3",
            holding_date=date(2026, 9, 30),
            disclosure_date=date(2026, 10, 21),
            source="unit-test",
        )
        self.assertEqual(snapshot.assets[0].asset_id, "LLY")
        self.assertEqual(snapshot.assets[0].holding_date, date(2026, 9, 30))


class PriceSeriesTests(unittest.TestCase):
    def make_series(self) -> PriceSeries:
        points = [
            PricePoint(price_date=date(2026, 11, 9), close=100.0, adj_close=99.0),
            PricePoint(price_date=date(2026, 11, 10), close=101.0, adj_close=100.0),
            PricePoint(price_date=date(2026, 11, 12), close=103.0, adj_close=102.0),
        ]
        return PriceSeries(ticker="LLY", currency="USD", market="US", points=points)

    def test_adjusted_close_preferred(self):
        series = self.make_series()
        value, day = series.price_on_or_before(date(2026, 11, 12))
        self.assertEqual(value, 102.0)
        self.assertEqual(day, date(2026, 11, 12))

    def test_no_future_point(self):
        series = self.make_series()
        value, day = series.price_on_or_before(date(2026, 11, 11))
        self.assertEqual(day, date(2026, 11, 10))
        self.assertLess(day, date(2026, 11, 11))
        value, day = series.price_on_or_before(date(2026, 11, 8))
        self.assertIsNone(value)
        self.assertIsNone(day)

    def test_points_are_sorted(self):
        series = PriceSeries(
            ticker="X",
            points=[
                PricePoint(price_date=date(2026, 11, 12), close=3.0),
                PricePoint(price_date=date(2026, 11, 9), close=1.0),
            ],
        )
        self.assertEqual(series.first_date, date(2026, 11, 9))
        self.assertEqual(series.last_date, date(2026, 11, 12))

    def test_dict_roundtrip(self):
        series = self.make_series()
        clone = PriceSeries.from_points(
            series.ticker,
            [PricePoint.from_dict(point.to_dict()) for point in series.points],
            market=series.market,
            currency=series.currency,
        )
        self.assertEqual(clone.to_dict(), series.to_dict())

    def test_available_and_used_price_type_are_separate(self):
        # The source offered both columns; nothing has been used yet.
        point = PricePoint(price_date=date(2026, 11, 9), close=100.0, adj_close=99.0)
        self.assertEqual(point.available_price_type, "close+adj_close")
        self.assertEqual(point.used_price_type, "")
        self.assertEqual(point.price_type, "close+adj_close")  # back-compat alias

        close_only = PricePoint(price_date=date(2026, 11, 9), close=100.0)
        self.assertEqual(close_only.available_price_type, "close")

        chosen = point.with_used_price_type("adjusted_close")
        self.assertEqual(chosen.used_price_type, "adjusted_close")
        self.assertEqual(chosen.price_type, "adjusted_close")
        self.assertEqual(point.used_price_type, "")  # original untouched

        clone = PricePoint.from_dict(chosen.to_dict())
        self.assertEqual(clone.available_price_type, "close+adj_close")
        self.assertEqual(clone.used_price_type, "adjusted_close")

    def test_legacy_price_type_field_still_reads(self):
        legacy = PricePoint.from_dict(
            {"price_date": "2026-11-09", "close": 100.0, "price_type": "adjusted_close"}
        )
        self.assertEqual(legacy.available_price_type, "adjusted_close")


class FxSeriesTests(unittest.TestCase):
    def test_rate_on_or_before(self):
        series = FxSeries.from_pairs(
            "USD", [(date(2026, 11, 10), 7.10), (date(2026, 11, 12), 7.20)]
        )
        rate, day = series.rate_on_or_before(date(2026, 11, 13))
        self.assertEqual(rate, 7.20)
        self.assertEqual(day, date(2026, 11, 12))
        self.assertEqual(series.rate_on_or_before(date(2026, 11, 1)), (None, None))
        self.assertEqual(series.points[0].currency, "CNY")


class ShapleyAttributionTests(unittest.TestCase):
    def test_split_adds_up_to_cny_return(self):
        weight, local, fx = 0.1043, 0.10, 0.01
        c_local, c_fx = shapley_contributions(weight, local, fx)
        r_cny = (1 + local) * (1 + fx) - 1
        self.assertAlmostEqual(c_local + c_fx, weight * r_cny, places=15)
        self.assertAlmostEqual(c_local, 0.1043 * 0.10 * 1.005, places=15)
        self.assertAlmostEqual(c_fx, 0.1043 * 0.01 * 1.05, places=15)

    def test_zero_fx_is_all_local(self):
        c_local, c_fx = shapley_contributions(0.5, 0.02, 0.0)
        self.assertAlmostEqual(c_local, 0.01)
        self.assertAlmostEqual(c_fx, 0.0)

    def test_negative_returns_are_not_double_counted(self):
        weight, local, fx = 0.3, -0.05, -0.02
        c_local, c_fx = shapley_contributions(weight, local, fx)
        r_cny = (1 + local) * (1 + fx) - 1
        self.assertAlmostEqual(c_local + c_fx, weight * r_cny, places=15)


class ReturnResultTests(unittest.TestCase):
    def test_auto_contributions(self):
        from fa.models import AssetReturnResult

        result = AssetReturnResult(
            asset_id="LLY",
            local_return=0.10,
            fx_return=0.01,
            return_cny=0.111,
            weight=0.1,
        )
        self.assertTrue(result.is_ok)
        self.assertAlmostEqual(result.contribution, 0.1 * 0.111, places=12)

    def test_excluded_asset_contributes_nothing(self):
        from fa.models import AssetReturnResult

        result = AssetReturnResult(
            asset_id="XYZ", status=ReturnStatus.MISSING_PRICE, weight=0.2
        )
        self.assertFalse(result.is_ok)
        self.assertEqual(result.contribution, 0.0)

    def test_status_parse_fallback(self):
        from fa.models import AssetReturnResult

        result = AssetReturnResult(asset_id="A", status="not-a-status")
        self.assertIs(result.status, ReturnStatus.UNSUPPORTED_ASSET)


class DataQualityTests(unittest.TestCase):
    def rows(self, statuses_and_weights):
        return [
            {"status": status, "weight": weight}
            for status, weight in statuses_and_weights
        ]

    def test_full_coverage_is_high(self):
        report = assess_quality(self.rows([("ok", 0.6), ("ok", 0.4)]))
        self.assertIs(report.grade, QualityGrade.HIGH)
        self.assertEqual(report.coverage_ratio, 1.0)
        self.assertAlmostEqual(report.score, 100.0)

    def test_partial_coverage_lowers_score(self):
        report = assess_quality(self.rows([("ok", 0.46), ("missing-price", 0.1)]))
        self.assertLess(report.score, 85.0)
        self.assertAlmostEqual(report.excluded_weight, 0.1)
        self.assertTrue(any("被排除" in reason for reason in report.reasons))

    def test_stale_price_is_soft(self):
        report = assess_quality(self.rows([("ok", 0.5), ("stale-price", 0.5)]))
        self.assertAlmostEqual(report.coverage_ratio, 1.0)
        self.assertAlmostEqual(report.stale_weight, 0.5)
        self.assertLess(report.score, 100.0)

    def test_estimated_disclosure_penalty(self):
        fresh = assess_quality(self.rows([("ok", 1.0)]))
        estimated = assess_quality(self.rows([("ok", 1.0)]), disclosure_date_estimated=True)
        self.assertAlmostEqual(fresh.score - estimated.score, 5.0)
        self.assertIn("推算值", " ".join(estimated.reasons))

    def test_grade_thresholds(self):
        self.assertIs(QualityGrade.from_score(90), QualityGrade.HIGH)
        self.assertIs(QualityGrade.from_score(70), QualityGrade.MEDIUM)
        self.assertIs(QualityGrade.from_score(10), QualityGrade.LOW)

    def test_report_serialises(self):
        payload = assess_quality(self.rows([("ok", 1.0)])).to_dict()
        self.assertEqual(payload["grade"], "high")
        self.assertIn("reasons", payload)


class EstimationModelTests(unittest.TestCase):
    def test_run_row(self):
        run = EstimationRun(
            fund_code="000369",
            target_nav_date=date(2026, 11, 13),
            trade_date=date(2026, 11, 15),
            base_nav_date=date(2026, 11, 12),
            as_of=datetime(2026, 11, 15, 20, 0, tzinfo=timezone.utc),
        )
        row = run.to_row()
        self.assertEqual(row["target_nav_date"], "2026-11-13")
        self.assertEqual(row["model_version"], "2.2.0")
        self.assertEqual(len(run.run_id), 32)

    def test_run_ids_unique(self):
        first = EstimationRun(fund_code="A", target_nav_date=date(2026, 1, 5), trade_date=date(2026, 1, 6))
        second = EstimationRun(fund_code="A", target_nav_date=date(2026, 1, 5), trade_date=date(2026, 1, 6))
        self.assertNotEqual(first.run_id, second.run_id)

    def test_result_success_flag(self):
        partial = EstimationResult(
            run_id="r1", status=PortfolioStatus.PARTIAL, estimated_nav=2.82
        )
        self.assertTrue(partial.is_successful)
        failed = EstimationResult(run_id="r2", status=PortfolioStatus.INSUFFICIENT_DATA)
        self.assertFalse(failed.is_successful)

    def test_status_parse(self):
        self.assertIs(PortfolioStatus.parse("PARTIAL"), PortfolioStatus.PARTIAL)

    def test_empty_run_id_rejected(self):
        with self.assertRaises(ValueError):
            EstimationResult(run_id="")


if __name__ == "__main__":
    unittest.main()
