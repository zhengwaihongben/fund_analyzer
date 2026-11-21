# -*- coding: utf-8 -*-
"""V2.2 asset return calculator tests (equity / ETF / bond ETF / cash / FX)."""
from __future__ import annotations

import unittest
from datetime import date

from fa.analysis.returns import (
    calculate_asset,
    cash_return,
    compute_fx_return,
    get_calculator,
    supported_asset_types,
)
from fa.analysis.returns.base import ReturnCalculatorError
from fa.models.asset import Asset, AssetType
from fa.models.market_data import FxSeries, PricePoint, PriceSeries
from fa.models.return_result import ReturnStatus

BASE = date(2025, 6, 5)
TARGET = date(2025, 6, 6)


def prices(days_values, ticker="LLY", market="US", currency="USD", adjusted=True):
    points = [
        PricePoint(
            price_date=day,
            close=value,
            adj_close=value if adjusted else None,
            market=market,
            currency=currency,
        )
        for day, value in days_values
    ]
    return PriceSeries.from_points(ticker, points, market=market, currency=currency)


def usd_fx(currency="USD", base=7.00, target=7.07):
    return FxSeries.from_pairs(
        currency, [(BASE, base), (TARGET, target)], source="test"
    )


def asset(kind=AssetType.EQUITY, weight=0.5, currency="USD", **kwargs) -> Asset:
    return Asset(
        asset_id=kwargs.pop("asset_id", "LLY"),
        name=kwargs.pop("name", "礼来"),
        asset_type=kind,
        ticker=kwargs.pop("ticker", "LLY"),
        currency=currency,
        weight=weight,
        **kwargs,
    )


class EquityCalculatorTests(unittest.TestCase):
    def test_adjusted_close_return_is_case_a(self) -> None:
        result = calculate_asset(
            asset(), base_date=BASE, target_date=TARGET,
            prices=prices([(BASE, 100.0), (TARGET, 110.0)]), fx=usd_fx(),
        )
        self.assertEqual(result.status, ReturnStatus.OK)
        self.assertAlmostEqual(result.local_return, 0.10, places=12)
        self.assertAlmostEqual(result.fx_return, 0.01, places=12)
        self.assertAlmostEqual(result.return_cny, 0.111, places=12)

    def test_missing_series_is_not_zero(self) -> None:
        result = calculate_asset(asset(), base_date=BASE, target_date=TARGET,
                                 prices=None, fx=usd_fx())
        self.assertEqual(result.status, ReturnStatus.MISSING_PRICE)
        self.assertIsNone(result.return_cny)
        self.assertFalse(result.is_ok)

    def test_missing_base_or_target_price(self) -> None:
        only_target = prices([(TARGET, 110.0)])
        result = calculate_asset(asset(), base_date=BASE, target_date=TARGET,
                                 prices=only_target, fx=usd_fx())
        self.assertEqual(result.status, ReturnStatus.MISSING_BASE_PRICE)

        # A series that ends before the target date is not "missing": the last
        # observation on or before the date is used and flagged as stale.
        only_base = prices([(BASE, 100.0)])
        result = calculate_asset(asset(), base_date=BASE, target_date=TARGET,
                                 prices=only_base, fx=usd_fx())
        self.assertTrue(result.is_usable)
        self.assertEqual(result.status, ReturnStatus.STALE_PRICE)
        self.assertTrue(any("落後" in w for w in result.warnings))

    def test_series_starting_after_target_is_missing(self) -> None:
        later = prices([(date(2025, 7, 1), 100.0)])
        result = calculate_asset(asset(), base_date=BASE, target_date=TARGET,
                                 prices=later, fx=usd_fx())
        self.assertEqual(result.status, ReturnStatus.MISSING_BASE_PRICE)

    def test_stale_price_is_excluded_by_policy(self) -> None:
        # Base exact on 2025-05-28, target quoted 2025-05-30 but wanted 06-06:
        # 5 US sessions behind, i.e. >= STALE_SESSIONS_EXCLUDE -> excluded.
        stale = prices([(date(2025, 5, 28), 100.0), (date(2025, 5, 30), 101.0)])
        result = calculate_asset(asset(), base_date=date(2025, 5, 28),
                                 target_date=date(2025, 6, 6), prices=stale, fx=usd_fx())
        self.assertFalse(result.is_ok)
        self.assertEqual(result.status, ReturnStatus.STALE_PRICE)
        self.assertIsNone(result.return_cny)

    def test_slightly_stale_price_is_kept_with_warning(self) -> None:
        one_behind = prices([(date(2025, 6, 3), 100.0), (date(2025, 6, 5), 110.0)])
        fx = FxSeries.from_pairs("USD", [(date(2025, 6, 3), 7.00),
                                         (date(2025, 6, 6), 7.07)], source="test")
        result = calculate_asset(asset(), base_date=date(2025, 6, 3),
                                 target_date=date(2025, 6, 6), prices=one_behind,
                                 fx=fx)
        self.assertTrue(result.is_usable)
        self.assertEqual(result.status, ReturnStatus.STALE_PRICE)
        self.assertTrue(any("落後" in w for w in result.warnings))

    def test_raw_close_fallback_warns(self) -> None:
        raw = prices([(BASE, 100.0), (TARGET, 110.0)], adjusted=False)
        result = calculate_asset(asset(), base_date=BASE, target_date=TARGET,
                                 prices=raw, fx=usd_fx())
        self.assertTrue(result.is_ok)
        self.assertTrue(any("原始收盤價" in w for w in result.warnings))

    def test_missing_currency_is_not_cny(self) -> None:
        result = calculate_asset(asset(currency=""), base_date=BASE, target_date=TARGET,
                                 prices=prices([(BASE, 100.0), (TARGET, 110.0)],
                                               currency=""),
                                 fx=None)
        self.assertEqual(result.status, ReturnStatus.MISSING_CURRENCY)
        self.assertIsNone(result.return_cny)

    def test_cny_asset_needs_no_fx(self) -> None:
        result = calculate_asset(asset(currency="CNY"), base_date=BASE,
                                 target_date=TARGET,
                                 prices=prices([(BASE, 100.0), (TARGET, 110.0)],
                                               currency="CNY"), fx=None)
        self.assertTrue(result.is_ok)
        self.assertEqual(result.fx_return, 0.0)
        self.assertAlmostEqual(result.return_cny, 0.10, places=12)

    def test_missing_fx_for_foreign_currency(self) -> None:
        result = calculate_asset(asset(), base_date=BASE, target_date=TARGET,
                                 prices=prices([(BASE, 100.0), (TARGET, 110.0)]),
                                 fx=None)
        self.assertEqual(result.status, ReturnStatus.MISSING_FX)
        self.assertAlmostEqual(result.local_return, 0.10, places=12)

    def test_currency_cny_alias(self) -> None:
        result = calculate_asset(asset(currency="RMB"), base_date=BASE,
                                 target_date=TARGET,
                                 prices=prices([(BASE, 100.0), (TARGET, 110.0)],
                                               currency="RMB"),
                                 fx=None)
        self.assertTrue(result.is_ok)
        self.assertEqual(result.fx_return, 0.0)

    def test_declared_vs_quote_currency_mismatch_is_rejected(self) -> None:
        result = calculate_asset(
            asset(currency="USD"), base_date=BASE, target_date=TARGET,
            prices=prices([(BASE, 100.0), (TARGET, 110.0)], currency="HKD"),
            fx=usd_fx(),
        )
        self.assertEqual(result.status, ReturnStatus.CURRENCY_MISMATCH)
        self.assertFalse(result.is_usable)
        self.assertIsNone(result.return_cny)

    def test_fx_series_currency_must_match(self) -> None:
        result = calculate_asset(
            asset(currency="USD"), base_date=BASE, target_date=TARGET,
            prices=prices([(BASE, 100.0), (TARGET, 110.0)], currency="USD"),
            fx=FxSeries.from_pairs("EUR", [(BASE, 7.6), (TARGET, 7.7)], source="test"),
        )
        self.assertEqual(result.status, ReturnStatus.MISSING_FX)
        self.assertTrue(any("匯率序列貨幣" in w for w in result.warnings))


class EtfCalculatorTests(unittest.TestCase):
    def test_etf_uses_adjusted_close(self) -> None:
        result = calculate_asset(asset(AssetType.ETF), base_date=BASE,
                                 target_date=TARGET,
                                 prices=prices([(BASE, 100.0), (TARGET, 105.0)]),
                                 fx=usd_fx())
        self.assertAlmostEqual(result.local_return, 0.05, places=12)

    def test_etf_currency_mismatch_warns(self) -> None:
        result = calculate_asset(
            asset(AssetType.ETF, currency="HKD"), base_date=BASE, target_date=TARGET,
            prices=prices([(BASE, 100.0), (TARGET, 105.0)], currency="USD"),
            fx=usd_fx(),
        )
        self.assertEqual(result.status, ReturnStatus.CURRENCY_MISMATCH)
        self.assertTrue(any("不一致" in w for w in result.warnings))

    def test_etf_identifies_currency_from_quote(self) -> None:
        result = calculate_asset(
            asset(AssetType.ETF, currency=""), base_date=BASE, target_date=TARGET,
            prices=prices([(BASE, 100.0), (TARGET, 105.0)], currency="USD"),
            fx=usd_fx(),
        )
        self.assertEqual(result.currency, "USD")
        self.assertTrue(result.is_ok)

    def test_etf_without_exchange_warns(self) -> None:
        result = calculate_asset(
            asset(AssetType.ETF, exchange=None), base_date=BASE, target_date=TARGET,
            prices=prices([(BASE, 100.0), (TARGET, 105.0)], market="", currency="USD"),
            fx=usd_fx(),
        )
        self.assertTrue(any("交易所" in w for w in result.warnings))


class BondEtfCalculatorTests(unittest.TestCase):
    def test_total_return_series_is_used(self) -> None:
        result = calculate_asset(asset(AssetType.BOND_ETF), base_date=BASE,
                                 target_date=TARGET,
                                 prices=prices([(BASE, 100.0), (TARGET, 101.0)]),
                                 fx=usd_fx())
        self.assertTrue(result.is_ok)
        self.assertAlmostEqual(result.local_return, 0.01, places=12)

    def test_price_only_series_is_rejected(self) -> None:
        price_only = prices([(BASE, 100.0), (TARGET, 101.0)], adjusted=False)
        result = calculate_asset(asset(AssetType.BOND_ETF), base_date=BASE,
                                 target_date=TARGET, prices=price_only, fx=usd_fx())
        self.assertEqual(result.status, ReturnStatus.UNSUPPORTED_ASSET)
        self.assertIsNone(result.return_cny)
        self.assertTrue(any("總報酬" in w for w in result.warnings))


class CashCalculatorTests(unittest.TestCase):
    def test_compounding_matches_case_d(self) -> None:
        self.assertAlmostEqual(cash_return(0.02, 30), (1 + 0.02 / 365) ** 30 - 1,
                               places=12)
        self.assertAlmostEqual(cash_return(0.02, 30), 0.0016451, places=7)

    def test_different_day_counts(self) -> None:
        self.assertLess(cash_return(0.02, 1), cash_return(0.02, 10))

    def test_non_positive_days_is_an_input_error(self) -> None:
        """V2.2.1 review P2-11: no silent 0 % for a broken holding period."""
        with self.assertRaises(ReturnCalculatorError):
            cash_return(0.02, 0)
        with self.assertRaises(ReturnCalculatorError):
            cash_return(0.02, -5)

    def test_non_positive_days_yields_invalid_input(self) -> None:
        result = calculate_asset(
            asset(AssetType.CASH, currency="CNY", interest_rate=0.02),
            base_date=date(2025, 6, 30), target_date=date(2025, 6, 30),
            prices=None, fx=None,
        )
        self.assertIs(result.status, ReturnStatus.INVALID_INPUT)
        self.assertFalse(result.is_usable)

    def test_manual_rate_is_used(self) -> None:
        result = calculate_asset(
            asset(AssetType.CASH, currency="CNY", interest_rate=0.02,
                  interest_rate_source="manual", ticker=None),
            base_date=date(2025, 5, 31), target_date=date(2025, 6, 30),
            prices=None, fx=None,
        )
        self.assertTrue(result.is_ok)
        self.assertEqual(result.extra["days"], 30)
        self.assertEqual(result.extra["rate_source"], "manual")
        self.assertAlmostEqual(result.return_cny, cash_return(0.02, 30), places=12)

    def test_missing_rate_uses_assumption_with_warning(self) -> None:
        result = calculate_asset(
            asset(AssetType.CASH, currency="CNY", ticker=None),
            base_date=BASE, target_date=TARGET, prices=None, fx=None,
        )
        self.assertTrue(result.is_ok)
        self.assertEqual(result.extra["rate_source"], "assumption")
        self.assertTrue(any("assumption" in w for w in result.warnings))

    def test_foreign_cash_is_converted(self) -> None:
        result = calculate_asset(
            asset(AssetType.CASH, currency="USD", interest_rate=0.0,
                  interest_rate_source="manual", ticker=None),
            base_date=BASE, target_date=TARGET, prices=None, fx=usd_fx(),
        )
        self.assertTrue(result.is_ok)
        self.assertAlmostEqual(result.fx_return, 0.01, places=12)
        self.assertAlmostEqual(result.return_cny, 0.01, places=12)


class FxCalculatorTests(unittest.TestCase):
    def test_direction_is_case_b(self) -> None:
        outcome = compute_fx_return("USD", usd_fx(), BASE, TARGET)
        self.assertTrue(outcome.ok)
        self.assertAlmostEqual(outcome.fx_return, 0.01, places=12)

    def test_cny_needs_no_series(self) -> None:
        outcome = compute_fx_return("CNY", None, BASE, TARGET)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.fx_return, 0.0)

    def test_unknown_currency_is_not_cny(self) -> None:
        outcome = compute_fx_return("", None, BASE, TARGET)
        self.assertEqual(outcome.status, ReturnStatus.MISSING_CURRENCY)

    def test_missing_rate_is_not_invented(self) -> None:
        outcome = compute_fx_return("USD", None, BASE, TARGET)
        self.assertEqual(outcome.status, ReturnStatus.MISSING_FX)

    def test_series_without_a_declared_base_currency_is_rejected(self) -> None:
        # 只有「USD 序列」不足以說明分母；沒有 CNY 聲明就不能假設是 CNY per USD。
        fx = FxSeries.from_pairs("USD", [(BASE, 7.00), (TARGET, 7.07)], source="test")
        fx.base_currency = ""
        outcome = compute_fx_return("USD", fx, BASE, TARGET)
        self.assertEqual(outcome.status, ReturnStatus.MISSING_FX)
        self.assertTrue(any("基準貨幣" in note for note in outcome.warnings))

    def test_from_pairs_declares_cny_by_default(self) -> None:
        fx = FxSeries.from_pairs("USD", [(BASE, 7.00), (TARGET, 7.07)], source="test")
        self.assertEqual(fx.base_currency, "CNY")
        self.assertTrue(compute_fx_return("USD", fx, BASE, TARGET).ok)


class RegistryTests(unittest.TestCase):
    def test_supported_types(self) -> None:
        self.assertEqual(
            supported_asset_types(),
            frozenset({AssetType.EQUITY, AssetType.ETF, AssetType.BOND_ETF,
                       AssetType.CASH}),
        )

    def test_unknown_has_no_calculator(self) -> None:
        with self.assertRaises(ReturnCalculatorError):
            get_calculator(AssetType.UNKNOWN)

    def test_unknown_asset_is_excluded_not_guessed(self) -> None:
        result = calculate_asset(
            asset(AssetType.UNKNOWN), base_date=BASE, target_date=TARGET,
            prices=prices([(BASE, 100.0), (TARGET, 200.0)]), fx=usd_fx(),
        )
        self.assertEqual(result.status, ReturnStatus.UNSUPPORTED_ASSET)
        self.assertIsNone(result.return_cny)


if __name__ == "__main__":
    unittest.main()
