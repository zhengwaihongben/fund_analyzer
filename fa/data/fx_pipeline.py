# -*- coding: utf-8 -*-
"""FX pipeline: decide *whether* to download, validate, then persist.

The division of labour is deliberate and must stay that way:

* ``fx_provider`` fetches raw Yahoo data and converts its timestamps;
* **this module** decides when a download is needed, validates the bars,
  fills in the availability fields and hands a complete series to storage;
* ``Repository`` owns persistence, the revision ledger and point-in-time
  queries.

Keeping the decision here (and not in ``fx.py`` or the estimator) is what
lets the same pipeline serve the CLI, a backtest and the GUI later without
any of them re-implementing the cache rule.

Stage 2.4 does **not** touch the NAV estimator: ``FA_FX_PIPELINE`` is the
Stage 2.5 switch and is intentionally not read here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable

from fa.config import (
    FX_OVERLAP_CALENDAR_DAYS,
    FX_OVERLAP_SESSIONS,
    PRICE_JUMP_WARN,
)
from fa.data.fx_provider import (
    FX_QUOTE_CONVENTIONS,
    FX_SANITY_BANDS,
    FxBar,
    FxFetch,
    fetch_fx_bars,
    infer_availability,
)
from fa.data.database.connection import DatabaseError
from fa.models.market_data import (
    AvailabilitySource,
    FxSeries,
    MarketDataError,
    PricePoint,
)
from fa.utils.logging_setup import get_logger

log = get_logger(__name__)

# ``str`` keys are the trading dates a caller asked about; the values are the
# reason that currency was left alone this run.
Provider = Callable[[str, date, date], FxFetch]


def availability_note(lag_minutes: int | None = None) -> str:
    """The provenance string stored on every pipeline-written FX bar."""
    from fa.config import FX_AVAILABILITY_LAG_MINUTES

    lag = FX_AVAILABILITY_LAG_MINUTES if lag_minutes is None else lag_minutes
    return (
        "Yahoo FX daily bar stamped at the start of the Europe/London day; "
        "treated as closed at the end of that London day; available at "
        f"next-day 00:00 London + {lag} min (inferred)"
    )


@dataclass(frozen=True)
class FxRequest:
    """One FX update request.

    ``currencies`` is normalised (upper case, stripped, de-duplicated, empty
    entries dropped) so that callers cannot accidentally download USD twice.
    CNY is accepted here but skipped by :func:`update_fx`: the base currency
    needs no series, and a synthetic 1.0 would be a fabricated observation.
    """

    currencies: list[str]
    start: date
    end: date
    prefer_cache: bool = True

    def __post_init__(self) -> None:
        for name, value in (("start", self.start), ("end", self.end)):
            if isinstance(value, datetime) or not isinstance(value, date):
                raise TypeError(
                    f"FxRequest.{name} 必須是 date（交易日期）；datetime 不明確"
                )
        if self.start > self.end:
            raise ValueError("FxRequest.start 不可晚於 end")

        wanted: list[str] = []
        for currency in self.currencies or []:
            code = str(currency or "").strip().upper()
            if code and code not in wanted:
                wanted.append(code)
        object.__setattr__(self, "currencies", wanted)


@dataclass
class FxUpdateOutcome:
    """What one :func:`update_fx` call actually did."""

    series: dict[str, FxSeries] = field(default_factory=dict)
    inserted: int = 0
    updated: int = 0
    revisions: int = 0
    downloads: int = 0
    skipped: dict[str, str] = field(default_factory=dict)
    anomalies: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# --- session helpers ---------------------------------------------------------


def previous_fx_session(day: date) -> date:
    """The most recent Monday–Friday on or before ``day``.

    A deliberately tiny rule: FX has no exchange calendar here, and treating
    a vendor holiday as "missing data" would force a download on every run.
    Weekends are the only gaps we are confident about.
    """
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def next_fx_session(day: date) -> date:
    """The first Monday–Friday on or after ``day``."""
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _first_stored_date(series: FxSeries) -> date | None:
    return series.points[0].price_date if series.points else None


def _last_stored_date(series: FxSeries) -> date | None:
    return series.points[-1].price_date if series.points else None


_USABLE_AVAILABILITY = frozenset({
    AvailabilitySource.OBSERVED,
    AvailabilitySource.VENDOR_TIMESTAMP,
    AvailabilitySource.INFERRED,
})


def _has_complete_availability(series: FxSeries) -> bool:
    """True when every stored bar already carries a usable publication time.

    Date coverage alone is not a cache hit. Stage 2.2 deliberately keeps legacy
    rows readable through ``load_fx()`` even when ``available_at`` is NULL and
    ``availability_source`` is ``unknown``; such rows never enter
    ``fx_as_of()``, so a pipeline that trusted the dates alone would leave them
    permanently invisible to point-in-time queries.
    """
    for point in series.points:
        if point.available_at is None:
            return False
        if point.availability_source not in _USABLE_AVAILABILITY:
            return False
    return True


def _enrich_availability(
    currency: str,
    stored: FxSeries,
    *,
    now: datetime,
    lag_minutes: int | None = None,
) -> tuple[list[PricePoint], int]:
    """Rebuild stored bars with inferred availability, keeping every rate.

    Only the time fields change: the rate, the trading date and any stamp the
    vendor already provided are copied verbatim. The repository's upsert treats
    a NULL ``available_at`` as enrichment, so this cannot create a revision.
    """
    note = availability_note(lag_minutes)
    points: list[PricePoint] = []
    changed = 0
    for point in stored.points:
        if (
            point.available_at is not None
            and point.availability_source in _USABLE_AVAILABILITY
        ):
            points.append(point)
            continue
        changed += 1
        points.append(
            PricePoint(
                price_date=point.price_date,
                close=point.close,
                market_timestamp=point.market_timestamp,
                available_at=(
                    infer_availability(point.price_date, lag_minutes)
                    if lag_minutes is not None
                    else infer_availability(point.price_date)
                ),
                availability_source=AvailabilitySource.INFERRED,
                availability_note=note,
                fetched_at=now,
                market="FX",
                currency="CNY",
                available_price_type=point.available_price_type or "fx_close",
                used_price_type="fx_close",
                source=point.source or "database",
            )
        )
    return points, changed


def _overlap_start(request_start: date, last: date) -> date:
    """How far back a tail update re-fetches.

    Both configured knobs participate: the window reaches back at least
    ``FX_OVERLAP_SESSIONS`` trading days and at least
    ``FX_OVERLAP_CALENDAR_DAYS`` calendar days — whichever reaches further back
    wins. Sessions cover "the vendor revised the last few observations";
    calendar days cover a run that sat idle across a long holiday stretch.
    """
    cursor = last
    counted = 0
    while counted < max(FX_OVERLAP_SESSIONS, 0):
        cursor -= timedelta(days=1)
        if cursor.weekday() < 5:
            counted += 1
    calendar_start = last - timedelta(days=FX_OVERLAP_CALENDAR_DAYS)
    return max(request_start, min(cursor, calendar_start))


def _plan_download(
    request: FxRequest, stored: FxSeries
) -> tuple[bool, date | None]:
    """Return ``(cache_hit, fetch_start)`` for one currency.

    Completeness is judged on the *window edges*, not on the newest date: a
    database that only holds the last few days is not a cache hit for a
    two-year request just because its newest row reaches ``end``.

    A hole in the middle is *not* treated as incomplete — Yahoo really does
    skip vendor holidays, and demanding every weekday would trigger a full
    re-download forever.
    """
    if not stored.points:
        return False, request.start

    expected_first = next_fx_session(request.start)
    expected_last = previous_fx_session(request.end)
    first = _first_stored_date(stored)
    last = _last_stored_date(stored)

    history_gap = first is None or first > expected_first
    tail_gap = last is None or last < expected_last

    if request.prefer_cache and not history_gap and not tail_gap:
        return True, None
    if history_gap or not request.prefer_cache:
        # Missing history (or an explicit refresh): re-download the request.
        return False, request.start
    # Only the tail moved: re-fetch the overlap so vendor revisions are seen.
    overlap_start = _overlap_start(request.start, last)
    return False, overlap_start


# --- validation --------------------------------------------------------------


def _is_finite_positive(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    return value == value and value not in (float("inf"), float("-inf")) and value > 0


def _validate_bars(
    currency: str,
    bars: Iterable[FxBar],
    request: FxRequest,
    stored: FxSeries,
    *,
    now: datetime,
    lag_minutes: int | None = None,
) -> tuple[list[PricePoint], list[dict], list[str], str | None]:
    """Turn provider bars into PricePoints, or explain why the series is unusable.

    Returns ``(points, anomalies, warnings, skip_reason)``.  A per-bar problem
    drops that bar only; a series-wide problem (inverted quote) returns
    ``skip_reason`` so the caller writes nothing at all.
    """
    anomalies: list[dict] = []
    warnings: list[str] = []

    kept: list[FxBar] = []
    for bar in bars:
        if not _is_finite_positive(bar.rate):
            anomalies.append({
                "currency": currency,
                "rate_date": bar.rate_date.isoformat(),
                "kind": "invalid-rate",
                "value": bar.rate,
            })
            continue
        if not request.start <= bar.rate_date <= request.end:
            anomalies.append({
                "currency": currency,
                "rate_date": bar.rate_date.isoformat(),
                "kind": "out-of-range",
                "value": bar.rate,
            })
            continue
        kept.append(bar)

    # Keep the last observation for a repeated trading date (vendor fix-ups).
    by_day: dict[date, FxBar] = {}
    duplicates = 0
    for bar in sorted(kept, key=lambda item: (item.rate_date, item.market_timestamp)):
        if bar.rate_date in by_day:
            duplicates += 1
        by_day[bar.rate_date] = bar
    if duplicates:
        anomalies.append({
            "currency": currency,
            "kind": "duplicate-date",
            "count": duplicates,
        })
        warnings.append(f"{currency}: duplicate-dates: {duplicates}")

    ordered = [by_day[day] for day in sorted(by_day)]
    if not ordered:
        return [], anomalies, warnings, "no-valid-bars"

    # Direction check: never reciprocate an inverted series.
    low, high = FX_SANITY_BANDS.get(currency, (0.0, float("inf")))
    outside = [bar for bar in ordered if not low <= bar.rate <= high]
    if outside:
        inverted = sum(1 for bar in ordered if low <= 1.0 / bar.rate <= high)
        if inverted == len(ordered):
            return [], anomalies, warnings, "inverted-quote"
        for bar in outside:
            anomalies.append({
                "currency": currency,
                "rate_date": bar.rate_date.isoformat(),
                "kind": "implausible",
                "value": bar.rate,
            })
        warnings.append(
            f"{currency}: quote-direction-suspect: {len(outside)} bar(s) outside "
            f"[{low}, {high}]"
        )

    # Jump check against the bars that precede this batch, including stored ones.
    combined = {point.price_date: point.value for point in stored.points}
    combined.update({bar.rate_date: bar.rate for bar in ordered})
    timeline = sorted(combined)
    for index in range(1, len(timeline)):
        day, previous = timeline[index], timeline[index - 1]
        if day not in by_day:
            continue  # only report bars this run introduces
        jump = abs(combined[day] / combined[previous] - 1.0)
        if jump > PRICE_JUMP_WARN:
            anomalies.append({
                "currency": currency,
                "rate_date": day.isoformat(),
                "kind": "jump",
                "value": combined[day],
                "change": jump,
            })
            warnings.append(f"{currency}: jump {day.isoformat()} {jump:.2%} (kept)")

    note = availability_note(lag_minutes)
    points = [
        PricePoint(
            price_date=bar.rate_date,
            close=bar.rate,
            market_timestamp=bar.market_timestamp,
            available_at=infer_availability(bar.rate_date, lag_minutes)
            if lag_minutes is not None
            else infer_availability(bar.rate_date),
            availability_source=AvailabilitySource.INFERRED,
            availability_note=note,
            fetched_at=now,
            market="FX",
            currency="CNY",
            available_price_type="fx_close",
            used_price_type="fx_close",
            source=bar.source or "yahoo",
        )
        for bar in ordered
    ]
    return points, anomalies, warnings, None


def _skip_reason(error: str | None) -> str:
    """Normalise a provider error string into the ``skipped`` vocabulary."""
    if not error:
        return "no-bars"
    head = error.split(":", 1)[0].strip()
    if head in {"timeout", "invalid-json", "no-bars", "unsupported-currency",
                "no-result", "missing-currency"}:
        return head
    if head.startswith("http-"):
        return head
    if head == "network-error":
        return "network-error"
    if head == "non-cny-quote":
        return "non-cny-quote"
    if head == "quote-direction-suspect":
        # The pipeline re-checks the numbers; keep the label only if it agrees.
        return "quote-direction-suspect"
    return head or "error"


# --- public API --------------------------------------------------------------


def update_fx(
    request: FxRequest,
    repository,
    *,
    provider: Provider | None = None,
    now: datetime | None = None,
) -> FxUpdateOutcome:
    """Download missing FX data, validate it and persist it.

    Each currency is isolated: a timeout, an unsupported code or a suspicious
    quote only lands in ``outcome.skipped``; the other currencies still get
    written.  Nothing is written before a whole series has been validated, so
    a failed download can never delete or overwrite existing rows.
    """
    outcome = FxUpdateOutcome()
    if not request.currencies:
        return outcome

    moment = now or datetime.now(timezone.utc)
    if not isinstance(moment, datetime) or moment.tzinfo is None:
        raise ValueError("update_fx 的 now 必須是帶時區的 datetime")
    moment = moment.astimezone(timezone.utc)

    fetch = provider or fetch_fx_bars
    for currency in request.currencies:
        if currency == "CNY":
            outcome.skipped[currency] = "base-currency"
            continue
        if currency not in FX_QUOTE_CONVENTIONS:
            outcome.skipped[currency] = "unsupported-currency"
            continue

        try:
            stored = repository.load_fx(currency, request.start, request.end)
        except DatabaseError as exc:
            outcome.skipped[currency] = f"database-error: {exc}"
            continue

        cache_hit, fetch_start = _plan_download(request, stored)
        if cache_hit:
            if _has_complete_availability(stored):
                outcome.series[currency] = stored
                continue
            # Dates cover the window but publication times are missing (rows
            # written before Stage 2.4). Backfill them from the known London
            # date rule instead of re-downloading two years of rates, and
            # never touch a rate or create a revision.
            try:
                points, changed = _enrich_availability(currency, stored, now=moment)
                series = FxSeries.from_bars(
                    currency,
                    points,
                    source=stored.source or "database",
                    base_currency="CNY",
                )
                before = repository.count_fx_revisions(currency)
                repository.save_fx(series)
                revisions = repository.count_fx_revisions(currency) - before
            except MarketDataError as exc:
                outcome.skipped[currency] = f"invalid-series: {exc}"
                continue
            except DatabaseError as exc:
                outcome.skipped[currency] = f"database-error: {exc}"
                continue
            outcome.updated += changed
            outcome.revisions += revisions
            outcome.series[currency] = series
            outcome.warnings.append(
                f"{currency}: metadata-enriched: {changed} bar(s)"
            )
            log.info(
                "FX %s: metadata-enriched %d bar(s) from stored dates (no download)",
                currency,
                changed,
            )
            continue
        assert fetch_start is not None

        outcome.downloads += 1
        try:
            result = fetch(currency, fetch_start, request.end)
        except Exception as exc:  # noqa: BLE001 - 單一貨幣失敗不得影響其他貨幣
            outcome.skipped[currency] = f"provider-error: {exc}"
            continue
        if not isinstance(result, FxFetch):
            outcome.skipped[currency] = "provider-contract: not FxFetch"
            continue
        if result.warnings:
            outcome.warnings.extend(
                f"{currency}: {warning}" for warning in result.warnings
            )
        if result.duplicates:
            # Kept separate from the pipeline's own "duplicate-date" finding:
            # this count is what the provider already collapsed, which the
            # pipeline can no longer observe from the bars it received.
            outcome.anomalies.append({
                "currency": currency,
                "rate_date": None,
                "kind": "provider-duplicate-date",
                "count": result.duplicates,
            })
            outcome.warnings.append(
                f"{currency}: provider-duplicate-dates: {result.duplicates}"
            )
        if result.error and not result.bars:
            outcome.skipped[currency] = _skip_reason(result.error)
            continue

        points, anomalies, warnings, reason = _validate_bars(
            currency, result.bars, request, stored, now=moment
        )
        outcome.anomalies.extend(anomalies)
        outcome.warnings.extend(warnings)
        if reason is not None:
            outcome.skipped[currency] = reason
            continue

        try:
            series = FxSeries.from_bars(
                currency, points, source="yahoo", base_currency="CNY"
            )
        except MarketDataError as exc:
            outcome.skipped[currency] = f"invalid-series: {exc}"
            continue

        existing = {point.price_date for point in stored.points}
        inserted = sum(1 for point in points if point.price_date not in existing)
        updated = len(points) - inserted

        # Persistence and both revision counts form one atomic-ish reporting
        # unit: if any part raises, this currency is skipped and no counter
        # claims rows that were never written.
        try:
            before = repository.count_fx_revisions(currency)
            repository.save_fx(series)
            after = repository.count_fx_revisions(currency)
        except DatabaseError as exc:
            outcome.skipped[currency] = f"database-error: {exc}"
            continue

        outcome.inserted += inserted
        outcome.updated += updated
        outcome.revisions += after - before
        outcome.series[currency] = series
        log.info(
            "FX %s: %d bars (start %s), inserted %d, updated %d, revisions %d",
            currency,
            len(points),
            fetch_start,
            inserted,
            updated,
            after - before,
        )
    return outcome


__all__ = [
    "FX_OVERLAP_SESSIONS",
    "FxRequest",
    "FxUpdateOutcome",
    "availability_note",
    "next_fx_session",
    "previous_fx_session",
    "update_fx",
]
