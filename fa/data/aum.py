"""Fund size (AUM) lookup."""

from datetime import datetime

import akshare as ak

from fa.utils.logging_setup import get_logger


log = get_logger(__name__)

def fetch_aum(fund_code: str) -> tuple:
    """从雪球接口自动获取基金最新规模。返回 (规模亿元, 日期)。失败返回 (None, None)。"""
    try:
        info = ak.fund_individual_basic_info_xq(symbol=fund_code)
        # 提取最新规模
        aum_series = info.loc[info['item'] == '最新规模', 'value']
        if not aum_series.empty:
            aum_str = aum_series.values[0]
            # 提取数字（部分接口返回如 "47.06亿" 的字符串）
            import re
            match = re.search(r"[\d\.]+", str(aum_str))
            if match:
                aum = float(match.group())
                log.info(f"[{fund_code}] 自动获取最新规模: {aum} 亿元")
                return aum, datetime.now().strftime("%Y-%m-%d")
    except Exception as e:
        log.warning(f"[{fund_code}] 自动获取规模失败: {e}")
    return None, None
