# -*- coding: utf-8 -*-
"""Turn raw holdings rows into typed :class:`~fa.models.asset.Asset` objects.

The fund's holdings file tells us a ticker, a name and a weight — not what kind
of instrument it is.  Guessing "everything is an equity" is wrong (a QDII fund
can hold ETFs, bond ETFs and cash), and guessing "it has a Yahoo price, so it is
an equity" is worse: it silently values things we never identified.

Classification therefore uses only *explicit* evidence, in this order:

1. a manual override table (highest priority, for anything the heuristics miss);
2. an asset-type field supplied by the data source;
3. the holding's formal name (``...ETF``, ``...债券ETF``, ``现金``, ...).

Anything that fits none of these stays :attr:`AssetType.UNKNOWN`, keeps its
weight, and is excluded from valuation with an explicit warning.  A ticker's
*shape* is not evidence of anything — an ETF or a fund can share the same
symbol format as an equity — and price data is never used as evidence either.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from fa.models.asset import Asset, AssetType, normalise_currency

#: Manual overrides: ``{"TICKER": AssetType.X}``.  Fill this in when a holding is
#: misclassified; it always wins over the heuristics below.
MANUAL_TYPE_OVERRIDES: dict[str, AssetType] = {}

#: Name fragments that identify an ETF.
ETF_MARKERS = ("ETF", "交易所交易基金", "交易型开放式", "交易型開放式")

#: Name fragments that identify a bond ETF (checked before :data:`ETF_MARKERS`).
BOND_ETF_MARKERS = ("债券ETF", "債券ETF", "国债ETF", "國債ETF", "公司债ETF", "公司債ETF")

#: Name fragments that identify cash / money-market holdings.
CASH_MARKERS = ("现金", "現金", "货币", "貨幣", "存款", "银行间", "銀行間")

#: Some sources (Eastmoney among them) write a foreign listing as ``"ROP SW"``
#: instead of ``"ROP.SW"``.  The space is an exchange separator, not part of the
#: code, so it is normalised before the plain-ticker test.
_SPACED_TICKER = re.compile(r"^\s*([A-Za-z0-9]{1,12})\s+([A-Za-z]{1,3})\s*$")


def normalise_ticker(ticker: Any) -> str:
    """Return the ticker with a space-separated exchange suffix turned into ``.``."""
    code = _text(ticker).upper()
    match = _SPACED_TICKER.match(code)
    if match:
        return f"{match.group(1)}.{match.group(2)}"
    return code


def _text(value: Any) -> str:
    return str(value or "").strip()


def classify_asset_type(
    *,
    ticker: str = "",
    name: str = "",
    declared: Any = None,
    overrides: Mapping[str, AssetType] | None = None,
) -> tuple[AssetType, str]:
    """Return ``(asset_type, evidence)`` for one holding.

    ``evidence`` is a short machine-readable reason (``"manual"``,
    ``"declared"``, ``"name-bond-etf"``, ``"name-etf"``, ``"name-cash"``,
    ``"unclassified"``) recorded on the asset so a later reader can see *why* a
    holding was valued the way it was.
    """
    code = normalise_ticker(ticker)
    label = _text(name)
    table = MANUAL_TYPE_OVERRIDES if overrides is None else overrides

    if code and code in table:
        return AssetType.parse(table[code]), "manual"

    declared_type = AssetType.parse(declared)
    if declared_type is not AssetType.UNKNOWN:
        return declared_type, "declared"

    lower = label.lower()
    if any(marker.lower() in lower for marker in BOND_ETF_MARKERS):
        return AssetType.BOND_ETF, "name-bond-etf"
    if any(marker.lower() in lower for marker in ETF_MARKERS):
        return AssetType.ETF, "name-etf"
    if any(marker in label for marker in CASH_MARKERS):
        return AssetType.CASH, "name-cash"

    return AssetType.UNKNOWN, "unclassified"


def build_assets(
    holdings: Any,
    price_hits: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    overrides: Mapping[str, AssetType] | None = None,
) -> list[Asset]:
    """Convert a holdings frame into :class:`Asset` objects.

    ``price_hits`` is used only for *market/exchange metadata* (to pick the
    right trading calendar) and for the quote currency — never to decide the
    asset type.
    """
    price_hits = price_hits or {}
    rows: Iterable[Mapping[str, Any]]
    if hasattr(holdings, "to_dict"):
        rows = holdings.to_dict("records")
    else:  # pragma: no cover - defensive, callers pass a DataFrame
        rows = list(holdings or [])

    assets: list[Asset] = []
    for row in rows:
        ticker = _text(row.get("ticker"))
        name = _text(row.get("name")) or ticker
        hit = price_hits.get(ticker) or {}
        asset_type, evidence = classify_asset_type(
            ticker=ticker,
            name=name,
            declared=row.get("asset_type"),
            overrides=overrides,
        )
        exchange = (
            _text(hit.get("exchange"))
            or _text(hit.get("market"))
            or _text(row.get("market"))
            or None
        )
        assets.append(
            Asset(
                asset_id=ticker or name,
                name=name,
                asset_type=asset_type,
                ticker=ticker or None,
                market=exchange,
                exchange=exchange,
                # Keep the *declared* currency as the source of truth; the quote
                # currency lives on the PriceSeries so a mismatch stays visible.
                currency=normalise_currency(row.get("currency_hint")),
                weight=float(row.get("weight") or 0.0),
                holding_date=row.get("holding_date"),
                source=_text(row.get("source")),
                interest_rate=row.get("interest_rate"),
                interest_rate_source=row.get("interest_rate_source"),
                extra={"classification": evidence},
            )
        )
    return assets


__all__ = [
    "BOND_ETF_MARKERS",
    "CASH_MARKERS",
    "ETF_MARKERS",
    "MANUAL_TYPE_OVERRIDES",
    "build_assets",
    "classify_asset_type",
]
