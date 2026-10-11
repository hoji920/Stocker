import numpy as np
import pandas as pd

from backend.analysis import (alert_lines, analyze, drawing_value, drawing_values, find_channel,
                              find_levels, find_trendlines, rsi, trend_pattern, trendline_candidates)
from backend.demo import demo_candles
from backend.toss_client import normalize_candles


def _box_market(n=200, low=100.0, high=120.0):
    """low~high 사이를 왕복하는 박스권 → 지지선≈low, 저항선≈high 가 나와야 한다."""
    rows = []
    t = pd.date_range("2025-01-01", periods=n, freq="B")
    for i in range(n):
        phase = (np.sin(i / 6) + 1) / 2
        c = low + (high - low) * phase
        rows.append({"time": t[i].date().isoformat(), "open": c, "high": c + 0.5,
                     "low": c - 0.5, "close": c, "volume": 1e6})
    rows[-1]["close"] = (low + high) / 2      # 현재가는 박스 중간
    return rows


def test_box_levels():
    lv = find_levels(pd.DataFrame(_box_market()))
    assert lv["support"] and lv["resistance"]
    assert abs(lv["support"][0]["price"] - 99.5) < 1.5
    assert abs(lv["resistance"][0]["price"] - 120.5) < 1.5
    assert lv["support"][0]["touches"] >= 3
    assert all(1 <= x["strength"] <= 4 for x in lv["support"] + lv["resistance"])


def test_levels_sides_relative_to_close():
    candles = demo_candles("005930", "KR")
    last = candles[-1]["close"]
    lv = find_levels(pd.DataFrame(candles))
    assert all(x["price"] < last for x in lv["support"])
    assert all(x["price"] >= last for x in lv["resistance"])


def test_rsi_bounds():
    s = pd.Series(np.cumsum(np.random.default_rng(0).normal(0, 1, 300)) + 100)
    r = rsi(s).dropna()
    assert ((r >= 0) & (r <= 100)).all()
    assert rsi(pd.Series(np.arange(1, 50, dtype=float))).iloc[-1] == 100.0


def test_analyze_no_advice_words():
    out = analyze(demo_candles("AAPL", "US"), lambda p: f"${p:.2f}")
    text = " ".join(i["title"] + i["body"] for i in out["insights"])
    for banned in ("매수하세요", "매도하세요", "사세요", "파세요"):
        assert banned not in text


def _uptrend(n=200):
    """기울기 0.2/일로 오르면서 출렁이는 시장 → 상승 추세선이 잡혀야 한다."""
    t = pd.date_range("2025-01-01", periods=n, freq="B")
    rows = []
    for i in range(n):
        c = 100 + 0.2 * i + 4 * (np.sin(i / 5) + 1)
        rows.append({"time": t[i].date().isoformat(), "open": c, "high": c + 0.3,
                     "low": c - 0.3, "close": c, "volume": 1e6})
    return rows


def test_uptrend_line():
    lines = {tl["kind"]: tl for tl in find_trendlines(pd.DataFrame(_uptrend()))}
    sup = lines["support"]
    assert sup["label"] == "상승 추세선"
    assert sup["touches"] >= 3
    assert sup["price"] < _uptrend()[-1]["close"]
    assert abs(sup["slope_pct"] / 100 * sup["price"] - 0.2) < 0.05


def test_uptrend_channel_is_parallel():
    df = pd.DataFrame(_uptrend())
    ch = find_channel(df, find_trendlines(df))
    assert ch["label"] == "상승 채널" and ch["base_kind"] == "support"
    base_slope = ch["base"][1]["value"] - ch["base"][0]["value"]
    other_slope = ch["other"][1]["value"] - ch["other"][0]["value"]
    assert abs(base_slope - other_slope) < 1e-6           # 평행
    assert ch["upper"] > ch["lower"]
    assert 0 <= ch["position_pct"] <= 100
    # 사인파 진폭 8 → 채널 폭 ≈ 8 (+고가/저가 꼬리 0.6)
    assert 7 < ch["upper"] - ch["lower"] < 10


def test_alert_lines_include_channel_other_side():
    out = alert_lines(_uptrend(), [])
    assert any(lv.get("id") == "auto-channel" for lv in out["support"] + out["resistance"])


def test_trend_pattern_parallel_and_converging():
    df = pd.DataFrame(_uptrend())
    assert "평행 채널" in trend_pattern(find_trendlines(df), df["close"].iloc[-1])["title"]

    # 진폭이 줄어드는 파동 → 고점선은 내려오고 저점선은 올라감 = 수렴
    t = pd.date_range("2025-01-01", periods=200, freq="B")
    rows = []
    for i in range(200):
        c = 100 + (20 - 0.08 * i) * np.sin(i / 5)
        rows.append({"time": t[i].date().isoformat(), "open": c, "high": c + 0.3, "low": c - 0.3, "close": c, "volume": 1e6})
    df2 = pd.DataFrame(rows)
    pat = trend_pattern(find_trendlines(df2), df2["close"].iloc[-1])
    assert "수렴" in pat["title"] and "거래일 뒤" in pat["body"]


def test_candidates_ranked_distinct_with_reasons():
    df = pd.DataFrame(_uptrend())
    cands = trendline_candidates(df)
    sup = cands["support"]
    assert sup and sup[0]["id"] == "auto-support" and sup[0]["rank"] == 1
    assert [c["score"] for c in sup] == sorted([c["score"] for c in sup], reverse=True)
    assert all(1 <= c["stars"] <= 3 and "번 닿음" in c["why"] for c in sup)
    assert len(sup[0]["touch_points"]) == sup[0]["touches"]
    # 후보끼리는 '사실상 같은 선'이 아니어야 한다
    for a, b in zip(sup, sup[1:]):
        assert abs(a["price"] - b["price"]) > 1e-6 or abs(a["points"][0]["value"] - b["points"][0]["value"]) > 1e-6


def test_channel_ignores_single_spike():
    rows = _uptrend()
    rows[150]["high"] += 30          # 꼬리 하나만 튐
    df = pd.DataFrame(rows)
    ch = find_channel(df, find_trendlines(df))
    assert ch["upper"] - ch["lower"] < 12      # 스파이크(30)를 따라가지 않음
    assert ch["stars"] >= 1 and ch["touch_points"]


def test_manual_channel_values():
    times = ["d0", "d1", "d2", "d3", "d4"]
    d = {"id": "c1", "type": "channel", "p1": {"time": "d0", "price": 10.0},
         "p2": {"time": "d1", "price": 11.0}, "p3": {"time": "d1", "price": 15.0}}
    assert drawing_values(times, d) == [("내 채널 기준선", 14.0), ("내 채널 평행선", 18.0)]


def test_drawing_value_extends_by_bars():
    times = ["2026-01-01", "2026-01-02", "2026-01-05", "2026-01-06"]
    d = {"type": "trend", "p1": {"time": times[0], "price": 10.0}, "p2": {"time": times[1], "price": 11.0}}
    assert drawing_value(times, d) == 13.0
    assert drawing_value(times, {"type": "horizontal", "p1": {"time": times[0], "price": 7.0}}) == 7.0


def test_alert_lines_include_trend_and_drawings():
    candles = _uptrend()
    last = candles[-1]["close"]
    d = {"id": "d1", "type": "horizontal", "p1": {"time": candles[0]["time"], "price": last * 1.05}}
    out = alert_lines(candles, [d])
    assert any(lv.get("label") == "상승 추세선" for lv in out["support"])
    assert any(lv.get("id") == "d1" and lv["label"] == "내 수평선" for lv in out["resistance"])


def test_normalize_candles_sorts_and_parses():
    raw = [
        {"timestamp": "2026-03-26T09:00:00+09:00", "openPrice": "2", "highPrice": "3",
         "lowPrice": "1", "closePrice": "2.5", "volume": "10", "currency": "KRW"},
        {"timestamp": "2026-03-25T09:00:00+09:00", "openPrice": "1", "highPrice": "2",
         "lowPrice": "0.5", "closePrice": "1.5", "volume": "5", "currency": "KRW"},
    ]
    out = normalize_candles(raw)
    assert [c["time"] for c in out] == ["2026-03-25", "2026-03-26"]
    assert out[1]["close"] == 2.5


def _rows(closes):
    t = pd.date_range("2025-01-01", periods=len(closes), freq="B")
    return [{"time": t[i].date().isoformat(), "open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 1e6}
            for i, c in enumerate(closes)]


def _breakout_above_old_ceiling():
    """100~120 박스(고점 120 근처에서 여러 번 막힘) 뒤 130 위로 올라온 차트."""
    box = [110 + 10 * np.sin(i / 6) for i in range(150)]
    return _rows(box + list(np.linspace(box[-1], 135, 20)) + [135, 134, 133, 132, 131, 130.5, 130])


def test_support_made_of_old_highs_is_described_as_role_reversal():
    from backend.analysis import level_history
    lv = find_levels(pd.DataFrame(_breakout_above_old_ceiling()))
    s = lv["support"][0]
    assert abs(s["price"] - 120.5) < 1.5 and s["highs"] >= 2 and s["lows"] == 0
    text = level_history(s, "support")
    assert "상승이 막혔던" in text and "저항→지지" in text and "하락이 멈" not in text


def test_single_prior_high_fills_empty_resistance():
    # 위쪽엔 한 번 찍은 고점(135.5)뿐 → '저항선 없음' 대신 그 전고점을 보여준다
    lv = find_levels(pd.DataFrame(_breakout_above_old_ceiling()))
    assert len(lv["resistance"]) == 1 and lv["resistance"][0]["touches"] == 1
    assert abs(lv["resistance"][0]["price"] - 135.5) < 0.1


def test_extremes_and_no_overclaim_at_period_high():
    rows = _rows(list(np.linspace(100, 200, 300)))          # 계속 올라 마지막 봉이 최고가
    out = analyze(rows, lambda p: f"{p:.2f}")
    assert out["extremes"]["label"] == "52주" and out["extremes"]["high"]["price"] == 200.5
    none = next(c for c in out["insights"] if c["title"] == "위쪽 저항선 없음")
    assert "300일 안에서는" in none["body"] and "더 오래된 고점" in none["body"]


def test_channel_break_wording_depends_on_direction():
    from backend.analysis import channel_insight
    ch = {"label": "하락 채널", "position_pct": 120.0, "lower": 90.0, "upper": 100.0, "width_pct": 11.1}
    fmt = lambda p: f"{p:.0f}"
    assert "내리던 흐름이 깨졌다" in channel_insight(ch, fmt)["body"]
    assert "내리는 속도가 더 빨라졌다" in channel_insight({**ch, "position_pct": -20.0}, fmt)["body"]
    assert "오르던 흐름이 깨졌다" in channel_insight({**ch, "label": "상승 채널", "position_pct": -20.0}, fmt)["body"]


def test_two_point_trendline_is_tentative():
    for tl in find_trendlines(pd.DataFrame(demo_candles("AAPL", "US"))):
        assert tl["tentative"] == (tl["touches"] < 3)
