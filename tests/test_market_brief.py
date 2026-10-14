from dataclasses import replace
from datetime import datetime

import pytest

from backend import market_brief, news
from backend.config import get_settings


def _it(symbol, name, unit, last, pct, date, bp=None):
    return {"symbol": symbol, "name": name, "unit": unit, "last": last, "change_pct": pct, "change_bp": bp, "date": date}


def _groups(spx=-1.0, vix=5.0):
    return [
        {"group": "미국 지수", "items": [_it("^GSPC", "S&P 500", "pt", 6000.5, spx, "2026-10-09"),
                                     _it("^VIX", "VIX 공포지수", "pt", 20.0, vix, "2026-10-09"),
                                     {"symbol": "^DJI", "name": "다우존스", "unit": "pt", "error": True}]},
        {"group": "미국 국채", "items": [_it("^TNX", "10년 금리", "pct", 4.321, 1.2, "2026-10-09", bp=5.1)]},
        {"group": "환율·코인", "items": [_it("BTC-USD", "비트코인", "usd", 100000.0, 2.0, "2026-10-12")]},
    ]


def _fake_openai(monkeypatch, content):
    import openai
    sent = {}

    class FakeClient:
        def __init__(self, **_):
            reply = type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": content})})]})
            create = staticmethod(lambda **k: sent.update(k) or reply)
            self.chat = type("Chat", (), {"completions": type("Comp", (), {"create": create})})
    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    return replace(get_settings(), openai_api_key="k"), sent


def test_table_uses_bp_for_rates_skips_missing_and_dates_each_group():
    assert market_brief.table(_groups()).splitlines() == [
        "[미국 지수 · 2026-10-09 기준] S&P 500 6,000.50 (-1.00%), VIX 공포지수 20.00 (+5.00%)",
        "[미국 국채 · 2026-10-09 기준] 10년 금리 4.321% (+5.1bp)",
        "[환율·코인 · 2026-10-12 기준] 비트코인 100,000.00 (+2.00%)"]


@pytest.mark.parametrize("spx, vix, expected", [
    (0.8, -3.0, "위험 선호"), (-0.8, 4.0, "위험 회피"),
    (0.8, 4.0, "중립"),          # 주식은 올랐는데 VIX 도 오름 → 엇갈림
    (0.1, -3.0, "중립"),         # 거의 안 움직임
])
def test_mood_comes_from_numbers(spx, vix, expected):
    assert market_brief.mood(_groups(spx, vix)) == expected


def test_table_leaves_out_korean_market():
    groups = _groups() + [{"group": "국내", "items": [_it("^KS11", "코스피", "pt", 6400.0, -1.5, "2026-10-12")]}]
    assert "코스피" not in market_brief.table(groups)             # 미국 시장 요약


def test_build_uses_computed_mood_and_index_date(monkeypatch):
    s, sent = _fake_openai(monkeypatch, '{"mood": "위험 선호", "headline": "h", "lines": ["a", "b", "c", "d"]}')
    out = market_brief.build(replace(s, openai_brief_model="gpt-5.5"), _groups(-1.0, 5.0), [])
    assert out.pop("made_at")
    assert out == {"mood": "위험 회피", "headline": "h", "drivers": [], "lines": ["a", "b", "c"],
                   "as_of": "2026-10-09"}                         # LLM 이 준 mood 는 안 쓴다
    assert "분위기: 위험 회피" in sent["messages"][1]["content"]
    assert sent["model"] == "gpt-5.5" and "temperature" not in sent   # gpt-5 계열엔 temperature 를 안 보낸다


def test_brief_day_changes_at_7am_korea_time():
    at = lambda h: datetime(2026, 10, 12, h, 30, tzinfo=market_brief.KST)
    assert market_brief.brief_day(at(6)) == "2026-10-11"          # 새벽엔 아직 어제 요약을 쓴다 (미국 장이 안 끝났다)
    assert market_brief.brief_day(at(7)) == "2026-10-12"
    assert market_brief.brief_day(at(23)) == "2026-10-12"


def test_saved_brief_is_reused_only_on_the_same_day(tmp_path):
    assert market_brief.load_saved(tmp_path, "2026-10-12") is None            # 파일 없음
    market_brief.save(tmp_path / "sub", "2026-10-12", {"headline": "한글"})
    assert market_brief.load_saved(tmp_path / "sub", "2026-10-12") == {"headline": "한글"}
    assert market_brief.load_saved(tmp_path / "sub", "2026-10-13") is None    # 다음 날엔 새로 만든다


def test_summary_endpoint_builds_once_a_day(monkeypatch, tmp_path):
    from backend import main
    s = replace(get_settings(), openai_api_key="k", data_dir=tmp_path)
    calls = []
    monkeypatch.setattr(main, "get_settings", lambda: s)
    monkeypatch.setattr(main.macro, "quotes", lambda: _groups())
    monkeypatch.setattr(market_brief, "headlines", lambda *a: [])
    monkeypatch.setattr(market_brief, "build", lambda *a: calls.append(1) or {"headline": f"v{len(calls)}"})
    assert main.macro_summary()["summary"] == {"headline": "v1"}
    assert main.macro_summary()["summary"] == {"headline": "v1"} and len(calls) == 1      # 두 번째는 저장된 것
    assert main.macro_summary(refresh=True)["summary"] == {"headline": "v2"}               # '다시 만들기'
    assert main.macro_summary()["summary"] == {"headline": "v2"}
    monkeypatch.setattr(market_brief, "brief_day", lambda: "2099-01-01")
    assert main.macro_summary()["summary"] == {"headline": "v3"}                           # 날이 바뀌면 새로


def _news(title, day="2026-10-10", url=None):
    return news.NewsItem(title=title, summary="", date=day, source="yna.co.kr", url=url or title, lang="ko")


def test_build_keeps_only_drivers_with_real_sources(monkeypatch):
    reply = ('{"headline": "h", "lines": ["a", "b", "c"], "drivers": ['
             '{"what": "기술주 반등", "effect": "나스닥 상승", "sources": [1, 1, 9]},'
             '{"what": "근거 없는 소식", "effect": "x", "sources": []},'
             '{"what": "없는 번호", "effect": "x", "sources": [7]}]}')
    s, sent = _fake_openai(monkeypatch, reply)
    out = market_brief.build(s, _groups(), [_news("뉴욕증시, 기술주 반등에 상승")])
    assert out["drivers"] == [{"what": "기술주 반등", "effect": "나스닥 상승", "after_close": False, "sources": [
        {"title": "뉴욕증시, 기술주 반등에 상승", "url": "뉴욕증시, 기술주 반등에 상승", "source": "yna.co.kr", "date": "2026-10-10"}]}]
    assert "[1] (2026-10-10, yna.co.kr) 뉴욕증시, 기술주 반등에 상승" in sent["messages"][1]["content"]


def test_headlines_are_us_market_articles_since_the_session(monkeypatch):
    found = [_news("뉴욕증시, 기술주 반등·러시아 경유 공급 합의에 상승(종합)"),
             _news("뉴욕증시 3대 지수 올랐지만 코스피 부진"),                 # 한국 장 기사
             _news("[마켓뷰] 연휴서 돌아온 코스피, 중동정세 악화속 반등 시도하나"),
             _news("뉴욕증시, 혼조 마감… 국제유가는 4% 급등", day="2026-10-09"),   # 한국 날짜 9일 기사 = 8일 장 얘기
             _news("유가·금리 상승에도 기술주 실적 기대감…증시 반등[뉴욕마감]"),
             _news("반도체 수출 호조")]                                     # 미국 시장 기사 아님
    reuters = news.NewsItem(title="Oil jumps", summary="", date="2026-10-11", source="Reuters", url="r")
    old = news.NewsItem(title="Old", summary="", date="2026-10-07", source="Reuters", url="o")
    monkeypatch.setattr(news, "fetch_kr_news", lambda *a, **k: list(found))
    monkeypatch.setattr(news, "fetch_market_news", lambda *a, **k: [reuters, old])
    titles = [it.title for it in market_brief.headlines(get_settings(), "2026-10-09")]
    assert titles == ["뉴욕증시, 기술주 반등·러시아 경유 공급 합의에 상승(종합)",
                      "유가·금리 상승에도 기술주 실적 기대감…증시 반등[뉴욕마감]", "Oil jumps"]


def test_build_rejects_empty_summary(monkeypatch):
    s, _ = _fake_openai(monkeypatch, '{"lines": []}')
    with pytest.raises(ValueError):                       # 빈 요약은 캐시에 남지 않게 예외
        market_brief.build(s, _groups(), [])


def test_build_is_off_without_key():
    assert market_brief.build(replace(get_settings(), openai_api_key=""), _groups(), []) is None
