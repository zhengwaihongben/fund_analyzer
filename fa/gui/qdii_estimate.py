# -*- coding: utf-8 -*-
"""QDII 估值視窗（V2.4 Stage B）。

三種呈現路徑都只渲染 application service 給的 ``EstimationView``：

* ``live``     — worker 呼叫 ``run_estimate``，``persisted`` 來自 read-back；
* ``stored``   — worker 呼叫 ``load_estimate``，不估算、不寫入；
* ``handover`` — 分析結果直接交接，標籤明說「未寫入資料庫」。

這裡不 import 估算引擎、不讀 JSON、不碰 SQLite：所有欄位都從 view 取，
GUI 只決定「哪個已保存的欄位怎麼格式化」。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

from fa.application.estimation import (
    NO_ASSET_SOURCE_TEXT,
    EstimationView,
    load_estimate,
    run_estimate,
)
from fa.gui.formatting import (
    MISSING,
    color_for,
    date_text,
    look_ahead_text,
    number,
    pct,
    quality_text,
    scope_text,
    status_text,
)
from fa.gui.worker import WorkerBridge
from fa.utils.logging_setup import get_logger


log = get_logger(__name__)

_POSITIVE = "#e53935"   # 紅漲
_NEGATIVE = "#43a047"   # 綠跌
_MUTED = "#888888"

# (key, heading, width)：表格欄位順序即規格 §6.3 的順序。
TABLE_COLUMNS = (
    ("ticker", "代碼", 82),
    ("name", "名稱", 150),
    ("asset_type", "類型", 90),
    ("market", "市場", 58),
    ("currency", "幣種", 58),
    ("weight", "權重", 70),
    ("base_price_date", "基準日", 92),
    ("target_price_date", "目標日", 92),
    ("local_return", "本地回報", 90),
    ("fx_return", "匯率回報", 90),
    ("cny_return", "人民幣回報", 95),
    ("contribution", "淨值貢獻", 90),
    ("status", "狀態", 110),
)

HISTORY_COLUMNS = (
    ("fund_code", "基金代碼", 80),
    ("target_nav_date", "估值基準日", 105),
    ("status", "狀態", 110),
    ("estimated_nav", "估算淨值", 90),
    ("coverage_ratio", "覆蓋率", 80),
    ("data_quality", "資料品質", 80),
    ("run_id", "Run ID", 250),
)

_PERSISTED_TEXT = {
    "live-saved": "已寫入（read-back 確認）",
    "live-missing": "未寫入（僅檔案）",
    "stored": "已保存紀錄（唯讀）",
    "handover": "未寫入資料庫（分析結果交接）",
}

_STATUS_HEADLINE = ("estimated", "partial")


# --------------------------------------------------------------------------- #
# 呈現用的純函式（不建 Tk、不讀 DB、不做任何估值運算）
# --------------------------------------------------------------------------- #


def _ticker(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return MISSING
    return str(value)


def _nav(value: Any) -> str:
    if value is None:
        return MISSING
    try:
        return number(value, digits=4)
    except (TypeError, ValueError):
        return MISSING


def _return(value: Any) -> str:
    return pct(value, digits=2, signed=True)


def _ratio(value: Any) -> str:
    return pct(value, digits=1, signed=False)


def _weight(value: Any) -> str:
    return pct(value, digits=2, signed=False)


def summary_fields(view: EstimationView) -> list[tuple[str, str]]:
    """Summary panel: every field comes straight from the stored record."""
    record = view.record or {}
    if view.mode == "stored":
        persisted = _PERSISTED_TEXT["stored"]
    elif view.mode == "live":
        persisted = _PERSISTED_TEXT["live-saved" if view.persisted else "live-missing"]
    else:
        persisted = _PERSISTED_TEXT["handover"]
    return [
        ("基金代碼", str(record.get("fund_code") or MISSING)),
        ("上次官方淨值", _nav(record.get("base_nav"))),
        ("估算淨值", _nav(record.get("estimated_nav"))),
        ("估算漲跌", _return(record.get("estimated_return"))),
        ("估算基準日", date_text(record.get("target_nav_date"))),
        ("基準淨值日", date_text(record.get("base_nav_date"))),
        ("持倉覆蓋率", _ratio(record.get("coverage_ratio"))),
        ("未覆蓋權重", _ratio(record.get("uncovered_weight"))),
        ("資料品質", quality_text(record.get("data_quality"))),
        (
            "前視狀態",
            look_ahead_text(
                record.get("look_ahead_status"), safe=record.get("look_ahead_safe")
            ),
        ),
        ("模型版本", str(record.get("model_version") or MISSING)),
        ("Run ID", view.run_id or MISSING),
        ("寫入狀態", persisted),
    ]


def asset_table_rows(view: EstimationView) -> list[tuple]:
    """Rows in the fixed §6.3 column order; no number is invented."""
    rows: list[tuple] = []
    for row in view.asset_rows:
        status = row.get("status")
        ok = status == "ok"
        cny = _return(row.get("cny_return")) if ok else status_text(status)
        contribution = _return(row.get("contribution")) if ok else status_text(status)
        rows.append(
            (
                _ticker(row.get("ticker")),
                str(row.get("name") or MISSING),
                str(row.get("asset_type") or MISSING),
                str(row.get("market") or MISSING),
                str(row.get("currency") or MISSING),
                _weight(row.get("weight")),
                date_text(row.get("base_price_date")),
                date_text(row.get("target_price_date")),
                _return(row.get("local_return")),
                _return(row.get("fx_return")),
                cny,
                contribution,
                status_text(status) if status is not None else MISSING,
            )
        )
    return rows


def _holding_meta(inputs: dict[str, Any]) -> dict[str, Any]:
    """The real inputs payload nests holdings metadata under ``holdings``.

    ``_inputs_snapshot()`` writes ``{"holdings": {"quarter", "scope",
    "disclosure_date", "disclosure_date_estimated", "source", …}, "assets": […]}``;
    older/synthetic payloads put the same keys at the top level, so both shapes
    are accepted, the nested one first.
    """
    nested = inputs.get("holdings")
    return nested if isinstance(nested, dict) else {}


def quality_lines(view: EstimationView) -> list[str]:
    """Data-quality block: only facts the view actually carries."""
    record = view.record or {}
    inputs = view.inputs if isinstance(view.inputs, dict) else {}
    meta = _holding_meta(inputs)
    lines: list[str] = []

    quarter = record.get("holdings_quarter") or meta.get("quarter") or MISSING
    scope = scope_text(record.get("holdings_scope") or meta.get("scope"))
    lines.append(f"持倉季度：{quarter} · 口徑：{scope}")

    disclosure = record.get("disclosure_date") or meta.get("disclosure_date")
    if disclosure:
        estimated = bool(
            record.get("disclosure_date_estimated")
            or meta.get("disclosure_date_estimated")
        )
        suffix = "（推算）" if estimated else ""
        lines.append(f"披露日：{date_text(disclosure)}{suffix}")

    holdings_source = record.get("holdings_source") or meta.get("source")
    if holdings_source:
        lines.append(f"持倉來源：{holdings_source}")

    fx_source = record.get("fx_source") or inputs.get("fx_source")
    if fx_source:
        lines.append(f"匯率來源：{fx_source}")

    price_types = sorted(
        {
            str(item.get("used_price_type"))
            for item in inputs.get("assets") or []
            if isinstance(item, dict) and item.get("used_price_type")
        }
    )
    if price_types:
        lines.append("價格型別：" + "、".join(price_types))

    lines.append(
        "前視狀態："
        + look_ahead_text(
            record.get("look_ahead_status"), safe=record.get("look_ahead_safe")
        )
    )

    uncovered = record.get("uncovered_weight")
    if uncovered is not None:
        lines.append(f"未覆蓋權重：{_ratio(uncovered)}（未估算，不代表 0% 報酬）")

    if view.asset_source:
        lines.append(f"資產明細來源：{view.asset_source}")
    if not view.asset_rows:
        lines.append(view.message or NO_ASSET_SOURCE_TEXT)
    for warning in view.warnings:
        lines.append(f"警告：{warning}")
    return lines


def history_rows(rows: list[dict[str, Any]]) -> list[tuple]:
    """History list, exactly the rows the service returned and in its order."""
    result: list[tuple] = []
    for row in rows:
        result.append(
            (
                str(row.get("fund_code") or MISSING),
                date_text(row.get("target_nav_date")),
                status_text(row.get("status")),
                _nav(row.get("estimated_nav")),
                _ratio(row.get("coverage_ratio")),
                quality_text(row.get("data_quality")),
                str(row.get("run_id") or MISSING),
            )
        )
    return result


# --------------------------------------------------------------------------- #
# Worker 進入點：module level，第一參數是 ProgressReporter
# --------------------------------------------------------------------------- #


def _live_estimate_job(reporter, fund_code, save_dir):
    reporter.report("正在估算 QDII 淨值…")
    view = run_estimate(fund_code, save_dir)
    reporter.report("估算完成")
    return view


def _load_estimate_job(reporter, run_id, save_dir):
    reporter.report("正在讀取估值紀錄…")
    view = load_estimate(run_id, save_dir=save_dir)
    reporter.report("讀取完成")
    return view


def _handover_view(estimate: dict[str, Any]) -> EstimationView:
    """Wrap an analysis handover record; it is never claimed as persisted."""
    holdings = estimate.get("holdings") if isinstance(estimate, dict) else None
    rows = [dict(item) for item in holdings or [] if isinstance(item, dict)]
    return EstimationView(
        record=dict(estimate or {}),
        mode="handover",
        run_id=str(estimate.get("run_id")) if estimate and estimate.get("run_id") else None,
        persisted=False,
        asset_rows=rows,
        asset_source="估值結果（未寫入資料庫）",
        warnings=[str(item) for item in (estimate or {}).get("warnings") or []],
        message="此結果來自本次分析，未經服務層寫入確認" if rows else NO_ASSET_SOURCE_TEXT,
    )


class QdiiEstimateWindow(tk.Toplevel):
    """Full estimation view: live, stored and analysis-handover modes."""

    def __init__(
        self,
        master,
        fund_code,
        save_dir,
        estimate=None,
        on_close=None,
        *,
        mode: str = "live",
        run_id: str | None = None,
        on_saved=None,
    ):
        super().__init__(master)
        self.title(f"QDII 淨值估算 - {fund_code}")
        self.geometry("1180x760")
        self.minsize(980, 620)
        self.fund_code = fund_code
        self.save_dir = save_dir
        self.mode = "handover" if estimate is not None else mode
        self.run_id = run_id
        self.view: EstimationView | None = None

        self._on_close_callback = on_close
        self._on_saved = on_saved
        self._closed = False
        self.worker = WorkerBridge(self)

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.close)
        if estimate is not None:
            self._render_view(_handover_view(estimate))
        elif mode == "stored" and run_id:
            self.load_stored(run_id)
        else:
            self.refresh()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=(12, 8, 12, 4))
        top.pack(fill="x")
        self.lbl_headline = ttk.Label(
            top, text="準備中…", font=("Arial", 20, "bold"), foreground="#333333"
        )
        self.lbl_headline.pack(pady=(0, 4))
        self.lbl_status = ttk.Label(top, text="", foreground=_MUTED)
        self.lbl_status.pack(anchor="w")

        self.summary_frame = ttk.LabelFrame(self, text="摘要", padding=8)
        self.summary_frame.pack(fill="x", padx=12, pady=(0, 6))

        self.quality_frame = ttk.LabelFrame(self, text="資料品質", padding=8)
        self.quality_frame.pack(fill="x", padx=12, pady=(0, 6))
        # Warnings are shown verbatim, so the block must not clip them: a
        # scrollbar and a taller viewport (B-3 review D6).
        self.quality_text = tk.Text(
            self.quality_frame, height=10, wrap="word", relief="flat",
            background="#f5f5f5", font=("Microsoft JhengHei UI", 10),
        )
        self.quality_scroll = ttk.Scrollbar(
            self.quality_frame, orient="vertical", command=self.quality_text.yview
        )
        self.quality_text.configure(yscrollcommand=self.quality_scroll.set)
        self.quality_text.pack(side="left", fill="both", expand=True)
        self.quality_scroll.pack(side="right", fill="y")
        self.quality_text.configure(state="disabled")

        table_frame = ttk.LabelFrame(self, text="資產明細", padding=8)
        table_frame.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        keys = tuple(key for key, _, _ in TABLE_COLUMNS)
        self.asset_tree = ttk.Treeview(
            table_frame, columns=keys, show="headings", height=14
        )
        for key, heading, width in TABLE_COLUMNS:
            self.asset_tree.heading(key, text=heading)
            anchor = "w" if key in ("name", "asset_type", "status") else "center"
            self.asset_tree.column(key, width=width, anchor=anchor)
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.asset_tree.yview)
        self.asset_tree.configure(yscrollcommand=scroll.set)
        self.asset_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.asset_tree.tag_configure("positive", foreground=_POSITIVE)
        self.asset_tree.tag_configure("negative", foreground=_NEGATIVE)
        self.asset_tree.tag_configure("muted", foreground=_MUTED)

        bar = ttk.Frame(self, padding=(12, 0, 12, 10))
        bar.pack(fill="x")
        self.action_btn = ttk.Button(bar, text="重新估算", command=self.refresh)
        self.action_btn.pack(side="left")
        ttk.Button(bar, text="關閉", command=self.close).pack(side="right")

    def start_live(self) -> None:
        """Switch the window to live estimation and start it.

        A handover or stored window must still be able to estimate: the old
        implementation simply returned for ``handover``, which made
        「重新估算」 a dead button (B-3 review F1).
        """
        if self._closed:
            return
        self.mode = "live"
        self.run_id = None
        self.action_btn.configure(text="重新估算")
        self.refresh()

    def show_handover(self, record: dict[str, Any]) -> None:
        """Show an analysis handover record in an already-open window.

        The analysis button must reopen the *handover* view; reusing a live or
        stored window silently would leave the old content on screen (B-3
        review: the handover entry was unreachable).
        """
        if self._closed:
            return
        self.mode = "handover"
        self.run_id = None
        self.action_btn.configure(text="重新估算", state="normal")
        self._render_view(_handover_view(record))
        self.lift()

    def refresh(self) -> None:
        if self.mode == "stored":
            self.load_stored(self.run_id)
            return
        if self.mode == "handover":
            # No estimate was written for the handover record; re-running means
            # switching to the live path rather than looking at stale data.
            self.start_live()
            return
        if self._closed or self.worker.is_running:
            return
        self.action_btn.configure(state="disabled")
        self.lbl_headline.configure(text="計算中…", foreground="#333333")
        self.lbl_status.configure(text="已送交 worker，UI 保持可用")
        self.worker.submit(
            _live_estimate_job,
            self.fund_code,
            self.save_dir,
            on_progress=self._on_progress,
            on_result=self._on_worker_result,
        )

    def load_stored(self, run_id: str | None) -> None:
        if self._closed or self.worker.is_running or not run_id:
            return
        self.mode = "stored"
        self.run_id = str(run_id)
        self.action_btn.configure(text="重新載入")
        self.action_btn.configure(state="disabled")
        self.lbl_headline.configure(text="讀取紀錄中…", foreground="#333333")
        self.worker.submit(
            _load_estimate_job,
            str(run_id),
            self.save_dir,
            on_progress=self._on_progress,
            on_result=self._on_worker_result,
        )

    def _on_progress(self, message: Any) -> None:
        if not self._closed and isinstance(message, str):
            self.lbl_status.configure(text=message)

    def _on_worker_result(self, ok: bool, payload: Any) -> None:
        if self._closed:
            return
        self.action_btn.configure(state="normal")
        if ok and isinstance(payload, EstimationView):
            # Follow the view: a stored lookup must not leave the window in
            # live mode (the 重新載入 button would then re-estimate).
            self.mode = payload.mode
            self.run_id = payload.run_id
            self._render_view(payload)
            if payload.mode == "live" and payload.persisted and self._on_saved:
                self._on_saved(payload.run_id)
            return
        if not ok and isinstance(payload, LookupError):
            self.lbl_headline.configure(text="找不到估值紀錄", foreground=_MUTED)
            self.lbl_status.configure(text=str(payload))
            return
        self.lbl_headline.configure(text="讀取失敗", foreground=_MUTED)
        self.lbl_status.configure(text=str(payload))

    # ------------------------------------------------------------------
    def _render_view(self, view: EstimationView) -> None:
        if self._closed:
            return
        self.view = view
        record = view.record or {}
        status = record.get("status")
        change = record.get("estimated_return")

        if status in _STATUS_HEADLINE and change is not None:
            label = "預估" if status == "partial" else "估算"
            self.lbl_headline.configure(
                text=f"{label}漲跌 {_return(change)}", foreground=color_for(change)
            )
        elif view.mode == "handover":
            self.lbl_headline.configure(text="估值結果（分析交接）", foreground=_MUTED)
        else:
            self.lbl_headline.configure(
                text=f"無法估算（{status_text(status)}）", foreground=_MUTED
            )

        self.lbl_status.configure(
            text=f"{view.mode} · {status_text(status)} · 來源：{view.asset_source or '—'}"
        )
        self._render_summary(view)
        self._render_quality(view)
        self._render_assets(view)

        if view.mode == "live" and not view.persisted:
            self.action_btn.configure(text="重新估算")
        elif view.mode == "stored":
            self.action_btn.configure(text="重新載入")

    def _render_summary(self, view: EstimationView) -> None:
        for child in self.summary_frame.winfo_children():
            child.destroy()
        for index, (label, value) in enumerate(summary_fields(view)):
            row, col = divmod(index, 3)
            cell = ttk.Frame(self.summary_frame)
            cell.grid(row=row, column=col, sticky="w", padx=(0, 22), pady=2)
            ttk.Label(cell, text=label + "：", foreground=_MUTED).pack(side="left")
            ttk.Label(cell, text=value, font=("Arial", 10, "bold")).pack(side="left")

    def _render_quality(self, view: EstimationView) -> None:
        self.quality_text.configure(state="normal")
        self.quality_text.delete("1.0", "end")
        self.quality_text.insert("1.0", "\n".join(quality_lines(view)))
        self.quality_text.configure(state="disabled")

    def _render_assets(self, view: EstimationView) -> None:
        for item in self.asset_tree.get_children():
            self.asset_tree.delete(item)
        for row, raw in zip(asset_table_rows(view), view.asset_rows):
            ok = raw.get("status") == "ok"
            value = raw.get("cny_return")
            if not ok:
                tag = "muted"
            elif value is not None and value > 0:
                tag = "positive"
            elif value is not None and value < 0:
                tag = "negative"
            else:
                tag = ""
            self.asset_tree.insert("", "end", values=row, tags=(tag,))

    # ------------------------------------------------------------------
    def close(self, *, timeout: float = 2.0) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.worker.close(timeout=timeout)
        except Exception:  # noqa: BLE001 - 關閉時不可再拋
            log.debug("關閉 worker 失敗", exc_info=True)
        try:
            self.destroy()
        finally:
            callback = self._on_close_callback
            if callback is not None:
                try:
                    callback()
                except Exception:  # noqa: BLE001
                    log.debug("QDII 視窗關閉回調失敗", exc_info=True)
