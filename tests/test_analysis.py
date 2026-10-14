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
    # 현재가가 걸쳐 있는 가격대(at_price)는 몇 틱 반대편에 있어도 지금까지의 역할대로 둔다
    assert all(x["price"] < last or x["at_price"] for x in lv["support"])
    assert all(x["price"] >= last or x["at_price"] for x in lv["resistance"])


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


def _uptrend_then_drop(bars=3, to=132.0):
    """상승 추세선(오늘 값 ≈ 140)을 마지막 몇 봉이 종가로 내준 시장."""
    rows = _uptrend()
    for r in rows[-bars:]:
        r.update(open=to, high=to + 0.3, low=to - 0.3, close=to)
    return rows


def test_recently_broken_trendline_is_kept_as_broken():
    from backend.analysis import broken_trendlines
    assert broken_trendlines(pd.DataFrame(_uptrend())) == []          # 안 깨졌으면 없다
    df = pd.DataFrame(_uptrend_then_drop())
    assert all(tl["price"] < 132 for tl in find_trendlines(df) if tl["kind"] == "support")   # 살아 있는 선 목록에서는 빠진다
    b = next(x for x in broken_trendlines(df) if x["kind"] == "support")
    assert b["id"] == "broken-support" and b["label"] == "깨진 상승 추세선" and b["broken"]
    assert b["touches"] >= 3 and b["bars_ago"] == 2 and b["broken_at"] == df["time"].iloc[-3]
    assert b["price"] > 132 and not b["reclaimed"]
    assert "아래로 내려갔어요" in b["why"] and "2봉 전" in b["why"]
    out = analyze(_uptrend_then_drop(), lambda p: f"${p:.2f}")
    assert out["broken_trendlines"][0]["id"] == "broken-support"
    assert any(c["kind"] == "broken" for c in out["insights"])
    assert all(t["id"] != "broken-support" for t in out["trendlines"] + out["trend_candidates"]["support"])


def test_trendline_broken_long_ago_is_not_shown():
    from backend.analysis import broken_trendlines
    df = pd.DataFrame(_uptrend_then_drop(bars=15))                    # 10봉보다 전에 깨진 선은 지나간 일
    out = broken_trendlines(df)
    assert all(x["bars_ago"] < 10 for x in out)
    assert all(x["points"][0]["time"] != "2025-01-31" for x in out)   # 14봉 전에 깨진 6번 닿은 선은 안 나온다


def test_channel_marks_other_side_far_from_price():
    df = pd.DataFrame(_uptrend())
    assert find_channel(df, find_trendlines(df))["other_far"] is False          # 폭 8 짜리 채널은 양쪽 다 가깝다
    # 폭이 60 인 채널의 바닥 근처(현재가 ≈ 140): 천장은 40% 넘게 위
    t = pd.date_range("2025-01-01", periods=192, freq="B")
    rows = [{"time": t[i].date().isoformat(), "open": c, "high": c + 0.3, "low": c - 0.3, "close": c, "volume": 1e6}
            for i, c in ((i, 100 + 0.2 * i + 30 * (np.sin(i / 8) + 1)) for i in range(192))]
    wide = pd.DataFrame(rows)
    ch = find_channel(wide, [tl for tl in find_trendlines(wide) if tl["kind"] == "support"])
    assert ch["other_kind"] == "resistance" and ch["other_far"] is True and ch["other_distance_pct"] > 25


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


def _seg(a, b, n):
    return list(np.linspace(a, b, n, endpoint=False))


def test_zone_under_price_by_a_few_cents_stays_support():
    # 저점이 100 근처에 세 번 모인 가격대. 현재가가 그보다 살짝 아래(99.9)여도 아직 뚫린 게 아니다
    closes = [110.0] * 10
    for _ in range(3):
        closes += _seg(110, 100.5, 12) + _seg(100.5, 110, 12)
    closes += _seg(110, 99.9, 12) + [99.9]
    lv = find_levels(pd.DataFrame(_rows(closes)))
    s = lv["support"][0]
    assert s["at_price"] and s["lows"] >= 3 and abs(s["price"] - 100) < 1
    assert all(abs(r["price"] - 100) > 1 for r in lv["resistance"])


def test_year_old_price_zone_is_not_offered_as_support():
    # 10 근처 박스권(2번 넘게 닿은 가격대가 많음) → 40 까지 급등 → 저점 30, 34 를 한 번씩 찍고 현재가 36
    box = [10 + np.sin(i / 5) for i in range(150)]
    closes = box + _seg(box[-1], 40, 30) + _seg(40, 30, 10) + _seg(30, 42, 12) + _seg(42, 34, 10) + _seg(34, 41, 10) + _seg(41, 36, 8) + [36.0]
    lv = find_levels(pd.DataFrame(_rows(closes)))
    assert lv["support"] and all(x["price"] > 25 for x in lv["support"])     # 10 근처 가격대는 후보에 없다
    assert abs(lv["support"][0]["price"] - 33.5) < 1                         # 가장 가까운 전저점


def test_trendline_far_from_price_is_dropped():
    # 저점 46 → 29 를 이은 가파른 선을 오늘까지 늘이면 10 근처. 현재가는 40 안팎 → 후보에서 빠져야 한다
    closes = ([60.0] * 80 + _seg(60, 46, 20) + _seg(46, 55, 20) + _seg(55, 29, 30) + _seg(29, 45, 20)
              + [40 + 3 * np.sin(i / 3) for i in range(40)])
    df = pd.DataFrame(_rows(closes))
    last = closes[-1]
    for side in trendline_candidates(df, k=20).values():          # 순위에서 밀린 후보까지 전부
        assert all(abs(tl["price"] / last - 1) < 0.5 for tl in side)


def test_channel_suggestion_from_trend_start():
    from backend.analysis import channel_suggestions
    # 계단식 상승: 100 에서 시작해 10 오르고 6 내리는 걸 반복 → 저점은 100, 104, 108 … 한 줄, 폭은 첫 계단 높이
    closes = [110.0] * 10 + _seg(110, 100, 10)
    for k in range(6):
        closes += _seg(100 + 16 * k, 110 + 16 * k + 6 * k, 20) + _seg(110 + 16 * k + 6 * k, 100 + 16 * (k + 1), 12)
    closes += [closes[-1] + 2, closes[-1] + 4]
    df = pd.DataFrame(_rows(closes))
    big = [s for s in channel_suggestions(df) if s["label"] == "큰 흐름"]
    assert big and big[0]["direction"] == "up"
    s = big[0]
    assert s["p1"]["price"] == 99.5 and s["p1"]["time"] < s["p3"]["time"] <= s["p2"]["time"]   # 시작 저점 → 고점 → 두 번째 저점
    assert s["p2"]["price"] > s["p1"]["price"] and 1.5 <= s["layers"] <= 8
    # 기준선 아래로 내려간 종가가 없어야 한다
    i1, i2 = (df.index[df.time == s[k]["time"]][0] for k in ("p1", "p2"))
    slope = (s["p2"]["price"] - s["p1"]["price"]) / (i2 - i1)
    assert all(c >= s["p1"]["price"] + slope * (i - i1) - 1 for i, c in enumerate(df.close) if i >= i1)


def test_channel_layers_added_to_alert_lines_only_outside_base_channel():
    times = [f"d{i}" for i in range(5)]
    d = {"id": "c1", "type": "channel", "p1": {"time": "d0", "price": 10.0},
         "p2": {"time": "d2", "price": 12.0}, "p3": {"time": "d1", "price": 15.0}}     # 오늘 기준선 14, 평행선 18, 폭 4
    assert drawing_values(times, d, last=16.0) == [("내 채널 기준선", 14.0), ("내 채널 평행선", 18.0)]
    assert drawing_values(times, d, last=27.0) == [("내 채널 기준선", 14.0), ("내 채널 평행선", 18.0),
                                                 ("내 채널 층 선", 26.0), ("내 채널 층 선", 30.0)]
    assert ("내 채널 층 선", 10.0) in drawing_values(times, d, last=12.0)


def test_atr_and_swings_are_remembered_per_frame_not_across_frames():
    from backend import analysis
    a = pd.DataFrame(_rows([100 + 10 * np.sin(i / 5) for i in range(120)]))
    b = pd.DataFrame(_rows([200 + 30 * np.sin(i / 7) for i in range(120)]))
    atr_a, sw_a = analysis.atr(a).copy(), analysis.swing_points(a)
    assert analysis.atr(a) is analysis.atr(a)                         # 같은 프레임이면 다시 계산하지 않는다
    atr_b = analysis.atr(b)                                           # 다른 프레임이면 새로 계산
    assert not np.allclose(atr_a.dropna(), atr_b.dropna())
    assert analysis.atr(a).equals(atr_a) and analysis.swing_points(a) == sw_a   # 다시 a 로 돌아와도 a 의 값
    sw_a.append("x")
    assert "x" not in analysis.swing_points(a)                        # 받은 목록을 고쳐도 기억해 둔 값은 그대로

def _exponential_market(n=200, start=50.0, rate=0.01, swing=0.08):
    """매일 1%씩 오르는 추세 위에서 20봉마다 저점을 찍는 시세. 저점들은 '로그에서 직선'(= 같은 % 속도)인 선 위에 있다."""
    from backend import analysis  # noqa: F401
    t = pd.date_range("2025-01-01", periods=n, freq="B")
    rows = []
    for i in range(n):
        base = start * np.exp(rate * i)
        c = base * (1 + swing * abs(np.sin(np.pi * i / 20)))
        rows.append({"time": t[i].date().isoformat(), "open": c, "high": c * 1.004, "low": c * 0.996, "close": c, "volume": 1e6})
    return rows


def test_log_trendline_follows_constant_percent_growth(monkeypatch):
    from backend import analysis
    df = pd.DataFrame(_exponential_market())
    linear = trendline_candidates(df)["support"][0]
    monkeypatch.setattr(analysis, "LOG_TRENDS", True)
    log = trendline_candidates(df)["support"][0]
    assert log["log"] and not linear["log"]
    assert log["touches"] > linear["touches"] and log["touches"] >= 5        # 직선은 뒤쪽 저점에서 멀어지고, 로그 선은 다 닿는다
    assert abs(log["slope_pct"] - 1.0) < 0.05                                # 하루 1% 씩 오르는 선
    first, last = log["points"]
    mid = log["touch_points"][len(log["touch_points"]) // 2]                  # 가운데 닿은 점은 두 끝점의 '비율' 보간 위에 있다
    times = df["time"].tolist()
    frac = (times.index(mid["time"]) - times.index(first["time"])) / (times.index(last["time"]) - times.index(first["time"]))
    assert abs(mid["value"] - first["value"] * (last["value"] / first["value"]) ** frac) < 0.01 * mid["value"]
    assert abs(log["price"] / df["low"].iloc[-1] - 1) < 0.02                 # 오늘 값이 실제 저점 근처


def test_log_channel_is_parallel_in_ratio(monkeypatch):
    from backend import analysis
    df = pd.DataFrame(_exponential_market())
    monkeypatch.setattr(analysis, "LOG_TRENDS", True)
    ch = find_channel(df, find_trendlines(df))
    assert ch and ch["log"] and ch["upper"] > ch["lower"] and 0 <= ch["position_pct"] <= 100
    ratio = lambda pts_a, pts_b, k: pts_b[k]["value"] / pts_a[k]["value"]
    assert abs(ratio(ch["base"], ch["other"], 0) - ratio(ch["base"], ch["other"], 1)) < 1e-3     # 두 선은 어디서나 같은 비율만큼 떨어져 있다
    assert abs(ch["step_pct"] - 1.0) < 0.05


def test_linear_is_still_the_default():
    from backend import analysis
    assert analysis.LOG_TRENDS is False
    tl = find_trendlines(pd.DataFrame(_exponential_market()))
    assert tl and all(not t["log"] for t in tl)
