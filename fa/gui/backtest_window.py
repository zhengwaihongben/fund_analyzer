# -*- coding: utf-8 -*-
"""Stage C: the backtest window and the pure presentation helpers behind it.

Two rules shape this module:

* the form converts and constructs a :class:`StrategySpec`; it does **not**
  re-implement the engine's validation, so an input the engine would reject
  must come back as a ``BacktestSpecError`` from ``StrategySpec`` itself;
* every result panel is rendered from the persisted ``BacktestRun`` (its
  ``metrics``, ``trades``, ``equity_curve``, ``spec_json``, ``costs_json``),
  never from numbers recomputed in the GUI.

Cost source is request metadata (``request_cost_source``) and is only shown as
"this request's mode" for a freshly created run.  For a reused or historical
run the original mode is not persisted, so the window says so instead of
guessing from the numbers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import json
from typing import Any, Iterable, Mapping, Sequence

import tkinter as tk
from tkinter import ttk

from fa.application import backtesting as service
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
from fa.gui.formatting import MISSING, date_text, money, number, pct
from fa.gui.worker import WorkerBridge

COST_MODE_DEFAULT = "default"
COST_MODE_CUSTOM = "custom"
WEIGHTINGS = ("equal", "fixed", "momentum")
FREQUENCIES = ("daily", "weekly", "monthly", "quarterly")
RUNNING_STATE = "running"
IDLE_STATE = "idle"

METRIC_ROWS = (
    ("sessions", "交易日", "number"),
    ("trades", "交易筆數", "number"),
    ("cagr", "CAGR", "pct"),
    ("annualised_vol", "年化波動", "pct"),
    ("sharpe", "Sharpe", "number4"),
    ("sortino", "Sortino", "number4"),
    ("max_drawdown", "最大回撤", "pct"),
    ("calmar", "Calmar", "number4"),
    ("turnover", "換手率", "number4"),
    ("hit_rate", "勝率", "pct"),
    ("exposure", "平均曝險", "pct"),
    ("cost_drag", "成本拖累", "pct"),
    ("total_costs_base", "總成本（CNY）", "money"),
)


@dataclass
class BacktestForm:
    """The raw widget values, and their conversion to / from a spec."""

    spec_id: str = "stage-c-demo"
    universe: list[dict[str, str]] = field(
        default_factory=lambda: [{"ticker": "LLY", "market": "US", "currency": "USD"}]
    )
    start: str = ""
    end: str = ""
    initial_capital: str = "1000000"
    weighting: str = "equal"
    rebalance_frequency: str = "monthly"
    rebalance_anchor: str = "1"
    target_weights: str = ""
    momentum_window: str = ""
    drift_threshold: str = "0.02"
    benchmark: str = ""
    cost_mode: str = COST_MODE_DEFAULT
    commission_rate: str = "0.0005"
    min_commission: str = "0.0"
    slippage_bps: str = "10.0"
    fx_spread_bps: str = "20.0"

    # -- conversion ---------------------------------------------------
    def to_spec(self) -> StrategySpec:
        instruments = []
        for row in self.universe:
            ticker = str(row.get("ticker") or "").strip()
            if not ticker:
                continue
            instruments.append(
                Instrument(
                    ticker=ticker,
                    market=str(row.get("market") or "").strip(),
                    currency=str(row.get("currency") or "").strip(),
                )
            )
        anchor_text = str(self.rebalance_anchor or "").strip()
        anchor = int(anchor_text) if anchor_text else None
        momentum_text = str(self.momentum_window or "").strip()
        return StrategySpec(
            spec_id=str(self.spec_id or "").strip(),
            universe=tuple(instruments),
            weighting=Weighting(str(self.weighting or "equal")),
            start=_parse_date(self.start, "start"),
            end=_parse_date(self.end, "end"),
            initial_capital=_parse_float(self.initial_capital, "initial_capital"),
            rebalance=RebalanceRule(
                frequency=str(self.rebalance_frequency or "monthly"),
                anchor=anchor,
            ),
            target_weights=_parse_weights(self.target_weights),
            momentum_window=int(momentum_text) if momentum_text else None,
            drift_threshold=_parse_float(self.drift_threshold, "drift_threshold"),
            benchmark=str(self.benchmark or "").strip() or None,
            costs=self._costs(),
        )

    def _costs(self) -> CostModel | object:
        if str(self.cost_mode or COST_MODE_DEFAULT) != COST_MODE_CUSTOM:
            return COST_MODEL_DEFAULT
        return CostModel(
            commission_rate=_parse_float(self.commission_rate, "commission_rate"),
            min_commission=_parse_float(self.min_commission, "min_commission"),
            slippage_bps=_parse_float(self.slippage_bps, "slippage_bps"),
            fx_spread_bps=_parse_float(self.fx_spread_bps, "fx_spread_bps"),
        )

    @classmethod
    def from_spec(cls, spec: StrategySpec) -> "BacktestForm":
        costs = spec.costs
        explicit = costs is not COST_MODEL_DEFAULT
        form = cls(
            spec_id=spec.spec_id,
            universe=[
                {"ticker": item.ticker, "market": item.market,
                 "currency": item.currency}
                for item in spec.universe
            ],
            start=spec.start.isoformat(),
            end=spec.end.isoformat(),
            initial_capital=repr(float(spec.initial_capital)),
            weighting=spec.weighting.value,
            rebalance_frequency=spec.rebalance.frequency.value,
            rebalance_anchor="" if spec.rebalance.anchor is None
            else str(spec.rebalance.anchor),
            target_weights=", ".join(
                f"{ticker}={weight}"
                for ticker, weight in spec.target_weights.items()
            ),
            momentum_window="" if spec.momentum_window is None
            else str(spec.momentum_window),
            drift_threshold=repr(float(spec.drift_threshold)),
            benchmark=spec.benchmark or "",
            cost_mode=COST_MODE_CUSTOM if explicit else COST_MODE_DEFAULT,
        )
        if explicit:
            form.commission_rate = repr(float(costs.commission_rate))
            form.min_commission = repr(float(costs.min_commission))
            form.slippage_bps = repr(float(costs.slippage_bps))
            form.fx_spread_bps = repr(float(costs.fx_spread_bps))
        return form


def _parse_float(value: Any, label: str) -> float:
    text = str(value if value is not None else "").strip()
    if not text:
        raise BacktestSpecError(f"{label} 不可為空")
    try:
        return float(text)
    except ValueError as exc:
        raise BacktestSpecError(f"{label} 不是數字：{text!r}") from exc


def _parse_date(value: Any, label: str) -> date:
    text = str(value or "").strip()
    if not text:
        raise BacktestSpecError(f"{label} 不可為空（YYYY-MM-DD）")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise BacktestSpecError(f"{label} 不是有效日期：{text!r}") from exc


def _parse_weights(value: Any) -> dict[str, float]:
    text = str(value or "").strip()
    weights: dict[str, float] = {}
    if not text:
        return weights
    for part in text.replace("\n", ",").split(","):
        chunk = part.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise BacktestSpecError(f"target_weights 需為 TICKER=weight：{chunk!r}")
        ticker, raw = chunk.split("=", 1)
        ticker = ticker.strip().upper()
        if not ticker:
            raise BacktestSpecError(f"target_weights 缺少代碼：{chunk!r}")
        weights[ticker] = _parse_float(raw, f"target_weights[{ticker}]")
    return weights


# ---------------------------------------------------------------------------
# Result panels (pure)
# ---------------------------------------------------------------------------
def _value(value: Any, kind: str) -> str:
    if value is None:
        return MISSING
    if kind == "pct":
        return pct(value)
    if kind == "money":
        return money(value)
    if kind == "number":
        return number(value, digits=0)
    if kind == "number4":
        return number(value, digits=4)
    return str(value)


def _json_mapping(value: Any) -> dict[str, Any]:
    """Persisted payloads are canonical JSON strings in the database."""
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def summary_rows(run: BacktestRun, *, created: bool,
                 request_cost_source: str) -> list[tuple[str, str]]:
    spec_payload = _json_mapping(run.spec_json)
    fingerprint = run.data_fingerprint or ""
    rows = [
        ("Run ID", run.run_id),
        ("策略 ID", str(spec_payload.get("spec_id") or run.spec_id)),
        ("期間", f"{date_text(run.start)} → {date_text(run.end)}"),
        ("初始資金（CNY）", money(_initial_capital(run))),
        ("模型版本", run.model_version),
        ("資料指紋", f"{fingerprint[:12]}…" if fingerprint else MISSING),
        ("前視狀態", run.look_ahead_status),
        ("異常筆數", str(len(run.anomalies))),
        ("執行狀態", "Completed"),
        ("重用狀態", "Existing Run Reused（已重用既有結果）" if not created
         else "Created（本次新建）"),
    ]
    return rows


def _initial_capital(run: BacktestRun) -> float | None:
    spec_payload = _json_mapping(run.spec_json)
    try:
        return float(spec_payload.get("initial_capital"))
    except (TypeError, ValueError):
        return None


def runtime_rows(run: BacktestRun) -> list[tuple[str, str]]:
    spec_payload = _json_mapping(run.spec_json)
    runtime = spec_payload.get("runtime") or {}
    if not isinstance(runtime, Mapping):
        runtime = {}
    costs = _json_mapping(run.costs_json)
    rows = [
        ("決策延遲（分鐘）", _value(runtime.get("decision_lag_minutes"), "raw")),
        ("執行延遲（交易日）", _value(runtime.get("execution_lag_sessions"), "raw")),
        ("零股（fractional_shares）",
         "是" if runtime.get("fractional_shares") is True
         else "否" if runtime.get("fractional_shares") is False else MISSING),
        ("無風險利率", _value(runtime.get("risk_free_rate"), "pct")),
        ("佣金率", _value(costs.get("commission_rate"), "raw")),
        ("最低佣金（CNY）", money(costs.get("min_commission"))),
        ("滑價（bps）", _value(costs.get("slippage_bps"), "raw")),
        ("換匯價差（bps）", _value(costs.get("fx_spread_bps"), "raw")),
    ]
    return rows


def cost_source_rows(run: BacktestRun, *, created: bool,
                     request_cost_source: str) -> list[tuple[str, str]]:
    costs = _json_mapping(run.costs_json)
    rows = [
        ("成本來源", "本次請求模式：" + (
            "預設（FA_BACKTEST_*）" if request_cost_source == service.COST_SOURCE_DEFAULT
            else "自訂")
         if created else service.UNKNOWN_COST_SOURCE_TEXT),
        ("佣金率", _value(costs.get("commission_rate"), "raw")),
        ("最低佣金", money(costs.get("min_commission"))),
        ("滑價（bps）", _value(costs.get("slippage_bps"), "raw")),
        ("換匯價差（bps）", _value(costs.get("fx_spread_bps"), "raw")),
    ]
    return rows


def metrics_rows(run: BacktestRun) -> list[tuple[str, str, str]]:
    metrics = run.metrics
    return [
        (label, getattr(metrics, attribute, None), kind)
        for attribute, label, kind in METRIC_ROWS
    ]


def equity_rows(run: BacktestRun) -> list[tuple[str, str, str, str, str, str, str]]:
    rows = []
    for point in run.equity_curve:
        rows.append((
            date_text(point.session),
            money(point.cash_base),
            money(point.holdings_base),
            money(point.total_base),
            pct(point.exposure, signed=False),
            money(point.benchmark_base) if point.benchmark_base is not None else MISSING,
            pct(point.drawdown) if point.drawdown is not None else MISSING,
        ))
    return rows


def trade_rows(run: BacktestRun) -> list[tuple[str, ...]]:
    rows = []
    for trade in run.trades:
        rows.append((
            date_text(trade.session),
            trade.ticker,
            "買入" if trade.side == "buy" else "賣出",
            number(trade.quantity, digits=6),
            number(trade.price_local, digits=4),
            trade.price_source or MISSING,
            number(trade.fx_rate, digits=6) if trade.fx_rate else MISSING,
            trade.fx_source or MISSING,
            money(trade.gross_base),
            money(trade.commission_base),
            money(trade.slippage_base),
            money(trade.fx_spread_base),
            money(trade.net_base),
            trade.reason or MISSING,
        ))
    return rows


def anomaly_lines(run: BacktestRun) -> list[str]:
    items = [str(item) for item in run.anomalies]
    return items if items else ["無異常"]


def equity_series(run: BacktestRun) -> tuple[list[float | None], list[float | None]]:
    """Total and benchmark levels for the canvas chart."""
    totals: list[float | None] = []
    benchmarks: list[float | None] = []
    for point in run.equity_curve:
        totals.append(point.total_base)
        benchmarks.append(point.benchmark_base)
    return totals, benchmarks


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RunOutcome:
    result: service.BacktestRunResult


def _run_job(reporter, form_values: Mapping[str, Any],
             repository_path: str | None) -> RunOutcome:
    """Worker entry point: build the spec, then run it through the service."""
    reporter.report("建立 StrategySpec 並開始回測…")
    spec = BacktestForm(**dict(form_values)).to_spec()
    outcome = RunOutcome(result=service.run_strategy(spec, repository_path))
    reporter.report("回測完成，讀取已保存結果…")
    return outcome


class BacktestWindow(tk.Toplevel):
    """Strategy configuration + persisted results."""

    def __init__(self, master, *, repository_path: str | None = None,
                 on_close=None) -> None:
        super().__init__(master)
        self.title("策略回測 — Fund Analyzer")
        self.geometry("1080x760")
        self.minsize(880, 620)
        self.repository_path = repository_path
        self.on_close = on_close
        self.state = IDLE_STATE
        self.result: service.BacktestRunResult | None = None
        self.worker = WorkerBridge(self)
        self._closed = False
        self.entries: dict[str, ttk.Entry] = {}
        self.vars: dict[str, tk.Variable] = {}
        self._build_ui()

    # -- UI -----------------------------------------------------------
    def _build_ui(self) -> None:
        form = ttk.LabelFrame(self, text="策略設定")
        form.pack(fill="x", padx=8, pady=6)
        self._entry(form, "spec_id", "策略 ID", 0, 0)
        self._entry(form, "start", "開始日期", 0, 2)
        self._entry(form, "end", "結束日期", 0, 4)
        self._entry(form, "initial_capital", "初始資金", 1, 0)
        self._combo(form, "weighting", "權重方式", WEIGHTINGS, 1, 2)
        self._combo(form, "rebalance_frequency", "再平衡", FREQUENCIES, 1, 4)
        self._entry(form, "rebalance_anchor", "再平衡錨點", 2, 0)
        self._entry(form, "momentum_window", "動能窗口", 2, 2)
        self._entry(form, "drift_threshold", "偏離閾值", 2, 4)
        self._entry(form, "benchmark", "基準指數", 3, 0)
        self._entry(form, "target_weights", "目標權重（T=0.5,…）", 3, 2)
        self._combo(form, "cost_mode", "成本模式",
                    (COST_MODE_DEFAULT, COST_MODE_CUSTOM), 3, 4)
        self._entry(form, "commission_rate", "佣金率", 4, 0)
        self._entry(form, "min_commission", "最低佣金", 4, 2)
        self._entry(form, "slippage_bps", "滑價 bps", 4, 4)
        self._entry(form, "fx_spread_bps", "換匯 bps", 5, 0)
        ttk.Label(form, text="標的（每行 ticker,market,currency）").grid(
            row=5, column=2, sticky="w", padx=4, pady=2)
        self.universe_text = tk.Text(form, height=3, width=32)
        self.universe_text.grid(row=5, column=3, columnspan=3, sticky="we", padx=4)
        self.universe_text.insert(
            "1.0", "\n".join("LLY,US,USD" for _ in [0]))

        actions = ttk.Frame(self)
        actions.pack(fill="x", padx=8)
        self.run_button = ttk.Button(actions, text="執行回測",
                                     command=self.submit)
        self.run_button.pack(side="left", padx=4, pady=4)
        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(actions, textvariable=self.status_var).pack(side="left", padx=10)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=6)
        self.summary_text = self._text_tab("摘要")
        self.metrics_tree = self._tree_tab(
            "指標", ("指標", "值"), (200, 200))
        self.runtime_tree = self._tree_tab(
            "執行設定（唯讀）", ("項目", "值"), (240, 260))
        self.cost_tree = self._tree_tab(
            "成本來源", ("項目", "值"), (240, 320))
        self.equity_tree = self._tree_tab(
            "權益曲線",
            ("交易日", "現金", "持倉", "總資產", "曝險", "基準", "回撤"),
            (110, 110, 110, 120, 90, 110, 90))
        self.chart = tk.Canvas(self.notebook, width=600, height=220,
                               background="white", highlightthickness=0)
        self.notebook.add(self.chart, text="資產走勢")
        self.trades_tree = self._tree_tab(
            "交易明細",
            ("交易日", "代碼", "方向", "數量", "價格", "價格來源", "匯率",
             "匯率來源", "成交額", "佣金", "滑價", "匯差", "淨額", "原因"),
            (100, 80, 60, 100, 90, 80, 80, 80, 110, 80, 80, 80, 110, 140))
        self.anomaly_text = self._text_tab("異常")
        for key in ("weighting", "cost_mode"):
            self.vars[key].trace_add("write", lambda *_: self._sync_field_state())
        self._sync_field_state()

    def _sync_field_state(self) -> None:
        """Only the selected weighting / cost mode is editable."""
        weighting = self.vars["weighting"].get()
        self.entries["target_weights"].configure(
            state="normal" if weighting == "fixed" else "disabled")
        self.entries["momentum_window"].configure(
            state="normal" if weighting == "momentum" else "disabled")
        custom = self.vars["cost_mode"].get() == COST_MODE_CUSTOM
        for key in ("commission_rate", "min_commission",
                    "slippage_bps", "fx_spread_bps"):
            self.entries[key].configure(state="normal" if custom else "disabled")

    def _entry(self, parent, key: str, label: str, row: int, column: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w",
                                           padx=4, pady=2)
        entry = ttk.Entry(parent, width=18)
        entry.grid(row=row, column=column + 1, sticky="we", padx=4)
        self.entries[key] = entry
        defaults = BacktestForm()
        entry.insert(0, str(getattr(defaults, key, "")))

    def _combo(self, parent, key: str, label: str,
               values: Sequence[str], row: int, column: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w",
                                           padx=4, pady=2)
        var = tk.StringVar(value=str(values[0]))
        combo = ttk.Combobox(parent, textvariable=var, values=list(values),
                             state="readonly", width=16)
        combo.grid(row=row, column=column + 1, sticky="we", padx=4)
        self.vars[key] = var

    def _text_tab(self, title: str) -> tk.Text:
        frame = ttk.Frame(self.notebook)
        text = tk.Text(frame, height=8, wrap="none")
        text.pack(fill="both", expand=True)
        self.notebook.add(frame, text=title)
        return text

    def _tree_tab(self, title: str, columns: Sequence[str],
                  widths: Sequence[int]) -> ttk.Treeview:
        frame = ttk.Frame(self.notebook)
        tree = ttk.Treeview(frame, columns=list(columns), show="headings")
        for name, width in zip(columns, widths):
            tree.heading(name, text=name)
            tree.column(name, width=width, anchor="w")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.notebook.add(frame, text=title)
        return tree

    # -- form values --------------------------------------------------
    def form_values(self) -> BacktestForm:
        defaults = BacktestForm()
        values = {}
        for key in ("spec_id", "start", "end", "initial_capital",
                    "rebalance_anchor", "momentum_window", "drift_threshold",
                    "benchmark", "target_weights", "commission_rate",
                    "min_commission", "slippage_bps", "fx_spread_bps"):
            values[key] = self.entries[key].get()
        for key in ("weighting", "rebalance_frequency", "cost_mode"):
            values[key] = self.vars[key].get()
        rows = []
        for line in self.universe_text.get("1.0", "end").splitlines():
            parts = [part.strip() for part in line.split(",")]
            if not parts or not parts[0]:
                continue
            rows.append({
                "ticker": parts[0],
                "market": parts[1] if len(parts) > 1 else "",
                "currency": parts[2] if len(parts) > 2 else "",
            })
        values["universe"] = rows or defaults.universe
        return BacktestForm(**values)

    # -- execution ----------------------------------------------------
    def submit(self) -> None:
        if self.state == RUNNING_STATE or self._closed:
            return
        try:
            form = self.form_values()
            form.to_spec()
        except BacktestSpecError as exc:
            self.status_var.set(f"設定錯誤：{exc}")
            return
        self.state = RUNNING_STATE
        self.status_var.set("Running 執行中…")
        self.run_button.state(["disabled"])
        self.worker.submit(
            _run_job,
            {key: getattr(form, key) for key in form.__dataclass_fields__},
            self.repository_path,
            on_result=self._on_result,
            on_progress=self._on_progress,
        )

    def _on_progress(self, payload: Any) -> None:
        if not self._closed:
            self.status_var.set(f"Running 執行中… {payload}")

    def _on_result(self, ok: bool, payload: Any) -> None:
        if self._closed:
            return
        self.run_button.state(["!disabled"])
        self.state = IDLE_STATE
        if not ok:
            self.status_var.set(f"Failed：{type(payload).__name__}: {payload}")
            return
        self.result = payload.result
        self.state = "completed"
        self.status_var.set("Completed 完成")
        self.render(payload.result)

    def render(self, result: service.BacktestRunResult) -> None:
        run = result.run
        self._fill_text(self.summary_text,
                        summary_rows(run, created=result.created,
                                     request_cost_source=result.request_cost_source))
        self._fill_tree(self.metrics_tree,
                        [(label, _value(value, kind))
                         for label, value, kind in metrics_rows(run)])
        self._fill_tree(self.runtime_tree, runtime_rows(run))
        self._fill_tree(self.cost_tree,
                        cost_source_rows(run, created=result.created,
                                         request_cost_source=result.request_cost_source))
        self._fill_tree(self.equity_tree, equity_rows(run))
        self._fill_tree(self.trades_tree, trade_rows(run))
        self._fill_text(self.anomaly_text,
                        [(line, "") for line in anomaly_lines(run)])
        self._draw_chart(equity_series(run))

    @staticmethod
    def _fill_text(widget: tk.Text, rows: Iterable[tuple[str, str]]) -> None:
        widget.delete("1.0", "end")
        for label, value in rows:
            widget.insert("end", f"{label}: {value}\n")

    @staticmethod
    def _fill_tree(tree: ttk.Treeview, rows: Iterable[Sequence[Any]]) -> None:
        tree.delete(*tree.get_children())
        for row in rows:
            tree.insert("", "end", values=tuple(row))

    def _draw_chart(self, series: tuple[list[float | None], list[float | None]]) -> None:
        totals, benchmarks = series
        self.chart.delete("all")
        values = [value for value in totals if value is not None]
        if len(values) < 2:
            self.chart.create_text(300, 110, text="No equity curve")
            return
        low = min(values)
        high = max(values)
        span = high - low or 1.0
        width, height, margin = 600, 220, 16
        step = (width - 2 * margin) / (len(values) - 1)
        points = []
        for index, value in enumerate(values):
            x = margin + index * step
            y = height - margin - (value - low) / span * (height - 2 * margin)
            points.extend([x, y])
        self.chart.create_line(*points, fill="#1565c0", width=2)
        bench = [value for value in benchmarks if value is not None]
        if len(bench) >= 2:
            low_b, high_b = min(bench), max(bench)
            span_b = high_b - low_b or 1.0
            step_b = (width - 2 * margin) / (len(bench) - 1)
            bench_points = []
            for index, value in enumerate(bench):
                x = margin + index * step_b
                y = height - margin - (value - low_b) / span_b * (height - 2 * margin)
                bench_points.extend([x, y])
            self.chart.create_line(*bench_points, fill="#9e9e9e", width=1,
                                   dash=(4, 3))
        self.chart.create_rectangle(12, 8, 24, 18, fill="#1565c0", outline="")
        self.chart.create_text(30, 13, text="總資產", anchor="w", fill="#333333")
        self.chart.create_line(80, 13, 96, 13, fill="#9e9e9e", dash=(4, 3))
        self.chart.create_text(102, 13, text="基準", anchor="w", fill="#333333")

    def close(self, *, timeout: float = 2.0) -> None:
        if self._closed:
            return
        self._closed = True
        self.worker.close(timeout=timeout)
        if callable(self.on_close):
            try:
                self.on_close()
            except Exception:
                pass
        self.destroy()
