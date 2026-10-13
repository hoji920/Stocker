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
    assert [c["time"] for c in out["candles"]] == ["2026-01-01", "2026-01-02", "2026-01-03"]     # 차트가 이미 가진 봉과 안 겹친다
    assert asked == [("TEST", main.bars.DAILY["1w"])]                                            # 주봉이 쓰는 긴 일봉을 같이 쓴다


def test_older_bars_are_empty_in_demo(monkeypatch):
    monkeypatch.setattr(main, "get_settings", lambda: replace(get_settings(), toss_client_id="", toss_client_secret=""))
    assert main.chart_older("TEST", before="2026-01-04") == {"candles": []}


def test_long_days_reuses_a_longer_download(monkeypatch):
    monkeypatch.setitem(main._cache, f"days:TEST:{main.bars.DAILY['1y']}", (main.time.time(), _days(5)))
    monkeypatch.setattr(main, "toss", lambda: (_ for _ in ()).throw(AssertionError("다시 받으면 안 된다")))
    assert len(main.long_days("TEST", main.bars.DAILY["1w"])) == 5
