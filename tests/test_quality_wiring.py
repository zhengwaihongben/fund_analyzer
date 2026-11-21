# -*- coding: utf-8 -*-
"""Stale-price / quality wiring tests (V2.2.1 review, cases 1-3 and 6-7).

The review found that a stale price was reported as a *warning* but still
returned ``status=OK``, so :func:`assess_quality` never saw it.  These tests pin
the contract: slightly stale stays in the estimate and lowers confidence,
severely stale is excluded, and a stale FX leg is visible in the quality grade.
"""
from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

from fa.analysis.returns import calculate_asset
from fa.analysis.valuation import value_portfolio
from fa.data.asset_classifier import classify_asset_type, normalise_ticker
from fa.data.qdii_holdings import build_qdii_holdings
from fa.models.asset import Asset, AssetType
from fa.models.market_data import FxSeries, PricePoint, PriceSeries
from fa.models.return_result import ReturnStatus
from fa.utils.calendars import get_calendar
from fa.utils.data_quality import assess_quality

BASE = date(2025, 5, 30)
TARGET = date(2025, 6, 9)


def _us():
    return get_calendar("US")


def _prices(last_day: date, currency: str = "USD", market: str = "US") -> PriceSeries:
    points = [PricePoint(price_date=BASE, close=100.0, market=market, currency=currency)]
    if last_day > BASE:
        points.append(
            PricePoint(price_date=last_day, close=110.0, market=market, currency=currency)
        )
    return PriceSeries(ticker="LLY", market=market, currency=currency, points=points)


def _fx(currency: str = "USD", last_day: date = TARGET) -> FxSeries:
    pairs = [(BASE, 7.00)]
    if last_day > BASE:
        pairs.append((last_day, 7.07))
    return FxSeries.from_pairs(currency, pairs, source="test")


def _asset(weight: float = 0.20, asset_type: AssetType = AssetType.EQUITY) -> Asset:
    return Asset(
        asset_id="LLY",
        name="礼来",
        asset_type=asset_type,
        ticker="LLY",
        market="US",
        currency="USD",
        weight=weight,
    )


class StalePriceQualityTests(unittest.TestCase):
    def test_slightly_stale_price_stays_in_coverage_but_lowers_quality(self):
        """2 sessions late: still usable, still covered, quality drops."""
        result = calculate_asset(
            _asset(),
            base_date=BASE,
            target_date=TARGET,
            prices=_prices(date(2025, 6, 5)),
            fx=_fx(),
            calendar=_us(),
        )
        self.assertIs(result.status, ReturnStatus.STALE_PRICE)
        self.assertTrue(result.is_usable)
        self.assertIsNotNone(result.return_cny);

        portfolio = value_portfolio(None, [result])
        self.assertAlmostEqual(portfolio.coverage_ratio, 0.20)

        quality = assess_quality([result])
        self.assertAlmostEqual(quality.stale_weight, 0.20, places=6)
        self.assertLess(quality.score, 100.0)

        fresh = calculate_asset(
            _asset(),
            base_date=BASE,
            target_date=TARGET,
            prices=_prices(TARGET),
            fx=_fx(),
            calendar=_us(),
        )
        fresh_quality = assess_quality([fresh])
        self.assertGreater(fresh_quality.score, quality.score)

    def test_three_sessions_late_is_excluded_from_coverage(self):
        """3 sessions late: excluded, so the uncovered weight grows."""
        result = calculate_asset(
            _asset(),
            base_date=BASE,
            target_date=TARGET,
            prices=_prices(date(2025, 6, 4)),
            fx=_fx(),
            calendar=_us(),
        )
        self.assertFalse(result.is_usable)
        self.assertIsNone(result.return_cny)
        self.assertTrue(result.extra.get("excluded"))

        portfolio = value_portfolio(None, [result])
        self.assertAlmostEqual(portfolio.coverage_ratio, 0.0)
        self.assertAlmostEqual(portfolio.uncovered_weight, 1.0)

        quality = assess_quality([result])
        self.assertAlmostEqual(quality.excluded_weight, 0.20, places=6)

    def test_stale_fx_lowers_quality(self):
        result = calculate_asset(
            _asset(),
            base_date=BASE,
            target_date=TARGET,
            prices=_prices(TARGET),
            fx=_fx(last_day=date(2025, 6, 5)),
            calendar=_us(),
        )
        self.assertIn(
            result.status,
            {ReturnStatus.STALE_FX, ReturnStatus.STALE_BASE_FX},
        )
        self.assertTrue(result.is_usable)
        quality = assess_quality([result])
        self.assertGreater(quality.stale_weight, 0.0)


class CashAndUnknownQualityTests(unittest.TestCase):
    def test_assumption_rate_is_flagged_and_penalised(self):
        cash = Asset(
            asset_id="CASH",
            name="现金",
            asset_type=AssetType.CASH,
            currency="CNY",
            weight=0.10,
        )
        result = calculate_asset(
            cash, base_date=BASE, target_date=TARGET, prices=None, fx=None,
            calendar=_us(),
        )
        self.assertTrue(any("假設年利率" in w for w in result.warnings))
        quality = assess_quality([result])
        self.assertAlmostEqual(quality.assumption_weight, 0.10, places=6)
        self.assertTrue(any("假設利率" in reason for reason in quality.reasons))

    def test_known_rate_is_not_penalised(self):
        cash = Asset(
            asset_id="CASH",
            name="现金",
            asset_type=AssetType.CASH,
            currency="CNY",
            weight=0.10,
            interest_rate=0.02,
            interest_rate_source="policy_rate",
        )
        result = calculate_asset(
            cash, base_date=BASE, target_date=TARGET, prices=None, fx=None,
            calendar=_us(),
        )
        quality = assess_quality([result])
        self.assertEqual(quality.assumption_weight, 0.0)

    def test_unknown_asset_keeps_its_weight_but_is_not_valued(self):
        unknown = Asset(
            asset_id="XYZ",
            name="未知資產",
            asset_type=AssetType.UNKNOWN,
            weight=0.15,
        )
        result = calculate_asset(
            unknown, base_date=BASE, target_date=TARGET, prices=None, fx=None
        )
        self.assertIs(result.status, ReturnStatus.UNSUPPORTED_ASSET)
        self.assertFalse(result.is_usable)

        portfolio = value_portfolio(None, [result])
        self.assertAlmostEqual(portfolio.coverage_ratio, 0.0)
        self.assertAlmostEqual(portfolio.uncovered_weight, 1.0)
        self.assertEqual(result.weight, 0.15)


class AssetClassificationTests(unittest.TestCase):
    """V2.2.2: a ticker's shape is not evidence of an asset type."""

    def test_ticker_shape_alone_does_not_make_an_equity(self):
        # 一般代碼格式（含 "ROP SW" 這種交易所後綴）不能證明是股票：
        # ETF 也可能長這樣，所以沒有來源聲明時一律保留為 unknown。
        for ticker in ("ROP SW", "ROP.SW", "AAPL", "510300", "0700.HK"):
            with self.subTest(ticker=ticker):
                asset_type, evidence = classify_asset_type(ticker=ticker, name="")
                self.assertIs(asset_type, AssetType.UNKNOWN)
                self.assertEqual(evidence, "unclassified")
        self.assertEqual(normalise_ticker("ROP SW"), "ROP.SW")
        self.assertEqual(normalise_ticker("ROP.SW"), "ROP.SW")

    def test_declared_type_from_the_data_source_is_used(self):
        # Eastmoney 的「股票投资明细」表就是來源聲明：這是由 qdii_holdings 帶進來的。
        asset_type, evidence = classify_asset_type(
            ticker="ROP SW", name="罗氏控股股份公司", declared="equity"
        )
        self.assertIs(asset_type, AssetType.EQUITY)
        self.assertEqual(evidence, "declared")

    def test_qdii_holdings_declare_equity_from_the_source_table(self):
        raw = pd.DataFrame({
            "股票代码": ["ROP SW"], "股票名称": ["罗氏控股股份公司"],
            "占净值比例": ["3.39"], "季度": ["2026年3季度股票投资明细"],
        })
        holdings = build_qdii_holdings(raw)
        self.assertEqual(holdings.iloc[0]["asset_type"], "equity")

    def test_unknown_source_table_does_not_declare_a_type(self):
        raw = pd.DataFrame({
            "股票代码": ["LLY"], "股票名称": ["礼来"],
            "占净值比例": ["10.43"], "季度": ["2026年3季度债券投资明细"],
        })
        holdings = build_qdii_holdings(raw)
        self.assertEqual(holdings.iloc[0]["asset_type"], "")

    def test_etf_and_bond_etf_names_win_over_the_ticker_rule(self):
        self.assertIs(classify_asset_type(ticker="510300", name="华夏沪深300ETF")[0], AssetType.ETF)
        self.assertIs(
            classify_asset_type(ticker="511010", name="国债ETF")[0], AssetType.BOND_ETF
        )

    def test_manual_override_has_priority(self):
        asset_type, evidence = classify_asset_type(
            ticker="XYZ", name="XYZ", overrides={"XYZ": AssetType.CASH}
        )
        self.assertIs(asset_type, AssetType.CASH)
        self.assertEqual(evidence, "manual")

    def test_junk_stays_unknown(self):
        asset_type, evidence = classify_asset_type(ticker="??", name="??")
        self.assertIs(asset_type, AssetType.UNKNOWN)
        self.assertEqual(evidence, "unclassified")


class ReliabilityPatchTests(unittest.TestCase):
    """V2.2.2: FX quote convention, cash stale FX, price-type provenance."""

    def test_fx_series_with_non_cny_base_is_rejected(self):
        from fa.analysis.returns.base import convert_to_cny

        pairs = [(BASE, 0.92), (TARGET, 0.93)]
        fx = FxSeries.from_pairs("USD", pairs, source="test")
        fx.base_currency = "EUR"
        outcome = convert_to_cny("USD", fx, BASE, TARGET, calendar=_us())
        self.assertEqual(outcome.status, ReturnStatus.MISSING_FX)
        self.assertTrue(any("並非 CNY" in note for note in outcome.warnings))

    def test_cash_keeps_a_stale_fx_status(self):
        # 匯率落後 1 個交易日：現金仍可估值，但狀態要傳到 Data Quality。
        pairs = [(BASE, 7.00), (date(2025, 6, 6), 7.07)]
        fx = FxSeries.from_pairs("USD", pairs, source="test")
        cash = Asset(
            asset_id="CASH-USD", name="美元现金", asset_type=AssetType.CASH,
            currency="USD", weight=0.05,
            interest_rate=0.02, interest_rate_source="manual",
        )
        result = calculate_asset(cash, base_date=BASE, target_date=TARGET, fx=fx)
        self.assertEqual(result.status, ReturnStatus.STALE_FX)
        self.assertTrue(result.is_usable)
        report = assess_quality([result])
        self.assertGreater(report.stale_weight, 0.0)

    def test_series_from_frame_reports_what_the_source_offered(self):
        from fa.analysis.returns.base import series_from_frame

        close_only = pd.DataFrame({
            "Date": pd.to_datetime(["2025-06-06"]), "Close": [100.0],
        })
        both = pd.DataFrame({
            "Date": pd.to_datetime(["2025-06-06"]),
            "Close": [100.0], "AdjClose": [98.0],
        })
        self.assertEqual(series_from_frame(close_only, ticker="X").points[0].available_price_type, "close")
        self.assertEqual(
            series_from_frame(both, ticker="X").points[0].available_price_type,
            "close+adj_close",
        )

    def test_market_metadata_reaches_the_asset(self):
        from fa.data.asset_classifier import build_assets

        holdings = pd.DataFrame([
            {"ticker": "LLY", "name": "礼来", "weight": 0.5, "currency_hint": "USD",
             "asset_type": "equity"},
        ])
        assets = build_assets(holdings, {"LLY": {"market": "NYSE", "currency": "USD"}})
        self.assertEqual(assets[0].market, "NYSE")


class AssetTypePersistenceTests(unittest.TestCase):
    """V2.2.3: the source-declared asset type must survive the SQLite write."""

    def _frame(self):
        return pd.DataFrame([
            {"ticker": "LLY", "name": "礼来", "weight": 0.5, "currency_hint": "USD",
             "market": "NYSE", "quarter": "2026Q3",
             "holding_date": "2026-09-30", "disclosure_date": "2026-10-21",
             "disclosure_date_estimated": True, "asset_type": "equity"},
            {"ticker": "BOND1", "name": "某债券", "weight": 0.2, "currency_hint": "CNY",
             "market": "CN", "quarter": "2026Q3",
             "holding_date": "2026-09-30", "disclosure_date": "2026-10-21",
             "disclosure_date_estimated": True, "asset_type": ""},
        ])

    def test_undeclared_holdings_stay_unknown_in_the_database(self):
        import tempfile
        from pathlib import Path

        from fa.data.database.repository import Repository
        from fa.data.qdii_holdings import save_quarters_to_db

        with tempfile.TemporaryDirectory() as tmp:
            with Repository(path=Path(tmp) / "t.db") as repo:
                saved = save_quarters_to_db(repo, "000369", self._frame(), source="test")
                self.assertEqual(saved, 1)
                snapshots = repo.load_snapshots("000369")
                types = {asset.ticker: asset.asset_type for asset in snapshots[0].assets}
            self.assertEqual(types["LLY"], AssetType.EQUITY)
            # 沒有來源聲明的持倉不能在寫入資料庫時被「升級」成股票。
            self.assertEqual(types["BOND1"], AssetType.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
