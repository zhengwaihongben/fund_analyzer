# -*- coding: utf-8 -*-
"""Stage C application service: one backtest request, one repository.

The GUI must not open SQLite itself, and the accepted engine API takes an
open ``Repository``.  This module is the only place that creates and closes
one for a request: ``run_backtest()`` is called on a fresh repository and the
handle is closed in ``finally`` whether the run succeeded or raised.

Cost-source semantics (spec §3.2/§3.4): ``StrategySpec.costs`` defaults to the
``COST_MODEL_DEFAULT`` sentinel, which means "use the values from
``FA_BACKTEST_*``".  Whether a request used that sentinel or an explicit
``CostModel`` is **request metadata only** — it is never persisted, and it must
never be inferred afterwards by comparing stored numbers with today's
environment.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from fa.backtesting import run_backtest
from fa.backtesting.models import (
    COST_MODEL_DEFAULT,
    BacktestRun,
    BacktestSpecError,
    CostModel,
    Instrument,
    RebalanceRule,
    StrategySpec,
    Weighting,
)
from fa.config import PROJECT_ROOT
from fa.data.database.repository import Repository

DEFAULT_DATABASE = PROJECT_ROOT / "data" / "database" / "fund_analyzer.db"
COST_SOURCE_DEFAULT = "default"
COST_SOURCE_CUSTOM = "custom"
UNKNOWN_COST_SOURCE_TEXT = "成本來源未持久化（此紀錄無法確認原始模式）"


@dataclass(frozen=True)
class BacktestRunResult:
    """What the GUI needs after one request."""

    run: BacktestRun
    created: bool
    request_cost_source: str

    @property
    def reused(self) -> bool:
        return not self.created


def request_cost_source(spec: StrategySpec) -> str:
    """Describe this request's cost mode without touching stored numbers."""
    return COST_SOURCE_DEFAULT if spec.costs is COST_MODEL_DEFAULT else COST_SOURCE_CUSTOM


def _database_path(value: Path | str | None) -> Path:
    return Path(value) if value is not None else DEFAULT_DATABASE


def run_strategy(
    spec: StrategySpec,
    repository_path: Path | str | None = None,
    *,
    force: bool = False,
    now: datetime | None = None,
    repository_factory: Callable[[Path], Repository] = Repository,
    runner: Callable[..., tuple[BacktestRun, bool]] = run_backtest,
) -> BacktestRunResult:
    """Run one strategy through a short-lived repository.

    Exceptions from ``StrategySpec`` validation, missing data, integrity
    problems and the database propagate unchanged so the GUI can show the
    class name without inventing a category.
    """
    source = request_cost_source(spec)
    repository = repository_factory(_database_path(repository_path))
    try:
        run, created = runner(spec, repository, now=now, force=force)
    finally:
        repository.close()
    return BacktestRunResult(run=run, created=bool(created), request_cost_source=source)


def load_run(
    run_id: str,
    repository_path: Path | str | None = None,
    *,
    repository_factory: Callable[[Path], Repository] = Repository,
) -> BacktestRun:
    """Read a persisted run; ``LookupError`` when the id is unknown."""
    if not str(run_id or "").strip():
        raise LookupError("找不到回測紀錄：run_id 不可為空")
    repository = repository_factory(_database_path(repository_path))
    try:
        run = repository.load_backtest_run(str(run_id))
    finally:
        repository.close()
    if run is None:
        raise LookupError(f"找不到回測紀錄：{run_id}")
    return run


def count_runs(
    repository_path: Path | str | None = None,
    *,
    repository_factory: Callable[[Path], Repository] = Repository,
) -> int:
    repository = repository_factory(_database_path(repository_path))
    try:
        return int(repository.count_backtest_runs())
    finally:
        repository.close()


# ---------------------------------------------------------------------------
# Cost-preserving serialisation
# ---------------------------------------------------------------------------
def cost_model_to_json(costs: CostModel | object) -> dict[str, float] | None:
    """``None`` for the sentinel, an explicit object otherwise."""
    if costs is COST_MODEL_DEFAULT:
        return None
    model = costs
    return {
        "commission_rate": float(getattr(model, "commission_rate")),
        "min_commission": float(getattr(model, "min_commission")),
        "slippage_bps": float(getattr(model, "slippage_bps")),
        "fx_spread_bps": float(getattr(model, "fx_spread_bps")),
    }


def _cost_model_from_json(value: Any) -> CostModel | object:
    if value is None:
        return COST_MODEL_DEFAULT
    if not isinstance(value, Mapping):
        raise BacktestSpecError(f"costs 必須是物件或 null：{value!r}")
    unknown = set(value) - {
        "commission_rate", "min_commission", "slippage_bps", "fx_spread_bps"
    }
    if unknown:
        raise BacktestSpecError(f"costs 有未知欄位：{sorted(unknown)}")
    return CostModel(
        commission_rate=float(value.get("commission_rate", 0.0005)),
        min_commission=float(value.get("min_commission", 0.0)),
        slippage_bps=float(value.get("slippage_bps", 10.0)),
        fx_spread_bps=float(value.get("fx_spread_bps", 20.0)),
    )


def spec_to_json(spec: StrategySpec) -> dict[str, Any]:
    """StrategySpec → JSON that keeps the cost sentinel (``null``)."""
    return {
        "spec_id": spec.spec_id,
        "universe": [
            {"ticker": item.ticker, "market": item.market, "currency": item.currency}
            for item in spec.universe
        ],
        "weighting": spec.weighting.value,
        "start": spec.start.isoformat(),
        "end": spec.end.isoformat(),
        "initial_capital": float(spec.initial_capital),
        "rebalance": {
            "frequency": spec.rebalance.frequency.value,
            "anchor": spec.rebalance.anchor,
        },
        "target_weights": dict(spec.target_weights),
        "momentum_window": spec.momentum_window,
        "drift_threshold": spec.drift_threshold,
        "base_currency": spec.base_currency,
        "benchmark": spec.benchmark,
        "costs": cost_model_to_json(spec.costs),
    }


def _date_value(value: Any, label: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise BacktestSpecError(f"{label} 不是有效日期：{value!r}") from exc
    raise BacktestSpecError(f"{label} 必須是 YYYY-MM-DD：{value!r}")


def spec_from_json(payload: Mapping[str, Any]) -> StrategySpec:
    """JSON → StrategySpec; absent/``null`` costs mean the default sentinel.

    This deliberately does **not** reuse ``scripts/backtest_report.py::
    spec_from_json``, which builds ``CostModel(...)`` unconditionally and thus
    erases the default/custom distinction.
    """
    if not isinstance(payload, Mapping):
        raise BacktestSpecError("策略設定必須是 JSON 物件")
    universe_raw = payload.get("universe")
    if not isinstance(universe_raw, (list, tuple)) or not universe_raw:
        raise BacktestSpecError("universe 至少需要一個標的")
    universe = []
    for item in universe_raw:
        if not isinstance(item, Mapping):
            raise BacktestSpecError("universe 的每一列都必須是物件")
        universe.append(
            Instrument(
                ticker=str(item.get("ticker") or ""),
                market=str(item.get("market") or ""),
                currency=str(item.get("currency") or ""),
            )
        )
    weighting = Weighting(str(payload.get("weighting") or Weighting.EQUAL.value))
    rebalance_raw = payload.get("rebalance") or {}
    if not isinstance(rebalance_raw, Mapping):
        raise BacktestSpecError("rebalance 必須是物件")
    rebalance = RebalanceRule(
        frequency=str(rebalance_raw.get("frequency") or "monthly"),
        anchor=rebalance_raw.get("anchor", 1),
    )
    momentum_window = payload.get("momentum_window")
    weights = payload.get("target_weights") or {}
    if not isinstance(weights, Mapping):
        raise BacktestSpecError("target_weights 必須是物件")
    return StrategySpec(
        spec_id=str(payload.get("spec_id") or ""),
        universe=tuple(universe),
        weighting=weighting,
        start=_date_value(payload.get("start"), "start"),
        end=_date_value(payload.get("end"), "end"),
        initial_capital=float(payload.get("initial_capital"))
        if payload.get("initial_capital") is not None
        else None,
        rebalance=rebalance,
        target_weights={str(key): float(value) for key, value in weights.items()},
        momentum_window=int(momentum_window) if momentum_window is not None else None,
        drift_threshold=float(payload.get("drift_threshold", 0.02)),
        base_currency=str(payload.get("base_currency") or "CNY"),
        benchmark=payload.get("benchmark") or None,
        costs=_cost_model_from_json(payload.get("costs")),
    )
