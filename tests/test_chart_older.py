from dataclasses import replace

from backend import main
from backend.config import get_settings


def _days(n):
    return [{"time": f"2026-01-{i + 1:02d}", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0} for i in range(n)]


def test_older_bars_are_only_those_before_the_chart(monkeypatch):
    monkeypatch.setattr(main, "get_settings", lambda: replace(get_settings(), toss_client_id="id", toss_client_secret="secret"))
    asked = []
    monkeypatch.setattr(main, "long_days", lambda symbol, pages: asked.append((symbol, pages)) or _days(10))
    out = main.chart_older(" test ", before="2026-01-04")
    # 차트의 첫 봉(01-04)까지: 화면이 첫 봉을 이걸로 바꿔 낀다
    assert [c["time"] for c in out["candles"]] == ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"] and out["replace_first"]
    assert asked == [("TEST", main.bars.DAILY_ALL)]                                              # 상장 이후 전부


def test_older_weekly_bars_end_with_the_full_week_of_the_first_bar(monkeypatch):
    monkeypatch.setattr(main, "get_settings", lambda: replace(get_settings(), toss_client_id="id", toss_client_secret="secret"))
    days = [{"time": t, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}
            for t in ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-12", "2026-01-13", "2026-01-14", "2026-01-15"]]
    monkeypatch.setattr(main, "long_days", lambda symbol, pages: days)
    # 차트의 첫 주봉은 01-13(화)부터 받아 덜 찬 봉 → 그 주 전체(01-12~)를 마지막에 주고 바꿔 끼게 한다
    out = main.chart_older("TEST", before="2026-01-13", interval="1w")
    assert [(c["time"], c["volume"]) for c in out["candles"]] == [("2026-01-05", 3.0), ("2026-01-12", 4.0)] and out["replace_first"]


def test_older_minute_bars_are_aggregated_and_cut_at_the_first_bar(monkeypatch):
    monkeypatch.setattr(main, "get_settings", lambda: replace(get_settings(), toss_client_id="id", toss_client_secret="secret"))
    t0 = 1_760_000_000 - 1_760_000_000 % 300                         # 5분 경계 (유닉스 초)
    raw = [{"time": t0 + 60 * i, "open": i, "high": i, "low": i, "close": i, "volume": 1.0} for i in range(20)]   # 1분봉 20개
    pages = []

    class Fake:
        def minute_candles(self, symbol, n):
            pages.append(n)
            return raw
    monkeypatch.setattr(main, "toss", lambda: Fake())
    monkeypatch.setattr(main, "_cache", {})
    shift = main.bars.KST_SHIFT
    out = main.chart_older("TEST", before=str(t0 + shift + 600), interval="5m")["candles"]
    assert [c["time"] for c in out] == [t0 + shift, t0 + shift + 300, t0 + shift + 600]      # 5분봉으로 묶고 첫 봉 시각까지
    assert out[-1]["volume"] == 5.0 and pages == [main.bars.MINUTE_LONG["5m"]]               # 꽉 찬 봉 (화면의 첫 봉을 바꿔 낄 것)


def test_older_bars_are_empty_in_demo(monkeypatch):
    monkeypatch.setattr(main, "get_settings", lambda: replace(get_settings(), toss_client_id="", toss_client_secret=""))
    assert main.chart_older("TEST", before="2026-01-04")["candles"] == []
    assert main.chart_older("TEST", before="1760000000", interval="1m") == {"candles": [], "replace_first": False}


def test_long_days_reuses_a_longer_download(monkeypatch):
    monkeypatch.setitem(main._cache, f"days:TEST:{main.bars.DAILY['1y']}", (main.time.time(), _days(5)))
    monkeypatch.setattr(main, "toss", lambda: (_ for _ in ()).throw(AssertionError("다시 받으면 안 된다")))
    assert len(main.long_days("TEST", main.bars.DAILY["1w"])) == 5
