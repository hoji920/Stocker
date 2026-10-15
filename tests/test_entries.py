import json
from datetime import datetime
from types import SimpleNamespace as NS

from backend import judgments as j
from backend import main


def _setup(monkeypatch, tmp_path, minutes_of):
    log = j.Log(tmp_path)
    monkeypatch.setattr(main, "judge_log", log)
    monkeypatch.setattr(main, "ASKED_PATH", tmp_path / "asked" / "asked.jsonl")
    monkeypatch.setattr(main, "ENTRIES_PATH", tmp_path / "entries" / "entries.jsonl")
    monkeypatch.setattr(main, "get_settings", lambda: NS(toss_enabled=True))
    monkeypatch.setattr(main, "toss", lambda: NS(minute_candles=lambda symbol, pages: minutes_of(symbol)))
    monkeypatch.setattr(main, "_cache", {})
    return log


def test_fill_entries_writes_the_30_minute_average_once(monkeypatch, tmp_path):
    made = "2026-10-19T07:00:00+09:00"                                    # 월요일 아침 (주간거래 전)
    t = datetime.fromisoformat(made).timestamp()
    first = t + 3 * 3600                                                   # 세 시간 뒤 첫 체결
    bars = lambda base: [{"time": t - 7200, "close": base - 10}] + [{"time": first + 60 * k, "close": base + k} for k in range(45)]
    log = _setup(monkeypatch, tmp_path, lambda s: bars(500.0 if s == "SPY" else 100.0))
    rec = {"week": "2026-W43", "symbol": "AAA", "model": "m", "arm": "news", "as_of": "2026-10-16", "made_at": made}
    log.append(rec)
    log.append({**rec, "arm": "nonews"})                                  # 같은 시각의 다른 조건 → 출발 가격 하나
    assert main.fill_entries(now_ts=first + 60 * 29) == 0                 # 30분이 아직 안 지났다
    assert main.fill_entries(now_ts=first + 60 * 40) == 1
    e = main.read_entries()[("AAA", made)]
    assert (e["price"], e["bench_price"], e["bars"], e["minutes"]) == (114.5, 514.5, 30, 30)    # 판단 전 봉(90)은 안 섞인다
    assert main.fill_entries(now_ts=first + 60 * 40) == 0                 # 이미 있으면 다시 안 적는다
    old = {**rec, "week": "2026-W42", "made_at": "2026-10-13T13:00:00+09:00"}
    log.append(old)
    assert main.fill_entries(now_ts=first + 60 * 40) == 0                 # W42 는 기록된 가격을 쓴다
