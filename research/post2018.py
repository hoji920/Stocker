"""F2 를 2018년 이후 기간에서 확인한다 (F2 를 한 번도 돌려보지 않은 기간).

F2 = 200일선 위일 때 내 규칙(D) 자리가 오면 사서, 목표·손절 없이 종가가 200일선 아래로 내려가면 판다.
2017년 말까지 검증(pre2018.py)의 확인 반에서 미리 정한 기준(D 보다 거래 초과수익이 높고 샤프 차이가 나음)을 통과했다.
이 기간은 예전에 규칙 후보(rule_variants)를 고를 때 본 기간이지만, F2 자체는 여기서 돌려본 적이 없다.

미리 정한 것 (2026-10-16, 돌리기 전)
- 기간: 2018-01-01 ~ 지금. 선·200일선 계산용으로 그 전 400봉을 같이 넣고, 매매는 2018-01-01 부터만 한다
- 규칙 숫자·선 계산은 pre2018 과 같다 (지금 '내 규칙' 숫자, 직선 선, 튀는 꼬리 무시는 끄고)
- 방식: D(지금 규칙) · F2 · 단순 추세(200일선) · 그냥 보유. 지표는 pre2018 과 같다
- 종목: ① S&P 500 (2026-10-12 구성) 중 2018년 뒤 일봉이 1,000개 이상인 것 ← 주 판정
        ② ① 중 2018년 전에 이미 지수에 있던 것 (생존 편향이 덜한 쪽)
        ③ 중형·고변동성 후보 (참고) ④ 내 종목 (참고)
- 판정 (①에서):
    'F2 가 지금 규칙보다 낫다' = F2 의 거래 초과수익과 샤프 차이(보유 대비)가 둘 다 D 보다 크다 (pre2018 과 같은 기준)
    'F2 가 그냥 보유보다 낫다' = 샤프 차이의 95% 구간이 0 위
    'F2 가 단순 추세보다 낫다' = 샤프(F2) − 샤프(추세) 의 95% 구간이 0 위 (선으로 고른 진입이 보탬이 되나)
- 결과가 어떻든 F2 나 기간·종목을 바꿔 다시 돌리지 않는다

한계: 지금 살아남은 S&P 500 종목만 (그냥 보유가 실제보다 좋게 나온다). 수수료·세금 미반영, 종가에 사고판다고 가정.

실행: python -m research.post2018      결과: research/results/post2018.md
"""
from __future__ import annotations
import json
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from backend import analysis, rules
from research import backtest_lines as b
from research import midcap
from research import pre2018 as p
from research.my_rules_backtest import MINE
from research.rule_variants import sharpe_diff_ci

START = "2018-01-01"
MIN_BARS = 1000
CACHE = b.DATA / "post2018"
VARIANTS = ["D", "F2", "추세"]


def window(candles: list[dict]) -> list[dict] | None:
    """2018-01-01 전 400봉(선·200일선 계산용) + 그 뒤 전부. 400봉이 안 되면 None (매매 시작이 2018년보다 늦어진다)."""
    first = next((i for i, c in enumerate(candles) if c["time"] >= START), None)
    if first is None or first < rules.LOOKBACK or len(candles) - first < MIN_BARS:
        return None
    return candles[first - rules.LOOKBACK:]


def main() -> None:
    from backend import main as app
    analysis.LOG_TRENDS = False
    analysis.DESPIKE = False
    my = app.my_rules()
    sp = json.loads((b.ROOT / "research" / "sp500.json").read_text())
    want = sorted(set(sp["symbols"]) | set(midcap.CANDIDATES) | set(MINE))
    t0 = time.time()
    raw = p.fetch_full(want)
    print(f"[data] {len(raw)}/{len(want)}종목 ({time.time() - t0:.0f}초)", flush=True)
    data = {s: w for s, cs in raw.items() if (w := window(cs))}
    g1 = sorted(s for s in sp["symbols"] if s in data)
    g2 = [s for s in g1 if sp["date_added"].get(s, "9") < START]
    g3 = sorted(s for s in midcap.CANDIDATES if s in data)
    g4 = sorted(s for s in MINE if s in data)
    print(f"[universe] ① {len(g1)} ② {len(g2)} ③ {len(g3)} ④ {len(g4)}", flush=True)

    need = sorted(set(g1) | set(g3) | set(g4))
    t0, sig = time.time(), {}
    with ProcessPoolExecutor() as ex:
        for k, r in enumerate(ex.map(p.signals, [(s, data[s], my, CACHE) for s in need], chunksize=1), 1):
            sig[r["symbol"]] = r
            if k % 50 == 0:
                print(f"[signals] {k}/{len(need)} ({time.time() - t0:.0f}초)", flush=True)

    rng = np.random.default_rng(0)
    res = p.run_variants(data, sig, need, VARIANTS)
    span = lambda syms: f"{min(data[s][rules.LOOKBACK]['time'] for s in syms)} ~ {max(data[s][-1]['time'] for s in syms)}"
    out = ["# F2 를 2018년 이후에서 확인\n",
           f"- 매매 기간: 2018-01-01 ~ 지금 (그 전 400봉은 선 계산용) · 규칙 숫자: "
           + ", ".join(f"{k} {v}" for k, v in my.items() if k != "note"),
           "- D = 지금 규칙 / F2 = 200일선 위 + 내 규칙 자리에서 사고 200일선 이탈 때 팖 / 단순 추세 = 200일선 위면 보유",
           "- 지표는 pre2018.md 와 같다. 'F2 − 추세' = 두 방식 샤프의 차이 (95% 구간은 달 묶음 부트스트랩)",
           "- 지금 살아남은 종목만이라 그냥 보유가 실제보다 좋게 나온다. 수수료·세금 미반영\n"]
    for title, syms in [("① S&P 500 (지금 구성)", g1), ("② S&P 500 중 2018년 전에 이미 지수에 있던 종목", g2),
                        ("③ 중형·고변동성 후보 (참고)", g3), ("④ 내 종목 (참고)", g4)]:
        syms = [s for s in syms if s in res]
        if not syms:
            continue
        out += [f"## {title} ({len(syms)}종목, {span(syms)})\n", *p.table(res, syms, VARIANTS, rng)]
        port = {v: pd.DataFrame({s: pd.Series(res[s]["returns"][v], index=res[s]["dates"]) for s in syms}).sort_index().mean(axis=1)
                for v in ("F2", "추세")}
        sh = lambda r: r.mean() / r.std() * np.sqrt(252) if r.std() else 0.0
        lo, hi = sharpe_diff_ci(port["F2"], port["추세"], rng)
        out += ["", f"- F2 − 단순 추세 샤프: {sh(port['F2']) - sh(port['추세']):+.2f} (95% 구간 {lo:+.2f} ~ {hi:+.2f})", ""]
    (b.OUT / "post2018.md").write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out), flush=True)


if __name__ == "__main__":
    main()
