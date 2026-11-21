# -*- coding: utf-8 -*-
"""Stage D: the fingerprint hashes per-session PIT inputs, not a row count (BT-14)."""
from __future__ import annotations

import dataclasses
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from fa.backtesting import DataAccess, RebalanceRule, StrategySpec, run_backtest
from fa.backtesting.engine import is_rebalance_day
from fa.backtesting.runner import (
    _grid,
    data_fingerprint,
    decision_instant,
    fingerprint_session_payload,
)
from fa.data.database.repository import Repository
from fa.models.market_data import PriceSeries
from fa.utils.calendars import calendar_for
from tests.fixtures.backtesting import (
    NOW,
    build_store,
    fx_series,
    hk_instrument,
    make_spec,
    price_point,
    price_series,
    trading_days,
    us_instrument,
)

UTC = NOW.tzinfo
LAG = 30


def next_us_session(after: datetime) -> date:
    """The first US session strictly after ``after`` (a stand-in for "today")."""
    day = after.date() + timedelta(days=1)
    calendar = calendar_for("US")
    while not calendar.is_trading_day(day):
        day = day + timedelta(days=1)
    return day


class FingerprintTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repository = Repository(Path(self._tmp.name) / "fingerprint.db")
        self.addCleanup(self.repository.close)
        self.store = build_store(self.repository, days=30)
        self.spec = make_spec(self.store, sessions=30)
        self.access = DataAccess(self.repository)

    @property
    def grid(self) -> tuple[date, ...]:
        return _grid(self.spec)

    def fingerprint(self, sessions=None, *, spec=None, benchmark=None) -> str:
        spec = spec or self.spec
        chosen = tuple(sessions or self.grid)
        return data_fingerprint(
            spec, self.access, sessions=chosen, lag_minutes=LAG,
            benchmark=benchmark)

    def revise_close(self, index: int, close: float) -> None:
        series = price_series(us_instrument(), self.store.days)
        points = list(series.points)
        target = points[index]
        points[index] = dataclasses.replace(
            target, close=close,
            adj_close=None if target.adj_close is None else close)
        self.repository.save_prices(PriceSeries.from_points(
            "LLY", points, market="US", currency="USD", source="fixture"))

    def stats(self) -> tuple[int, str, str, str]:
        row = self.repository._conn.execute(
            """
            SELECT COUNT(*) AS n, MIN(price_date) AS first, MAX(price_date) AS last,
                   MAX(fetched_at) AS fetched
            FROM market_prices WHERE ticker = ?
            """,
            ("LLY",),
        ).fetchone()
        return int(row["n"]), row["first"], row["last"], row["fetched"]

    def revision_count(self) -> int:
        return self.repository.count_price_revisions("LLY")

    def variant_fingerprint(
        self,
        *,
        start_index: int = 0,
        changed_index: int | None = None,
        close: float = 0.0,
        sessions: tuple[date, ...] | None = None,
    ) -> str:
        """Fingerprint of a second database that differs only in one old close.

        Prices are seeded with their historical availability, so the difference
        is genuinely a different *input*, not a revision published today.
        """
        with tempfile.TemporaryDirectory() as tmp:
            repository = Repository(Path(tmp) / "variant.db")
            try:
                series = price_series(us_instrument(), self.store.days)
                points = list(series.points)
                if changed_index is not None:
                    target = points[changed_index]
                    points[changed_index] = dataclasses.replace(
                        target, close=close,
                        adj_close=None if target.adj_close is None else close)
                repository.save_prices(PriceSeries.from_points(
                    "LLY", points, market="US", currency="USD", source="fixture"))
                repository.save_fx(fx_series(
                    "USD", self.store.days,
                    {day: 7.10 for day in self.store.days}))
                spec = dataclasses.replace(
                    self.spec, start=self.store.days[start_index])
                chosen = tuple(sessions) if sessions else _grid(spec)
                return data_fingerprint(
                    spec, DataAccess(repository), sessions=chosen,
                    lag_minutes=LAG)
            finally:
                repository.close()


class FingerprintContentTests(FingerprintTestCase):
    def test_history_before_the_run_start_is_not_hashed(self) -> None:
        # A FIXED/EQUAL engine only reads the session's own price, so a bar dated
        # before the run start is not an input at all (review round 6, P2-12).
        sessions = tuple(_grid(dataclasses.replace(
            self.spec, start=self.store.days[10])))
        self.assertEqual(
            self.variant_fingerprint(start_index=10, changed_index=2,
                                     close=100.0, sessions=sessions),
            self.variant_fingerprint(start_index=10, changed_index=2,
                                     close=999.0, sessions=sessions),
            "回測開始之前、未被任何決策使用的價格不應改變指紋")

    def test_a_used_session_price_moves_the_fingerprint(self) -> None:
        self.assertNotEqual(
            self.variant_fingerprint(changed_index=12, close=112.0),
            self.variant_fingerprint(changed_index=12, close=512.0),
            "回測期間實際使用的價格改變，指紋必須改變")

    def test_a_bar_published_for_a_future_session_changes_the_fingerprint(self) -> None:
        future = next_us_session(datetime.now(UTC))
        sessions = self.grid + (future,)
        before = self.fingerprint(sessions)
        early = self.fingerprint(self.grid)

        self.repository.save_prices(PriceSeries.from_points(
            "LLY", [price_point(future, 123.0)],
            market="US", currency="USD", source="fixture"))

        self.assertEqual(self.fingerprint(self.grid), early,
                         "新公布的價格不能影響更早的決策時刻")
        self.assertNotEqual(self.fingerprint(sessions), before,
                            "該 session 的決策時刻必須看到新價格")
        self.assertEqual(_grid(self.spec), self.grid)

    def test_a_missing_session_price_is_a_marker_a_later_bar_replaces(self) -> None:
        future = next_us_session(datetime.now(UTC))
        sessions = self.grid + (future,)
        before = self.fingerprint(sessions)
        self.repository.save_prices(PriceSeries.from_points(
            "LLY", [price_point(future, 77.0)],
            market="US", currency="USD", source="fixture"))
        self.assertNotEqual(self.fingerprint(sessions), before)

    def test_identical_redownload_does_not_change_fingerprint(self) -> None:
        before = self.fingerprint(self.grid)
        fetched_before = self.stats()[3]
        later = NOW + timedelta(minutes=1)
        series = price_series(us_instrument(), self.store.days)
        refreshed = [dataclasses.replace(point, fetched_at=later)
                     for point in series.points]
        self.repository.save_prices(PriceSeries.from_points(
            "LLY", refreshed, market="US", currency="USD", source="fixture"))
        self.assertNotEqual(
            self.stats()[3], fetched_before,
            "重新下載應更新 fetched_at（否則這個測試沒有測到東西）")
        self.assertEqual(
            self.fingerprint(self.grid), before,
            "只刷新抓取時間、沒有任何市場輸入改變時，指紋不應改變")

    def test_a_new_run_after_a_new_bar_gets_a_new_id(self) -> None:
        future = next_us_session(datetime.now(UTC))
        spec = dataclasses.replace(self.spec, end=future)
        first, _ = run_backtest(spec, self.repository, now=NOW)

        self.repository.save_prices(PriceSeries.from_points(
            "LLY", [price_point(future, 999.0)],
            market="US", currency="USD", source="fixture"))

        second, created = run_backtest(spec, self.repository, now=NOW)
        self.assertTrue(created)
        self.assertNotEqual(second.run_id, first.run_id)
        self.assertNotEqual(second.data_fingerprint, first.data_fingerprint)

    def test_identical_data_reuses_the_stored_run(self) -> None:
        first, _ = run_backtest(self.spec, self.repository, now=NOW)
        again, created = run_backtest(self.spec, self.repository, now=NOW)
        self.assertFalse(created)
        self.assertEqual(again.run_id, first.run_id)
        self.assertEqual(self.repository.count_backtest_runs(), 1)

    def test_the_fingerprint_depends_on_the_decision_instant(self) -> None:
        first_session = self.fingerprint(self.grid[:1])
        whole_window = self.fingerprint(self.grid)
        self.assertNotEqual(first_session, whole_window)


class FingerprintFxTests(FingerprintTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.spec = make_spec(
            self.store, sessions=30, universe=(us_instrument(),),
            weighting="fixed", target_weights={"LLY": 1.0})

    def revise_last_rate(self, rate: float) -> None:
        """Re-save USD/CNY: only the last session's rate changes."""
        rates = {day: 7.10 for day in self.store.days}
        rates[self.store.days[-1]] = rate
        self.repository.save_fx(fx_series("USD", self.store.days, rates))

    def test_a_rate_published_later_cannot_reach_an_earlier_session(self) -> None:
        future = next_us_session(datetime.now(UTC))
        early_before = self.fingerprint(self.grid)
        full_before = self.fingerprint(self.grid + (future,))

        rates = {day: 7.10 for day in self.store.days}
        rates[future] = 9.99
        self.repository.save_fx(
            fx_series("USD", [*self.store.days, future], rates))

        self.assertEqual(self.fingerprint(self.grid), early_before,
                         "新公布的匯率不能影響更早的決策時刻")
        self.assertNotEqual(self.fingerprint(self.grid + (future,)),
                            full_before,
                            "公布後的決策時刻應使用新匯率")

    def test_an_in_place_fx_revision_still_reaches_earlier_sessions(self) -> None:
        """Known limitation: FX has no version reconstruction (decision Q7).

        ``save_fx`` keeps a revised rate's original ``available_at``, so an
        in-place correction is visible to decision instants before it was made.
        Prices do not have this problem because ``save_prices`` pushes the new
        availability to ``replaced_at`` and ``prices_as_of`` reconstructs the old
        version from the ledger.  Recorded as a limit in spec §18.8.
        """
        early_before = self.fingerprint(self.grid)
        self.revise_last_rate(7.90)
        self.assertNotEqual(self.fingerprint(self.grid), early_before)


class BackendRunIdentityTests(FingerprintTestCase):
    def test_two_databases_with_different_histories_hash_differently(self) -> None:
        # Two databases whose bars agree except for one session the engine
        # actually reads must not share a digest (review round 6, P2-12).
        before = self.fingerprint(self.grid)
        changed = self.variant_fingerprint(changed_index=3, close=150.0)
        self.assertNotEqual(before, changed)


class StrategyFxInputTests(unittest.TestCase):
    """P1-10: the fingerprint hashes the FX the engine actually used."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repository = Repository(Path(self._tmp.name) / "fx-inputs.db")
        self.addCleanup(self.repository.close)
        self.access = DataAccess(self.repository)
        self.days = trading_days(12)
        self.us = us_instrument("LLY")
        self.repository.save_prices(price_series(self.us, self.days))
        self.spec = StrategySpec(
            spec_id="fx-inputs", universe=(self.us,), weighting="fixed",
            target_weights={"LLY": 1.0}, start=self.days[0],
            end=self.days[-1], initial_capital=100_000.0,
        )

    def fingerprint(self) -> str:
        return data_fingerprint(self.spec, self.access,
                                sessions=tuple(self.days[:4]), lag_minutes=LAG)

    def save_rates(self, rates: dict[date, float]) -> None:
        self.repository.save_fx(fx_series("USD", sorted(rates), rates))

    def other_fingerprint(self, rates: dict[date, float]) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            repository = Repository(Path(tmp) / "other.db")
            try:
                repository.save_prices(price_series(self.us, self.days))
                repository.save_fx(fx_series("USD", sorted(rates), rates))
                return data_fingerprint(
                    self.spec, DataAccess(repository),
                    sessions=tuple(self.days[:4]), lag_minutes=LAG)
            finally:
                repository.close()

    def test_an_observation_before_the_lookback_is_used_not_missing(self) -> None:
        old = self.days[0] - timedelta(days=40)
        self.save_rates({old: 7.20})
        before = self.fingerprint()
        self.save_rates({old: 7.35})  # the same observation, corrected
        self.assertNotEqual(self.fingerprint(), before,
                            "Engine 會用 lookback 之前的匯率，指紋必須記錄同一筆")

    def test_the_observation_date_is_part_of_the_digest(self) -> None:
        old = self.days[0] - timedelta(days=40)
        later = old + timedelta(days=1)
        with_first = self.other_fingerprint({old: 7.20})
        with_both = self.other_fingerprint({old: 7.20, later: 7.20})
        self.assertNotEqual(with_first, with_both,
                            "相同匯率、不同觀測日期必須區分")

    def test_a_missing_rate_stays_missing(self) -> None:
        without = self.fingerprint()
        old = self.days[0] - timedelta(days=40)
        self.save_rates({old: 7.20})
        self.assertNotEqual(self.fingerprint(), without)


class CrossMarketMomentumFingerprintTests(unittest.TestCase):
    """P1-11: the momentum window belongs to each instrument's own venue."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repository = Repository(Path(self._tmp.name) / "multi.db")
        self.addCleanup(self.repository.close)
        self.access = DataAccess(self.repository)
        self.days = trading_days(50)
        self.us = us_instrument("LLY")
        self.hk = hk_instrument("0700")
        self.repository.save_prices(price_series(self.us, self.days))
        self.sparse = self.days[::3]
        self.repository.save_prices(
            price_series(self.hk, self.sparse, base=400.0, step=2.0))
        self.repository.save_fx(
            fx_series("USD", self.days, {day: 7.10 for day in self.days}))
        self.repository.save_fx(
            fx_series("HKD", self.days, {day: 0.91 for day in self.days}))
        self.spec = StrategySpec(
            spec_id="cross-market", universe=(self.us, self.hk),
            weighting="momentum", momentum_window=20,
            start=self.days[24], end=self.days[-1],
            initial_capital=500_000.0,
            rebalance=RebalanceRule(frequency="monthly", anchor=1),
        )

    @property
    def sessions(self) -> tuple[date, ...]:
        return tuple(_grid(self.spec))

    def fingerprint(self) -> str:
        return data_fingerprint(self.spec, self.access,
                                sessions=self.sessions, lag_minutes=LAG)

    def _fingerprint_with(self, *, hk_first: float = 400.0,
                          us_first: float = 100.0) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            repository = Repository(Path(tmp) / "alt.db")
            try:
                us_series = price_series(self.us, self.days)
                us_points = list(us_series.points)
                us_points[0] = dataclasses.replace(
                    us_points[0], close=us_first,
                    adj_close=None if us_points[0].adj_close is None else us_first)
                repository.save_prices(PriceSeries.from_points(
                    "LLY", us_points, market="US", currency="USD",
                    source="fixture"))
                hk_series = price_series(self.hk, self.sparse, base=400.0, step=2.0)
                hk_points = list(hk_series.points)
                hk_points[0] = dataclasses.replace(
                    hk_points[0], close=hk_first,
                    adj_close=None if hk_points[0].adj_close is None else hk_first)
                repository.save_prices(PriceSeries.from_points(
                    "0700", hk_points, market="HK", currency="HKD",
                    source="fixture"))
                repository.save_fx(fx_series(
                    "USD", self.days, {day: 7.10 for day in self.days}))
                repository.save_fx(fx_series(
                    "HKD", self.days, {day: 0.91 for day in self.days}))
                return data_fingerprint(
                    self.spec, DataAccess(repository), sessions=self.sessions,
                    lag_minutes=LAG)
            finally:
                repository.close()

    def test_a_sparse_venue_keeps_its_whole_momentum_window(self) -> None:
        # The HK series has one observation every third weekday, so its 21st
        # observation reaches back before spec.start.  A global lookback taken
        # from one calendar would drop these rows.
        self.assertNotEqual(
            self._fingerprint_with(hk_first=400.0),
            self._fingerprint_with(hk_first=999.0),
            "觀測稀疏的市場，其較早観測值仍屬動能窗口，指紋不得漏掉")

    def test_history_outside_every_window_stays_out(self) -> None:
        # The US series is dense, so its first bar is older than any session's
        # 21-observation window and must not affect the digest.
        self.assertEqual(
            self._fingerprint_with(us_first=100.0),
            self._fingerprint_with(us_first=777.0),
            "任何決策時刻都用不到的歷史不應改變指紋")


class FixedStrategyFingerprintTests(FingerprintTestCase):
    """Review round 6 scenarios A/B/C/E: only consumed prices are hashed."""

    SATURDAY = date(2025, 1, 11)

    def _digest(self, *, changes: dict[int, float] | None = None,
                drop: date | None = None,
                extra_saturday: float | None = None) -> str:
        sessions = tuple(_grid(self.spec))
        with tempfile.TemporaryDirectory() as tmp:
            repository = Repository(Path(tmp) / "variant.db")
            try:
                points = list(price_series(us_instrument(), self.store.days).points)
                for index, close in (changes or {}).items():
                    target = points[index]
                    points[index] = dataclasses.replace(
                        target, close=close,
                        adj_close=None if target.adj_close is None else close)
                if drop is not None:
                    points = [p for p in points if p.price_date != drop]
                if extra_saturday is not None:
                    points.append(price_point(
                        self.SATURDAY, extra_saturday))
                repository.save_prices(PriceSeries.from_points(
                    "LLY", points, market="US", currency="USD",
                    source="fixture"))
                repository.save_fx(fx_series(
                    "USD", self.store.days,
                    {day: 7.10 for day in self.store.days}))
                return data_fingerprint(self.spec, DataAccess(repository),
                                        sessions=sessions, lag_minutes=LAG)
            finally:
                repository.close()

    def test_a_prices_before_the_run_start_do_not_change_the_fingerprint(self) -> None:
        spec = dataclasses.replace(self.spec, start=self.store.days[10])
        sessions = tuple(_grid(spec))
        base = data_fingerprint(spec, self.access, sessions=sessions,
                                lag_minutes=LAG)
        self.revise_close(2, 999.0)  # before the start, never read
        self.assertEqual(
            data_fingerprint(spec, self.access, sessions=sessions, lag_minutes=LAG),
            base, "回測開始前的價格不是任何決策的輸入")

    def test_b_a_used_session_price_changes_the_fingerprint(self) -> None:
        self.assertNotEqual(self._digest(changes={12: 112.0}),
                            self._digest(changes={12: 512.0}))

    def test_c_a_price_on_a_closed_day_is_not_hashed(self) -> None:
        self.assertEqual(
            self._digest(extra_saturday=100.0),
            self._digest(extra_saturday=999.0),
            "休市日的價格不是引擎輸入")

    def test_e_a_missing_session_price_is_a_stable_marker(self) -> None:
        self.assertNotEqual(self._digest(), self._digest(drop=self.store.days[5]))


class ClosedVenueMomentumFingerprintTests(unittest.TestCase):
    """Review round 7, P1-17: a shut venue still feeds the momentum window.

    ``_Engine._momentum_weights()`` asks every ticker for its series regardless
    of whether that venue traded that day, so a Hong Kong holiday inside a US
    rebalance session is still an input.  The fingerprint must hash it.
    """

    CLOSED = date(2025, 1, 29)   # HK holiday, US open, monthly anchor 29
    START = date(2025, 1, 6)
    END = date(2025, 3, 14)

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.us = us_instrument("LLY")
        self.hk = hk_instrument("0700")
        self.us_days = _sessions(date(2024, 12, 2), self.END, "US")
        self.hk_days = [day for day in _sessions(date(2024, 12, 2), self.END, "HK")
                        if day != self.CLOSED]
        self.spec = StrategySpec(
            spec_id="closed-venue-momentum",
            universe=(self.us, self.hk),
            weighting="momentum", momentum_window=20,
            start=self.START, end=self.END, initial_capital=500_000.0,
            rebalance=RebalanceRule(frequency="monthly", anchor=29),
        )
        self.grid = tuple(sorted(set(self.us_days) | set(self.hk_days)))
        self.grid = tuple(day for day in self.grid if self.START <= day <= self.END)
        self.repository, self.access = self._build()
        self.addCleanup(self.repository.close)

    def _build(self, *, hk_close: float | None = None):
        repository = Repository(Path(tempfile.mkdtemp()) / "venue.db")
        self.addCleanup(repository.close)
        repository.save_prices(price_series(self.us, self.us_days))
        hk_series = price_series(self.hk, self.hk_days, base=400.0, step=2.0)
        if hk_close is None:
            points = list(hk_series.points)
        else:
            points = list(hk_series.points)
            visible = [index for index, point in enumerate(points)
                       if point.price_date <= self.CLOSED]
            index = visible[-10]   # inside the 21-observation window at CLOSED
            target = points[index]
            points[index] = dataclasses.replace(
                target, close=hk_close,
                adj_close=None if target.adj_close is None else hk_close)
        repository.save_prices(PriceSeries.from_points(
            "0700", points, market="HK", currency="HKD", source="fixture"))
        repository.save_fx(fx_series(
            "USD", self.us_days, {day: 7.10 for day in self.us_days}))
        repository.save_fx(fx_series(
            "HKD", self.hk_days, {day: 0.91 for day in self.hk_days}))
        return repository, DataAccess(repository)

    def _fingerprint(self, access: DataAccess) -> str:
        return data_fingerprint(self.spec, access, sessions=self.grid,
                                lag_minutes=LAG)

    def _payload(self, access: DataAccess, session: date):
        rebalance = {day for day in self.grid
                     if is_rebalance_day(self.spec, day, self.grid)}
        instant = decision_instant(self.spec, session, lag_minutes=LAG)
        return fingerprint_session_payload(
            self.spec, access, session, instant=instant,
            rebalance_days=rebalance, window=20)

    def test_preconditions(self) -> None:
        self.assertFalse(calendar_for("HK").is_trading_day(self.CLOSED))
        self.assertTrue(calendar_for("US").is_trading_day(self.CLOSED))
        self.assertIn(self.CLOSED, self.grid)
        self.assertTrue(is_rebalance_day(self.spec, self.CLOSED, self.grid))
        visible = self.access.prices(
            self.hk, decision_instant(self.spec, self.CLOSED, lag_minutes=LAG),
            end=self.CLOSED).points
        self.assertGreaterEqual(len(visible), 21)

    def test_momentum_window_is_hashed_when_venue_is_closed(self) -> None:
        before = self._fingerprint(self.access)
        other, access = self._build(hk_close=999.0)
        after = self._fingerprint(access)
        self.assertNotEqual(before, after,
                            "休市市場的動能窗口仍是 Engine 的輸入，必須進入指紋")
        # The engine's own read path must see the changed bar, inside the tail
        # it consumes: otherwise this test would prove nothing about P1-17.
        instant = decision_instant(self.spec, self.CLOSED, lag_minutes=LAG)
        visible = access.prices(self.hk, instant, end=self.CLOSED).points[-21:]
        self.assertIn(999.0, [float(point.close) for point in visible])

    def test_roles_split_session_price_from_momentum_window(self) -> None:
        payload = self._payload(self.access, self.CLOSED)
        hk_roles = {row["role"] for row in payload["prices"]
                    if row["ticker"] == "0700"}
        us_roles = {row["role"] for row in payload["prices"]
                    if row["ticker"] == "LLY"}
        self.assertEqual(hk_roles, {"momentum_window"},
                         "休市市場不得產生 session_price，但必須有 momentum_window")
        self.assertEqual(us_roles, {"session_price", "momentum_window"})

        quiet = next(day for day in self.grid
                     if day > self.CLOSED
                     and not is_rebalance_day(self.spec, day, self.grid)
                     and not calendar_for("HK").is_trading_day(day))
        quiet_payload = self._payload(self.access, quiet)
        quiet_hk = {row["role"] for row in quiet_payload["prices"]
                    if row["ticker"] == "0700"}
        quiet_us = {row["role"] for row in quiet_payload["prices"]
                    if row["ticker"] == "LLY"}
        self.assertEqual(quiet_hk, set())
        self.assertEqual(quiet_us, {"session_price"})


def _sessions(start: date, end: date, market: str) -> list[date]:
    calendar = calendar_for(market)
    out: list[date] = []
    cursor = start
    while cursor <= end:
        if calendar.is_trading_day(cursor):
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
