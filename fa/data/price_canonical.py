# -*- coding: utf-8 -*-
"""Canonical price-version selection, shared by the repository and the pipeline.

The project has two related but distinct questions about a stored price:

* the repository asks *what was public at this instant?* — answered by
  ``prices_as_of()``, which filters on availability and supersession;
* the pipeline asks *does this trading session already have a usable version?* —
  answered per **trading date**, never per database row, because a session may
  legitimately hold more than one ``price_type`` (a V2.2 ``adjusted_close`` row
  that predates the availability model can sit next to a re-downloaded
  ``close+adj_close`` row).

Before this module the two sides each carried their own ordering rule.  That is
the kind of drift where the pipeline updates one version while the backtest
reads another, so both now import the rule from here.  Everything in this module
is a pure function: no IO, no database handle, no clock.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable

#: Sources whose ``available_at`` is evidence rather than a guess.  A stored row
#: with any other source stays readable through ``load_prices()`` but is not
#: usable for point-in-time work.
PIT_AVAILABILITY_SOURCES = ("observed", "vendor_timestamp", "inferred")

_PIT_SOURCE_SET = frozenset(PIT_AVAILABILITY_SOURCES)

#: ``close+adj_close`` is the richest row (it carries the split/dividend-adjusted
#: series), ``close`` is the fallback, anything else is a legacy key.
_PRICE_TYPE_RANK = {"close+adj_close": 2, "close": 1}


def source_value(availability_source: Any) -> str:
    """Return the stored form of an ``AvailabilitySource`` or plain string."""
    return str(getattr(availability_source, "value", availability_source))


def pit_usable(available_at: Any, availability_source: Any) -> bool:
    """True when a row/point carries publication evidence we may trust."""
    return available_at is not None and source_value(availability_source) in _PIT_SOURCE_SET


def version_key(
    available_at: Any,
    adj_close: Any,
    price_type: str | None,
    revision: bool = False,
) -> tuple[str, int, int, int, str]:
    """Order two versions of the same trading date.

    Later availability wins (it is the fresher evidence at that instant); on a
    tie prefer the row that carries ``adj_close``, then the richer price type,
    then a revision row, and finally the type name so the order is total and
    never depends on the database's row order.
    """
    kind = price_type or ""
    return (
        str(available_at) if available_at is not None else "",
        1 if adj_close is not None else 0,
        _PRICE_TYPE_RANK.get(kind, 0),
        1 if revision else 0,
        kind,
    )


def point_key(point: Any) -> tuple[str, int, int, int, str]:
    """``version_key()`` for a ``PricePoint`` (current rows only, no revisions)."""
    return version_key(
        point.available_at,
        point.adj_close,
        point.available_price_type,
    )


def point_is_pit_usable(point: Any) -> bool:
    return pit_usable(point.available_at, point.availability_source)


def canonical_points_by_day(points: Iterable[Any]) -> dict[date, Any]:
    """Pick one canonical point per trading date.

    A usable version always beats an unusable one, so a legacy ``unknown`` row
    cannot make a session look incomplete when a re-downloaded row for the same
    date already carries evidence.  When no version is usable the richest
    version is still returned — it is the row a re-download should update.
    """
    grouped: dict[date, list[Any]] = {}
    for point in points:
        grouped.setdefault(point.price_date, []).append(point)
    canonical: dict[date, Any] = {}
    for day in sorted(grouped):
        candidates = grouped[day]
        usable = [point for point in candidates if point_is_pit_usable(point)]
        pool = usable or candidates
        canonical[day] = max(pool, key=point_key)
    return canonical
