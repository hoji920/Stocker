from backend import conditions


def _bars(closes, start=1, high_pad=1.0):
    return [{"time": f"2026-11-{start + i:02d}", "open": c, "high": c + high_pad, "low": c - high_pad, "close": float(c)} for i, c in enumerate(closes)]


def test_zone_alert_reaches_then_rejects_or_breaks():
    c = {"id": "c1", "type": "zone", "symbol": "SOXX", "low": 157.0, "high": 161.0, "created": "2026-11-01", "state": {}}
    assert conditions.evaluate(c, _bars([146, 150]), 152.0) == []
    ev = conditions.evaluate(c, _bars([146, 150]), 158.0)                       # 실시간 가격이 구간 안 → 도착
    assert [e["key"] for e in ev] == ["cond:c1:reach"] and not c.get("done")
    ev = conditions.evaluate(c, _bars([146, 150, 159, 154]), 154.0)             # 닿은 뒤 종가가 구간 아래 → 꺾임
    assert [e["key"] for e in ev] == ["cond:c1:reject"] and c["done"]
    d = {"id": "c2", "type": "zone", "symbol": "SOXX", "low": 157.0, "high": 161.0, "created": "2026-11-01", "state": {}}
    ev = conditions.evaluate(d, _bars([150, 163]), 163.0)                       # 구간을 건너뛰어 갭 상승 → 도착 없이 돌파
    assert [e["key"] for e in ev] == ["cond:c2:break"] and d["done"]
    e = {"id": "c3", "type": "zone", "symbol": "SOXX", "low": 157.0, "high": 161.0, "created": "2026-11-01", "state": {}}
    ev = conditions.evaluate(e, _bars([150, 158, 163]), 163.0)                  # 구간 안에서 놀다가 위로 마감 → 도착 + 돌파
    assert [x["key"] for x in ev] == ["cond:c3:reach", "cond:c3:break"]


def test_rsi_alert_turns_near_the_level():
    from datetime import date, timedelta
    closes = [100 - i for i in range(30)] + [70 + i * 1.5 for i in range(8)] + [80, 77, 74]   # 내리다 반등했다가 다시 꺾임
    day0 = date(2026, 9, 1)
    bars = [{"time": (day0 + timedelta(days=i)).isoformat(), "open": x, "high": x + 1, "low": x - 1, "close": float(x)} for i, x in enumerate(closes)]
    c = {"id": "r1", "type": "rsi", "symbol": "SOXX", "level": 50, "created": bars[30]["time"], "state": {}}
    keys = [e["key"] for e in conditions.evaluate(c, bars, None)]
    assert "cond:r1:turn" in keys and c["done"] and c["state"]["peak"] >= 48
