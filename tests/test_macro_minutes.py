"""미국 선물 분봉 (야후): 분봉 되는 지표 구분, 늦게 끝난 봉 뒤를 1분봉으로 채우기."""
import pandas as pd

from backend import bars, macro


def _frame(symbol: str, start: str, n: int, minutes: int, price: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq=f"{minutes}min", tz="UTC")
    cols = {(k, symbol): [price + i for i in range(n)] for k in ("Open", "High", "Low", "Close")}
    cols[("Volume", symbol)] = [10.0] * n
    return pd.DataFrame(cols, index=idx)


def test_has_minutes_only_futures():
    assert macro.has_minutes("NQ=F") and macro.has_minutes("CL=F")
    assert not macro.has_minutes("^IXIC") and not macro.has_minutes("BTC-USD") and not macro.has_minutes("AAPL")


def test_stale_5m_filled_with_1m(monkeypatch):
    macro._cache.clear()
    frames = {"5m": _frame("NQ=F", "2026-11-10 00:00", 4, 5),            # 00:00~00:15 (마지막 봉은 00:20 에 끝)
              "1m": _frame("NQ=F", "2026-11-10 00:00", 32, 1, 200.0)}    # 00:00~00:31
    monkeypatch.setattr(macro, "_download", lambda syms, period, interval="1d": frames[interval])
    raw = macro.minute_candles("NQ=F", "5m")
    out = bars.aggregate_minutes(raw, 5)
    times = [c["time"] - bars.KST_SHIFT for c in out]
    start = int(pd.Timestamp("2026-11-10 00:00", tz="UTC").timestamp())
    assert times == [start + i * 300 for i in range(7)]                  # 5분봉 4개 + 1분봉으로 채운 00:20·00:25·00:30
    assert out[3]["close"] == 103.0 and out[4]["open"] == 220.0 and out[-1]["close"] == 231.0
    macro._cache.clear()
