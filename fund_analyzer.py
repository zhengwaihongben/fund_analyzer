#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Fund Analyzer — 输入基金代码，一键输出净值、持仓、技术指标、风险指标

Usage:
    python fund_analyzer.py 017436
    python fund_analyzer.py 017436 --benchmark .NDX --risk-free 0.02
    python fund_analyzer.py 017436 --show -v

實作已拆分到 ``fa/`` 套件（設定 / 數據 / 分析 / 繪圖 / GUI）。本文件保留為
命令列入口與舊有 ``import fund_analyzer`` 路徑的兼容層。
"""
from fa import *  # noqa: F401,F403
from fa import __all__ as __all__  # noqa: F401
from fa import main  # noqa: F401

if __name__ == "__main__":
    main()
