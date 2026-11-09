from dataclasses import replace

from backend import company
from backend.config import get_settings


def test_profile_is_saved_and_reused_for_a_month(monkeypatch, tmp_path):
    s = replace(get_settings(), data_dir=tmp_path)
    calls = []
    monkeypatch.setattr(company, "fetch_yahoo", lambda sym: calls.append(sym) or {
        "type": "EQUITY", "sector": "Technology", "industry": "Semiconductors", "summary_en": "Makes chips."})
    monkeypatch.setattr(company, "korean", lambda settings, info: {"summary": "칩을 만들어요.", "industry_ko": "반도체", "tokens": 10})
    p = company.profile(s, "NVDA", {"name": "엔비디아", "listDate": "1999-01-22"})
    assert (p["sector_ko"], p["industry_ko"], p["summary"], p["name"]) == ("기술", "반도체", "칩을 만들어요.", "엔비디아")
    assert company.profile(s, "NVDA")["summary"] == "칩을 만들어요." and calls == ["NVDA"]     # 두 번째는 저장한 걸 쓴다 (AI 안 부름)
    path = tmp_path / "company" / "NVDA.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"saved_at": ', '"saved_at": -'), encoding="utf-8")   # 오래된 것
    company.profile(s, "NVDA")
    assert calls == ["NVDA", "NVDA"]


def test_money_uses_the_currency_yahoo_reports(monkeypatch):
    from backend import company
    payload = {"quoteSummary": {"result": [{"quoteType": {"quoteType": "EQUITY"}, "summaryDetail": {"marketCap": {"raw": 8.169e13}, "currency": "KRW"},
                                            "financialData": {"totalCash": {"raw": 8.79e13}, "financialCurrency": "KRW"}}]}}

    class R:
        def raise_for_status(self): pass
        def json(self): return payload

    class C:
        def get(self, *a, **k): return R()
    monkeypatch.setattr(company, "_client", lambda: (C(), "crumb"))
    company._fund.pop("TEST.KS", None)
    f = company.fundamentals("TEST.KS")
    assert f["market_cap"] == "81.69조 원" and f["balance"]["total_cash"] == "87.90조 원" and "market_cap_usd" not in f


def test_fib_levels_measure_the_up_move_down_from_the_high_or_the_down_move_up_from_the_low():
    from backend import analysis
    # 100 → 200 (오름) → 140 (조정): 상승분 되돌림 60% / 하락분 회복은 '200 뒤 최저 140, 그 전 최고 200' 기준 0%
    path = [100 + i * 5 for i in range(21)] + [200 - i * 6 for i in range(1, 11)]
    cs = [{"time": f"d{i:03d}", "open": p, "high": p, "low": p, "close": p} for i, p in enumerate(path)]
    up = analysis.fib_levels(cs, 120, "up")
    assert (up["mode"], up["high"]["price"], up["low"]["price"], up["now_pct"]) == ("up", 200, 100, 60.0)
    assert [round(x["price"], 1) for x in up["levels"]][1:4] == [176.4, 161.8, 150.0]          # 23.6 · 38.2 · 50% 되돌림
    assert analysis.fib_levels(cs, 120, "auto")["mode"] == "up"                                   # 최저가(처음) → 최고가 순서
    down = analysis.fib_levels(cs, 120, "down")                                                   # 최저가 100 이 맨 앞이라 그 전 최고도 100
    assert down is None
