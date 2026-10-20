from datetime import datetime

from backend import judgments as j


def _days(n, start=100.0):
    return [{"time": f"2026-09-{i + 1:02d}", "open": start + i, "high": start + i, "low": start + i, "close": start + i, "volume": 1.0}
            for i in range(n)]


def test_asked_record_uses_last_finished_close_and_maps_view():
    now = datetime(2026, 9, 10, 12, 0, tzinfo=j.ET)                  # 9/10 정규장 중 → 9/09 종가가 기준
    r = j.asked_record("AAA", "에이", "m", "weak", 140, "이유", "조건", _days(10), _days(10, 500.0), now=now)
    assert (r["as_of"], r["price"], r["bench_price"]) == ("2026-09-09", 108.0, 508.0)
    assert (r["d5"], r["d20"], r["c20"], r["arm"], r["fixed"]) == ("underperform", "underperform", 100, "asked", False)


def test_only_first_view_per_symbol_and_day_counts():
    a = {"symbol": "AAA", "as_of": "2026-09-09", "view": "weak"}
    b = {"symbol": "AAA", "as_of": "2026-09-09", "view": "strong"}       # 같은 날 다시 물어 바꾼 의견
    c = {"symbol": "AAA", "as_of": "2026-09-10", "view": "strong"}
    assert j.first_per_day([a, b, c]) == [a, c]


def test_asked_views_are_kept_out_of_the_weekly_log(tmp_path):
    (tmp_path / "asked").mkdir()
    (tmp_path / "asked" / "asked.jsonl").write_text('{"symbol": "AAA"}\n', encoding="utf-8")
    assert j.Log(tmp_path).read() == []                                   # 주 1회 기록(미리 정한 실험)에 섞이지 않는다
