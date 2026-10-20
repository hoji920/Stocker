from backend import options, thesis


def _chain(strikes, call, put):
    return {"options": [{"calls": [{"strike": k, "bid": call(k) - 0.1, "ask": call(k) + 0.1, "impliedVolatility": 0.5, "volume": 10, "openInterest": 100}
                                   for k in strikes],
                         "puts": [{"strike": k, "bid": put(k) - 0.1, "ask": put(k) + 0.1, "impliedVolatility": 0.6, "volume": 20, "openInterest": 300}
                                  for k in strikes]}]}


def test_straddle_uses_the_nearest_strike_and_quote_midpoints():
    ch = _chain([95, 100, 105], call=lambda k: 3.0, put=lambda k: 2.0)
    s = options.straddle(ch, spot=101.0)
    assert s["strike"] == 100 and s["straddle"] == 5.0 and s["move_pct"] == round(5 / 101 * 100, 1)
    assert (s["low"], s["high"]) == (96.0, 106.0) and s["atm_iv_pct"] == 55.0
    assert s["put_call_volume"] == 2.0 and s["put_call_oi"] == 3.0


def test_straddle_skips_strikes_without_quotes():
    ch = _chain([100, 105], call=lambda k: 3.0, put=lambda k: 2.0)
    for side in ("calls", "puts"):
        ch["options"][0][side][0].update(bid=0, ask=0, lastPrice=0)            # 100 은 호가가 없다
    assert options.straddle(ch, spot=101.0)["strike"] == 105


def test_thesis_needs_a_check_after_a_week_or_after_earnings():
    t = {"checks": [{"at": "2026-10-20T09:00:00+09:00"}], "earnings_watch": "2026-10-29"}
    assert thesis.stale({"checks": []}) == "아직 한 번도 점검하지 않았어요"
    assert thesis.stale(t, today="2026-10-22") is None
    assert "실적 발표(2026-10-29)" in thesis.stale(t, today="2026-10-30")
    assert "7일" in thesis.stale({**t, "earnings_watch": None}, today="2026-10-27")


def test_clean_lines_accepts_text_or_list():
    assert thesis.clean_lines("- 하나\n\n• 둘 \n셋") == ["하나", "둘", "셋"]
    assert thesis.clean_lines([" a ", "", "b"], cap=1) == ["a"]
