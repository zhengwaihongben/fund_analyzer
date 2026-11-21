# -*- coding: utf-8 -*-
"""Point-in-time read layer for the V2.3.2 backtest (Stages B and D).

Every read a backtest performs goes through this class.  It exists so the
engine cannot accidentally reach a non-PIT source: the module exposes only
wrappers around repository queries:

* :meth:`prices` → :meth:`Repository.prices_as_of` (version reconstruction)
* :meth:`fx` → :meth:`Repository.fx_as_of` (publication-gated rates)
* :meth:`official_nav_history` → :meth:`Repository.load_official_nav`
* :meth:`series_for` → the same PIT price reader for a ticker that is not a
  strategy holding (the benchmark)

``load_prices()`` (the V2.2 reader that ignores ``available_at``) is
deliberately not wrapped: using it inside a backtest is the look-ahead mistake
Stage A was built to prevent.  Nothing here writes, and nothing here touches
the network.

Two kinds of failure are distinguished since Stage D (review P1-4):

* :class:`BacktestDataMissingError` — a hole in otherwise valid data (no row for
  a session, no FX rate yet).  The simulation may record an anomaly and carry on.
* :class:`BacktestDataIntegrityError` — the data contradicts itself or the
  strategy (a stored venue that differs from the instrument, a price without a
  currency, an FX series quoted against something other than CNY).  A backtest
  must abort: silently treating it as "missing" would produce a number from data
  nobody vouched for.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from fa.data.database.repository import Repository
from fa.models.market_data import FxSeries, PricePoint, PriceSeries
from fa.utils.calendars import normalise_exchange

from .models import Instrument


class BacktestDataError(RuntimeError):
    """Base class: PIT data could not be read as the backtest requires."""


class BacktestDataMissingError(BacktestDataError):
    """A hole in valid data — recoverable, recorded as an anomaly."""


class BacktestDataIntegrityError(BacktestDataError):
    """The data contradicts the instrument or the specification — abort."""


class DataAccess:
    """PIT-only facade over :class:`Repository`.

    Parameters
    ----------
    repository:
        An open repository; this class never creates or migrates a database.
    """

    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    @property
    def repository(self) -> Repository:
        return self._repository

    # -- prices ---------------------------------------------------------------
    def prices(
        self,
        instrument: Instrument,
        as_of: datetime,
        start: date | None = None,
        end: date | None = None,
    ) -> PriceSeries:
        """The price version public at ``as_of``.

        The result must belong to the instrument's venue and currency before a
        simulation may use it: a ticker whose listing changed from NYSE to SIX
        would otherwise mix currencies under one name.
        """
        series = self._repository.prices_as_of(
            instrument.ticker, as_of, start=start, end=end
        )
        if not series.points:
            raise BacktestDataMissingError(
                f"{instrument.ticker}: 在 {as_of.isoformat()} 沒有可見的 PIT 價格")
        self._check_market(instrument.ticker, instrument.market, series)
        self._check_currency(instrument, series)
        return series

    def price_at(
        self,
        instrument: Instrument,
        as_of: datetime,
        session: date,
    ) -> PricePoint:
        """The single point for ``session`` visible at ``as_of``."""
        series = self.prices(instrument, as_of, start=session, end=session)
        points = [point for point in series.points if point.price_date == session]
        if not points:
            raise BacktestDataMissingError(
                f"{instrument.ticker}: {session.isoformat()} 在 {as_of.isoformat()} 沒有可見價格")
        return points[0]

    def series_for(
        self,
        ticker: str,
        as_of: datetime,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> PriceSeries:
        """PIT series for a ticker read from its own stored metadata.

        Used by the benchmark, which is not part of the strategy universe and so
        has no :class:`Instrument`: the venue and currency come from the stored
        rows (``Repository.price_metadata``), never from the ticker's shape, and
        a CNY-denominated index is allowed here because nothing is held.
        """
        code = str(ticker or "").strip().upper()
        if not code:
            raise BacktestDataIntegrityError("series_for() 需要 ticker")
        market, currency = self.instrument_metadata(code)
        series = self._repository.prices_as_of(code, as_of, start=start, end=end)
        if not series.points:
            raise BacktestDataMissingError(
                f"{code}: 在 {as_of.isoformat()} 沒有可見的 PIT 價格")
        self._check_market(code, market, series)
        expected_currency = currency.upper()
        for point in series.points:
            stored = str(point.currency or "").strip().upper()
            if not stored:
                raise BacktestDataIntegrityError(
                    f"{code}: {point.price_date.isoformat()} 的價格沒有標示計價幣別，拒絕使用")
            if stored != expected_currency:
                raise BacktestDataIntegrityError(
                    f"{code}: 儲存計價幣別 {point.currency!r} 與既有紀錄 "
                    f"{currency!r} 不一致")
        return series

    def instrument_metadata(self, ticker: str) -> tuple[str, str]:
        """``(market, currency)`` declared by the stored rows for ``ticker``."""
        code = str(ticker or "").strip().upper()
        if not code:
            raise BacktestDataIntegrityError("instrument_metadata() 需要 ticker")
        found = self._repository.price_metadata(code)
        if found is None:
            raise BacktestDataMissingError(f"{code}: 資料庫沒有任何價格紀錄")
        market, currency = found
        if not str(currency or "").strip():
            raise BacktestDataIntegrityError(f"{code}: 價格紀錄沒有標示計價幣別")
        return market, currency

    def _check_market(self, ticker: str, market: str, series: PriceSeries) -> None:
        expected = normalise_exchange(market)
        if not expected:
            raise BacktestDataIntegrityError(
                f"{ticker}: 指定的市場 {market!r} 無法辨識")
        for point in series.points:
            actual = normalise_exchange(point.market)
            if not actual:
                raise BacktestDataIntegrityError(
                    f"{ticker}: {point.price_date.isoformat()} 的價格沒有標示"
                    "交易所市場，拒絕使用來源不明的價格")
            if actual != expected:
                raise BacktestDataIntegrityError(
                    f"{ticker}: 儲存市場 {point.market!r} 與指定的 "
                    f"{market!r} 不一致，拒絕混用不同交易所的價格")

    @staticmethod
    def _check_currency(instrument: Instrument, series: PriceSeries) -> None:
        for point in series.points:
            stored = str(point.currency or "").strip().upper()
            if not stored:
                raise BacktestDataIntegrityError(
                    f"{instrument.ticker}: {point.price_date.isoformat()} 的價格沒有標示"
                    "計價幣別，拒絕使用")
            if stored != instrument.currency:
                raise BacktestDataIntegrityError(
                    f"{instrument.ticker}: 儲存計價幣別 {point.currency!r} 與策略指定的 "
                    f"{instrument.currency!r} 不一致")

    # -- FX -------------------------------------------------------------------
    def fx(
        self,
        currency: str,
        as_of: datetime,
        start: date | None = None,
        end: date | None = None,
    ) -> FxSeries | None:
        """The FX series public at ``as_of``; ``None`` for the CNY base.

        A missing series for a foreign currency is *not* an error here — the
        engine decides whether the gap can be tolerated (unvalued position) or
        aborts.  What this method refuses is a series quoted against anything but
        CNY, because the engine assumes ``rate = CNY per 1 unit``.
        """
        code = str(currency or "").strip().upper()
        if not code:
            raise BacktestDataIntegrityError("fx() 需要計價貨幣")
        if code == "CNY":
            return None
        series = self._repository.fx_as_of(code, as_of, start=start, end=end)
        if series is not None and series.base_currency.upper() != "CNY":
            raise BacktestDataIntegrityError(
                f"FX {code}: 基準貨幣為 {series.base_currency!r}，"
                "回測只接受 CNY per 1 外幣")
        return series

    def fx_rate(
        self,
        currency: str,
        as_of: datetime,
        session: date,
    ) -> tuple[float, date] | None:
        """``(rate, rate_date)`` for ``session`` visible at ``as_of``.

        FX pairs are not published on every equity session, so this reads every
        rate public by ``as_of`` **up to** ``session`` and then takes the latest
        one on or before it (``rate_on_or_before``).  Limiting the read to
        ``start=session, end=session`` would return ``None`` on a holiday even
        when the previous session's rate is perfectly usable — and asking the
        repository for a range and then falling back inside it would be two
        different answers to the same question.
        """
        series = self.fx(currency, as_of, end=session)
        if series is None:
            return (1.0, session) if str(currency).upper() == "CNY" else None
        return series.rate_on_or_before(session) if len(series) else None

    # -- official NAV ---------------------------------------------------------
    def official_nav_history(self, fund_code: str) -> list[tuple[date, float]]:
        """Published NAV history ``[(date, nav), …]`` for reporting only.

        The rows carry a trading date but no publication instant, so they are
        **not** usable as a PIT signal inside the simulation; Stage E may compare
        a strategy against them after the fact.
        """
        code = str(fund_code or "").strip()
        if not code:
            raise BacktestDataIntegrityError("official_nav_history() 需要基金代碼")
        return self._repository.load_official_nav(code)

    # -- helpers for the fingerprint (Stage D) -------------------------------
    @staticmethod
    def revision_rows(series: PriceSeries) -> list[tuple[str, date, int]]:
        """``(ticker, date, revision_id)`` for every consumed price row.

        Exposed here so the fingerprint builder never has to reach into the
        repository again; ``revision_id`` is ``0`` when the current row supplied
        the value.
        """
        return [
            (series.ticker, point.price_date, int(point.revision_id))
            for point in series.points
        ]


def utc_now() -> datetime:
    """The clock, isolated so tests can freeze it at the call site."""
    return datetime.now(timezone.utc)


__all__ = [
    "BacktestDataError",
    "BacktestDataIntegrityError",
    "BacktestDataMissingError",
    "DataAccess",
    "utc_now",
]
