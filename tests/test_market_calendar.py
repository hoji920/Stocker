from backend import market_calendar as mc


def test_event_names_are_matched_to_korean_and_noise_is_dropped():
    assert mc._match("Core CPI") == ("근원 소비자물가(CPI)", 2)
    assert mc._match("CPI") == ("소비자물가(CPI)", 2)
    assert mc._match("Fed Interest Rate Decision") == ("FOMC 금리 결정", 2)
    assert mc._match("Initial Jobless Claims") == ("신규 실업수당 청구", 1)
    for noise in ("Atlanta Fed GDPNow", "Cleveland CPI", "Fed Waller Speaks", "MBA Mortgage Applications", "Jobless Claims 4-Week Avg."):
        assert mc._match(noise) is None


def test_econ_day_converts_eastern_time_to_korea_and_merges_units(monkeypatch):
    rows = [{"country": "United States", "eventName": "CPI", "gmt": "08:30", "actual": "&nbsp;", "consensus": "0.3%", "previous": "0.4%"},
            {"country": "United States", "eventName": "CPI", "gmt": "08:30", "actual": "&nbsp;", "consensus": "3.1%", "previous": "3.0%"},
            {"country": "United Kingdom", "eventName": "CPI", "gmt": "02:00", "actual": "", "consensus": "", "previous": ""},
            {"country": "United States", "eventName": "Fed Waller Speaks", "gmt": "10:00", "actual": "", "consensus": "", "previous": ""}]

    class R:
        def raise_for_status(self): pass
        def json(self): return {"data": {"rows": rows}}
    monkeypatch.setattr(mc.httpx, "get", lambda *a, **k: R())
    mc._day_cache.clear()
    ev = mc.econ_day("2026-10-15", "2026-10-14")
    assert len(ev) == 1 and ev[0]["name"] == "소비자물가(CPI)"
    assert ev[0]["time_kst"] == "10-15 21:30"                          # 미국 동부 08:30 (서머타임) = 한국 21:30
    assert [v["consensus"] for v in ev[0]["values"]] == ["0.3%", "3.1%"] and ev[0]["values"][0]["actual"] == ""
