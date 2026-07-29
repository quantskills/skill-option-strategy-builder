"""
data_source.py — Pandadata 适配层（期权策略构建器）

三层回退：
  1) 生产：import panda_data SDK 直连（组织标准）。
  2) 无 SDK：回退 examples/sample_data/*.json 内置样本，保证离线可跑。
  3) --prefer sample 强制样本。

接口日期格式 YYYYMMDD；symbol 传 "" 或省略表示全市场。
SDK 返回可能是 DataFrame / list[dict] / {"result":[...]} / gateway
dataframe({"type":"dataframe","columns":[...],"rows":[[...]]}) 四种形态，统一成 list[dict]。
"""
from __future__ import annotations
import json
from datetime import datetime, timedelta
from pathlib import Path

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "examples" / "sample_data"


def _sdk_available() -> bool:
    try:
        import panda_data  # noqa: F401
        return True
    except Exception:
        return False


def _call_sdk(method: str, **params) -> list[dict]:
    """调用 SDK 并把四种返回形态统一为 list[dict]。"""
    import panda_data
    fn = getattr(panda_data, method)
    res = fn(**params)
    # 1) DataFrame
    try:
        import pandas as pd
        if isinstance(res, pd.DataFrame):
            return res.to_dict("records")
    except Exception:
        pass
    # 2) list[dict]
    if isinstance(res, list):
        return res
    # 3/4) dict：{"result":[...]} 或 gateway dataframe {"type","columns","rows"}
    if isinstance(res, dict):
        r = res.get("result", res)
        if isinstance(r, dict) and r.get("type") == "dataframe":
            cols = r.get("columns", [])
            return [dict(zip(cols, row)) for row in r.get("rows", [])]
        if isinstance(r, list):
            return r
    return []


def _call_sample(method: str, **params) -> list[dict]:
    """离线样本：读 examples/sample_data/{method}.json，按 underlying_symbol/symbol/call_put_code 过滤。"""
    f = SAMPLE_DIR / f"{method}.json"
    if not f.exists():
        return []
    rows = json.loads(f.read_text(encoding="utf-8"))

    und = params.get("underlying_symbol")
    if und and rows and "underlying_symbol" in rows[0]:
        rows = [r for r in rows if r.get("underlying_symbol") == und]

    cp = params.get("call_put_code")
    if cp and rows and "call_put_code" in rows[0]:
        rows = [r for r in rows if r.get("call_put_code") == cp]

    symbol = params.get("symbol")
    if symbol and rows and "symbol" in rows[0]:
        syms = [symbol] if isinstance(symbol, str) else list(symbol)
        rows = [r for r in rows if r.get("symbol") in syms]

    return rows


class DataSource:
    def __init__(self, prefer: str | None = None):
        if prefer == "sample":
            self.backend = "sample"
        elif prefer == "sdk" or _sdk_available():
            self.backend = "sdk"
        elif SAMPLE_DIR.exists():
            self.backend = "sample"
        else:
            self.backend = "none"

    def fetch(self, method: str, **params) -> list[dict]:
        if self.backend == "sdk":
            try:
                return _call_sdk(method, **params)
            except Exception as e:  # noqa: BLE001
                print(f"[data_source] SDK {method} failed: {e}")
                return []
        if self.backend == "sample":
            return _call_sample(method, **params)
        return []

    # --- 期权语义化封装 ---
    def option_static(self, start, end, underlying_symbol=None, call_put_code=None):
        """合约要素：strike_price/call_put_code(CO/PO)/delisted_date/contract_size/
        exercise_style/margin(单位保证金)/underlying_pre_close/open_interest/pre_close。"""
        p = {"start_date": start, "end_date": end}
        if underlying_symbol:
            p["underlying_symbol"] = underlying_symbol
        if call_put_code:
            p["call_put_code"] = call_put_code
        return self.fetch("get_option_static", **p)

    def option_daily(self, start, end, symbol=None):
        """期权日线：date/symbol/close/settlement/volume/open_interest。"""
        return self.fetch("get_option_daily", start_date=start, end_date=end, symbol=symbol or "")

    def option_risk_indicators(self, start, end, symbol=None):
        """希腊字母：symbol/name/exchange/date/delta/gamma/vega/theta/rho。"""
        return self.fetch("get_option_risk_indicators", start_date=start, end_date=end, symbol=symbol or "")

    def option_iv(self, start, end, symbol=None):
        """隐含波动率：date/symbol/implied_volatility。"""
        return self.fetch("get_option_implied_volatility", start_date=start, end_date=end, symbol=symbol or "")

    def latest_nonempty_static(self, underlying, as_of, max_lookback_days=5):
        """Find the latest non-empty option-static snapshot on or before as_of."""
        requested = datetime.strptime(as_of, "%Y%m%d")
        attempted = []
        for offset in range(max_lookback_days + 1):
            date = (requested - timedelta(days=offset)).strftime("%Y%m%d")
            attempted.append(date)
            rows = self.option_static(date, date, underlying_symbol=underlying)
            if rows:
                if self.backend == "sample":
                    symbols = [row.get("symbol") for row in rows if row.get("symbol")]
                    observed = [
                        str(row["date"]) for row in self.option_daily(date, date, symbols)
                        if row.get("date") and str(row["date"]) <= as_of
                    ]
                    if observed:
                        date = max(observed)
                return rows, date, attempted
        return [], None, attempted
