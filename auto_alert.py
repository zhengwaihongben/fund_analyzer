#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
自动盘前/盘后监控脚本（支持外部配置文件）
"""
import tkinter as tk
from tkinter import ttk
import threading
from pathlib import Path
import datetime
import time

# 导入现有的分析核心函数
from fund_analyzer import fetch_post_market_estimate

# ================= 配置区 =================
# 配置文件路径（与脚本同目录）
CONFIG_FILE = Path(__file__).parent / "alert_config.txt"
# 输出目录
BASE_OUTPUT = Path(__file__).parent / "output"

# 触发弹窗的阈值设定
THRESHOLD_NQ = 1.0       # 纳指期货波动 > ±1%
THRESHOLD_STOCK = 3.0    # 任意个股盘后波动 > ±3%
THRESHOLD_PORTFOLIO = 1.5 # 综合预估波动 > ±1.5%

# ==========================================

def load_fund_codes():
    """从外部配置文件读取基金代码"""
    # 如果文件不存在，自动创建一个模板
    if not CONFIG_FILE.exists():
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write("# 请在此处输入需要监控的基金代码，每行一个\n")
            f.write("# 以 # 开头的行将被忽略\n")
            f.write("000369\n")
            f.write("017436\n")
        return ["000369", "017436"]
        
    codes = []
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            # 忽略空行和注释行
            if line and not line.startswith("#"):
                codes.append(line)
    return codes


class AlertWindow:
    def __init__(self, root, alerts_data):
        self.root = root
        self.root.title("⚠️ 盘前/盘后异动提醒")
        
        # 窗口居中偏右下
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        width, height = 500, 400
        x = screen_width - width - 50
        y = screen_height - height - 100
        
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.attributes("-topmost", True) # 置顶
        self.root.configure(bg="#f8f9fa")
        
        self._build_ui(alerts_data)

    def _build_ui(self, alerts_data):
        # 顶部标题
        header = tk.Frame(self.root, bg="#343a40", height=40)
        header.pack(fill="x")
        tk.Label(header, text="盘前/盘后预警 (14:30)", fg="white", bg="#343a40", font=("微软雅黑", 12, "bold")).pack(pady=8)
        
        # 内容区
        main_frame = tk.Frame(self.root, bg="#f8f9fa", padx=10, pady=10)
        main_frame.pack(fill="both", expand=True)
        
        for data in alerts_data:
            fund_code = data["fund_code"]
            est = data.get("estimate_return")
            nq = data.get("nq_futures_return")
            
            frame = tk.LabelFrame(main_frame, text=f"基金 {fund_code}", bg="#f8f9fa", font=("微软雅黑", 10, "bold"), padx=10, pady=5)
            frame.pack(fill="x", pady=5)
            
            # 大盘与预估
            text = f"纳指期货: {nq:+.2f}%  |  综合预估: {est:+.2f}%" if est else "预估: 无法计算"
            color = "#d32f2f" if (est and est > 0) else "#388e3c" if (est and est < 0) else "#333"
            tk.Label(frame, text=text, fg=color, bg="#f8f9fa", font=("Arial", 11, "bold")).pack(anchor="w")
            
            # 个股异动
            for h in data.get("holdings", []):
                chg = h.get("change_pct")
                if chg is not None and abs(chg) > THRESHOLD_STOCK:
                    c = "#d32f2f" if chg > 0 else "#388e3c"
                    txt = f"  • {h['name']} ({h['weight']:.1f}%): {chg:+.2f}%"
                    tk.Label(frame, text=txt, fg=c, bg="#f8f9fa", font=("微软雅黑", 9)).pack(anchor="w")

        # 底部关闭按钮
        tk.Button(self.root, text="我知道了", command=self.root.destroy, bg="#007bff", fg="white", font=("微软雅黑", 10), relief="flat").pack(pady=10, fill="x", padx=10)


def run_monitor():
    fund_codes = load_fund_codes()
    if not fund_codes:
        print("配置文件中没有有效的基金代码。")
        return
        
    print(f"开始扫描基金: {fund_codes}")
    alerts = []
    current_time = datetime.datetime.now()
    
    for code in fund_codes:
        save_dir = BASE_OUTPUT / code
        if not save_dir.exists():
            print(f"跳过 {code}，未找到本地数据 (请先在主程序分析一次)")
            continue
            
        try:
            data = fetch_post_market_estimate(code, save_dir)
            if data.get("status") != "success":
                continue
                
            trigger = False
            nq = data.get("nq_futures_return") or 0
            est = data.get("estimate_return") or 0
            
            if abs(nq) > THRESHOLD_NQ: trigger = True
            if abs(est) > THRESHOLD_PORTFOLIO: trigger = True
                
            for h in data.get("holdings", []):
                chg = h.get("change_pct")
                if chg is not None and abs(chg) > THRESHOLD_STOCK:
                    trigger = True
                    break
                    
            if trigger:
                alerts.append(data)
                print(f"🚨 {code} 触发预警条件！")
                
        except Exception as e:
            print(f"获取 {code} 失败: {e}")

    if alerts:
        root = tk.Tk()
        AlertWindow(root, alerts)
        root.mainloop()
    else:
        print(f"{current_time.strftime('%H:%M')} 扫描完成，所有基金均未触发异动阈值。")

if __name__ == "__main__":
    run_monitor()