"""옵션이 예상하는 움직임 (야후 옵션 체인, 미국 종목·ETF).

- 만기마다 등가격(ATM) 스트래들 값 = 콜 + 풋 (호가 중간값, 없으면 마지막 체결가) ÷ 현재가 → '그 만기까지 ±몇 %'.
  시장이 옵션 가격으로 매긴 예상 움직임의 흔한 근사다 (실제 움직임이 그 안에 머문다는 보장은 없다).
- 다음 실적 발표일이 있으면 실적 직후 첫 만기와 그 바로 전 만기를 비교해 실적에 붙은 몫을 따로 본다.
- 등가격 내재변동성(연율), 풋/콜 거래량·미결제약정 비율도 같이.
- 15분 캐시. 야후 값이라 몇 분 늦을 수 있고, 장이 닫혀 있으면 마지막 호가 기준이다.
"""
from __future__ import annotations
import math
import time
from datetime import datetime, timezone

from backend import company

_cache: dict[str, tuple[float, dict]] = {}
TTL = 15 * 60


def _chain(symbol: str, expiry: int | None = None) -> dict:
    c, crumb = company._client()
    params = {"crumb": crumb, **({"date": expiry} if expiry else {})}
    r = c.get(f"https://query2.finance.yahoo.com/v7/finance/options/{symbol}", params=params)
    r.raise_for_status()
    res = (r.json().get("optionChain", {}).get("result") or [None])[0]
    if not res:
        raise ValueError("옵션 체인이 없어요")
    return res


def _mid(o: dict) -> float | None:
    bid, ask, last = o.get("bid") or 0, o.get("ask") or 0, o.get("lastPrice") or 0
    if bid > 0 and ask > 0:
        return (bid + ask) / 2
    return last or None


def straddle(chain: dict, spot: float) -> dict | None:
    """한 만기의 등가격 스트래들. 콜·풋이 같은 행사가에 둘 다 있어야 한다."""
    opt = (chain.get("options") or [{}])[0]
    calls, puts = {x["strike"]: x for x in opt.get("calls", [])}, {x["strike"]: x for x in opt.get("puts", [])}
    both = sorted(set(calls) & set(puts), key=lambda k: abs(k - spot))
    for k in both[:3]:                                         # 가장 가까운 행사가에 호가가 없으면 다음 것
        c, p = _mid(calls[k]), _mid(puts[k])
        if c and p:
            iv = [x.get("impliedVolatility") for x in (calls[k], puts[k]) if x.get("impliedVolatility")]
            vol = lambda xs, f: sum((x.get(f) or 0) for x in xs)
            cv, pv = vol(opt["calls"], "volume"), vol(opt["puts"], "volume")
            co, po = vol(opt["calls"], "openInterest"), vol(opt["puts"], "openInterest")
            move = c + p
            share = lambda a, b: round(a / (a + b) * 100) if a + b else None
            return {"strike": k, "straddle": round(move, 2), "move_pct": round(move / spot * 100, 1),
                    "low": round(spot - move, 2), "high": round(spot + move, 2),
                    "atm_iv_pct": round(sum(iv) / len(iv) * 100, 1) if iv else None,
                    "put_call_volume": round(pv / cv, 2) if cv else None, "put_call_oi": round(po / co, 2) if co else None,
                    # 콜이 몇 %인지 (그 만기 전체 행사가 합). 풋 % = 100 − 콜 %
                    "call_volume": int(cv), "put_volume": int(pv), "call_oi": int(co), "put_oi": int(po),
                    "call_volume_pct": share(cv, pv), "call_oi_pct": share(co, po)}
    return None


def expected_moves(symbol: str, earnings_date: str | None = None) -> dict:
    """{spot, as_of, expiries: [...가까운 3개], earnings: {...} | None}."""
    key = f"{symbol}:{earnings_date}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    first = _chain(symbol)
    q = first.get("quote") or {}
    spot = q.get("regularMarketPrice")
    if not spot:
        raise ValueError("현재가가 없어요")
    exps = first.get("expirationDates") or []
    day = lambda e: datetime.fromtimestamp(e, timezone.utc).date().isoformat()
    today = datetime.now(timezone.utc).date()
    rows, chains = [], {}

    def row(e):
        ch = first if e == exps[0] else _chain(symbol, e)
        chains[e] = ch
        s = straddle(ch, spot)
        if s:
            d = (datetime.fromisoformat(day(e)).date() - today).days
            rows.append({"expiry": day(e), "days": max(d, 0), **s})

    for e in exps[:3]:
        row(e)
    earn = None
    if earnings_date:
        after = [e for e in exps if day(e) > earnings_date]       # 실적 '다음 날' 이후 만기 (장 마감 뒤 발표면 다음 날 반응)
        before = [e for e in exps if day(e) <= earnings_date]
        if after:
            if after[0] not in chains:
                row(after[0])
            a = next((r for r in rows if r["expiry"] == day(after[0])), None)
            b = next((r for r in rows if before and r["expiry"] == day(before[-1])), None) if before else None
            if a:
                earn = {"date": earnings_date, "expiry": a["expiry"], "move_pct": a["move_pct"], "low": a["low"], "high": a["high"]}
                # 실적 몫 ≈ √(실적 뒤 만기 분산 − 실적 전 만기 분산을 기간 비율로 늘린 것). 근사라 음수가 되면 뺀다
                if b and b["days"] > 0 and a["days"] > b["days"]:
                    rest = (b["move_pct"] ** 2) * (a["days"] / b["days"])
                    if a["move_pct"] ** 2 > rest:
                        earn["earnings_only_pct"] = round(math.sqrt(a["move_pct"] ** 2 - rest), 1)
    rows.sort(key=lambda r: r["expiry"])
    # 보여주는 만기들을 합친 콜·풋 비중 (거래량 = 오늘 하루, 미결제약정 = 쌓여 있는 계약)
    tot = {k: sum(r[k] for r in rows[:4]) for k in ("call_volume", "put_volume", "call_oi", "put_oi")}
    pct = lambda a, b: round(a / (a + b) * 100) if a + b else None
    flow = {**tot, "call_volume_pct": pct(tot["call_volume"], tot["put_volume"]), "call_oi_pct": pct(tot["call_oi"], tot["put_oi"])}
    out = {"symbol": symbol, "spot": spot, "flow": flow, "as_of": datetime.fromtimestamp(q.get("regularMarketTime") or time.time(), timezone.utc).isoformat(),
           "expiries": rows[:4], "earnings": earn, "source": "야후 파이낸스 옵션 (몇 분 늦을 수 있음)"}
    _cache[key] = (time.time(), out)
    return out
