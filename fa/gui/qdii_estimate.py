# -*- coding: utf-8 -*-
"""QDII NAV estimation window.

Shows the estimated NAV next to the last official NAV, the coverage ratio and
the per-holding contributions, so the number is never presented as if it were
an official NAV.  Estimation runs in a worker thread; the UI is updated from a
queue on the main thread (same pattern as the post-market monitor).
"""
from __future__ import annotations

import queue
import threading

import tkinter as tk
from tkinter import ttk

from fa.analysis.nav_estimator import estimate_qdii_nav
from fa.utils.logging_setup import get_logger


log = get_logger(__name__)

_POSITIVE = "#e53935"   # 紅漲
_NEGATIVE = "#43a047"   # 綠跌
_MUTED = "#888888"


def _pct(value, digits: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:+.{digits}f}%"


class QdiiEstimateWindow(tk.Toplevel):
    def __init__(self, master, fund_code, save_dir, estimate=None, on_close=None):
        super().__init__(master)
        self.title(f"QDII 淨值估算 - {fund_code}")
        self.geometry("980x620")
        self.fund_code = fund_code
        self.save_dir = save_dir
        self._on_close_callback = on_close
        self._closed = False
        self._worker = None
        self._poll_id = None
        self.result_queue = queue.Queue()

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.close)
        if estimate:
            self._update_ui(estimate)
        else:
            self.refresh()

    # ------------------------------------------------------------------
    def _build_ui(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        self.lbl_headline = ttk.Label(top, text="估算中...", font=("Arial", 22, "bold"))
        self.lbl_headline.pack(pady=4)

        grid = ttk.Frame(top)
        grid.pack(fill="x", pady=4)
        self.var_fields: dict[str, tk.StringVar] = {}
        fields = [
            ("official", "上次官方淨值"),
            ("estimated", "估算淨值"),
            ("change", "估算漲跌"),
            ("coverage", "持倉覆蓋率"),
            ("uncovered", "未覆蓋權重"),
            ("fx", "匯率貢獻"),
            ("local", "本地貢獻"),
            ("target_date", "估算基準日"),
            ("disclosure", "持倉披露日"),
            ("market_date", "市場資料日"),
        ]
        for idx, (key, label) in enumerate(fields):
            row, col = divmod(idx, 4)
            cell = ttk.Frame(grid)
            cell.grid(row=row, column=col, sticky="w", padx=(0, 18), pady=3)
            ttk.Label(cell, text=label + "：", foreground=_MUTED).pack(side="left")
            var = tk.StringVar(value="--")
            self.var_fields[key] = var
            ttk.Label(cell, textvariable=var, font=("Arial", 11, "bold")).pack(side="left")

        self.lbl_note = ttk.Label(top, text="", foreground=_MUTED, wraplength=920,
                                  justify="left")
        self.lbl_note.pack(fill="x", pady=(2, 6))

        columns = ("ticker", "name", "weight", "currency", "local", "fx", "cny", "contrib")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=14)
        headings = {
            "ticker": "代碼", "name": "名稱", "weight": "權重(%)", "currency": "幣種",
            "local": "本地回報", "fx": "匯率回報", "cny": "人民幣回報", "contrib": "淨值貢獻",
        }
        for key, text in headings.items():
            self.tree.heading(key, text=text)
        widths = {"ticker": 90, "name": 200, "weight": 80, "currency": 70,
                  "local": 100, "fx": 100, "cny": 110, "contrib": 100}
        for key, width in widths.items():
            anchor = "w" if key == "name" else "center"
            self.tree.column(key, width=width, anchor=anchor)
        self.tree.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        self.tree.tag_configure("positive", foreground=_POSITIVE)
        self.tree.tag_configure("negative", foreground=_NEGATIVE)
        self.tree.tag_configure("missing", foreground=_MUTED)

        bar = ttk.Frame(self, padding=(10, 0, 10, 10))
        bar.pack(fill="x")
        self.refresh_btn = ttk.Button(bar, text="重新估算", command=self.refresh)
        self.refresh_btn.pack(side="left")
        ttk.Button(bar, text="關閉", command=self.close).pack(side="right")

    # ------------------------------------------------------------------
    def refresh(self):
        if self._closed or (self._worker is not None and self._worker.is_alive()):
            return
        self.refresh_btn.configure(state="disabled")
        self.lbl_headline.configure(text="計算中...", foreground="#333333")
        self._worker = threading.Thread(target=self._task, daemon=True)
        self._worker.start()
        self._poll_id = self.after(200, self._poll_result_queue)

    def _task(self):
        try:
            estimate = estimate_qdii_nav(self.fund_code, self.save_dir)
            self.result_queue.put(("ok", estimate))
        except Exception as exc:  # noqa: BLE001 - 交給主线程顯示
            log.exception("QDII 淨值估算失敗")
            self.result_queue.put(("error", exc))

    def _poll_result_queue(self):
        if self._closed:
            return
        try:
            status, payload = self.result_queue.get_nowait()
        except queue.Empty:
            self._poll_id = self.after(200, self._poll_result_queue)
            return

        if status == "ok":
            self._update_ui(payload)
        else:
            self.lbl_headline.configure(text="估算失敗", foreground="#333333")
            self.lbl_note.configure(text=str(payload))
        self.refresh_btn.configure(state="normal")
        self._poll_id = None

    # ------------------------------------------------------------------
    def _update_ui(self, data: dict):
        if self._closed:
            return
        status = data.get("status")
        change = data.get("estimated_return")

        if change is not None and status in ("estimated", "partial"):
            color = _POSITIVE if change > 0 else _NEGATIVE if change < 0 else "#333333"
            label = "預估" if status == "partial" else "估算"
            self.lbl_headline.configure(
                text=f"{label}漲跌 {_pct(change)}", foreground=color)
        else:
            self.lbl_headline.configure(text=f"無法估算（{status}）", foreground=_MUTED)

        disclosure = data.get("disclosure_date") or "--"
        if data.get("disclosure_date") and data.get("disclosure_date_estimated", True):
            disclosure += "（推算）"

        values = {
            "official": f"{data['base_nav']:.4f}" if data.get("base_nav") else "--",
            "estimated": f"{data['estimated_nav']:.4f}" if data.get("estimated_nav") else "--",
            "change": _pct(change),
            "coverage": _pct(data.get("coverage_ratio"), 1).lstrip("+"),
            "uncovered": _pct(data.get("uncovered_weight"), 1).lstrip("+"),
            "fx": _pct(data.get("fx_contribution")),
            "local": _pct(data.get("local_contribution")),
            "target_date": data.get("target_nav_date") or "--",
            "disclosure": disclosure,
            "market_date": data.get("market_data_date") or "--",
        }
        for key, text in values.items():
            self.var_fields[key].set(text)

        note_parts = []
        if data.get("holdings_quarter"):
            scope = {"top10": "前十大（揭露口徑）", "full_portfolio": "完整組合"}.get(
                data.get("holdings_scope"), "範圍未知")
            note_parts.append(
                f"持倉 {data['holdings_quarter']} · {scope}（權重為披露值，非實際持倉）")
        if data.get("look_ahead_safe"):
            note_parts.append("只用目標日以前已披露的季度（無前視偏差）")
        if data.get("nav_lag_days"):
            note_parts.append(f"官方淨值落後 {data['nav_lag_days']} 個交易日")
        note_parts.extend(data.get("warnings") or [])
        if status in ("estimated", "partial"):
            note_parts.append("估算值不是官方淨值，僅供盤前參考")
        self.lbl_note.configure(text="；".join(note_parts))

        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in data.get("holdings") or []:
            ok = row.get("status") == "ok"
            tag = "missing" if not ok else (
                "positive" if (row.get("cny_return") or 0) > 0 else
                "negative" if (row.get("cny_return") or 0) < 0 else "")
            weight = row.get("weight")
            self.tree.insert("", "end", values=(
                row.get("ticker") or "",
                row.get("name") or "",
                f"{weight * 100:.2f}" if weight is not None else "--",
                row.get("currency") or "--",
                _pct(row.get("local_return")),
                _pct(row.get("fx_return")),
                _pct(row.get("cny_return")) if ok else row.get("status", "--"),
                _pct(row.get("contribution")),
            ), tags=(tag,))

    # ------------------------------------------------------------------
    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._poll_id:
            try:
                self.after_cancel(self._poll_id)
            except Exception:
                log.debug("取消定時器失敗", exc_info=True)
            self._poll_id = None
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=2.0)
        try:
            self.destroy()
        finally:
            callback = self._on_close_callback
            if callback is not None:
                try:
                    callback()
                except Exception:
                    log.debug("QDII 視窗關閉回調失敗", exc_info=True)
