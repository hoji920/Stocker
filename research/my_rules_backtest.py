"""내 매매 규칙(지지 근처·채널 하단에서 매수, 저항이 목표, 지지 아래 손절)을 여러 종목의 과거에 그대로 적용.

앱의 '과거 대조'(backend.rules.backtest)와 같은 계산을 종목 묶음 전체에 돌린다 (미래 정보 없음).
- 규칙 값: 지금 앱에 저장된 내 규칙 (data/settings.json). 없으면 기본값
- 기간: 받아둔 일봉 전체 (약 7~8년)
- 초과수익 = 거래 수익 − '그 종목을 아무 날 사서 같은 봉 수만큼 들고 있었을 때'의 평균 수익
  (원래 많이 오른 종목·오래 들고 있어서 생긴 수익을 뺀다. 0 이면 규칙으로 고른 날이 아무 날과 같다는 뜻)
- 95% 구간·p: 매수한 달 단위 묶음 부트스트랩 2000회
- 수수료·세금·호가 차이는 안 뺐다 (빼면 더 불리하다)

종목 묶음: 내 종목(보유·관심) / 미국 중형·고변동성 100 (midcap.py 의 종목군) / 미국 대형주 68

실행: python -m research.my_rules_backtest   (research/data 캐시를 읽는다. 내 종목 중 없는 것만 토스에서 받는다)
결과: research/results/my_rules.md
"""
from __future__ import annotations
import json
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from backend import rules
from research import backtest_lines as b
from research import confirm, methods as m, midcap, near_high

MINE = ["MSTR", "BE", "INTC", "BITX", "AAPL", "TSLA", "INTU", "IREN", "SNOW"]      # 보유·관심 종목 (2026-10-12)
fmt = lambda p: f"${p:,.2f}"


def run_symbol(args) -> list[dict]:
    symbol, candles, my = args
    if len(candles) < 500:
        return []
    n = len(candles)
    r = rules.backtest(candles, my, fmt, symbol, symbol, bars=n, keep=None)
    close = np.array([c["close"] for c in candles])
    start = min(rules.LOOKBACK, n // 2)                  # rules.backtest 가 대조를 시작하는 봉 (bars=n 일 때)
    base: dict[int, float] = {}

    def any_day(h: int) -> float:                       # 이 종목을 아무 날 사서 h봉 들고 있었을 때 평균 수익 (%)
        if h not in base:
            base[h] = float(((close[start + h:] / close[start: n - h]) - 1).mean() * 100) if n - start > h else 0.0
        return base[h]
    return [{"symbol": symbol, "month": t["buy_date"][:7], "ret": t["return_pct"], "bars": t["bars"], "exit": t["exit"],
             "excess": t["return_pct"] - any_day(max(t["bars"], 1)),
             "total": r["total_return_pct"], "hold": r["buy_and_hold_pct"], "in_market": r["time_in_market_pct"]}
            for t in r["recent_trades"]]


def boot_mean(df: pd.DataFrame, col: str, rng) -> tuple[float, float, float, float]:
    g = df.groupby("month")[col].agg(["sum", "count"])
    s, n = g["sum"].to_numpy(), g["count"].to_numpy()
    idx = rng.integers(0, len(g), (m.N_BOOT, len(g)))
    means = s[idx].sum(1) / np.maximum(n[idx].sum(1), 1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(df[col].mean()), float(lo), float(hi), m._boot_p(means)


def main() -> None:
    from backend import main as app
    my = app.my_rules()
    mid_data = midcap.fetch(midcap.CANDIDATES)
    explore, conf, _ = midcap.universe(mid_data)
    data = {**mid_data, **midcap.fetch(MINE + b.US + confirm.HOLDOUT)}
    sets = [("내 종목 (보유·관심)", [s for s in MINE if s in data]),
            ("미국 중형·고변동성 100", explore + conf),
            ("미국 대형주 68", [s for s in b.US + confirm.HOLDOUT if s not in near_high.ETFS and s in data])]
    need = sorted({s for _, syms in sets for s in syms})
    with ProcessPoolExecutor() as ex:
        trades = pd.DataFrame([t for part in ex.map(run_symbol, [(s, data[s], my) for s in need]) for t in part])
    trades.to_csv(b.OUT / "my_rules_trades.csv", index=False)

    rng = np.random.default_rng(0)
    out = ["# 내 매매 규칙을 과거에 적용했다면\n",
           f"- 규칙: 지지선에서 ATR {my['buy_support_atr']:g}배 이내 또는 채널 아래 {my['buy_channel_pct']:g}% 이내에서 매수, "
           f"목표 = 가까운 저항선(없으면 채널 상단), 손절 = 지지선 아래 ATR {my['stop_atr']:g}배(종가 기준), 최소 손익비 {my['min_rr']:g}",
           f"- 목표·손절이 안 닿으면 {rules.MAX_HOLD}봉 뒤 정리. 한 종목에 한 번에 한 건. 받아둔 일봉 전체(약 7~8년)",
           "- **초과수익** = 거래 수익 − 그 종목을 아무 날 사서 같은 봉 수만큼 들고 있었을 때의 평균. 0 이면 규칙으로 고른 날이 아무 날과 같다",
           "- 95% 구간·p: 매수한 달 단위 묶음 부트스트랩 2000회. 수수료·세금·호가 차이는 안 뺐다\n",
           "| 종목 묶음 | 종목 | 거래 | 승률 | 한 번 평균 수익 | 평균 보유 | 초과수익 | 95% 구간 | p | 목표/손절/만료 | 규칙 누적 > 그냥 보유인 종목 |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, syms in sets:
        g = trades[trades.symbol.isin(syms)]
        if g.empty:
            continue
        x, lo, hi, p = boot_mean(g, "excess", rng)
        per = g.drop_duplicates("symbol")
        ex = g.exit.value_counts()
        out.append(f"| {name} | {g.symbol.nunique()} | {len(g):,} | {(g.ret > 0).mean() * 100:.1f}% | {g.ret.mean():+.2f}% | {g.bars.mean():.1f}봉 | "
                   f"{x:+.2f}%p | {lo:+.2f} ~ {hi:+.2f} | {p:.3g} | {ex.get('목표', 0):,}/{ex.get('손절', 0):,}/{ex.get('기간 만료', 0) + ex.get('보유 중', 0):,} | "
                   f"{int((per.total > per.hold).sum())}/{len(per)} (중앙값 {per.total.median():+.0f}% vs {per.hold.median():+.0f}%) |")
    mine = trades[trades.symbol.isin(sets[0][1])]
    out += ["\n## 내 종목별\n", "| 종목 | 거래 | 승률 | 한 번 평균 수익 | 초과수익 | 규칙 누적 | 그냥 보유 | 시장에 있던 비율 |", "|---|---|---|---|---|---|---|---|"]
    for s, g in mine.groupby("symbol"):
        out.append(f"| {s} | {len(g)} | {(g.ret > 0).mean() * 100:.0f}% | {g.ret.mean():+.2f}% | {g.excess.mean():+.2f}%p | "
                   f"{g.total.iloc[0]:+.0f}% | {g.hold.iloc[0]:+.0f}% | {g.in_market.iloc[0]:.0f}% |")
    (b.OUT / "my_rules.md").write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
