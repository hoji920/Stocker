from backend import rules

fmt = lambda p: f"${p:,.2f}"


def _chart(price, support=None, resistance=None, channel=None, trendlines=()):
    # 고가-저가 2 로 고정 → ATR = 2
    candles = [{"time": f"2026-01-{i + 1:02d}", "open": price, "high": price + 1, "low": price - 1, "close": price, "volume": 1.0}
               for i in range(30)]
    return {"symbol": "TEST", "name": "테스트", "candles": candles, "channel": channel, "trendlines": list(trendlines),
            "levels": {"support": [{"price": p} for p in (support or [])], "resistance": [{"price": p} for p in (resistance or [])]}}


def test_buy_zone_near_support_with_enough_reward():
    r = rules.evaluate(_chart(100, support=[99], resistance=[110]), rules.DEFAULT, fmt)
    assert r["tone"] == "buy" and r["verdict"] == "매수 조건 충족"
    assert r["target"]["price"] == 110 and r["stop"]["price"] == 98            # 지지 99 − 0.5 × ATR 2
    assert r["rr"] == 5.0                                                       # (110−100) ÷ (100−98)


def test_buy_zone_but_reward_too_small():
    r = rules.evaluate(_chart(100, support=[99], resistance=[101.5]), rules.DEFAULT, fmt)
    assert r["tone"] == "wait" and r["rr"] == 0.75


def test_not_near_support_and_near_resistance_is_sell_target():
    r = rules.evaluate(_chart(100, support=[90], resistance=[101]), rules.DEFAULT, fmt)
    assert r["tone"] == "sell" and r["verdict"] == "매도 목표 근처"


def test_channel_bottom_counts_as_buy_zone_and_channel_top_is_fallback_target():
    ch = {"label": "상승 채널", "position_pct": 10.0, "lower": 97.0, "upper": 130.0}
    r = rules.evaluate(_chart(100, channel=ch), rules.DEFAULT, fmt)
    assert r["tone"] == "buy" and r["target"]["name"] == "채널 상단" and r["stop"]["price"] == 96   # 채널 하단 97 − 1


def test_nothing_applies():
    r = rules.evaluate(_chart(100, support=[80], resistance=[130]), rules.DEFAULT, fmt)
    assert r["tone"] == "none"


def test_my_numbers_change_the_verdict():
    chart = _chart(100, support=[96], resistance=[120])
    assert rules.evaluate(chart, rules.DEFAULT, fmt)["tone"] == "none"                       # 지지에서 ATR 2배 → 기본(1배)으론 멀다
    assert rules.evaluate(chart, {"buy_support_atr": 3}, fmt)["tone"] == "buy"


def test_holding_adds_numbers_from_my_average():
    r = rules.evaluate(_chart(100, support=[99], resistance=[110]), rules.DEFAULT, fmt,
                       {"quantity": 3, "avg_price": 95.0, "pl_rate": 5.26})
    assert round(r["holding"]["target_from_avg_pct"], 1) == 15.8 and round(r["holding"]["stop_from_avg_pct"], 1) == 3.2


def test_backtest_buys_in_zone_and_sells_at_target_or_stop(monkeypatch):
    # 분석 결과를 고정: 지지 99, 저항 110 → 종가 100 이면 매수 조건 충족 (목표 110, 손절 98)
    monkeypatch.setattr(rules.analysis, "lines_only", lambda sub: {
        "levels": {"support": [{"price": 99}], "resistance": [{"price": 110}]}, "trendlines": [], "channel": None})
    bar = lambda i, close, high=None: {"time": f"d{i:03d}", "open": close, "high": high or close + 1, "low": close - 1, "close": close, "volume": 1.0}
    flat = [bar(i, 100) for i in range(40)]
    # 40: 매수(100) → 42: 고가 111 로 목표 110 도달(+10%) / 43: 다시 매수(100) → 45: 종가 97 로 손절(-3%)
    candles = flat + [bar(40, 100), bar(41, 104), bar(42, 108, high=111), bar(43, 100), bar(44, 99), bar(45, 97), bar(46, 90)]
    r = rules.backtest(candles, rules.DEFAULT, fmt, "TEST", "테스트", bars=7)
    assert [(t["buy_date"], t["sell_date"], t["return_pct"], t["exit"]) for t in r["recent_trades"]] == [
        ("d040", "d042", 10.0, "목표"), ("d043", "d045", -3.0, "손절")]
    assert (r["trades"], r["wins"], r["win_rate_pct"], r["avg_return_pct"]) == (2, 1, 50.0, 3.5)
    assert r["total_return_pct"] == 6.7                                          # 1.10 × 0.97
    assert r["buy_and_hold_pct"] == -10.0 and "10번 미만" in r["caution"]          # 같은 기간 그냥 보유: 100 → 90


def test_live_price_overrides_last_close():
    ch = {"label": "상승 채널", "position_pct": 50.0, "lower": 90.0, "upper": 130.0}
    chart = _chart(100, support=[99], resistance=[110], channel=ch)
    held = {"quantity": 1, "avg_price": 100.0, "pl_rate": 0.0}
    r = rules.evaluate(chart, rules.DEFAULT, fmt, held, price=108.0)                # 일봉 종가는 100, 지금은 108
    assert r["price"] == 108.0 and r["price_text"] == "$108.00"
    assert r["tone"] == "sell"                                                       # 지지(99)에서 멀어지고 저항(110) 근처
    assert r["channel"]["position_pct"] == 45.0                                      # (108−90) ÷ (130−90)
    assert r["holding"]["pl_rate"] == 8.0                                            # 평단 대비도 같은 가격으로
    assert rules.evaluate(chart, rules.DEFAULT, fmt)["price"] == 100.0               # 안 주면 마지막 종가


def test_live_price_source_order(monkeypatch):
    from backend import main
    s = main.get_settings()
    monkeypatch.setattr(main, "get_settings", lambda: __import__("dataclasses").replace(s, toss_client_id="", toss_client_secret=""))
    assert main.live_price("X", 100.0, given=101.5) == 101.5                         # 화면이 준 실시간 가격
    assert main.live_price("X", 100.0, given=5000) == 100.0                          # 엉뚱한 값은 무시 → (토스 없음) 마지막 종가
    assert main.live_price("X", 100.0, batch={"X": 99.0}) == 99.0                    # 전체 대조는 한 번에 받은 가격


def test_clean_clamps_bad_input():
    c = rules.clean({"min_rr": "abc", "buy_channel_pct": 999, "stop_atr": -3, "note": "x" * 900, "hack": 1})
    assert c["min_rr"] == rules.DEFAULT["min_rr"] and c["buy_channel_pct"] == 60 and c["stop_atr"] == 0.1
    assert len(c["note"]) == 500 and "hack" not in c


def test_rr_does_not_explode_when_price_sits_on_the_stop():
    # 지지 100.9, 손절 = 100.9 − 0.5×ATR(2) = 99.9, 현재가 100 → 손절까지 0.1 (ATR 0.05배)
    chart = _chart(100, support=[100.9], resistance=[110])
    chart["levels"]["support"][0]["at_price"] = True                 # 현재가가 걸쳐 있는 지지 가격대
    old = rules.evaluate(chart, {**rules.DEFAULT, "min_risk_atr": 0}, fmt)
    assert old["rr"] == 100.0 and not old["risk_floored"]             # 하한이 없으면 (110−100) ÷ 0.1
    new = rules.evaluate(chart, rules.DEFAULT, fmt)
    assert new["rr"] == 10.0 and new["risk_floored"]                  # 위험을 최소 ATR 0.5배(=1)로: (110−100) ÷ 1
    assert new["stop"]["price"] == old["stop"]["price"]               # 손절선 자체는 그대로
    assert any("위험을 ATR 0.5배" in c["text"] for c in new["checks"])


def test_risk_floor_leaves_normal_setups_alone():
    r = rules.evaluate(_chart(100, support=[99], resistance=[110]), rules.DEFAULT, fmt)
    assert r["rr"] == 5.0 and not r["risk_floored"]                   # 손절까지 2 (ATR 1배) → 하한에 안 걸린다


def test_risk_floor_can_turn_a_buy_into_wait():
    # 목표까지 +1.2, 손절까지 0.1: 하한 없으면 손익비 12 로 '충족', 하한(위험 1)이면 1.2 로 '미달'
    chart = _chart(100, support=[100.9], resistance=[101.2])
    chart["levels"]["support"][0]["at_price"] = True
    assert rules.evaluate(chart, {**rules.DEFAULT, "min_risk_atr": 0}, fmt)["tone"] == "buy"
    assert rules.evaluate(chart, rules.DEFAULT, fmt)["tone"] == "wait"
