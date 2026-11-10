"""반등·바닥 체크: '기술적 반등이야? 데드캣이야?'를 물을 때 에이전트에게 줄 숫자 (전부 코드가 계산, AI 는 설명만).

- 오늘 봉: 등락, 몸통이 평소 하루 변동(ATR)의 몇 배인지, 아래·위 꼬리
- RSI(14): 지금 값, 최근 값들, 과매도(30 아래)에서 오늘 위로 나왔는지, 강세 다이버전스(가격 저점은 낮아졌는데 RSI 저점은 높아졌나)
- 거래량: 오늘 vs 최근 하락일 평균, vs 20일 평균
- 이동평균 20·50·200일선
- 피보나치 되돌림(상승분 기준, 기준이 깨지면 더 큰 파동으로): 지금 위치, 오늘 다시 넘은 선
- 위쪽 '다음 시험대': 저항선·이동평균·피보나치 선이 3% 안에 몰린 가장 가까운 구간 / 아래쪽 지켜야 할 자리와 최근 저점

이건 차트를 읽는 재료다. 이 앱의 검증에서 자동 선·피보나치·패턴은 무작위 선과 차이가 없었다 (research/).
"""
from __future__ import annotations

import numpy as np

from backend import analysis

ZONE_PCT = 3.0          # 이 % 안에 몰린 선들을 한 구간으로
LOOK_PCT = 25.0         # 현재가에서 이 % 안의 선만 후보로


def _cluster(cands: list[tuple[float, str]], ascending: bool) -> list[dict]:
    """가격순으로 놓고 첫 선에서 ZONE_PCT 안에 드는 것끼리 묶는다."""
    cands = sorted(cands, key=lambda x: x[0], reverse=not ascending)
    zones: list[dict] = []
    for p, name in cands:
        z = zones[-1] if zones else None
        if z and abs(p / z["anchor"] - 1) * 100 <= ZONE_PCT:
            z["members"].append((p, name))
        else:
            zones.append({"anchor": p, "members": [(p, name)]})
    return zones


def check(candles: list[dict], fmt=lambda v: f"{v:,.2f}") -> dict:
    """candles = 끝난 일봉. 반환은 에이전트가 읽을 dict (가격은 fmt 로 적은 글자와 숫자 둘 다)."""
    df = analysis.to_frame(candles, despike=analysis.DESPIKE)
    o, h, l, c, v = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume"))
    n = len(c)
    if n < 60:
        return {"error": "일봉이 60개보다 적어서 볼 수 없어요"}
    last, prev = c[-1], c[-2]
    atr = float(analysis.atr(df).iloc[-1])
    rng = max(h[-1] - l[-1], 1e-9)
    body = c[-1] - o[-1]
    candle = {
        "date": str(df["time"].iloc[-1]), "change_pct": round((last / prev - 1) * 100, 2),
        "open": fmt(o[-1]), "high": fmt(h[-1]), "low": fmt(l[-1]), "close": fmt(last),
        "kind": "양봉" if body > 0 else "음봉" if body < 0 else "보합",
        "body_vs_atr": round(abs(body) / atr, 2) if atr else None,          # 1.5 넘으면 '장대'로 많이 본다
        "long_body": bool(atr and abs(body) / atr >= 1.5),                  # 몸통이 평소 하루 변동(ATR)의 1.5배 이상 = 장대봉. 아니면 '보통 크기'
        "change_vs_atr": round(abs(last - prev) / atr, 2) if atr else None,   # 전날 종가 대비 움직임이 ATR 몇 배 (갭 포함)
        "lower_wick_pct_of_range": round((min(o[-1], last) - l[-1]) / rng * 100),
        "upper_wick_pct_of_range": round((h[-1] - max(o[-1], last)) / rng * 100),
    }

    rsi = analysis.rsi(df["close"]).to_numpy(dtype=float)
    swings = [(i, p) for i, p, kind in analysis.swing_points(df) if kind == "low" and i >= n - 60]
    recent_low_i = int(np.argmin(l[-10:])) + n - 10                       # 아직 확정 안 된 최근 저점도 후보로
    lows = sorted({i: p for i, p in swings + [(recent_low_i, l[recent_low_i])]}.items())
    div = None
    if len(lows) >= 2:
        (i1, p1), (i2, p2) = lows[-2], lows[-1]
        div = {"earlier_low": {"date": str(df["time"].iloc[i1]), "price": fmt(p1), "rsi": round(float(rsi[i1]), 1)},
               "later_low": {"date": str(df["time"].iloc[i2]), "price": fmt(p2), "rsi": round(float(rsi[i2]), 1)},
               "bullish": bool(p2 < p1 and rsi[i2] > rsi[i1])}
    rsi_block = {
        "now": round(float(rsi[-1]), 1), "prev": round(float(rsi[-2]), 1),
        "last_10": [round(float(x), 1) for x in rsi[-10:]], "min_last_10": round(float(np.nanmin(rsi[-10:])), 1),
        "oversold_now": bool(rsi[-1] < 30), "crossed_above_30_today": bool(rsi[-2] < 30 <= rsi[-1]),
        "divergence": div,
    }

    down = [i for i in range(n - 10, n - 1) if c[i] < c[i - 1]]
    down_avg = float(np.mean(v[down])) if down else None
    volume = {"today": int(v[-1]), "avg_20d": int(np.mean(v[-21:-1])),
              "vs_20d": round(float(v[-1] / np.mean(v[-21:-1])), 2) if np.mean(v[-21:-1]) else None,
              "recent_down_days_avg": int(down_avg) if down_avg else None,
              "vs_recent_down_days": round(float(v[-1] / down_avg), 2) if down_avg else None}

    sma = {k: float(analysis.sma(df["close"], k).iloc[-1]) for k in (20, 50, 200) if n >= k}
    smas = {f"sma{k}": {"price": fmt(x), "distance_pct": round(float(x / last - 1) * 100, 1)} for k, x in sma.items()}

    fib = analysis.fib_levels(candles, 120, "up")
    fib_block = None
    if fib:
        names = {0.236: "23.6%", 0.382: "38.2%", 0.5: "50%", 0.618: "61.8%", 0.786: "78.6%"}
        lv = [(x["price"], f"피보 {names[x['r']]} 되돌림") for x in fib["levels"] if x["r"] in names]
        fib_block = {"swing": f"{fib['low']['time']} {fmt(fib['low']['price'])} → {fib['high']['time']} {fmt(fib['high']['price'])}",
                     "retraced_pct": fib["now_pct"], "extended": fib["extended"],
                     "levels": {name: fmt(p) for p, name in lv},
                     "reclaimed_today": [name for p, name in lv if prev < p <= last],
                     "lost_today": [name for p, name in lv if prev > p >= last]}

    lv_all = analysis.find_levels(df)
    cands = [(x["price"], "저항선") for x in lv_all["resistance"]] + [(x["price"], "지지선") for x in lv_all["support"]]
    cands += [(x, f"{k}일선") for k, x in sma.items()]
    if fib_block:
        cands += [(x["price"], f"피보 {names[x['r']]} 되돌림") for x in fib["levels"] if x["r"] in names]
    # 지금 가격에 걸쳐 있는 선(0.5% 안)은 '위·아래 자리'가 아니라 따로 적는다
    at_price = [f"{name} {fmt(p)}" for p, name in cands if abs(p / last - 1) * 100 < 0.5]
    near = [(p, name) for p, name in cands if 0.5 <= abs(p / last - 1) * 100 <= LOOK_PCT]
    zone = lambda z: {"from": fmt(min(p for p, _ in z["members"])), "to": fmt(max(p for p, _ in z["members"])),
                      "distance_pct": round(float(z["anchor"] / last - 1) * 100, 1),
                      "members": [f"{name} {fmt(p)}" for p, name in sorted(z["members"])], "count": len(z["members"])}
    above = _cluster([x for x in near if x[0] > last], ascending=True)
    below = _cluster([x for x in near if x[0] < last], ascending=False)
    # 다음 시험대 = 가장 가까운 구간, 단 선이 하나뿐이면 그 위에 둘 이상 몰린 구간이 있으면 그것도 같이
    test_zone = above[0] if above else None
    crowded = next((z for z in above if len(z["members"]) >= 2), None)

    lo20 = int(np.argmin(l[-20:])) + n - 20
    return {
        "candle": candle, "rsi14": rsi_block, "volume": volume, "moving_averages": smas, "fib_retracement": fib_block,
        "lines_at_price_now": at_price,
        "next_test_zone_above": zone(test_zone) if test_zone else None,
        "crowded_zone_above": zone(crowded) if crowded and crowded is not test_zone else None,
        "support_zones_below": [zone(z) for z in below[:2]],
        "recent_low_20d": {"date": str(df["time"].iloc[lo20]), "price": fmt(l[lo20]), "is_today": lo20 == n - 1,
                           "today_low_above_it": bool(l[-1] > l[lo20]) if lo20 != n - 1 else False},
        "atr": fmt(atr),
        "note": "숫자는 코드가 계산했어요. 이 앱의 검증에서 자동 지지·저항선, 피보나치, 차트 패턴은 무작위 선보다 잘 지켜지지 않았어요.",
    }
