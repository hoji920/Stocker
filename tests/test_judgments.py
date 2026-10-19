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


def test_nasdaq_100_joins_from_w43_and_overlaps_count_once():
    assert j.symbols_for("2026-W42") == j.UNIVERSE                                   # 첫 주는 중형 100 만
    both = j.symbols_for("2026-W43")
    assert len(both) == len(set(j.UNIVERSE) | set(j.NDX)) == len(set(both)) and both[:100] == j.UNIVERSE
    assert len(j.NDX) == 99 and {"AAPL", "TSLA", "MSTR", "MU"} <= set(j.NDX) and not {"HONA", "SPCX"} & set(j.NDX)
    recs = [_rec("MU"), _rec("AAPL"), _rec("IREN")]                                  # MU 는 양쪽, AAPL 은 나스닥만, IREN 은 중형만
    assert [r["symbol"] for r in j.in_set(recs, j.NDX)] == ["MU", "AAPL"]
    assert [r["symbol"] for r in j.in_set(recs, j.UNIVERSE)] == ["MU", "IREN"]


def test_expensive_models_only_judge_the_fixed_subset():
    import random
    assert sorted(j.PREMIUM) == sorted(random.Random(20261013).sample(sorted(j.UNIVERSE), 30))     # 골라 넣지 않고 뽑은 그대로
    models = ("mini", "big")
    assert j.models_for(j.PREMIUM[0], models) == ("mini", "big")
    assert j.models_for(next(s for s in j.UNIVERSE if s not in j.PREMIUM), models) == ("mini",)
    assert j.models_for("TSLA", models) == ("mini",)                                               # 내 종목도 첫 모델만


def test_repeats_only_for_the_subset_with_the_first_model_from_w43():
    models, sym = ("mini", "big"), j.PREMIUM[0]
    assert j.combos_for(sym, models, "2026-W42") == [("mini", "news"), ("mini", "nonews"), ("big", "news"), ("big", "nonews")]
    # W43 부터: 조건 3개 (가격만 / + 펀더멘탈 / + 펀더멘탈 + 기사), 반복은 양 끝만
    assert j.combos_for(sym, models, "2026-W43") == [("mini", "nonews"), ("mini", "fund"), ("mini", "fund_news"),
                                                     ("big", "nonews"), ("big", "fund"), ("big", "fund_news"),
                                                     ("mini", "nonews_r"), ("mini", "fund_news_r")]
    assert j.combos_for("AAPL", models, "2026-W43") == [("mini", "nonews"), ("mini", "fund"), ("mini", "fund_news")]   # 나스닥만: 반복도 비싼 모델도 없음


def test_repeat_gets_the_same_prompt_as_its_base_arm(monkeypatch):
    seen = []
    monkeypatch.setattr(j, "judge", lambda s, model, prompt: seen.append(prompt) or {"d5": "neutral", "c5": 50, "d20": "neutral", "c20": 50,
                                                                                     "reason": "", "invalid_if": "", "tokens": 1})
    days = pd.bdate_range(end="2026-10-09", periods=300).strftime("%Y-%m-%d")
    candles = [{"time": d, "open": 100, "high": 101, "low": 99, "close": 100.0, "volume": 10.0} for d in days]
    saved = []
    recs = j.make_records(get_settings(), "2026-W43", "MU", "마이크론", False, candles, candles,
                          [{"date": "2026-10-11", "source": "yna", "title": "기사", "summary": ""}],
                          [("mini", "news"), ("mini", "news_r")], now=datetime(2026, 10, 19, 8, 0, tzinfo=j.KST), save_input=saved.append)
    assert seen[0] == seen[1] and "기사" in seen[0] and len(saved) == 1                              # 입력은 한 번만 저장
    assert [r["arm"] for r in recs] == ["news", "news_r"] and recs[0]["prompt_sha"] == recs[1]["prompt_sha"]
    assert recs[1]["news_count"] == 1


def test_consistency_counts_agreement_and_flips():
    recs = [_rec("MU", model="mini", arm="news", d20="outperform", c20=60, d5="neutral"),
            _rec("MU", model="mini", arm="news_r", d20="outperform", c20=70, d5="neutral"),
            _rec("NET", model="mini", arm="news", d20="outperform", c20=60, d5="underperform"),
            _rec("NET", model="mini", arm="news_r", d20="underperform", c20=60, d5="neutral"),
            _rec("AI", model="mini", arm="news_r", d20="neutral")]                                    # 짝이 없는 반복은 세지 않는다
    row = j.consistency(recs)[0]
    assert (row["model"], row["arm"], row["pairs"]) == ("mini", "news", 2)
    assert (row["same_d20_pct"], row["opposite_d20_pct"], row["conf_gap_20"]) == (50.0, 50.0, 5.0)
    assert (row["same_d5_pct"], row["opposite_d5_pct"]) == (50.0, 0.0)                                # 약함 → 판단 안 함은 뒤집힌 게 아니다
    assert [r["arm"] for r in j.summarize([dict(r, scored={}) for r in recs])] == ["news", "news"]     # 성적표에는 반복이 안 들어간다


def test_paired_keeps_only_records_every_model_judged():
    recs = [dict(_rec("MU", model="mini"), scored={}), dict(_rec("MU", model="big"), scored={}),
            dict(_rec("NET", model="mini"), scored={}),                                             # big 은 BBB 를 안 봤다
            dict(_rec("MU", model="mini", week="2026-W43"), scored={})]                            # 다음 주엔 mini 만
    assert [(r["week"], r["symbol"], r["model"]) for r in j.paired(recs)] == [("2026-W42", "MU", "mini"), ("2026-W42", "MU", "big")]
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
            usage = NS(total_tokens=123, prompt_tokens=100, completion_tokens=23, completion_tokens_details=NS(reasoning_tokens=7))
            reply = NS(choices=[NS(message=NS(content=content))], usage=usage, model="gpt-5.5-2026-04-23")
            self.chat = NS(completions=NS(create=lambda **k: sent.update(k) or reply))
    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    return replace(get_settings(), openai_api_key="k"), sent


def test_judge_parses_and_clamps(monkeypatch):
    s, sent = _fake_openai(monkeypatch, '{"d5": "neutral", "c5": 90, "d20": "underperform", "c20": 140, "reason": "r", "invalid_if": "x"}')
    out = j.judge(s, "gpt-5.5", "p")
    assert {k: out[k] for k in ("d5", "c5", "d20", "c20", "reason", "invalid_if", "tokens")} == {
        "d5": "neutral", "c5": 50, "d20": "underperform", "c20": 100, "reason": "r", "invalid_if": "x", "tokens": 123}
    assert sent["model"] == "gpt-5.5" and "temperature" not in sent
    # 실제로 답한 모델 버전·호출 설정·토큰 내역을 같이 남긴다
    assert (out["model_version"], out["params"], out["tokens_in"], out["tokens_out"], out["tokens_reasoning"]) == (
        "gpt-5.5-2026-04-23", {"reasoning_effort": "low"}, 100, 23, 7)


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
    asked = []
    young = j.score([_rec(as_of="2026-10-09")], lambda s: asked.append(s) or data[s], today="2026-10-15")
    assert young[0]["scored"] == {} and asked == []          # 평일로 4일 → 5일 기간도 안 지났으니 시세를 안 받는다
    j.score([_rec(as_of="2026-10-09")], lambda s: asked.append(s) or data[s], today="2026-10-16")
    assert sorted(asked) == ["AAA", "SPY"]                   # 평일 5일이 지나면 받아서 채점해 본다
    fresh = j.score([_rec(as_of="d0030")], data.__getitem__)[0]["scored"]            # 아직 20봉이 안 지남
    assert 20 not in fresh and 5 in fresh


def test_summarize_compares_with_always_up():
    stock, bench = _candles([100] * 10 + [100 + i for i in range(1, 26)]), _candles([100] * 35)
    data = {"MU": stock, "NET": [dict(c, close=200 - c["close"]) for c in stock], "SPY": bench}   # BBB 는 내린다
    recs = [_rec("MU", d20="outperform", c20=80), _rec("NET", d20="underperform", c20=60, price=100.0),
            _rec("MU", arm="nonews", d20="underperform", c20=70)]
    rows = {(r["arm"], r["horizon"]): r for r in j.summarize(j.score(recs, data.__getitem__))}
    news20 = rows[("news", 20)]
    assert (news20["calls"], news20["hit_rate_pct"], news20["primary"]) == (2, 100.0, True)
    assert news20["mean_signed_excess_pct"] == 20.0 and news20["always_up_hit_rate_pct"] == 50.0 and news20["always_up_mean_excess_pct"] == 0.0
    assert news20["calibration"] == [{"bucket": "60~69", "n": 1, "hit_rate_pct": 100.0}, {"bucket": "80~89", "n": 1, "hit_rate_pct": 100.0}]
    assert rows[("nonews", 20)]["hit_rate_pct"] == 0.0 and rows[("news", 5)]["neutral"] == 2


def _et(day: str, hm: str) -> float:
    return datetime.fromisoformat(f"{day}T{hm}:00").replace(tzinfo=j.ET).timestamp()


def test_entry_is_the_first_30_minutes_of_the_next_regular_session():
    # 월요일 한국 아침 판단 = 미국 일요일 저녁 → 출발은 월요일 정규장 09:30~10:00 (동부). 주간거래·프리마켓 봉은 안 쓴다
    at = _et("2026-10-18", "20:00")
    mins = [{"time": _et("2026-10-18", "20:30") + 60 * k, "close": 50.0} for k in range(5)]       # 주간거래 (얇다)
    mins += [{"time": _et("2026-10-19", "04:00") + 60 * k, "close": 60.0} for k in range(30)]     # 프리마켓
    mins += [{"time": _et("2026-10-19", "09:30") + 60 * k, "close": 100.0 + k} for k in range(60)]
    assert j.entry_from_minutes(mins, at, now_ts=_et("2026-10-19", "09:59")) is None             # 30분이 아직 안 끝났다
    e = j.entry_from_minutes(mins, at, now_ts=_et("2026-10-19", "10:00"))
    assert e["t0"] == _et("2026-10-19", "09:30") and e["bars"] == 30 and e["price"] == 114.5     # 100 ~ 129 의 평균
    assert j.entry_ready_at(at) == _et("2026-10-19", "10:00")
    assert j.entry_from_minutes(mins[:35], at, now_ts=_et("2026-10-20", "12:00")) is None         # 정규장 체결이 없다


def test_entry_inside_the_session_starts_right_after_judging_and_stops_at_the_close():
    mins = [{"time": _et("2026-10-19", "09:30") + 60 * k, "close": 100.0 + k} for k in range(390)]   # 09:30 ~ 15:59
    at = _et("2026-10-19", "11:00") + 5                                                            # 장중에 물었다
    e = j.entry_from_minutes(mins, at, now_ts=_et("2026-10-19", "12:00"))
    assert e["t0"] == _et("2026-10-19", "11:01") and e["bars"] == 30
    late = j.entry_from_minutes(mins, _et("2026-10-19", "15:50") + 5, now_ts=_et("2026-10-19", "17:00"))
    assert late["bars"] == 9                                                                       # 15:51 ~ 15:59 까지만 (16:00 마감)
    assert j.entry_ready_at(_et("2026-10-16", "17:00")) == _et("2026-10-19", "10:00")             # 금요일 장 뒤 → 월요일


def test_new_rule_applies_from_w43_and_to_every_asked_view():
    assert not j.needs_entry(_rec(week="2026-W42")) and j.needs_entry(_rec(week="2026-W43"))
    assert j.needs_entry({"week": "2026-W42", "arm": j.ASKED_ARM})
    assert j.entry_key({"symbol": "A", "made_at": "x"}) == ("A", "x") and j.entry_key({"symbol": "A", "asked_at": "y"}) == ("A", "y")


def test_score_waits_for_the_entry_and_then_starts_from_it_not_from_the_close():
    # 기준일 d0010 종가 100, 주말 소식으로 다음 봉에 110 으로 갭 → 20봉 뒤 120. SPY 는 100 → 105
    stock = _candles([100] * 10 + [110] + [110 + i * 0.5 for i in range(1, 21)] + [125] * 5)
    bench = _candles([100] * 10 + [100 + i * 0.25 for i in range(1, 22)] + [106] * 5)
    data = {"AAA": stock, "SPY": bench}
    rec = _rec(week="2026-W43", made_at="2026-10-19T07:00:00+09:00")
    pending = j.score([rec], data.__getitem__, entries={})[0]
    assert pending["scored"] == {} and pending["entry"] is None and pending["entry_rule"]      # 출발 가격 전에는 채점 안 함
    entry = {"price": 110.0, "bench_price": 100.25}
    s = j.score([rec], data.__getitem__, entries={("AAA", rec["made_at"]): entry})[0]
    ex = s["scored"][20]["exit_day"]
    exit_close = next(c["close"] for c in stock if c["time"] == ex)
    assert s["entry"] == entry and s["scored"][20]["ret"] == round((exit_close / 110 - 1) * 100, 2)   # 갭(100→110)은 성적에 안 들어간다
    old = j.score([_rec(week="2026-W42")], data.__getitem__)[0]
    assert old["scored"][20]["ret"] == round((exit_close / 100 - 1) * 100, 2) and not old["entry_rule"]   # W42 는 기록된 가격 그대로


def test_always_up_baseline_is_also_given_on_the_same_calls():
    stock, bench = _candles([100] * 10 + [100 + i for i in range(1, 26)]), _candles([100] * 35)
    data = {"MU": stock, "NET": [dict(c, close=200 - c["close"]) for c in stock], "SPY": bench}
    recs = [_rec("MU", d20="outperform"), _rec("NET", d20="neutral", c20=50)]      # NET(내림)은 중립으로 피했다
    row = next(r for r in j.summarize(j.score(recs, data.__getitem__)) if r["arm"] == "news" and r["horizon"] == 20)
    assert row["hit_rate_pct"] == 100.0 and row["always_up_hit_rate_pct"] == 50.0      # 전체로 센 기준선은 모델에 유리하다
    assert row["always_up_calls_hit_rate_pct"] == 100.0                                # 같은 묶음으로 세면 차이가 없다


def test_news_for_judging_is_last_7_days_without_near_duplicates_up_to_30():
    it = lambda d, t, src="R": {"date": d, "source": src, "title": t, "summary": ""}
    items = [it("2026-10-05", "아주 오래된 기사"),                                    # 7일 밖
             it("2026-10-18", "Nvidia beats estimates, raises guidance"),
             it("2026-10-18", "NVIDIA beats estimates; raises guidance!", "CNBC"),     # 거의 같은 제목 → 하나만
             it("2026-10-17", "엔비디아, 새 칩 공개"), it("2026-10-12", "Nvidia shares slip")]
    got = j.select_news(items, "2026-10-19")
    assert [g["title"] for g in got] == ["Nvidia beats estimates, raises guidance", "엔비디아, 새 칩 공개", "Nvidia shares slip"]
    import hashlib                                                         # 서로 전혀 다른 제목 50개
    many = [it("2026-10-18", hashlib.md5(str(k).encode()).hexdigest()) for k in range(50)]
    assert len(j.select_news(many, "2026-10-19")) == 30


def test_prompt_takes_up_to_30_articles():
    feats = {"rsi14": 50}
    items = [{"date": "2026-10-18", "source": "R", "title": f"t{k}", "summary": "s"} for k in range(40)]
    p = j.build_prompt("A", "에이", "2026-10-16", feats, items)
    assert "최근 기사 30건" in p and "t29" in p and "t30" not in p


def test_three_arms_add_one_kind_of_information_at_a_time(monkeypatch):
    fund = {"type": "EQUITY", "valuation": {"pe_forward": 25.1}, "analysts": {"target_mean": 200.0, "price_now": 150.0},
            "next_earnings_date": "2026-10-29", "source": "야후 파이낸스 (몇 시간 늦을 수 있음)"}
    seen = {}
    monkeypatch.setattr(j, "judge", lambda settings, model, prompt: {
        "d5": "neutral", "c5": 50, "d20": "neutral", "c20": 50, "reason": "", "invalid_if": "", "tokens": 1})
    cs = [{"time": f"2026-0{1 + i // 28}-{1 + i % 28:02d}", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0 + i / 100, "volume": 1.0}
          for i in range(260)]
    news = [{"date": "2026-09-10", "source": "R", "title": "큰 계약 발표", "summary": "s"}]
    recs = j.make_records(get_settings(), "2026-W43", "AAA", "에이", False, cs, cs, news,
                          [("m", "nonews"), ("m", "fund"), ("m", "fund_news"), ("m", "fund_news_r")],
                          now=datetime(2026, 12, 1, tzinfo=j.ET), save_input=lambda e: seen.__setitem__(e["arm"], e["prompt"]), fund=fund)
    assert set(seen) == {"nonews", "fund", "fund_news"}                         # 반복은 원래 조건과 같은 입력 (따로 안 남김)
    assert "펀더멘탈" not in seen["nonews"] and "기사" not in seen["nonews"]
    assert "pe_forward" in seen["fund"] and "기사" not in seen["fund"]
    assert "pe_forward" in seen["fund_news"] and "큰 계약 발표" in seen["fund_news"]
    assert all("price_now" not in p and "몇 시간 늦을" not in p for p in seen.values())   # 야후 현재가·출처 표기는 빼고
    by = {r["arm"]: r for r in recs}
    assert (by["nonews"]["fundamentals"], by["fund"]["fundamentals"], by["fund_news"]["fundamentals"]) == (False, True, True)
    assert (by["fund"]["news_count"], by["fund_news"]["news_count"], by["fund_news_r"]["prompt_sha"]) == (0, 1, by["fund_news"]["prompt_sha"])
    none = j.make_records(get_settings(), "2026-W43", "AAA", "에이", False, cs, cs, [], [("m", "fund")],
                          now=datetime(2026, 12, 1, tzinfo=j.ET), fund=None)
    assert none[0]["fundamentals"] is False                                     # 야후에서 못 받으면 없이 판단하고 남긴다
