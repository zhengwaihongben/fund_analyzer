"""Quarter-label parsing for holdings data."""

import re

import pandas as pd


_QUARTER_RE = re.compile(r"^(\d{4})Q([1-4])$")


def latest_quarter_label(df_holdings: pd.DataFrame):
    """返回持倉表中最新的季度標籤（YYYYQn），找不到時返回 None。

    不再假設數據源按季度倒序返回，而是顯式解析後取最大值。
    """
    if df_holdings is None or df_holdings.empty:
        return None
    if "QuarterLabel" not in df_holdings.columns:
        return None

    labels = [str(x) for x in df_holdings["QuarterLabel"].dropna().unique()]
    latest_key, latest_label = None, None
    for label in labels:
        m = _QUARTER_RE.match(label)
        if not m:
            continue
        key = (int(m.group(1)), int(m.group(2)))
        if latest_key is None or key > latest_key:
            latest_key, latest_label = key, label
    if latest_label:
        return latest_label
    # 季度標籤格式突變時退回到原順序的最後一個
    return labels[-1] if labels else None
