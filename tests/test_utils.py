# -*- coding: utf-8 -*-
"""配置合併、季度標籤、ticker 正規化與緩存工具的單元測試。"""
import os
import shutil
import socket
import tempfile
import time
import unittest
from pathlib import Path

import pandas as pd

from fa.config import DEFAULT_CONFIG, merge_config
from fa.utils.cache import (
    _prune_stale_outputs,
    is_cache_fresh,
    read_cache_df,
    write_cache_df,
)
from fa.utils.dates import latest_quarter_label
from fa.utils.net import (
    NETWORK_CONNECTION,
    NETWORK_DNS,
    NETWORK_TIMEOUT,
    NETWORK_TLS,
    apply_default_timeout,
    classify_network_error,
    describe_network_error,
)
from fa.utils.tickers import to_yf_ticker


class MergeConfigTests(unittest.TestCase):
    def test_defaults_fill_missing_keys(self):
        cfg = merge_config()
        self.assertEqual(cfg["risk_free"], DEFAULT_CONFIG["risk_free"])
        self.assertEqual(cfg["trading_days"], DEFAULT_CONFIG["trading_days"])

    def test_user_values_win(self):
        cfg = merge_config({"risk_free": 0.035, "benchmark": "000300.SS"})
        self.assertEqual(cfg["risk_free"], 0.035)
        self.assertEqual(cfg["benchmark"], "000300.SS")
        self.assertEqual(cfg["trading_days"], DEFAULT_CONFIG["trading_days"])

    def test_explicit_none_disables_benchmark(self):
        # CLI 的 --benchmark "" 會被解析成 None；不能被默認值覆蓋回去
        self.assertIsNone(merge_config({"benchmark": None})["benchmark"])

    def test_defaults_are_not_mutated(self):
        before = dict(DEFAULT_CONFIG)
        merge_config({"sma_windows": [3], "boll_n": 5})
        self.assertEqual(DEFAULT_CONFIG, before)

    def test_empty_dict_is_safe(self):
        self.assertEqual(merge_config({})["boll_k"], DEFAULT_CONFIG["boll_k"])


class QuarterLabelTests(unittest.TestCase):
    def test_latest_quarter_uses_max_not_row_order(self):
        df = pd.DataFrame({"QuarterLabel": ["2024Q4", "2025Q2", "2025Q1"]})
        self.assertEqual(latest_quarter_label(df), "2025Q2")

    def test_latest_quarter_ignores_missing_column(self):
        self.assertIsNone(latest_quarter_label(pd.DataFrame({"x": [1]})))

    def test_latest_quarter_falls_back_on_unknown_format(self):
        df = pd.DataFrame({"QuarterLabel": ["2024年4季度", "2025年1季度"]})
        self.assertEqual(latest_quarter_label(df), "2025年1季度")


class TickerTests(unittest.TestCase):
    def test_normalisation(self):
        cases = {
            "0700": "0700.HK",
            "00700": "0700.HK",
            "09988": "9988.HK",
            "MSFT": "MSFT",
            "BRK.B": "BRK-B",
            "brk-b": "BRK-B",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(to_yf_ticker(raw), expected)

    def test_rejects_meaningless_input(self):
        for raw in (None, "", "   ", "1234567", "!!!"):
            with self.subTest(raw=raw):
                self.assertIsNone(to_yf_ticker(raw))

    def test_rejects_a_share_codes(self):
        # yfinance 無法可靠對應 6 位 A 股代碼，寧可跳過也不要猜錯
        self.assertIsNone(to_yf_ticker("000001"))


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_roundtrip_keeps_string_codes(self):
        path = self.tmp / "holdings.csv"
        write_cache_df(pd.DataFrame({"StockCode": ["00700"], "Weight": [3.5]}), path)
        df = read_cache_df(path)
        self.assertEqual(df.loc[0, "StockCode"], "00700")
        self.assertEqual(df.loc[0, "Weight"], "3.5")

    def test_read_missing_file_raises_cache_error(self):
        from fa.errors import CacheError
        with self.assertRaises(CacheError):
            read_cache_df(self.tmp / "nope.csv")

    def test_freshness_respects_ttl(self):
        path = self.tmp / "c.csv"
        write_cache_df(pd.DataFrame({"a": [1]}), path)
        self.assertTrue(is_cache_fresh(path, max_age_days=7))
        old = time.time() - 30 * 86400
        os.utime(path, (old, old))
        self.assertFalse(is_cache_fresh(path, max_age_days=7))
        # <= 0 表示永不過期
        self.assertTrue(is_cache_fresh(path, max_age_days=0))

    def test_prune_keeps_data_and_notes(self):
        for name in (
            "017436_daily_nav.csv",
            "017436_holdings_raw.csv",
            "017436_holdings_clean.csv",
            "017436_old_report.html",
            "note.md",
        ):
            (self.tmp / name).write_text("x", encoding="utf-8")

        _prune_stale_outputs(self.tmp, "017436", {"017436_daily_nav.csv"})

        self.assertTrue((self.tmp / "017436_daily_nav.csv").exists())
        self.assertTrue((self.tmp / "017436_holdings_raw.csv").exists())
        self.assertTrue((self.tmp / "note.md").exists())
        self.assertFalse((self.tmp / "017436_old_report.html").exists())


class NetworkDefaultTimeoutTests(unittest.TestCase):
    """第三方庫不暴露 timeout，靠 socket 默認超時兜底。"""

    def test_sets_and_clears_socket_default(self):
        original = socket.getdefaulttimeout()
        self.addCleanup(socket.setdefaulttimeout, original)

        self.assertEqual(apply_default_timeout(12.5), 12.5)
        self.assertEqual(socket.getdefaulttimeout(), 12.5)

        apply_default_timeout(0)
        self.assertIsNone(socket.getdefaulttimeout())


class NetworkErrorClassificationTests(unittest.TestCase):
    """網絡故障要能被歸類，log 才寫得出「這是網絡不是程式」的結論。"""

    def test_ssl_eof_is_tls(self):
        import ssl

        exc = ssl.SSLEOFError(8, "EOF occurred in violation of protocol")
        self.assertEqual(classify_network_error(exc), NETWORK_TLS)
        self.assertIn("TLS", describe_network_error(exc))

    def test_requests_ssl_error_is_tls(self):
        from requests.exceptions import SSLError

        exc = SSLError("Max retries exceeded (Caused by SSLError(SSLEOFError(8, ...)))")
        self.assertEqual(classify_network_error(exc), NETWORK_TLS)

    def test_timeout_is_timeout(self):
        self.assertEqual(classify_network_error(socket.timeout("timed out")), NETWORK_TIMEOUT)

    def test_dns_error(self):
        self.assertEqual(classify_network_error(socket.gaierror(11001, "getaddrinfo failed")), NETWORK_DNS)

    def test_connection_error(self):
        from requests.exceptions import ConnectionError as RequestsConnectionError

        exc = RequestsConnectionError("Connection aborted.")
        self.assertEqual(classify_network_error(exc), NETWORK_CONNECTION)

    def test_message_based_fallback(self):
        exc = RuntimeError("Caused by SSLError(SSLError(1, '[SSL: WRONG_VERSION_NUMBER] wrong version number'))")
        self.assertEqual(classify_network_error(exc), NETWORK_TLS)

    def test_wrapped_exception_chain_is_unwrapped(self):
        """akshare 的 APIError(x) from SSLError 要能歸類成 TLS，而不是『其他』。"""
        import ssl

        from requests.exceptions import SSLError

        class APIError(Exception):
            pass

        try:
            try:
                raise SSLError("(Caused by SSLError(SSLEOFError(8, 'EOF occurred in violation of protocol')))")
            except SSLError as err:
                raise APIError(f"FundArchivesDatas request failed: {err}") from err
        except APIError as wrapped:
            self.assertEqual(classify_network_error(wrapped), NETWORK_TLS)
            self.assertIn("TLS", describe_network_error(wrapped))
        self.assertIsInstance(ssl.SSLEOFError(8, "x"), ssl.SSLError)

    def test_unknown_error_is_other(self):
        self.assertEqual(classify_network_error(ValueError("boom")), "other")


if __name__ == "__main__":
    unittest.main()
