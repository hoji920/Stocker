"""52주 최고가에 가까운 종목이 그 뒤에 더 오르나 (George & Hwang 2004 의 '52주 고가 모멘텀'을 우리 종목으로).

methods.py 는 52주 고가를 '선'으로 보고 닿으면 튕기는지를 봤다 (대조군과 차이 없음).
여기서는 질문이 다르다: 선에 닿는 순간이 아니라 '지금 52주 최고가에서 얼마나 가까운가'가 이후 수익률을 가르나.

방법 (미래 정보 없음)
- 근접도 = 종가 ÷ 최근 252봉 최고가 (오늘 포함, 1 이면 신고가)
- 5봉마다, 그날 근접도로 종목을 나눈다:  가까움 ≥ 0.95 / 중간 / 멂 < 0.80   ← 돌리기 전에 정한 기준
- 이후 20봉·60봉 수익률에서 '같은 날 같은 시장 종목들의 평균'을 뺀 초과수익 (시장 전체가 오른 날의 효과 제거)
- 가설(미리 정함): 가까움의 초과수익 > 멂의 초과수익.  20봉·60봉 두 개 → Holm 보정 2개 기준
- 유의성: 월 단위 묶음 부트스트랩 2000회 (methods.py 와 같은 방식)
- 순위 상관(IC): 날짜별로 근접도 순위와 이후 수익률 순위의 스피어만 상관, 그 평균
- 탐색 종목(backtest_lines 의 미국·국내)과 확증 종목(confirm.HOLDOUT)을 따로 본다

한계: 지금 살아남은 대형주만 있다(상장폐지 종목 없음). 종목 수가 적어 날짜별 '멂' 묶음이 비는 날이 많다.

실행: python -m research.near_high   (research/data 캐시만 읽는다, 토스 호출 없음)
결과: research/results/near_high.md
"""
from __future__ import annotations
import json

import numpy as np
import pandas as pd

from research import backtest_lines as b
from research import methods as m
from research.confirm import HOLDOUT

ETFS = {"SPY", "QQQ"}
NEAR, FAR = 0.95, 0.80
HORIZONS = (20, 60)
STEP = 5


def panel(symbols: list[str]) -> pd.DataFrame:
    """종목×날짜: 근접도와 이후 수익률. 초과수익은 같은 날 이 묶음 종목들의 평균을 뺀 값."""
    rows = []
    for s in symbols:
        path = b.DATA / f"{s}.json"
        if not path.exists():
            continue
        df = pd.DataFrame(json.loads(path.read_text()))
        out = pd.DataFrame({"symbol": s, "date": df["time"],
                            "near": df["close"] / df["high"].rolling(252, min_periods=252).max()})
        for h in HORIZONS:
            out[f"fwd{h}"] = (df["close"].shift(-h) / df["close"] - 1) * 100
        rows.append(out.dropna(subset=["near"]))
    p = pd.concat(rows, ignore_index=True)
    dates = sorted(p["date"].unique())[::STEP]                # 5봉마다 (겹침 줄이기, 나머지는 월 묶음이 처리)
    p = p[p["date"].isin(set(dates))].copy()
    for h in HORIZONS:
        p[f"ex{h}"] = p[f"fwd{h}"] - p.groupby("date")[f"fwd{h}"].transform("mean")
    p["bucket"] = np.where(p["near"] >= NEAR, "near", np.where(p["near"] < FAR, "far", "mid"))
    p["month"] = p["date"].str[:7]
    return p


def boot_diff(p: pd.DataFrame, col: str, rng: np.random.Generator) -> dict:
    """가까움 − 멂 평균 초과수익, 월 묶음 부트스트랩."""
    d = p.dropna(subset=[col])
    months = sorted(d["month"].unique())
    agg = lambda x: x.groupby("month")[col].agg(["sum", "count"]).reindex(months, fill_value=0)
    a, f = agg(d[d.bucket == "near"]), agg(d[d.bucket == "far"])
    as_, an, fs, fn = a["sum"].to_numpy(), a["count"].to_numpy(), f["sum"].to_numpy(), f["count"].to_numpy()
    idx = rng.integers(0, len(months), (m.N_BOOT, len(months)))
    diffs = as_[idx].sum(1) / np.maximum(an[idx].sum(1), 1) - fs[idx].sum(1) / np.maximum(fn[idx].sum(1), 1)
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    means = d.groupby("bucket")[col].mean()
    return {"near": means.get("near", np.nan), "mid": means.get("mid", np.nan), "far": means.get("far", np.nan),
            "n_near": int(an.sum()), "n_far": int(fn.sum()), "diff": as_.sum() / an.sum() - fs.sum() / fn.sum(),
            "lo": lo, "hi": hi, "p": m._boot_p(diffs)}


def rank_ic(p: pd.DataFrame, h: int, rng: np.random.Generator) -> tuple[float, float, float, int]:
    """날짜별 스피어만 상관(근접도 vs 이후 수익률)의 평균과 월 묶음 부트스트랩 95% 구간."""
    d = p.dropna(subset=[f"fwd{h}"])
    ic = d.groupby("date").apply(lambda x: x["near"].rank().corr(x[f"fwd{h}"].rank()) if len(x) >= 8 else np.nan).dropna()
    by_month = ic.groupby(ic.index.str[:7]).agg(["sum", "count"])
    s, n = by_month["sum"].to_numpy(), by_month["count"].to_numpy()
    idx = rng.integers(0, len(s), (m.N_BOOT, len(s)))
    boots = s[idx].sum(1) / n[idx].sum(1)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return float(ic.mean()), lo, hi, len(ic)


def main() -> None:
    sets = [("탐색: 미국 28종목", [s for s in b.US if s not in ETFS]), ("탐색: 국내 10종목", b.KR), ("확증: 미국 40종목", HOLDOUT)]
    rng = np.random.default_rng(0)
    out = ["# 52주 최고가 근접도와 이후 수익률\n",
           f"- 근접도 = 종가 ÷ 최근 252봉 최고가. 가까움 ≥ {NEAR}, 멂 < {FAR} (돌리기 전에 정한 기준)",
           "- 초과수익 = 이후 수익률 − 같은 날 같은 묶음 종목들의 평균. 5봉마다 평가, 약 8년 일봉",
           "- 95% 구간·p: 월 단위 묶음 부트스트랩 2000회. Holm p 는 묶음 안의 20봉·60봉 2개 기준",
           "- IC: 날짜별 근접도 순위와 이후 수익률 순위의 상관, 그 평균 (0 이면 관계 없음)\n",
           "| 종목 묶음 | 기간 | 가까움 | 중간 | 멂 | 가까움−멂 | 95% 구간 | Holm p | 표본(가까움/멂) | IC | IC 95% 구간 |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, symbols in sets:
        p = panel(symbols)
        res = [boot_diff(p, f"ex{h}", rng) for h in HORIZONS]
        adj = m.holm([r["p"] for r in res])
        for h, r, pa in zip(HORIZONS, res, adj):
            ic, ilo, ihi, _ = rank_ic(p, h, rng)
            out.append(f"| {name} | {h}봉 | {r['near']:+.2f}% | {r['mid']:+.2f}% | {r['far']:+.2f}% | {r['diff']:+.2f}%p | "
                       f"{r['lo']:+.2f} ~ {r['hi']:+.2f} | {pa:.3g}{' ✓' if pa < 0.05 else ''} | {r['n_near']:,}/{r['n_far']:,} | "
                       f"{ic:+.3f} | {ilo:+.3f} ~ {ihi:+.3f} |")
    (b.OUT / "near_high.md").write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
