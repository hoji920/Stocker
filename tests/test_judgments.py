from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace as NS

import pandas as pd
import pytest

from backend import judgments as j
from backend.config import get_settings


def _candles(closes, start=1):
    return [{"time": f"d{start + i:04d}", "open": c, "high": c + 1, "low": c - 1, "close": float(c), "volume": 100.0} for i, c in enumerate(closes)]


def _rec(symbol="AAA", model="m1", arm="news", d20="outperform", c20=70, d5="neutral", as_of="d0010", price=100.0, week="2026-W42", **kw):
    return {"week": week, "symbol": symbol, "name": symbol, "mine": False, "fixed": True, "model": model, "arm": arm,
            "as_of": as_of, "price": price, "bench_as_of": as_of, "bench_price": 100.0, "d5": d5, "c5": 50, "d20": d20, "c20": c20,
            "reason": "", "invalid_if": "", "tokens": 10, "made_at": "2026-10-13T09:00:00+09:00", **kw}


def test_week_and_due():
    at = lambda day, hour: datetime(2026, 10, day, hour, 0, tzinfo=j.KST)          # 2026-10-12 는 월요일
    assert j.week_key(at(12, 9)) == j.week_key(at(18, 23)) == "2026-W42" and j.week_key(at(19, 0)) == "2026-W43"
    assert not j.is_due(at(12, 5))                                                 # 월요일 새벽
    assert j.is_due(at(12, 6)) and j.is_due(at(13, 7)) and j.is_due(at(17, 12))    # 월요일 아침부터 그 주 내내


def test_completed_drops_bars_of_unfinished_sessions():
    bars = [{"time": d, "close": 1.0} for d in ("2026-10-08", "2026-10-09", "2026-10-12")]
    et = lambda day, hour, minute=0: datetime(2026, 10, day, hour, minute, tzinfo=j.ET)
    days = lambda now: [b["time"] for b in j.completed(bars, now)]
    assert days(et(11, 20)) == ["2026-10-08", "2026-10-09"]          # 한국 월요일 오전(미국 일요일 밤): 주간거래가 만든 12일 봉은 뺀다
    assert days(et(12, 10)) == ["2026-10-08", "2026-10-09"]          # 미국 월요일 장중: 아직 안 끝난 봉
    assert days(et(12, 16, 10)) == days(et(13, 3)) == ["2026-10-08", "2026-10-09", "2026-10-12"]     # 장이 끝난 뒤
    kst_monday_morning = datetime(2026, 10, 12, 8, 30, tzinfo=j.KST)
    assert days(kst_monday_morning)[-1] == "2026-10-09"              # 한국 시간으로 줘도 같다: 기준은 금요일 종가


def test_make_records_uses_last_finished_close_and_saves_the_prompt(monkeypatch):
    monkeypatch.setattr(j, "judge", lambda s, model, prompt: {"d5": "neutral", "c5": 50, "d20": "outperform", "c20": 60,
                                                              "reason": "", "invalid_if": "", "tokens": 1})
    days = pd.bdate_range(end="2026-10-12", periods=300).strftime("%Y-%m-%d")
    candles = [{"time": d, "open": 100, "high": 101, "low": 99, "close": 100.0 + i, "volume": 10.0} for i, d in enumerate(days)]
    saved = []
    now = datetime(2026, 10, 12, 8, 30, tzinfo=j.KST)                 # 월요일 아침: 12일 봉(주간거래)은 아직 안 끝났다
    items = [{"date": "2026-10-11", "source": "yna", "title": "주말 기사", "summary": ""}]
    recs = j.make_records(get_settings(), "2026-W42", "MU", "마이크론", False, candles, candles, items,
                          [("mini", "news"), ("mini", "nonews"), ("big", "news")], now=now, save_input=saved.append)
    assert {r["as_of"] for r in recs} == {"2026-10-09"} and {r["price"] for r in recs} == {100.0 + 298}
    assert sorted(e["arm"] for e in saved) == ["news", "nonews"]      # 입력은 조건마다 한 번 (두 모델이 같은 입력)
    by_arm = {e["arm"]: e for e in saved}
    assert "주말 기사" in by_arm["news"]["prompt"] and "주말 기사" not in by_arm["nonews"]["prompt"]
    assert all(r["prompt_sha"] == by_arm[r["arm"]]["prompt_sha"] and r["system_sha"] == j.sha(j._SYSTEM) for r in recs)


def test_expensive_models_only_judge_the_fixed_subset():
    import random
    assert sorted(j.PREMIUM) == sorted(random.Random(20261013).sample(sorted(j.UNIVERSE), 30))     # 골라 넣지 않고 뽑은 그대로
    models = ("mini", "big")
    assert j.models_for(j.PREMIUM[0], models) == ("mini", "big")
    assert j.models_for(next(s for s in j.UNIVERSE if s not in j.PREMIUM), models) == ("mini",)
    assert j.models_for("TSLA", models) == ("mini",)                                               # 내 종목도 첫 모델만


def test_paired_keeps_only_records_every_model_judged():
    recs = [dict(_rec("AAA", model="mini"), scored={}), dict(_rec("AAA", model="big"), scored={}),
            dict(_rec("BBB", model="mini"), scored={}),                                             # big 은 BBB 를 안 봤다
            dict(_rec("AAA", model="mini", week="2026-W43"), scored={})]                            # 다음 주엔 mini 만
    assert [(r["week"], r["symbol"], r["model"]) for r in j.paired(recs)] == [("2026-W42", "AAA", "mini"), ("2026-W42", "AAA", "big")]
    assert j.paired([recs[2]]) == []                                                                # 모델이 하나뿐이면 비교할 게 없다


def test_features_use_only_given_bars():
    f = j.features(_candles(list(range(100, 400))), _candles([100] * 300))
    assert f["return_pct"]["5d"] == round((399 / 394 - 1) * 100, 1) and f["spy_return_pct"]["20d"] == 0.0
    assert f["vs_sma_pct"]["sma200"] > 0 and f["from_52w_high_pct"] < 0 and f["rsi14"] == 100


def test_prompt_has_news_only_in_news_arm():
    items = [{"date": "2026-10-12", "source": "yna", "title": "계약 체결", "summary": "내용"}]
    assert "계약 체결" in j.build_prompt("AAA", "에이", "d1", {}, items)
    assert "기사" not in j.build_prompt("AAA", "에이", "d1", {}, None)


def _fake_openai(monkeypatch, content):
    import openai
    sent = {}

    class FakeClient:
        def __init__(self, **_):
            reply = NS(choices=[NS(message=NS(content=content))], usage=NS(total_tokens=123))
            self.chat = NS(completions=NS(create=lambda **k: sent.update(k) or reply))
    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    return replace(get_settings(), openai_api_key="k"), sent


def test_judge_parses_and_clamps(monkeypatch):
    s, sent = _fake_openai(monkeypatch, '{"d5": "neutral", "c5": 90, "d20": "underperform", "c20": 140, "reason": "r", "invalid_if": "x"}')
    out = j.judge(s, "gpt-5.5", "p")
    assert out == {"d5": "neutral", "c5": 50, "d20": "underperform", "c20": 100, "reason": "r", "invalid_if": "x", "tokens": 123}
    assert sent["model"] == "gpt-5.5" and "temperature" not in sent


def test_judge_rejects_bad_direction(monkeypatch):
    s, _ = _fake_openai(monkeypatch, '{"d5": "up", "d20": "outperform"}')
    with pytest.raises(ValueError):                                                 # 형식이 어긋난 판단은 기록하지 않는다
        j.judge(s, "gpt-5.5", "p")


def test_log_is_append_only_and_keeps_first_of_duplicates(tmp_path):
    log = j.Log(tmp_path / "judgments")
    log.append(_rec(reason="먼저", made_at="2026-10-13T09:00:00+09:00"))
    log.append(_rec(reason="다른 PC 에서 나중에", made_at="2026-10-13T21:00:00+09:00"))
    log.append(_rec(arm="nonews"))
    assert [(r["arm"], r["reason"]) for r in log.read()] == [("news", "먼저"), ("nonews", "")]
    assert log.done_keys("2026-W42") == {("AAA", "m1", "news"), ("AAA", "m1", "nonews")} and log.done_keys("2026-W43") == set()
    assert len((tmp_path / "judgments" / "2026-W42.jsonl").read_text(encoding="utf-8").splitlines()) == 3   # 파일에는 전부 남는다


def test_score_uses_price_at_judgment_and_same_exit_day():
    # 판단일 d0010: 종목 100 → 20봉 뒤 120(+20%), SPY 100 → 105(+5%) → 초과 +15
    stock = _candles([100] * 10 + [100 + i for i in range(1, 21)] + [130] * 5)
    bench = _candles([100] * 10 + [100 + i * 0.25 for i in range(1, 21)] + [106] * 5)
    data = {"AAA": stock, "SPY": bench}
    up, down = _rec(d20="outperform"), _rec(arm="nonews", d20="underperform", d5="outperform")
    s = j.score([up, down], data.__getitem__)
    assert s[0]["scored"][20] == {"ret": 20.0, "bench": 5.0, "excess": 15.0, "exit_day": "d0030", "hit": True}
    assert s[0]["scored"][5]["hit"] is None                                          # neutral 은 판단 안 함
    assert s[1]["scored"][20]["hit"] is False and s[1]["scored"][5]["hit"] is True
    fresh = j.score([_rec(as_of="d0030")], data.__getitem__)[0]["scored"]            # 아직 20봉이 안 지남
    assert 20 not in fresh and 5 in fresh


def test_summarize_compares_with_always_up():
    stock, bench = _candles([100] * 10 + [100 + i for i in range(1, 26)]), _candles([100] * 35)
    data = {"AAA": stock, "BBB": [dict(c, close=200 - c["close"]) for c in stock], "SPY": bench}   # BBB 는 내린다
    recs = [_rec("AAA", d20="outperform", c20=80), _rec("BBB", d20="underperform", c20=60, price=100.0),
            _rec("AAA", arm="nonews", d20="underperform", c20=70)]
    rows = {(r["arm"], r["horizon"]): r for r in j.summarize(j.score(recs, data.__getitem__))}
    news20 = rows[("news", 20)]
    assert (news20["calls"], news20["hit_rate_pct"], news20["primary"]) == (2, 100.0, True)
    assert news20["mean_signed_excess_pct"] == 20.0 and news20["always_up_hit_rate_pct"] == 50.0 and news20["always_up_mean_excess_pct"] == 0.0
    assert news20["calibration"] == [{"bucket": "60~69", "n": 1, "hit_rate_pct": 100.0}, {"bucket": "80~89", "n": 1, "hit_rate_pct": 100.0}]
    assert rows[("nonews", 20)]["hit_rate_pct"] == 0.0 and rows[("news", 5)]["neutral"] == 2
