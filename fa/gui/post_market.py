"""Post-market monitor window (QDII time-zone blind spot)."""
from __future__ import annotations

import queue
import threading

import tkinter as tk
from tkinter import ttk

from fa.analysis.post_market import fetch_post_market_estimate
from fa.utils.logging_setup import get_logger

log = get_logger(__name__)


def _bounded_timeout(timeout: float = 2.0) -> float:
    """Clamp a caller-supplied close wait to two seconds (spec T-7)."""
    try:
        value = float(timeout)
    except (TypeError, ValueError):
        return 2.0
    if value != value:  # NaN
        return 2.0
    return min(max(value, 0.0), 2.0)


def _fetch_post_market_task(fund_code, save_dir, result_queue):
    """Background fetch.  Module level: it must reference no window object."""
    try:
        data = fetch_post_market_estimate(fund_code, save_dir)
        result_queue.put(("ok", data))
    except Exception as exc:  # noqa: BLE001 - 交給主線程顯示
        log.exception("盤後監控更新失敗")
        result_queue.put(("error", exc))


class PostMarketWindow(tk.Toplevel):
    def __init__(self, master, fund_code, save_dir, on_close=None):
        super().__init__(master)
        self.title(f"盘后/盘前监控 - {fund_code}")
        self.geometry("800x600")
        self.fund_code = fund_code
        self.save_dir = save_dir
        self._on_close_callback = on_close

        self.is_running = True
        self._fetching = False
        self._closed = False
        self.result_queue = queue.Queue()

        # 生命週期：close() 之後不再排任何 after() 定時器
        self._fetch_thread = None
        self._poll_id = None
        self._refresh_id = None

        self._build_ui()
        self.refresh_data()
        self._poll_id = self.after(200, self._poll_result_queue)

        # 窗口关闭时停止循环
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self):
        # 顶部信息区
        top_frame = ttk.Frame(self, padding=10)
        top_frame.pack(fill="x")
        
        self.lbl_estimate = ttk.Label(top_frame, text="计算中...", font=("Arial", 24, "bold"))
        self.lbl_estimate.pack(pady=5)
        
        self.lbl_detail = ttk.Label(top_frame, text="", font=("Arial", 11))
        self.lbl_detail.pack(pady=2)
        
        self.lbl_time = ttk.Label(top_frame, text="", foreground="#888888", font=("Arial", 9))
        self.lbl_time.pack(pady=2)

        # 表格区
        columns = ("ticker", "name", "weight", "regular", "post", "change")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=12)
        self.tree.heading("ticker", text="代码")
        self.tree.heading("name", text="名称")
        self.tree.heading("weight", text="权重(%)")
        self.tree.heading("regular", text="昨收/现价")
        self.tree.heading("post", text="盘后/盘前价")
        self.tree.heading("change", text="涨跌幅(%)")
        
        self.tree.column("ticker", width=80, anchor="center")
        self.tree.column("name", width=150, anchor="w")
        self.tree.column("weight", width=80, anchor="e")
        self.tree.column("regular", width=100, anchor="e")
        self.tree.column("post", width=100, anchor="e")
        self.tree.column("change", width=100, anchor="e")
        
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)
        
        # 颜色标签
        self.tree.tag_configure('positive', foreground='#e53935') # 红色
        self.tree.tag_configure('negative', foreground='#43a047') # 绿色
        self.tree.tag_configure('missing', foreground='#999999')  # 灰色

    def refresh_data(self):
        """啟動一次後台拉取；下一輪在上一輪完成後才排隊，避免任務堆疊。"""
        if self._closed or not self.is_running or self._fetching:
            return
        self._fetching = True

        self._fetch_thread = threading.Thread(
            target=_fetch_post_market_task,
            args=(self.fund_code, self.save_dir, self.result_queue),
            daemon=True,
            name="post-market-worker",
        )
        self._fetch_thread.start()

    def _poll_result_queue(self):
        """主線程輪詢拉取結果，並在完成後才安排下一輪刷新。"""
        if self._closed or not self.is_running:
            return
        try:
            while True:
                status, payload = self.result_queue.get_nowait()
                self._fetching = False
                if status == "ok":
                    self._update_ui(payload)
                else:
                    log.warning("盤後監控刷新失敗: %s", payload)
                # 只有這一輪真正結束後才排下一輪，避免多個線程同時拉取
                self._refresh_id = self.after(60000, self.refresh_data)
        except queue.Empty:
            pass
        self._poll_id = self.after(1000, self._poll_result_queue)

    def _update_ui(self, data):
        if self._closed or not self.is_running:
            return
            
        # 更新顶部大字
        if data.get("estimate_return") is not None:
            est = data["estimate_return"]
            # 如果是 0.00%，可能是休市状态
            if abs(est) < 0.01:
                self.lbl_estimate.configure(text=f"平盘 (或处于休市阶段)", foreground="#333333")
            else:
                color = "#e53935" if est > 0 else "#43a047" if est < 0 else "#333333"
                self.lbl_estimate.configure(text=f"当前盘前/盘后预估: {est:+.2f}%", foreground=color)
        else:
            self.lbl_estimate.configure(text="无法预估", foreground="#888888")
            
        # 昨日实际与期货参考
        yest = data.get("yesterday_official_return")
        nq = data.get("nq_futures_return")
        yest_str = f"{yest:+.2f}%" if yest is not None else "N/A"
        nq_str = f"{nq:+.2f}%" if nq is not None else "N/A"

        # 盤後預估的覆蓋率：避免把「只有部分持倉有盤後數據」的結果
        # 當成整檔基金的漲跌（Coverage Ratio + 對淨值的實際貢獻）
        cov = data.get("coverage_ratio")
        contrib = data.get("covered_weighted_return")
        
        detail = f"昨日实际净值: {yest_str}  |  纳指期货: {nq_str}"
        if cov is not None:
            detail += f"  |  持仓覆盖: {cov*100:.1f}%"
        if contrib is not None:
            detail += f"  |  已覆盖部分对净值贡献: {contrib:+.2f}%"
        
        # 状态信息拼接
        import datetime
        now = datetime.datetime.now()
        # 粗略判断美股盘前/盘后时间（北京时间）：
        # 冬令时：盘前 17:00-22:30，盘后 05:00-09:00
        # 夏令时：盘前 16:00-21:30，盘后 04:00-08:00
        # 简单起见，这里给个通用提示
        if 9 <= now.hour < 16:
            detail += "  |  ℹ️ 当前美股处于休市，数据基于昨日盘后/昨收，将于北京时间16:00盘前更新"
        elif data.get("message"):
            detail += f"  |  {data['message']}"
            
        self.lbl_detail.configure(text=detail)
        
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        self.lbl_time.configure(text=f"最后更新: {now_str} (每60秒自动刷新)")

        # 更新表格
        for item in self.tree.get_children():
            self.tree.delete(item)
            
        for h in data.get("holdings", []):
            change_val = h["change_pct"]
            if change_val is not None:
                change_str = f"{change_val:+.2f}%"
                tag = 'positive' if change_val > 0 else 'negative' if change_val < 0 else ''
            else:
                change_str = "--"
                tag = 'missing'
                
            reg_str = f"{h['regular_price']:.2f}" if h["regular_price"] else "--"
            post_str = f"{h['post_price']:.2f}" if h["post_price"] else "--"
            
            self.tree.insert("", "end", values=(
                h["ticker"], h["name"], f"{h['weight']:.2f}", 
                reg_str, post_str, change_str
            ), tags=(tag,))

    def close(self, *, timeout: float = 2.0):
        """停止刷新循環：取消定時器、等拉取線程退出，再銷毀窗口。"""
        if self._closed:
            return
        self._closed = True
        self.is_running = False

        for after_id in (self._poll_id, self._refresh_id):
            if after_id:
                try:
                    self.after_cancel(after_id)
                except Exception:
                    log.debug("取消定時器失敗: %s", after_id, exc_info=True)
        self._poll_id = self._refresh_id = None

        thread = self._fetch_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=_bounded_timeout(timeout))

        try:
            self.destroy()
        finally:
            callback = self._on_close_callback
            if callback is not None:
                try:
                    callback()
                except Exception:
                    log.debug("盤後監控窗口關閉回調失敗", exc_info=True)

    def on_close(self):
        self.close()
