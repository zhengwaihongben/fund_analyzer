# -*- coding: utf-8 -*-
"""Stage A: shared formatting rules (A-08)."""
from __future__ import annotations

import unittest
from datetime import date, datetime

from fa.gui import formatting


class PercentTests(unittest.TestCase):
    def test_positive_has_plus_sign(self):
        self.assertEqual(formatting.pct(0.0019), "+0.19%")

    def test_negative_keeps_minus_sign(self):
        self.assertEqual(formatting.pct(-0.0019), "-0.19%")

    def test_zero_is_not_signed(self):
        self.assertEqual(formatting.pct(0.0), "0.00%")

    def test_coverage_uses_one_decimal(self):
        self.assertEqual(formatting.pct(0.953, digits=1), "+95.3%")

    def test_unsigned_percent(self):
        self.assertEqual(formatting.pct(0.953, digits=1, signed=False), "95.3%")

    def test_missing_is_em_dash_not_zero(self):
        self.assertEqual(formatting.pct(None), "—")
        self.assertEqual(formatting.pct(float("nan")), "—")
        self.assertEqual(formatting.pct("not a number"), "—")


class MoneyTests(unittest.TestCase):
    def test_thousands_separator(self):
        self.assertEqual(formatting.money(1234.5), "1,234.50")

    def test_negative_money(self):
        self.assertEqual(formatting.money(-12.345), "-12.35")

    def test_missing_money(self):
        self.assertEqual(formatting.money(None), "—")


class NumberTests(unittest.TestCase):
    def test_number_digits(self):
        self.assertEqual(formatting.number(3.14159), "3.14")

    def test_missing_number_is_not_blank(self):
        self.assertEqual(formatting.number(None), "—")
        self.assertNotEqual(formatting.number(None), "")


class DateTests(unittest.TestCase):
    def test_iso_date(self):
        self.assertEqual(formatting.date_text(date(2026, 11, 20)), "2026-11-20")

    def test_datetime_keeps_only_the_date(self):
        value = datetime(2026, 11, 20, 15, 30)
        self.assertEqual(formatting.date_text(value), "2026-11-20")

    def test_iso_string_is_truncated(self):
        self.assertEqual(formatting.date_text("2026-11-20T15:30:00Z"), "2026-11-20")

    def test_missing_date(self):
        self.assertEqual(formatting.date_text(None), "—")
        self.assertEqual(formatting.date_text(""), "—")


class StatusTests(unittest.TestCase):
    def test_known_statuses(self):
        self.assertEqual(formatting.status_text("partial"), "部分覆蓋")
        self.assertEqual(formatting.status_text("insufficient"), "資料不足")

    def test_unknown_status_is_echoed(self):
        self.assertEqual(formatting.status_text("weird"), "weird")

    def test_missing_status(self):
        self.assertEqual(formatting.status_text(None), "—")


class QualityTests(unittest.TestCase):
    def test_known_quality_levels(self):
        self.assertEqual(formatting.quality_text("low"), "偏低")
        self.assertEqual(formatting.quality_text("high"), "高")

    def test_missing_quality(self):
        self.assertEqual(formatting.quality_text(None), "—")


class ColorTests(unittest.TestCase):
    def test_positive_is_red_negative_is_green(self):
        self.assertEqual(formatting.color_for(0.1), "#c62828")
        self.assertEqual(formatting.color_for(-0.1), "#2e7d32")

    def test_missing_is_grey(self):
        self.assertEqual(formatting.color_for(None), "#777777")

    def test_colour_never_replaces_the_sign(self):
        # The colour is a hint; the text must still carry the sign.
        self.assertIn("+", formatting.pct(0.01))
        self.assertIn("-", formatting.pct(-0.01))


if __name__ == "__main__":
    unittest.main()
