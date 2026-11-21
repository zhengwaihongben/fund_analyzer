# -*- coding: utf-8 -*-
"""Frozen dataclasses for the V2.3.2 strategy backtest (Stage B).

This module is deliberately inert: no I/O, no SQL, no clock.  It defines the
vocabulary the engine (Stage C) and the runner (Stage D) share, and it rejects
impossible specifications early so a partial spec can never reach a simulation.

The validation rules follow ``docs/technical_specification/V2/10_v232_backtest_engine.md``
§4.1: ``universe`` is non-empty and unique, fixed weights sum to one within
``1e-6``, a momentum window needs at least 20 sessions, the rebalance anchor
matches the frequency, the base currency is CNY and ``initial_capital`` is an
explicit positive number.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import Enum
from math import isfinite
from types import MappingProxyType
from typing import Any, Mapping

#: Tolerance for a fixed-weight portfolio to count as fully invested.
WEIGHT_TOLERANCE = 1e-6

#: Smallest momentum lookback the specification allows; below this the signal
#: is noise rather than momentum.
MIN_MOMENTUM_WINDOW = 20


class BacktestSpecError(ValueError):
    """Raised when a :class:`StrategySpec` cannot be simulated as written."""


def _require_finite(value: Any, label: str) -> float:
    """Coerce ``value`` to ``float`` and reject NaN/inf.

    ``NaN < 0`` is ``False``, so the sign checks alone would let a NaN or an
    infinity into the simulation and poison every downstream total.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise BacktestSpecError(f"{label} 必須是數值: {value!r}") from None
    if not isfinite(number):
        raise BacktestSpecError(f"{label} 必須是有限數值（不可為 NaN/Infinity）: {value!r}")
    return number


class Weighting(str, Enum):
    """How target weights are produced on a rebalance date."""

    EQUAL = "equal"
    FIXED = "fixed"
    MOMENTUM = "momentum"


class RebalanceFrequency(str, Enum):
    """How often the portfolio is allowed to rebalance."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


@dataclass(frozen=True)
class Instrument:
    """One tradable line: ticker, venue and quote currency.

    ``currency`` is the currency the price is quoted in, never the base
    currency: CNY is where the portfolio is measured, not something it holds.
    """

    ticker: str
    market: str
    currency: str

    def __post_init__(self) -> None:
        ticker = str(self.ticker or "").strip().upper()
        market = str(self.market or "").strip().upper()
        currency = str(self.currency or "").strip().upper()
        if not ticker:
            raise BacktestSpecError("Instrument.ticker 不可為空")
        if not market:
            raise BacktestSpecError(
                f"Instrument({ticker}) 需要明確的交易所市場；"
                "空值會讓市場一致性檢查失效（fail-open）")
        if not currency:
            raise BacktestSpecError(f"Instrument({ticker}) 需要明確的計價貨幣")
        if currency == "CNY":
            raise BacktestSpecError(
                f"Instrument({ticker}) 不可用 CNY：人民幣是計價基準，不是持倉幣別")
        object.__setattr__(self, "ticker", ticker)
        object.__setattr__(self, "market", market)
        object.__setattr__(self, "currency", currency)

    @property
    def needs_fx(self) -> bool:
        """Whether valuing this instrument requires an FX rate."""
        return self.currency != "CNY"


@dataclass(frozen=True)
class RebalanceRule:
    """Frequency plus the anchor that decides *which* session rebalances.

    * ``daily`` — no anchor.
    * ``weekly`` — ``anchor`` is a weekday, ``0`` (Monday) to ``4`` (Friday).
    * ``monthly`` / ``quarterly`` — ``anchor`` is a day of the month, ``1``–``31``;
      when the month is shorter the last session on or before it is used.
    """

    frequency: RebalanceFrequency = RebalanceFrequency.MONTHLY
    anchor: int | None = 1

    def __post_init__(self) -> None:
        frequency = RebalanceFrequency(self.frequency)
        object.__setattr__(self, "frequency", frequency)
        if frequency is RebalanceFrequency.DAILY:
            object.__setattr__(self, "anchor", None)
            return
        if self.anchor is None:
            raise BacktestSpecError(f"{frequency.value} 調倉需要 anchor")
        anchor = int(self.anchor)
        if frequency is RebalanceFrequency.WEEKLY:
            if not 0 <= anchor <= 4:
                raise BacktestSpecError(
                    f"weekly 的 anchor 必須是 0–4（週一至週五）: {anchor}")
        elif frequency in (RebalanceFrequency.MONTHLY, RebalanceFrequency.QUARTERLY):
            if not 1 <= anchor <= 31:
                raise BacktestSpecError(
                    f"{frequency.value} 的 anchor 必須是 1–31（每月第幾天）: {anchor}")
        object.__setattr__(self, "anchor", anchor)


@dataclass(frozen=True)
class CostModel:
    """Trading costs, all in the base currency unless stated per side.

    ``commission_rate`` is proportional to the gross notional with
    ``min_commission`` as a floor per trade; ``slippage_bps`` widens the fill
    against the trade; ``fx_spread_bps`` is charged only on the currency
    conversion part of a foreign trade.
    """

    commission_rate: float = 0.0005
    min_commission: float = 0.0
    slippage_bps: float = 10.0
    fx_spread_bps: float = 20.0

    def __post_init__(self) -> None:
        for name in ("commission_rate", "min_commission", "slippage_bps", "fx_spread_bps"):
            value = _require_finite(getattr(self, name), "CostModel." + name)
            if not value >= 0.0:
                raise BacktestSpecError(f"CostModel.{name} 不可為負數: {value!r}")
            object.__setattr__(self, name, value)
        if self.commission_rate > 0.1:
            raise BacktestSpecError(
                f"CostModel.commission_rate 不合理（>10%）: {self.commission_rate!r}")
        if self.slippage_bps > 1_000.0 or self.fx_spread_bps > 1_000.0:
            raise BacktestSpecError("CostModel 的 bps 值不可超過 1000（= 10%）")


#: The "costs were not specified" sentinel (review P2).  It is a real
#: :class:`CostModel`, so every consumer can read it directly, but the runner
#: compares by identity: only this exact instance means "let the environment
#: decide"; ``CostModel()`` passed by the caller is an explicit choice.
COST_MODEL_DEFAULT = CostModel()


@dataclass(frozen=True)
class StrategySpec:
    """A complete, validated strategy definition.

    Every field participates in the canonical JSON used for ``run_id``; there
    are therefore **no defaults** for the fields that change results —
    ``initial_capital`` in particular must be supplied explicitly (see §4.4).

    ``costs`` defaults to the module-level :data:`COST_MODEL_DEFAULT` sentinel
    instance.  The runner tells "the caller said nothing" apart from "the caller
    explicitly asked for the default costs" by identity, so an explicit
    ``CostModel()`` wins over the environment defaults (review P2) while an
    omitted one lets ``FA_BACKTEST_*`` apply.
    """

    spec_id: str
    universe: tuple[Instrument, ...]
    weighting: Weighting
    start: date
    end: date
    initial_capital: float
    rebalance: RebalanceRule = field(default_factory=RebalanceRule)
    target_weights: Mapping[str, float] = field(default_factory=dict)
    momentum_window: int | None = None
    drift_threshold: float = 0.02
    base_currency: str = "CNY"
    benchmark: str | None = None
    costs: CostModel = field(default=COST_MODEL_DEFAULT)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        spec_id = str(self.spec_id or "").strip()
        if not spec_id:
            raise BacktestSpecError("StrategySpec.spec_id 不可為空")
        object.__setattr__(self, "spec_id", spec_id)

        weighting = Weighting(self.weighting)
        object.__setattr__(self, "weighting", weighting)

        universe = tuple(self.universe or ())
        if not universe:
            raise BacktestSpecError("StrategySpec.universe 不可為空")
        tickers = [instrument.ticker for instrument in universe]
        if len(set(tickers)) != len(tickers):
            duplicates = sorted({t for t in tickers if tickers.count(t) > 1})
            raise BacktestSpecError(
                "StrategySpec.universe 有重複的 ticker: " + ", ".join(duplicates))
        object.__setattr__(self, "universe", universe)

        if not isinstance(self.start, date) or not isinstance(self.end, date):
            raise BacktestSpecError("StrategySpec.start/end 必須是 date")
        if self.start > self.end:
            raise BacktestSpecError(
                f"StrategySpec.start ({self.start}) 不可晚於 end ({self.end})")

        capital = _require_finite(self.initial_capital, "StrategySpec.initial_capital")
        if not capital > 0.0:
            raise BacktestSpecError(
                f"StrategySpec.initial_capital 必須大於零: {capital!r}")
        object.__setattr__(self, "initial_capital", capital)

        base = str(self.base_currency or "").strip().upper()
        if base != "CNY":
            raise BacktestSpecError(
                f"V2.3.2 只支援 CNY 計價基準，收到 {self.base_currency!r}")
        object.__setattr__(self, "base_currency", base)

        drift = _require_finite(self.drift_threshold, "StrategySpec.drift_threshold")
        if not 0.0 <= drift <= 1.0:
            raise BacktestSpecError(
                f"StrategySpec.drift_threshold 必須在 0–1: {drift!r}")
        object.__setattr__(self, "drift_threshold", drift)

        object.__setattr__(self, "rebalance", _as_rule(self.rebalance))
        object.__setattr__(self, "costs", _as_costs(self.costs))

        benchmark = self.benchmark
        if benchmark is not None:
            benchmark = str(benchmark).strip().upper() or None
        object.__setattr__(self, "benchmark", benchmark)

        self._validate_weighting(tickers)

    # -- validation helpers ---------------------------------------------------
    def _validate_weighting(self, tickers: list[str]) -> None:
        weighting = self.weighting
        if weighting is Weighting.FIXED:
            weights = {str(k).strip().upper(): float(v)
                       for k, v in dict(self.target_weights or {}).items()}
            unknown = sorted(set(weights) - set(tickers))
            missing = sorted(set(tickers) - set(weights))
            if unknown:
                raise BacktestSpecError(
                    "target_weights 含 universe 以外的 ticker: " + ", ".join(unknown))
            if missing:
                raise BacktestSpecError(
                    "fixed 權重缺少 ticker: " + ", ".join(missing))
            total = sum(weights.values())
            if abs(total - 1.0) > WEIGHT_TOLERANCE:
                raise BacktestSpecError(
                    f"fixed 權重必須合計為 1（誤差 ≤ {WEIGHT_TOLERANCE:g}），目前為 {total!r}")
            for ticker, weight in weights.items():
                if not weight >= 0.0:
                    raise BacktestSpecError(
                        f"target_weights[{ticker}] 不可為負數: {weight!r}")
            object.__setattr__(self, "target_weights", MappingProxyType(weights))
            object.__setattr__(self, "momentum_window", None)
            return
        if dict(self.target_weights or {}):
            raise BacktestSpecError(
                f"{weighting.value} 權重由規則決定，不可同時提供 target_weights")
        object.__setattr__(self, "target_weights", MappingProxyType({}))
        if weighting is Weighting.MOMENTUM:
            window = self.momentum_window
            if window is None:
                raise BacktestSpecError("momentum 權重需要 momentum_window")
            window = int(window)
            if window < MIN_MOMENTUM_WINDOW:
                raise BacktestSpecError(
                    f"momentum_window 至少 {MIN_MOMENTUM_WINDOW} 個 sessions: {window}")
            object.__setattr__(self, "momentum_window", window)
        else:
            object.__setattr__(self, "momentum_window", None)

    # -- reporting ------------------------------------------------------------
    def warnings(self) -> list[str]:
        """Non-fatal remarks a caller may want to surface before a long run."""
        remarks: list[str] = []
        span_days = (self.end - self.start).days
        if span_days < 730:
            remarks.append(
                f"回測期間只有 {span_days} 天（不足兩年），年化指標會非常不穩定")
        if self.drift_threshold == 0.0:
            remarks.append("drift_threshold=0：每次調倉都會重算，成本會偏高")
        if self.costs.min_commission > 0 and self.initial_capital < self.costs.min_commission * 10:
            remarks.append(
                "初始資金相對最低佣金偏低，小額交易會被最低佣金主導")
        return remarks

    def instruments_by_ticker(self) -> dict[str, Instrument]:
        return {instrument.ticker: instrument for instrument in self.universe}


def _as_rule(value: Any) -> RebalanceRule:
    if isinstance(value, RebalanceRule):
        return value
    if isinstance(value, RebalanceFrequency) or isinstance(value, str):
        return RebalanceRule(frequency=value)
    raise BacktestSpecError(f"rebalance 必須是 RebalanceRule: {value!r}")


def _as_costs(value: Any) -> CostModel:
    if isinstance(value, CostModel):
        return value
    if isinstance(value, Mapping):
        return CostModel(**dict(value))
    raise BacktestSpecError(f"costs 必須是 CostModel: {value!r}")


@dataclass(frozen=True)
class Position:
    """A holding at one point in time."""

    ticker: str
    quantity: float
    price: float
    market_value_base: float

    def __post_init__(self) -> None:
        quantity = _require_finite(self.quantity, "Position.quantity")
        price = _require_finite(self.price, "Position.price")
        market_value = _require_finite(self.market_value_base, "Position.market_value_base")
        if not quantity >= 0.0:
            raise BacktestSpecError(
                f"Position({self.ticker}) 數量不可為負數（本引擎不做放空）")
        if not price >= 0.0:
            raise BacktestSpecError(f"Position({self.ticker}).price 不可為負數")
        if not market_value >= 0.0:
            raise BacktestSpecError(
                f"Position({self.ticker}).market_value_base 不可為負數")
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "price", price)
        object.__setattr__(self, "market_value_base", market_value)


@dataclass(frozen=True)
class Trade:
    """One executed trade, priced in both local currency and the base."""

    session: date
    ticker: str
    side: str
    quantity: float
    price_local: float
    price_source: str
    fx_rate: float | None
    fx_source: str | None
    gross_base: float
    commission_base: float
    slippage_base: float
    fx_spread_base: float
    net_base: float
    reason: str = ""

    def __post_init__(self) -> None:
        side = str(self.side or "").strip().lower()
        if side not in ("buy", "sell"):
            raise BacktestSpecError(f"Trade.side 必須是 buy/sell: {self.side!r}")
        object.__setattr__(self, "side", side)
        quantity = _require_finite(self.quantity, "Trade.quantity")
        if not quantity > 0.0:
            raise BacktestSpecError(f"Trade({self.ticker}).quantity 必須大於零")
        object.__setattr__(self, "quantity", quantity)
        for name in ("price_local", "gross_base", "commission_base",
                     "slippage_base", "fx_spread_base", "net_base"):
            object.__setattr__(self, name, _require_finite(getattr(self, name), "Trade." + name))
        if self.fx_rate is not None:
            object.__setattr__(self, "fx_rate", _require_finite(self.fx_rate, "Trade.fx_rate"))
        for name in ("gross_base", "commission_base", "slippage_base",
                     "fx_spread_base", "net_base"):
            if getattr(self, name) < 0.0:
                raise BacktestSpecError(f"Trade.{name} 不可為負數")

    @property
    def total_costs_base(self) -> float:
        return self.commission_base + self.slippage_base + self.fx_spread_base


@dataclass(frozen=True)
class EquityPoint:
    """The portfolio's state at the close of one session."""

    session: date
    cash_base: float
    holdings_base: float
    total_base: float
    exposure: float
    benchmark_base: float | None = None
    drawdown: float | None = None

    def __post_init__(self) -> None:
        for name in ("cash_base", "holdings_base", "total_base", "exposure"):
            object.__setattr__(self, name,
                               _require_finite(getattr(self, name), "EquityPoint." + name))
        if not self.cash_base >= 0.0:
            raise BacktestSpecError(
                f"EquityPoint({self.session}).cash_base 不可為負數")
        if self.holdings_base < 0.0 or self.total_base < 0.0:
            raise BacktestSpecError(
                f"EquityPoint({self.session}) 的持倉或總資產不可為負數")
        for name in ("benchmark_base", "drawdown"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name,
                                   _require_finite(value, "EquityPoint." + name))


@dataclass(frozen=True)
class BacktestMetrics:
    """Summary statistics; ``None`` means "not computable", never zero."""

    sessions: int
    trades: int
    cagr: float | None = None
    annualised_vol: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    max_drawdown: float | None = None
    calmar: float | None = None
    turnover: float | None = None
    hit_rate: float | None = None
    exposure: float | None = None
    cost_drag: float | None = None
    total_costs_base: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "sessions": self.sessions,
            "trades": self.trades,
            "cagr": self.cagr,
            "annualised_vol": self.annualised_vol,
            "sharpe": self.sharpe,
            "sortino": self.sortino,
            "max_drawdown": self.max_drawdown,
            "calmar": self.calmar,
            "turnover": self.turnover,
            "hit_rate": self.hit_rate,
            "exposure": self.exposure,
            "cost_drag": self.cost_drag,
            "total_costs_base": self.total_costs_base,
        }


@dataclass(frozen=True)
class BacktestRun:
    """One persisted backtest: identity, spec snapshot and results.

    The spec is kept as JSON so the stored run can be re-read long after the
    dataclass gains fields; ``data_fingerprint`` records the PIT content, which
    is what makes "same id ⇒ same inputs" true (spec §4.4).
    """

    run_id: str
    spec_id: str
    model_version: str
    start: date
    end: date
    base_currency: str
    universe_json: str
    spec_json: str
    costs_json: str
    data_fingerprint: str
    look_ahead_status: str
    created_at: datetime
    metrics: BacktestMetrics
    trades: tuple[Trade, ...]
    equity_curve: tuple[EquityPoint, ...]
    anomalies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("run_id", "spec_id", "model_version", "data_fingerprint"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise BacktestSpecError(f"BacktestRun.{name} 不可為空")
            object.__setattr__(self, name, value)
        if len(self.data_fingerprint) != 64:
            raise BacktestSpecError(
                "BacktestRun.data_fingerprint 必須是 64 位 SHA-256 十六進位: "
                f"{self.data_fingerprint!r}")
        base = str(self.base_currency or "").strip().upper()
        if base != "CNY":
            raise BacktestSpecError(
                f"BacktestRun.base_currency 必須是 CNY: {self.base_currency!r}")
        object.__setattr__(self, "base_currency", base)
        if not isinstance(self.metrics, BacktestMetrics):
            raise BacktestSpecError("BacktestRun.metrics 必須是 BacktestMetrics")
        object.__setattr__(self, "trades", tuple(self.trades or ()))
        object.__setattr__(self, "equity_curve", tuple(self.equity_curve or ()))
        object.__setattr__(self, "anomalies", tuple(self.anomalies or ()))
        if not self.equity_curve:
            raise BacktestSpecError("BacktestRun.equity_curve 不可為空")
        status = str(self.look_ahead_status or "").strip().lower()
        object.__setattr__(self, "look_ahead_status", status)
        if not isinstance(self.created_at, datetime) or self.created_at.tzinfo is None:
            raise BacktestSpecError("BacktestRun.created_at 必須是帶時區的 datetime")


def with_initial_capital(spec: StrategySpec, capital: float) -> StrategySpec:
    """Rebuild a spec with a different starting capital (re-validates)."""
    return replace(spec, initial_capital=capital)


__all__ = [
    "BacktestMetrics",
    "BacktestRun",
    "BacktestSpecError",
    "COST_MODEL_DEFAULT",
    "CostModel",
    "EquityPoint",
    "Instrument",
    "MIN_MOMENTUM_WINDOW",
    "Position",
    "RebalanceFrequency",
    "RebalanceRule",
    "StrategySpec",
    "Trade",
    "WEIGHT_TOLERANCE",
    "Weighting",
    "with_initial_capital",
]
