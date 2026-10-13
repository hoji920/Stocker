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
    assert f["down"][0]["state"].startswith("지금 가격이 이 선에 걸쳐 있음") and f["down"][0]["rule"] == "종가가 $99.60 아래로 내려가면 이탈"
    assert f["down"][1]["note"] == "아직 두 점만 이은 임시 선"
    assert [(i["name"], i["price"]) for i in f["up"]] == [("저항선", "$107.10"), ("하락 추세선", "$124.85")]
    assert f["scale"].startswith("로그")


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
                                  '"above": ["$111.00 저항선"], "summary": "정리하면 중간이에요."}')
    r = reading.read(s, CHART, 100.9, fmt)
    assert r["below"] == ["$102.40 지지선", "$92.05"] and r["above"] == ["$111.00 저항선"]
    assert r["unverified"] == ["111.00"] and r["price"] == "$100.90" and r["as_of"] == "2026-10-13" and r["tokens"] == 900


def test_read_rejects_empty(monkeypatch):
    s = _fake_openai(monkeypatch, '{"structure": "", "summary": ""}')
    with pytest.raises(ValueError):
        reading.read(s, CHART, 100.9, fmt)
