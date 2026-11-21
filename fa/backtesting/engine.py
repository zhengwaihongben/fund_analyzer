# -*- coding: utf-8 -*-
"""The V2.3.2 strategy backtest engine (Stage C).

The engine simulates a long-only portfolio over a set of PIT-visible prices:
it decides on weights at ``session close + decision lag``, executes those
weights at the **next** session's close (queried at ``close + decision lag``,
i.e. after that close has become public), and records one equity point per
session.

Boundaries that make the simulation auditable:

* No database access.  Prices and FX come from :class:`DataAccess` only;
  ``engine.py`` never imports :class:`Repository` and never calls
  ``load_prices()`` (a test greps for it).
* No network, no clock, no randomness.  ``now`` never appears: the run is a
  function of the spec and the stored PIT data, so the same inputs replay
  identically.
* Costs are computed by :mod:`fa.backtesting.costs`; the engine only decides
  *when* to trade and *how much*.
* ``Trade.price_local`` stays the market price.  Slippage is recorded as a
  cost, not folded into the price, so it cannot be charged twice.
* A missing execution price or FX skips that order and records an anomaly; it
  never substitutes a nearby price.  Valuation may carry the last known price
  forward, but it says so in ``anomalies``.

Company actions follow spec §5.7: when the session's price point carries
``adj_close`` the series is treated as total return and no cash dividend is
credited (``adj_close`` already embeds it).  When only the raw ``close``
exists, the point's ``dividend`` is credited on its ex-date and ``split``
scales the held quantity; such a series is reported once per ticker as a
``raw-price-fallback`` anomaly so a run never silently mixes the two modes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import floor

from fa.backtesting.costs import calculate_trade_costs, round_money, round_quantity
from fa.backtesting.data_access import BacktestDataMissingError, DataAccess
from fa.backtesting.models import (
    EquityPoint,
    Position,
    RebalanceFrequency,
    StrategySpec,
    Trade,
    Weighting,
)
from fa.utils.calendars import calendar_for

#: Default distance between a session's close and the instant a decision may use it.
DEFAULT_DECISION_LAG_MINUTES = 30
#: Orders decided at session *t* execute at session *t + N*.
DEFAULT_EXECUTION_LAG_SESSIONS = 1

#: Rebalance months for ``quarterly``.
_QUARTER_MONTHS = (1, 4, 7, 10)


def is_rebalance_day(
    spec: StrategySpec, session: date, sessions: tuple[date, ...]
) -> bool:
    """Whether ``session`` triggers a rebalance under ``spec.rebalance``.

    Shared by the engine and the fingerprint: the hash must describe the
    momentum windows a rebalance day actually consumes, so both sides have to
    agree on which days those are (review round 6, P2-12).  The rule itself is
    the one the engine always used — an anchor day that is not a trading day
    falls back to the last session of that week/month, and ``quarterly`` only
    rebalances in the four quarter-opening months.
    """
    rule = spec.rebalance
    frequency = rule.frequency
    if frequency is RebalanceFrequency.DAILY:
        return True
    anchor = rule.anchor
    if frequency is RebalanceFrequency.WEEKLY:
        week = session.isocalendar()[:2]
        days = [day for day in sessions if day.isocalendar()[:2] == week]
        candidates = [day for day in days if day.weekday() >= anchor]
        return session == (candidates[0] if candidates else days[-1])
    month = (session.year, session.month)
    days = [day for day in sessions if (day.year, day.month) == month]
    if frequency is RebalanceFrequency.QUARTERLY and session.month not in _QUARTER_MONTHS:
        return False
    candidates = [day for day in days if day.day >= anchor]
    return session == (candidates[0] if candidates else days[-1])


@dataclass(frozen=True)
class EngineResult:
    """Everything a run produces before metrics or persistence."""

    trades: tuple[Trade, ...]
    equity_curve: tuple[EquityPoint, ...]
    final_positions: tuple[Position, ...]
    anomalies: tuple[str, ...]


def run_engine(
    spec: StrategySpec,
    data_access: DataAccess,
    *,
    decision_lag_minutes: int = DEFAULT_DECISION_LAG_MINUTES,
    execution_lag_sessions: int = DEFAULT_EXECUTION_LAG_SESSIONS,
    fractional_shares: bool = True,
) -> EngineResult:
    """Simulate ``spec`` against ``data_access`` and return the raw outcome."""
    if not isinstance(spec, StrategySpec):
        raise TypeError(f"spec 必須是 StrategySpec: {spec!r}")
    if not isinstance(data_access, DataAccess):
        raise TypeError(f"data_access 必須是 DataAccess: {data_access!r}")
    if decision_lag_minutes < 0:
        raise ValueError(f"decision_lag_minutes 不可為負數: {decision_lag_minutes!r}")
    if execution_lag_sessions < 1:
        raise ValueError(f"execution_lag_sessions 至少為 1: {execution_lag_sessions!r}")
    engine = _Engine(
        spec,
        data_access,
        decision_lag_minutes=decision_lag_minutes,
        execution_lag_sessions=execution_lag_sessions,
        fractional_shares=fractional_shares,
    )
    return engine.run()


class _Engine:
    def __init__(
        self,
        spec: StrategySpec,
        data_access: DataAccess,
        *,
        decision_lag_minutes: int,
        execution_lag_sessions: int,
        fractional_shares: bool,
    ) -> None:
        self.spec = spec
        self.data = data_access
        self.lag = timedelta(minutes=decision_lag_minutes)
        self.execution_lag = execution_lag_sessions
        self.fractional = fractional_shares

        self.instruments = {instrument.ticker: instrument for instrument in spec.universe}
        self.tickers = [instrument.ticker for instrument in spec.universe]
        self.calendars = {
            market: calendar_for(market) for market in sorted({i.market for i in spec.universe})
        }

        self.grid = self._build_grid()
        self.positions: dict[str, float] = {ticker: 0.0 for ticker in self.tickers}
        self.cash = round_money(spec.initial_capital)
        self.trades: list[Trade] = []
        self.curve: list[EquityPoint] = []
        self.anomalies: list[str] = []

        self._point_cache: dict[tuple[str, date], object] = {}
        self._fx_cache: dict[tuple[str, date], tuple[float, date] | None] = {}
        self._last_price: dict[str, tuple[float, date, str]] = {}
        self._last_fx: dict[str, float] = {}
        self._seen_adj: set[str] = set()
        self._reported_raw: set[str] = set()
        self._peak = 0.0
        self.pending: dict[str, float] | None = None
        self.pending_index: int | None = None

    # ---------------------------------------------------------------- grid
    def _build_grid(self) -> list[date]:
        """Union of the trading days of every market in the universe.

        ``HolidayCalendar.trading_days_between()`` returns a *count*, not the
        days, so the grid walks the date range and asks each calendar whether
        the day trades.  A union (not an intersection) is required: a US-only
        holiday must not stall a Hong Kong position.
        """
        days: set[date] = set()
        for calendar in self.calendars.values():
            cursor = self.spec.start
            while cursor <= self.spec.end:
                if calendar.is_trading_day(cursor):
                    days.add(cursor)
                cursor += timedelta(days=1)
        return sorted(days)

    def _decision_instant(self, session: date) -> datetime | None:
        closes = []
        for calendar in self.calendars.values():
            entry = calendar.session(session)
            if entry is not None:
                closes.append(entry.close)
        if not closes:
            return None
        return max(closes) + self.lag

    # -------------------------------------------------------------- helpers
    def _anomaly(self, message: str) -> None:
        if message not in self.anomalies:
            self.anomalies.append(message)

    def _market_trades(self, instrument, session: date) -> bool:
        """Whether the instrument's own venue is open on ``session``.

        The grid is the union of every venue in the universe, so a US holiday
        can fall inside a Hong Kong session.  A missing price on such a day is
        expected, not a data problem, and must not surface as an anomaly.
        """
        return self.calendars[instrument.market].is_trading_day(session)

    def _point(self, instrument, session: date):
        key = (instrument.ticker, session)
        if key not in self._point_cache:
            as_of = self._decision_instant(session)
            point = None
            if as_of is not None and self._market_trades(instrument, session):
                try:
                    point = self.data.price_at(instrument, as_of, session)
                except BacktestDataMissingError:
                    point = None
            self._point_cache[key] = point
        return self._point_cache[key]

    def _fx(self, instrument, session: date) -> tuple[float, date] | None:
        if instrument.currency == "CNY":
            return 1.0, session
        key = (instrument.currency, session)
        if key not in self._fx_cache:
            as_of = self._decision_instant(session)
            entry = None
            if as_of is not None:
                try:
                    entry = self.data.fx_rate(instrument.currency, as_of, session)
                except BacktestDataMissingError:
                    entry = None
            self._fx_cache[key] = entry
        return self._fx_cache[key]

    @staticmethod
    def _execution_price(point) -> tuple[float, str]:
        """The price a trade fills at: the session's official close.

        Specification §5.3: "The simulated fill price is that session's official
        close."  ``adj_close`` is a total-return series adjusted for dividends
        and splits, so it must never stand in for the traded price -- a 100.00
        close with a 95.00 adjusted close is a 100.00 fill, not a 95.00 one.
        """
        return float(point.close), "close"

    @staticmethod
    def _valuation_price(point) -> tuple[float, str]:
        """The price used to mark a position: adjusted close when available.

        ``adj_close`` embeds dividends and splits, so valuation and momentum use
        it; the raw close is the fallback that makes the corporate-action path
        necessary at all (a raw series has to credit its own dividends).
        """
        if point.adj_close is not None:
            return float(point.adj_close), "adj_close"
        return float(point.close), "close"

    def _valuation(self, instrument, session: date) -> tuple[float, float] | None:
        """(price_local, fx) for marking to market, carrying the last known price."""
        ticker = instrument.ticker
        if not self._market_trades(instrument, session):
            if ticker in self._last_price and ticker in self._last_fx:
                return self._last_price[ticker][0], self._last_fx[ticker]
            return None
        point = self._point(instrument, session)
        if point is not None:
            price, price_source = self._valuation_price(point)
            fx_entry = self._fx(instrument, session)
            if fx_entry is None:
                self._anomaly(
                    f"missing-fx: {ticker} 在 {session} 沒有可用匯率，持倉估值忽略當日變動")
                if ticker in self._last_price and ticker in self._last_fx:
                    return self._last_price[ticker][0], self._last_fx[ticker]
                return None
            self._last_price[ticker] = (price, session, price_source)
            self._last_fx[ticker] = fx_entry[0]
            return price, fx_entry[0]
        if ticker in self._last_price and ticker in self._last_fx:
            price, day, _ = self._last_price[ticker]
            self._anomaly(
                f"stale-valuation: {ticker} 在 {session} 沒有新價格，以 {day} 的價格估值")
            return price, self._last_fx[ticker]
        return None

    def _holdings_value(self, session: date) -> float:
        total = 0.0
        for ticker in self.tickers:
            quantity = self.positions[ticker]
            if quantity <= 0.0:
                continue
            instrument = self.instruments[ticker]
            valuation = self._valuation(instrument, session)
            if valuation is None:
                self._anomaly(f"missing-price: {ticker} 在 {session} 沒有任何可見價格，估值為 0")
                continue
            price, fx = valuation
            total += quantity * price * fx
        return total

    # ------------------------------------------------------ company actions
    def _apply_corporate_actions(self, session: date) -> None:
        for ticker in self.tickers:
            quantity = self.positions[ticker]
            if quantity <= 0.0:
                continue
            instrument = self.instruments[ticker]
            point = self._point(instrument, session)
            if point is None:
                continue
            price, price_source = self._valuation_price(point)
            if price_source == "adj_close":
                self._seen_adj.add(ticker)
                continue
            if ticker in self._seen_adj:
                self._anomaly(
                    f"mixed-price-mode: {ticker} 在 {session} 只有原始收盤價，"
                    "但先前的估值用過 adj_close；本引擎逐日處理，請避免混用同一序列的兩種價格")
            if ticker not in self._reported_raw:
                self._reported_raw.add(ticker)
                self._anomaly(
                    f"raw-price-fallback: {ticker} 沒有 adj_close，改用原始收盤價並以 "
                    "dividend/split 處理公司行動")
            fx_entry = self._fx(instrument, session)
            if point.dividend:
                if fx_entry is None:
                    # No published rate means no CNY amount.  Assuming 1.0 would
                    # invent cash in a currency that was never observed.
                    self._anomaly(
                        f"missing-fx-dividend: {ticker} 在 {session} 沒有可用匯率，"
                        "股息未計入 CNY 現金")
                else:
                    self.cash = round_money(
                        self.cash + quantity * float(point.dividend) * fx_entry[0])
            if point.split and float(point.split) != 1.0:
                self.positions[ticker] = round_quantity(quantity * float(point.split))

    # -------------------------------------------------------------- strategy
    def _rebalance_day(self, session: date) -> bool:
        return is_rebalance_day(self.spec, session, self.grid)

    def _target_weights(self, session: date, equity: float) -> dict[str, float] | None:
        weighting = self.spec.weighting
        if weighting is Weighting.EQUAL:
            share = 1.0 / len(self.tickers)
            return {ticker: share for ticker in self.tickers}
        if weighting is Weighting.FIXED:
            return {ticker: float(self.spec.target_weights.get(ticker, 0.0))
                    for ticker in self.tickers}
        return self._momentum_weights(session)

    def _momentum_weights(self, session: date) -> dict[str, float]:
        window = int(self.spec.momentum_window or 0)
        as_of = self._decision_instant(session)
        positive: dict[str, float] = {}
        if as_of is None:
            return {ticker: 0.0 for ticker in self.tickers}
        for ticker in self.tickers:
            instrument = self.instruments[ticker]
            series = self.data.prices(instrument, as_of, end=session)
            points = list(series.points)
            if len(points) < window + 1:
                self._anomaly(
                    f"momentum-excluded: {ticker} 在 {session} 只有 {len(points)} 個可見價格，"
                    f"需要 {window + 1} 個")
                continue
            start = points[-(window + 1)]
            latest = points[-1]
            start_price, _ = self._valuation_price(start)
            latest_price, _ = self._valuation_price(latest)
            if not start_price:
                continue
            momentum = latest_price / start_price - 1.0
            positive[ticker] = max(momentum, 0.0)
        total = sum(positive.values())
        if total <= 0.0:
            return {ticker: 0.0 for ticker in self.tickers}
        return {ticker: positive.get(ticker, 0.0) / total for ticker in self.tickers}

    def _current_weights(self, session: date, equity: float) -> dict[str, float]:
        weights = {}
        for ticker in self.tickers:
            quantity = self.positions[ticker]
            if quantity <= 0.0 or equity <= 0.0:
                weights[ticker] = 0.0
                continue
            valuation = self._valuation(self.instruments[ticker], session)
            value = 0.0 if valuation is None else quantity * valuation[0] * valuation[1]
            weights[ticker] = value / equity
        return weights

    def _needs_rebalance(self, session: date, targets: dict[str, float], equity: float) -> bool:
        threshold = self.spec.drift_threshold
        if threshold <= 0.0:
            return True
        current = self._current_weights(session, equity)
        drift = max(abs(targets[ticker] - current[ticker]) for ticker in self.tickers)
        return drift > threshold

    # ------------------------------------------------------------- execution
    def _execute(self, session: date, targets: dict[str, float]) -> None:
        equity = self.cash + self._holdings_value(session)
        if equity <= 0.0:
            self._anomaly(f"empty-portfolio: {session} 的總資產為 0，跳過交易")
            return
        sells: list[tuple[str, float]] = []
        buys: list[tuple[str, float]] = []
        for ticker in self.tickers:
            instrument = self.instruments[ticker]
            if not self._market_trades(instrument, session):
                # The venue is closed.  On a union grid this is normal, so the
                # order is skipped without a data-quality complaint.  Carrying it
                # to the venue's next session would redefine §5.3's "next session".
                continue
            point = self._point(instrument, session)
            if point is None:
                self._anomaly(f"missing-price: {ticker} 在 {session} 沒有成交價格，跳過訂單")
                continue
            fx_entry = self._fx(instrument, session)
            if fx_entry is None:
                self._anomaly(f"missing-fx: {ticker} 在 {session} 沒有匯率，跳過訂單")
                continue
            price, _ = self._execution_price(point)
            fx = fx_entry[0]
            unit_value = price * fx
            if unit_value <= 0.0:
                self._anomaly(f"invalid-price: {ticker} 在 {session} 的價格為 {price!r}，跳過訂單")
                continue
            target_value = equity * targets.get(ticker, 0.0)
            delta_value = target_value - self.positions[ticker] * unit_value
            if delta_value < 0.0:
                sells.append((ticker, delta_value))
            elif delta_value > 0.0:
                buys.append((ticker, delta_value))
        for ticker, delta_value in sells:
            self._sell(session, ticker, delta_value)
        for ticker, delta_value in buys:
            self._buy(session, ticker, delta_value)

    def _sell(self, session: date, ticker: str, delta_value: float) -> None:
        instrument = self.instruments[ticker]
        point = self._point(instrument, session)
        fx_entry = self._fx(instrument, session)
        if point is None or fx_entry is None:
            return
        price, price_source = self._execution_price(point)
        fx, fx_date = fx_entry
        quantity = min(self.positions[ticker], (-delta_value) / (price * fx))
        if quantity <= 0.0:
            return
        if not self.fractional:
            quantity = floor(quantity)
        quantity = round_quantity(quantity)
        if quantity <= 0.0:
            return
        if round_quantity(self.positions[ticker] - quantity) == self.positions[ticker]:
            return
        gross = quantity * price * fx
        costs = calculate_trade_costs(gross, instrument.currency != "CNY", self.spec.costs).rounded()
        net = round_money(gross - costs.total_base)
        if net <= 0.0:
            self._anomaly(
                f"costs-exceed-proceeds: {ticker} 在 {session} 的賣出所得低於交易成本，取消賣出")
            return
        self.positions[ticker] = round_quantity(self.positions[ticker] - quantity)
        self.cash = round_money(self.cash + net)
        self.trades.append(Trade(
            session=session,
            ticker=ticker,
            side="sell",
            quantity=quantity,
            price_local=price,
            price_source=price_source,
            fx_rate=fx,
            fx_source=f"pit:{fx_date.isoformat()}",
            gross_base=round_money(gross),
            commission_base=costs.commission_base,
            slippage_base=costs.slippage_base,
            fx_spread_base=costs.fx_spread_base,
            net_base=net,
            reason="rebalance",
        ))

    def _required_cash(self, gross: float, is_foreign: bool) -> float:
        costs = calculate_trade_costs(gross, is_foreign, self.spec.costs).rounded()
        return round_money(gross + costs.total_base)

    def _buy(self, session: date, ticker: str, delta_value: float) -> None:
        instrument = self.instruments[ticker]
        point = self._point(instrument, session)
        fx_entry = self._fx(instrument, session)
        if point is None or fx_entry is None:
            return
        price, price_source = self._execution_price(point)
        fx, fx_date = fx_entry
        unit_value = price * fx
        wanted = delta_value / unit_value
        if wanted <= 0.0:
            return
        cash_limited = False
        if self._required_cash(wanted * unit_value, instrument.currency != "CNY") > self.cash:
            cash_limited = True
            low, high = 0.0, wanted
            for _ in range(80):
                middle = (low + high) / 2.0
                if self._required_cash(middle * unit_value, instrument.currency != "CNY") <= self.cash:
                    low = middle
                else:
                    high = middle
            wanted = low
            self._anomaly(f"cash-limited: {ticker} 在 {session} 的買入量受現金限制而縮減")
        if not self.fractional:
            wanted = floor(wanted)
        elif cash_limited:
            # Floor to the persisted quantum: the bisection solves for an exact
            # cash balance and its result can round *up* by a quantum.
            wanted = floor(wanted * 1_000_000) / 1_000_000
        quantity = round_quantity(wanted)
        if quantity <= 0.0:
            return
        gross = quantity * unit_value
        costs = calculate_trade_costs(gross, instrument.currency != "CNY", self.spec.costs).rounded()
        net = round_money(gross + costs.total_base)
        for _ in range(100):
            if net <= self.cash:
                break
            quantity = round_quantity(quantity - 1e-6)
            if quantity <= 0.0:
                return
            gross = quantity * unit_value
            costs = calculate_trade_costs(
                gross, instrument.currency != "CNY", self.spec.costs).rounded()
            net = round_money(gross + costs.total_base)
        if net > self.cash:
            self._anomaly(f"cash-limited: {ticker} 在 {session} 的買入在四捨五入後超出現金，取消")
            return
        if round_quantity(self.positions[ticker] + quantity) == self.positions[ticker]:
            return
        self.positions[ticker] = round_quantity(self.positions[ticker] + quantity)
        self.cash = round_money(self.cash - net)
        if self.cash < 0.0:
            self.cash = 0.0
        self.trades.append(Trade(
            session=session,
            ticker=ticker,
            side="buy",
            quantity=quantity,
            price_local=price,
            price_source=price_source,
            fx_rate=fx,
            fx_source=f"pit:{fx_date.isoformat()}",
            gross_base=round_money(gross),
            commission_base=costs.commission_base,
            slippage_base=costs.slippage_base,
            fx_spread_base=costs.fx_spread_base,
            net_base=net,
            reason="rebalance",
        ))

    # ------------------------------------------------------------------ loop
    def run(self) -> EngineResult:
        for index, session in enumerate(self.grid):
            self._apply_corporate_actions(session)
            if self.pending is not None and self.pending_index == index:
                targets, self.pending, self.pending_index = self.pending, None, None
                self._execute(session, targets)

            holdings = self._holdings_value(session)
            total = round_money(self.cash + holdings)
            self._peak = max(self._peak, total)
            drawdown = None if self._peak <= 0.0 else total / self._peak - 1.0
            exposure = 0.0 if total <= 0.0 else holdings / total
            self.curve.append(EquityPoint(
                session=session,
                cash_base=self.cash,
                holdings_base=round_money(holdings),
                total_base=total,
                exposure=exposure,
                benchmark_base=None,
                drawdown=drawdown,
            ))

            if not self._rebalance_day(session):
                continue
            equity = total
            targets = self._target_weights(session, equity)
            if targets is None:
                continue
            if not self._needs_rebalance(session, targets, equity):
                continue
            next_index = index + self.execution_lag
            if next_index >= len(self.grid):
                continue
            self.pending = dict(targets)
            self.pending_index = next_index

        final_positions = []
        if self.grid:
            last = self.grid[-1]
            for ticker in self.tickers:
                quantity = self.positions[ticker]
                if quantity <= 0.0:
                    continue
                instrument = self.instruments[ticker]
                valuation = self._valuation(instrument, last)
                price = 0.0 if valuation is None else valuation[0]
                fx = 1.0 if valuation is None else valuation[1]
                final_positions.append(Position(
                    ticker=ticker,
                    quantity=quantity,
                    price=price,
                    market_value_base=round_money(quantity * price * fx),
                ))
        return EngineResult(
            trades=tuple(self.trades),
            equity_curve=tuple(self.curve),
            final_positions=tuple(final_positions),
            anomalies=tuple(self.anomalies),
        )


__all__ = [
    "DEFAULT_DECISION_LAG_MINUTES",
    "DEFAULT_EXECUTION_LAG_SESSIONS",
    "EngineResult",
    "run_engine",
]
