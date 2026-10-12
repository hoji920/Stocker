"""내 매매 규칙: 사용자가 정한 조건에 지금 가격이 해당하는지 '기계적으로' 대조한다 (AI 가 아니라 계산).

기본 규칙 (전부 사용자가 바꿀 수 있다)
- 매수 구간: 가장 가까운 지지선에서 ATR × buy_support_atr 이내  또는  채널 아래쪽 buy_channel_pct % 이내
- 매도 목표: 현재가 위의 가장 가까운 저항선 (없으면 채널 상단)
- 손절 기준: 지지선(없으면 채널 하단) 아래로 ATR × stop_atr
- 손익비 = (목표 − 현재가) ÷ (현재가 − 손절).  min_rr 미만이면 구간이어도 '조건 미달'

이건 추천이 아니다. research/ 백테스트에서 자동 지지/저항·채널은 무작위 선보다 잘 지켜지지 않았다.
'내가 정한 규칙에 맞는 자리인지'를 빠뜨리지 않고 확인하는 용도.
"""
from __future__ import annotations

from backend import analysis

DEFAULT = {
    "buy_support_atr": 1.0,     # 지지선에서 ATR 몇 배 이내면 '지지 근처'
    "buy_channel_pct": 25.0,    # 채널 안 위치(0=하단, 100=상단)가 이 값 이하면 '채널 하단'
    "target_near_atr": 1.0,     # 목표가(저항)에서 ATR 몇 배 이내면 '목표 근처'
    "stop_atr": 0.5,            # 지지선 아래로 ATR 몇 배를 손절 기준으로 (0.5 = 앱이 '뚫렸다'고 보는 기준과 같다)
    "min_rr": 1.5,              # 최소 손익비
    "note": "",                 # 말로 적어둔 내 원칙 (에이전트가 같이 읽는다)
}
LIMITS = {"buy_support_atr": (0.1, 5), "buy_channel_pct": (1, 60), "target_near_atr": (0.1, 5),
          "stop_atr": (0.1, 5), "min_rr": (0.5, 10)}


def clean(raw: dict | None) -> dict:
    """저장된/입력된 규칙을 허용 범위 안의 값으로."""
    out = dict(DEFAULT)
    for key, (lo, hi) in LIMITS.items():
        try:
            out[key] = min(hi, max(lo, float((raw or {}).get(key, DEFAULT[key]))))
        except (TypeError, ValueError):
            pass
    out["note"] = str((raw or {}).get("note", ""))[:500]
    return out


def _nearest(lines: list[dict], price: float, below: bool) -> dict | None:
    """현재가 아래(지지) 또는 위(저항)에서 가장 가까운 선. 현재가가 걸쳐 있는 선도 포함."""
    side = [l for l in lines if (l["price"] <= price if below else l["price"] >= price) or l.get("at_price")]
    return min(side, key=lambda l: abs(l["price"] - price)) if side else None


def evaluate(chart: dict, rules: dict, fmt, holding: dict | None = None) -> dict:
    """chart = /api/chart 의 일봉 결과. fmt = 가격 표시 함수."""
    r = clean(rules)
    candles = chart["candles"]
    price = candles[-1]["close"]
    atr = float(analysis.atr(analysis.to_frame(candles)).iloc[-1])
    pct = lambda v: (v / price - 1) * 100

    def lines(kind: str) -> list[dict]:
        horiz = [{"price": l["price"], "name": "지지선" if kind == "support" else "저항선", "at_price": l.get("at_price")}
                 for l in chart["levels"][kind]]
        trend = [{"price": t["price"], "name": t["label"]} for t in chart.get("trendlines", []) if t["kind"] == kind]
        return horiz + trend

    sup, res, ch = _nearest(lines("support"), price, True), _nearest(lines("resistance"), price, False), chart.get("channel")
    reasons, checks, near_support, in_channel_low = [], [], False, False     # checks = 화면의 체크 목록 (조건 하나씩 맞는지)
    if sup:
        gap = (price - sup["price"]) / atr
        near_support = gap <= r["buy_support_atr"]
        said = "지지 근처예요" if near_support else f"지지 근처 아님 (기준 {r['buy_support_atr']:g}배 이내)"
        reasons.append(f"{sup['name']} {fmt(sup['price'])}까지 {pct(sup['price']):+.1f}% (ATR {gap:.1f}배) → {said}")
        checks.append({"label": "지지 근처", "ok": near_support,
                       "text": f"{sup['name']}까지 ATR {gap:.1f}배 (기준 {r['buy_support_atr']:g}배 이내)"})
    else:
        reasons.append("현재가 아래에 자동 지지선이 없어요")
        checks.append({"label": "지지 근처", "ok": False, "text": "현재가 아래에 자동 지지선이 없어요"})
    if ch:
        in_channel_low = ch["position_pct"] <= r["buy_channel_pct"]
        said = "채널 하단이에요" if in_channel_low else f"채널 하단 아님 (기준 {r['buy_channel_pct']:g}% 이하)"
        reasons.append(f"{ch['label']} 안에서 아래에서 {ch['position_pct']:.0f}% 위치 → {said}")
        checks.append({"label": "채널 하단", "ok": in_channel_low,
                       "text": f"{ch['label']} 아래에서 {ch['position_pct']:.0f}% 위치 (기준 {r['buy_channel_pct']:g}% 이하)"})
    else:
        reasons.append("지금은 자동 채널이 없어요")
        checks.append({"label": "채널 하단", "ok": False, "text": "지금은 자동 채널이 없어요"})

    target = res["price"] if res and res["price"] > price else (ch["upper"] if ch and ch["upper"] > price else None)
    target_name = res["name"] if res and res["price"] > price else ("채널 상단" if target else None)
    base = sup["price"] if sup else (ch["lower"] if ch and ch["lower"] < price else None)
    stop = base - r["stop_atr"] * atr if base is not None else None
    rr = (target - price) / (price - stop) if target and stop is not None and price > stop else None
    near_target = bool(res) and abs(res["price"] - price) / atr <= r["target_near_atr"]

    zone = near_support or in_channel_low
    if zone and rr is not None and rr >= r["min_rr"]:
        verdict, tone = "매수 조건 충족", "buy"
    elif zone and rr is not None:
        verdict, tone = "매수 구간이지만 손익비 미달", "wait"
    elif zone:
        verdict, tone = "매수 구간이지만 목표·손절을 못 정함", "wait"
    elif near_target:
        verdict, tone = "매도 목표 근처", "sell"
    else:
        verdict, tone = "조건에 해당 없음", "none"
    if rr is not None:
        said = "기준 충족" if rr >= r["min_rr"] else f"기준 {r['min_rr']:g} 미달"
        reasons.append(f"손익비 {rr:.1f} (목표 {pct(target):+.1f}% ÷ 손절 {pct(stop):+.1f}%) → {said}")
    checks.append({"label": "손익비", "ok": rr is not None and rr >= r["min_rr"],
                   "text": f"{rr:.1f} (기준 {r['min_rr']:g} 이상)" if rr is not None else "목표나 손절 기준을 못 정했어요"})
    if near_target:
        reasons.append(f"{res['name']} {fmt(res['price'])}까지 {pct(res['price']):+.1f}% → 매도 목표 근처예요")

    out = {
        "symbol": chart["symbol"], "name": chart["name"], "price": price, "price_text": fmt(price), "atr": atr,
        "verdict": verdict, "tone": tone, "reasons": reasons, "checks": checks,
        "support": {"price": sup["price"], "text": fmt(sup["price"]), "pct": pct(sup["price"]), "name": sup["name"]} if sup else None,
        "target": {"price": target, "text": fmt(target), "pct": pct(target), "name": target_name} if target else None,
        "stop": {"price": stop, "text": fmt(stop), "pct": pct(stop)} if stop is not None else None,
        "rr": round(rr, 2) if rr is not None else None,
        "channel": {"label": ch["label"], "position_pct": ch["position_pct"], "lower": ch["lower"], "upper": ch["upper"]} if ch else None,
        "rules": r,
        "caution": "내가 정한 규칙에 맞는지만 본 거예요. 이 앱의 백테스트에서 자동 지지/저항·채널은 무작위 선보다 잘 지켜지지 않았어요.",
    }
    if holding:
        avg = holding["avg_price"]
        out["holding"] = {"quantity": holding["quantity"], "avg_price": avg, "avg_text": fmt(avg), "pl_rate": holding["pl_rate"],
                          "target_from_avg_pct": (target / avg - 1) * 100 if target else None,
                          "stop_from_avg_pct": (stop / avg - 1) * 100 if stop is not None else None}
    return out


LOOKBACK, MAX_HOLD = 400, 60       # 앱이 일봉 분석에 쓰는 길이 / 목표도 손절도 안 닿으면 60봉 뒤 정리


def backtest(candles: list[dict], rules: dict, fmt, symbol: str, name: str, bars: int = 750, keep: int | None = 6) -> dict:
    """내 규칙을 과거에 그대로 적용했다면 (미래 정보 없음: 봉 t 의 판정은 t 까지의 데이터로만).

    - 쉬는 날마다 그날까지의 봉으로 앱과 똑같이 선을 긋고 규칙을 대조 → '매수 조건 충족'이면 그날 종가에 산다
    - 그때 정해진 목표가에 고가가 닿으면 목표가에 팔고, 종가가 손절 기준 아래면 그 종가에 판다 (같은 날 둘 다면 손절로 친다)
    - MAX_HOLD 봉 안에 둘 다 안 되면 그날 종가에 정리. 한 번에 한 건만 보유. 수수료·세금·호가 차이는 안 뺐다
    - 비교: 같은 기간 그냥 들고 있기 / 아무 날 사서 평균 보유 기간만큼 들고 있기
    """
    n = len(candles)
    start = max(min(LOOKBACK, n // 2), n - bars)
    close = [c["close"] for c in candles]
    trades, i, held_bars = [], start, 0
    while i < n - 1:
        sub = candles[max(0, i - LOOKBACK + 1): i + 1]
        r = evaluate({"symbol": symbol, "name": name, "candles": sub, **analysis.analyze(sub, fmt, "일")}, rules, fmt)
        if r["tone"] != "buy":
            i += 1
            continue
        entry, target, stop = r["price"], r["target"]["price"], r["stop"]["price"]
        last = min(i + MAX_HOLD, n - 1)
        exit_i, exit_price, why = last, close[last], ("보유 중" if last == n - 1 and i + MAX_HOLD > n - 1 else "기간 만료")
        for j in range(i + 1, last + 1):
            if close[j] <= stop:
                exit_i, exit_price, why = j, close[j], "손절"
                break
            if candles[j]["high"] >= target:
                exit_i, exit_price, why = j, target, "목표"
                break
        trades.append({"buy_date": candles[i]["time"], "buy": fmt(entry), "sell_date": candles[exit_i]["time"], "sell": fmt(exit_price),
                       "return_pct": round((exit_price / entry - 1) * 100, 2), "bars": exit_i - i, "exit": why})
        held_bars += exit_i - i
        i = exit_i + 1
    rets = [t["return_pct"] for t in trades]
    total = 1.0
    for x in rets:
        total *= 1 + x / 100
    avg_hold = round(held_bars / len(trades)) if trades else 0
    any_day = [close[t + avg_hold] / close[t] - 1 for t in range(start, n - avg_hold)] if avg_hold else []
    return {
        "symbol": symbol, "name": name, "period": f"{candles[start]['time']} ~ {candles[-1]['time']}", "bars": n - start,
        "trades": len(trades), "wins": sum(x > 0 for x in rets),
        "win_rate_pct": round(sum(x > 0 for x in rets) / len(rets) * 100, 1) if rets else None,
        "avg_return_pct": round(sum(rets) / len(rets), 2) if rets else None,
        "total_return_pct": round((total - 1) * 100, 1), "avg_hold_bars": avg_hold,
        "time_in_market_pct": round(held_bars / (n - start) * 100, 1),
        "exits": {k: sum(t["exit"] == k for t in trades) for k in ("목표", "손절", "기간 만료", "보유 중")},
        "buy_and_hold_pct": round((close[-1] / close[start] - 1) * 100, 1),
        "any_day_same_hold_pct": round(sum(any_day) / len(any_day) * 100, 2) if any_day else None,
        "recent_trades": trades[-keep:] if keep else trades, "rules": clean(rules),      # keep=None 이면 전부 (research 용)
        "caution": "한 종목·적은 표본이라 우연일 수 있어요. 수수료·세금·호가 차이는 안 뺐어요. "
                   + ("거래가 10번 미만이라 승률·평균은 참고만 하세요. " if len(trades) < 10 else "")
                   + "과거 결과가 앞으로를 보장하지 않아요.",
    }
