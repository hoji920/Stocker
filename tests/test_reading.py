from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from backend import reading
from backend.config import get_settings

fmt = lambda p: f"${p:,.2f}"

CHART = {
    "symbol": "INTC", "name": "인텔", "log_scale": True, "candles": [{"time": "2026-10-13", "close": 100.9}],
    "levels": {"support": [{"price": 102.4, "tol": 2.8, "touches": 1, "at_price": True},           # 현재가보다 위지만 걸쳐 있는 지지선
                           {"price": 89.6, "tol": 2.8, "touches": 1}],
               "resistance": [{"price": 107.1, "tol": 2.8, "touches": 2}]},
    "trendlines": [{"label": "상승 추세선", "kind": "support", "price": 92.05, "touches": 2, "tentative": True, "slope_pct": 0.22},
                   {"label": "하락 추세선", "kind": "resistance", "price": 124.85, "touches": 3, "tentative": False, "slope_pct": -0.16}],
    "channel": {"label": "하락 채널", "lower": 80.56, "upper": 124.85, "other_price": 80.56, "position_pct": 51.5},
    "patterns": [], "extremes": None, "insights": [{"kind": "rsi", "title": "RSI 40", "body": "보통 구간이에요."}],
}


def test_facts_sort_lines_by_the_order_price_meets_them():
    f = reading.facts(CHART, 100.9, fmt)
    assert [(i["name"], i["price"]) for i in f["down"]] == [("지지선", "$102.40"), ("상승 추세선", "$92.05"), ("지지선", "$89.60"),
                                                           ("하락 채널 하단", "$80.56")]
    # 걸쳐 있는 지지선보다 가격이 아래면 '밀려 있음'이라고 사실대로 (종가로 확정된 건 아니다)
    assert f["down"][0]["state"].startswith("지금 가격이 이 선 아래로 1.5% 밀려 있음") and f["down"][0]["rule"] == "종가가 $99.60 아래로 내려가면 이탈"
    assert reading.facts(CHART, 103.0, fmt)["down"][0]["state"].startswith("지금 가격이 이 선에 걸쳐 있음")
    assert f["down"][1]["note"] == "아직 두 점만 이은 임시 선"
    assert [(i["name"], i["price"]) for i in f["up"]] == [("저항선", "$107.10"), ("하락 추세선", "$124.85")]
    assert f["scale"].startswith("로그")


def test_facts_skip_far_channel_side_and_say_which_side_of_a_broken_line_price_is_on():
    lost = {"label": "깨진 상승 추세선", "kind": "support", "price": 110.0, "touches": 3, "broken_at": "2026-10-09", "bars_ago": 2}
    won = {"label": "깨진 하락 추세선", "kind": "resistance", "price": 96.0, "touches": 2, "broken_at": "2026-10-13", "bars_ago": 0}
    chart = {**CHART, "channel": {**CHART["channel"], "other_far": True}, "broken_trendlines": [lost, won]}
    f = reading.facts(chart, 100.9, fmt)
    assert all("채널" not in i["name"] for i in f["down"] + f["up"])            # 너무 먼 채널 바닥은 '다음 선'으로 안 준다
    assert f["channel"]["bottom"] == "$80.56"                                 # 채널이 있다는 사실은 그대로
    up = next(i for i in f["up"] if i["name"] == "최근 내준 상승 추세선")         # 내준 선은 위에: 되찾으면 속임수
    assert up["price"] == "$110.00" and up["state"] == "10월 9일 종가로 내준 선. 지금 가격은 선 아래. 종가로 되찾으면 이번 이탈은 속임수"
    down = next(i for i in f["down"] if i["name"] == "최근 넘은 하락 추세선")     # 넘은 선은 아래에: 다시 내주면 돌파 실패
    assert down["state"].startswith("10월 13일 종가로 넘은 선. 지금 가격은 선 위. 다시 종가로 내주면 돌파 실패") and "마감 전" in down["state"]
    assert f["broken"] == [up, down]
    back = reading.facts(chart, 111.0, fmt)                                   # 가격이 내준 선 위로 돌아오면 '이탈 실패'
    assert "이탈 실패" in next(i for i in back["down"] if i["name"] == "최근 내준 상승 추세선")["state"]
    assert reading.facts(CHART, 100.9, fmt)["broken"] == []


def test_facts_tell_what_happened_recently_from_real_candles():
    from backend import analysis
    from tests.test_analysis import _uptrend_then_drop
    candles = _uptrend_then_drop()                                   # 상승 추세선(≈140)을 마지막 3봉이 132 로 내준 시장
    chart = {**analysis.analyze(candles, fmt), "candles": candles, "symbol": "T", "name": "테스트", "patterns": []}
    f = reading.facts(chart, 132.0, fmt)
    assert f["broken"][0]["name"] == "최근 내준 상승 추세선" and f["broken"][0] in f["up"]
    assert f["recent"]["high"]["date"].endswith("일") and "월" in f["recent"]["high"]["date"]      # 날짜는 읽는 말로
    assert f["recent"]["order"] == "지금 최근 저점을 새로 쓰는 중" and f["recent"]["low"]["bars_ago"] <= 2
    assert f["recent"]["high"]["from_now_pct"] > 0 and f["recent"]["high"]["bars_ago"] > 2
    lows = [i for i in f["down"] if i["name"] == "지난 저점"]            # 선을 다 내줬을 때의 다음 자리
    assert lows and all(i["from_now_pct"] < 0 and i["dates"] for i in lows)
    assert [i["from_now_pct"] for i in f["down"]] == sorted((i["from_now_pct"] for i in f["down"]), reverse=True)   # 가까운 순
    assert f["volume"]["recent5_vs_usual_pct"] == 0 and f["volume"]["pattern"].startswith("거래량이 평소 수준에서")   # 거래량이 늘 같은 시장


def test_unverified_flags_numbers_not_in_the_material():
    f = reading.facts(CHART, 100.9, fmt)
    assert reading.unverified("지금은 $102.40 지지선에 걸쳐 있고 위는 107 저항선, 아래는 92.1 추세선이에요. 1~2개.", f) == []
    assert reading.unverified("다음 지지는 $85.00 이에요.", f) == ["85.00"]


def _fake_openai(monkeypatch, content):
    import openai

    class FakeClient:
        def __init__(self, **_):
            reply = NS(choices=[NS(message=NS(content=content))], usage=NS(total_tokens=900))
            self.chat = NS(completions=NS(create=lambda **k: reply))
    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    return replace(get_settings(), openai_api_key="k")


def test_read_returns_sections_and_checks_numbers(monkeypatch):
    s = _fake_openai(monkeypatch, '{"structure": "위는 $124.85 하락 추세선이에요.", "below": ["$102.40 지지선", "$92.05", "셋째는 버린다"], '
                                  '"above": ["$111.00 저항선"], "summary": "단기 흐름은 꺾였어요.", "note": null}')
    r = reading.read(s, CHART, 100.9, fmt)
    assert r["below"] == ["$102.40 지지선", "$92.05"] and r["above"] == ["$111.00 저항선"] and r["note"] == ""
    assert r["unverified"] == ["111.00"] and r["price"] == "$100.90" and r["as_of"] == "2026-10-13" and r["tokens"] == 900


def test_read_rejects_empty(monkeypatch):
    s = _fake_openai(monkeypatch, '{"structure": "", "summary": ""}')
    with pytest.raises(ValueError):
        reading.read(s, CHART, 100.9, fmt)
