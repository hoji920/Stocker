"""깨진 저항 추세선이 지지로 바뀌나 (역할 전환 · 리테스트).

질문: 자동으로 그은 저항 추세선을 종가로 뚫고 올라간 뒤, 가격이 다시 그 선(연장선)까지 내려오면
      그 선이 지지로 작동하나 — 근거 없는 선보다 더 잘 받쳐 주나?
계기: 차트를 보다가 "깨진 저항 추세선이 지지가 된다"는 걸 여러 번 봤다 (2026-10-28).
      눈으로 보면 튕긴 경우만 기억에 남고, 가격 아래의 아무 선이나 꽤 자주 '받치는 것처럼' 보이므로 대조군과 비교한다.
      breakouts.py 는 '깨진 뒤 그 방향으로 이어지나'(아니었다)만 봤고, 다시 내려와 닿았을 때는 보지 않았다.

미리 정한 것 (2026-10-28, 돌리기 전. 탐색 결과를 보고 아래 숫자를 바꾸지 않는다)
- 선: 시점 t 마다 t 까지의 400봉만으로 앱과 같은 저항 추세선 후보(analysis.trendline_candidates)를 긋는다 (10봉 간격).
  현재가보다 위에 있는 것만. 직선, 튀는 꼬리 자르기 없음 (이전 선 검증과 같게: DESPIKE=False, LOG_TRENDS=False)
- 돌파: t 뒤 20봉 안에 종가가 선 + 0.5 ATR 위로 (ATR 은 t 시점 14일)
- 리테스트: 돌파한 봉 다음부터 40봉 안에 저가가 선(연장선) + 0.25 ATR 이내로 내려옴. 처음 닿은 봉
- 결과 (backtest_lines 의 지지선 판정과 같다): 닿은 봉부터 20봉 안에
    지켜짐 = 종가가 선 − 0.5 ATR 아래로 가기 전에 고가가 선 + 1 ATR 위로 (닿은 다음 봉부터)
    깨짐   = 종가가 선 − 0.5 ATR 아래로
  지켜진 비율 = 지켜짐 / (지켜짐 + 깨짐)
- 같은 돌파가 여러 시점·후보에서 잡히면 (종목, 돌파 봉)마다 하나만 (가장 이른 t, 그다음 후보 순위)
- 대조군: 같은 묶음의 저항 추세선 후보 전부에서, 현재가와의 거리(ATR 단위)만 무작위로 섞은 '근거 없는 선' (seed 7).
  시점·기울기는 그대로 두고 돌파 → 리테스트 → 결과를 똑같이 판정한다. 중복도 똑같이 하나만.
- 보조: 닿은 봉 종가에서 10·20봉 뒤 수익률 (자동 선 − 대조군). 참고: 돌파한 선 중 40봉 안에 다시 닿은 비율
- 종목 묶음
    탐색  = backtest_lines 의 미국 30 + 국내 10 (2018-10 ~ 2026-10, 캐시 그대로) — 보기만 한다
    확증1 = confirm.HOLDOUT 미국 40 (같은 기간, 선 검증 탐색에 안 쓴 종목)
    확증2 = S&P 500(research/sp500.json) 중 research/data/full 에 있고 2008-01-01 ~ 2017-12-31 일봉이 1,000개 이상인 종목을
            알파벳순으로 줄 세워 4개마다 하나 (다른 시대·다른 종목)
- 판정 (확증 묶음만): 자동 선 − 대조군 지켜진 비율 차이가 확증1·확증2 모두 + 이고 양측 p < 0.05 → '근거 있음',
  하나만 → '약한 근거', 둘 다 아니면 → '근거 없음'. p 는 두 비율 검정, 차이의 95% 구간은 종목 단위 부트스트랩(2,000번)

한계: 지금 살아남은 종목만, 수수료 없음, 일봉 종가·고저가 기준. 같은 날 여러 종목이 함께 움직이는 효과는 종목 단위 부트스트랩으로만 일부 반영.

실행: python -m research.retest      결과: research/results/retest.md
"""
from __future__ import annotations
import json
import math
import random
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from backend import analysis
from research import backtest_lines as b
from research import confirm

STEP, LOOKBACK = 10, 400
BREAK_WAIT, RETEST_WAIT, FOLLOW = 20, 40, 20
TOUCH, BOUNCE, BREAK = 0.25, 1.0, 0.5
RET_H = (10, 20)
START2, END2, MIN2 = "2008-01-01", "2018-01-01", 1000


def load(path) -> list[dict]:
    return json.loads(path.read_text())


def sets() -> dict[str, dict[str, list[dict]]]:
    explore = {s: load(b.DATA / f"{s}.json") for s in b.US + b.KR}
    conf1 = {s: load(b.DATA / f"{s}.json") for s in confirm.HOLDOUT}
    sp = json.loads((b.ROOT / "research" / "sp500.json").read_text())["symbols"]
    full = b.DATA / "full"
    cand = {}
    for s in sorted(sp):
        p = full / f"{s.replace('/', '_')}.json"
        if p.exists():
            cs = [c for c in load(p) if START2 <= c["time"] < END2]
            if len(cs) >= MIN2:
                cand[s] = cs
    conf2 = {s: cand[s] for s in sorted(cand)[0::4]}
    return {"탐색": explore, "확증1": conf1, "확증2": conf2}


def follow(high, low, close, t: int, v0: float, slope: float, a: float) -> dict | None:
    """돌파 → 리테스트 → 결과. 돌파가 없으면 None."""
    n = len(close)
    line = lambda i: v0 + slope * (i - t)
    bo = next((i for i in range(t + 1, min(t + BREAK_WAIT, n - 1) + 1) if close[i] - line(i) >= BREAK * a), None)
    if bo is None:
        return None
    touch = next((i for i in range(bo + 1, min(bo + RETEST_WAIT, n - 1) + 1) if low[i] <= line(i) + TOUCH * a), None)
    out = {"bo": bo, "touch": touch, "outcome": "no_retest"}
    if touch is None:
        return out
    out["outcome"] = "unresolved"
    for j in range(touch, min(touch + FOLLOW, n - 1) + 1):
        if close[j] - line(j) <= -BREAK * a:
            out["outcome"] = "break"
            break
        if j > touch and high[j] - line(j) >= BOUNCE * a:
            out["outcome"] = "bounce"
            break
    for h in RET_H:
        out[f"ret{h}"] = (close[touch + h] / close[touch] - 1) * 100 if touch + h < n else np.nan
    return out


def eval_symbol(args) -> list[dict]:
    """그 종목의 저항 추세선 후보 전부 (현재가 위). 자동 선의 결과도 같이."""
    symbol, candles = args
    analysis.DESPIKE, analysis.LOG_TRENDS = False, False
    df = analysis.to_frame(candles)
    atr = analysis.atr(df).to_numpy()
    high, low, close = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    rows = []
    for t in range(300, len(df) - (BREAK_WAIT + RETEST_WAIT + FOLLOW), STEP):
        a = float(atr[t])
        if not a or math.isnan(a):
            continue
        sub = df.iloc[max(0, t - LOOKBACK + 1): t + 1].reset_index(drop=True)
        for c in analysis.trendline_candidates(sub)["resistance"]:
            v0, slope = float(c["price"]), float(c["slope_pct"]) / 100 * float(c["price"])
            dist = (v0 - close[t]) / a
            if dist <= 0:
                continue
            ev = follow(high, low, close, t, v0, slope, a) or {"bo": None, "outcome": "no_break"}
            rows.append({"symbol": symbol, "t": t, "rank": c["rank"], "atr": a, "dist": dist, "slope": slope, **ev})
    return rows


def dedupe(df: pd.DataFrame) -> pd.DataFrame:
    """(종목, 돌파 봉)마다 하나 — 가장 이른 시점, 그다음 후보 순위."""
    ev = df[df["bo"].notna()].sort_values(["symbol", "bo", "t", "rank"])
    return ev.drop_duplicates(["symbol", "bo"]).reset_index(drop=True)


def controls(lines: pd.DataFrame, data: dict[str, list[dict]], seed: int = 7) -> pd.DataFrame:
    rng = random.Random(seed)
    d = list(lines["dist"])
    rng.shuffle(d)
    arrays = {}
    for s, cs in data.items():
        f = analysis.to_frame(cs)
        arrays[s] = (f["high"].to_numpy(), f["low"].to_numpy(), f["close"].to_numpy())
    rows = []
    for r, dist in zip(lines.itertuples(), d):
        h, lo, c = arrays[r.symbol]
        ev = follow(h, lo, c, r.t, c[r.t] + dist * r.atr, r.slope, r.atr) or {"bo": None, "outcome": "no_break"}
        rows.append({"symbol": r.symbol, "t": r.t, "rank": r.rank, "atr": r.atr, "dist": dist, "slope": r.slope, **ev})
    return pd.DataFrame(rows)


def respect(ev: pd.DataFrame) -> tuple[int, int]:
    return int((ev.outcome == "bounce").sum()), int((ev.outcome == "break").sum())


def boot_diff(real: pd.DataFrame, ctrl: pd.DataFrame, n: int = 2000, seed: int = 0) -> tuple[float, float]:
    """종목 단위 부트스트랩: 종목을 뽑아 다시 모아 (자동 − 대조) 지켜진 비율 차이의 95% 구간."""
    rng = np.random.default_rng(seed)
    syms = sorted(set(real.symbol) | set(ctrl.symbol))
    rg = {s: respect(g) for s, g in real.groupby("symbol")}
    cg = {s: respect(g) for s, g in ctrl.groupby("symbol")}
    out = []
    for _ in range(n):
        pick = rng.choice(syms, len(syms))
        rb = sum(rg.get(s, (0, 0))[0] for s in pick); rk = sum(rg.get(s, (0, 0))[1] for s in pick)
        cb = sum(cg.get(s, (0, 0))[0] for s in pick); ck = sum(cg.get(s, (0, 0))[1] for s in pick)
        if rb + rk and cb + ck:
            out.append(rb / (rb + rk) - cb / (cb + ck))
    return float(np.percentile(out, 2.5)) * 100, float(np.percentile(out, 97.5)) * 100


def run(name: str, data: dict[str, list[dict]]) -> dict:
    t0 = time.time()
    with ProcessPoolExecutor() as ex:
        parts = list(ex.map(eval_symbol, data.items(), chunksize=1))
    lines = pd.DataFrame([r for p in parts for r in p])
    real, ctrl = dedupe(lines), dedupe(controls(lines, data))
    (rb, rk), (cb, ck) = respect(real), respect(ctrl)
    rr, cr = (rb / (rb + rk) if rb + rk else np.nan), (cb / (cb + ck) if cb + ck else np.nan)
    lo, hi = boot_diff(real, ctrl)
    retested = lambda ev: (ev.outcome.isin(["bounce", "break", "unresolved"])).mean() * 100
    rets = {h: (real[real.touch.notna()][f"ret{h}"].mean(), ctrl[ctrl.touch.notna()][f"ret{h}"].mean()) for h in RET_H}
    res = {"name": name, "symbols": len(data), "span": f"{min(c[0]['time'] for c in data.values())} ~ {max(c[-1]['time'] for c in data.values())}",
           "breakouts": (len(real), len(ctrl)), "retest_pct": (retested(real), retested(ctrl)),
           "real": (rb, rk, rr), "ctrl": (cb, ck, cr), "diff": (rr - cr) * 100, "p": b.two_prop_p(rb, rb + rk, cb, cb + ck),
           "ci": (lo, hi), "rets": rets, "secs": time.time() - t0}
    print(f"[{name}] {res['symbols']}종목 돌파 {len(real)} · 자동 {rr:.1%} ({rb}/{rb + rk}) vs 대조 {cr:.1%} ({cb}/{cb + ck}) "
          f"차이 {res['diff']:+.1f}%p p={res['p']:.3g} ({res['secs']:.0f}초)", flush=True)
    return res


def verdict(res: list[dict]) -> str:
    ok = [r["diff"] > 0 and r["p"] < 0.05 for r in res if r["name"].startswith("확증")]
    return "근거 있음" if all(ok) else "약한 근거" if any(ok) else "근거 없음"


def main() -> None:
    res = [run(name, data) for name, data in sets().items()]
    pct = lambda x: "-" if x != x else f"{x * 100:.1f}%"
    lines = ["# 깨진 저항 추세선이 지지로 바뀌나 (역할 전환 · 리테스트)\n",
             "- 방법·판정 기준은 research/retest.py 맨 위 (돌리기 전에 정함). 탐색은 보기만, 판정은 확증 두 묶음으로",
             "- 지켜진 비율 = 다시 닿은 뒤 20봉 안에 1 ATR 튕김 / (튕김 + 종가 0.5 ATR 아래로 깨짐). 대조군 = 거리만 섞은 근거 없는 선\n",
             "| 묶음 | 종목 · 기간 | 돌파 (자동/대조) | 40봉 안에 다시 닿음 | 자동 선 지켜짐 | 대조군 지켜짐 | 차이 (95% 구간) | p |",
             "|---|---|---|---|---|---|---|---|"]
    for r in res:
        lines.append(f"| {r['name']} | {r['symbols']} · {r['span']} | {r['breakouts'][0]} / {r['breakouts'][1]} | "
                     f"{r['retest_pct'][0]:.0f}% / {r['retest_pct'][1]:.0f}% | {pct(r['real'][2])} ({r['real'][0]}/{r['real'][0] + r['real'][1]}) | "
                     f"{pct(r['ctrl'][2])} ({r['ctrl'][0]}/{r['ctrl'][0] + r['ctrl'][1]}) | {r['diff']:+.1f}%p ({r['ci'][0]:+.1f} ~ {r['ci'][1]:+.1f}) | {r['p']:.3g} |")
    lines += ["\n## 보조: 다시 닿은 봉 종가에서 몇 봉 뒤 수익률 (자동 / 대조)\n", "| 묶음 | 10봉 | 20봉 |", "|---|---|---|"]
    for r in res:
        lines.append(f"| {r['name']} | " + " | ".join(f"{r['rets'][h][0]:+.2f}% / {r['rets'][h][1]:+.2f}%" for h in RET_H) + " |")
    lines.append(f"\n**판정 (미리 정한 기준): {verdict(res)}**")
    out = b.OUT / "retest.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
