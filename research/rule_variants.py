"""내 규칙을 어떻게 고치면 나아지나: 후보 5개 + 단순 추세 규칙을 같은 틀로 비교 (돌리기 전에 정한 것만).

지금 규칙이 지는 이유로 본 것: ① 가까운 저항선에서 팔아 이익을 일찍 끊는다 ② 꺾인 종목의 '지지 근처'도 산다 ③ 시장에 절반만 있다.

  기준   예전 규칙: 지지 근처·채널 하단 + 손익비 ≥ 1.5 에서 매수, 저항선이 목표, 지지선 아래 ATR 0.5배 손절, 60봉 뒤 정리
         (손익비의 위험 = 손절까지 거리 그대로. 앱의 지금 기본값은 D)
  A      기준 + 추세 필터: 종가가 200일선 위일 때만 매수
  B      기준 진입 그대로, 목표가 없음: 손절선을 따라 올린다 (매수 뒤 최고 종가 − ATR 3배, 처음 손절선보다 내려가지 않음). 보유 기한 없음
  C      A + B
  D      기준 + 위험 하한: 손익비를 셀 때 위험을 최소 ATR 0.5배로 잡는다 (손절 바로 위에서 손익비가 부풀지 않게). 앱의 지금 기본값
  E      기준 + 손절까지 ATR 0.5배 미만이면 아예 사지 않는다

  D·E 는 2026-10-12 에 추가했다. 계기: 엔비디아·테슬라·애플 3년 144건을 보니 손절까지 ATR 0.25배 미만이던 거래의
  표시 손익비 중앙값이 11 인데 79% 가 손절로 끝났다. 이 세 종목은 '대형주' 묶음에 들어 있으니 그 묶음의 D·E 결과는
  완전히 새 자료가 아니다. 중형·고변동성 두 묶음은 D·E 를 정할 때 보지 않았다.
  추세   선 없이: 종가가 200일선 위면 보유, 아래로 내려가면 매도   ← 비교 기준. 후보가 이보다 못하면 복잡한 규칙을 쓸 이유가 없다
  보유   그냥 들고 있기

판단 (미리 정함)
- 거래 초과수익: 거래 수익 − 그 종목을 아무 날 사서 같은 봉 수만큼 들고 있었을 때의 평균 (매수한 달 묶음 부트스트랩)
- 포트폴리오: 종목들을 같은 비중으로 (안 들고 있는 종목은 현금 0%) → 연 수익, 최대 낙폭, 샤프(위험 대비 수익)
  샤프 차이(규칙 − 그냥 보유)의 95% 구간은 달 단위 묶음 부트스트랩
- 탐색 50종목에서 보고, 확증 50종목·대형주 66종목에서 같은 방향인지 확인
- S&P 500 전 종목(2026-10-12 위키백과 구성 종목)도 본다. 지금 지수에 들어 있는 종목만이라 '그냥 보유'가 실제보다 좋게 나온다:
  망해서 빠진 종목은 없고, 많이 올라서 새로 들어온 종목은 있다. 그래서 2019년 전부터 들어 있던 종목만도 따로 본다
  (그래도 그 뒤에 빠진 종목은 없으니 편향이 다 없어지지는 않는다)

한계: 지금 살아남은 종목만 있어 '그냥 보유'가 실제보다 좋게 나온다. 수수료·세금·호가 차이는 안 뺐다. 종가에 사고판다고 가정.

실행: python -m research.rule_variants     결과: research/results/rule_variants.md
"""
from __future__ import annotations
import hashlib
import json
import pickle
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from backend import analysis, rules
from research import backtest_lines as b
from research import confirm, methods as m, midcap, near_high
from research.my_rules_backtest import MINE, fmt

VARIANTS = ["기준", "A", "B", "C", "D", "E", "추세"]
NAMES = {"기준": "예전 규칙 (위험 하한 없음)", "A": "A 추세 필터", "B": "B 따라 올리는 손절", "C": "C 필터 + 따라 올리는 손절",
         "D": "D 위험 하한 (지금 기본값)", "E": "E 손절 가까우면 안 삼",
         "추세": "단순 추세 (200일선)", "보유": "그냥 보유"}
FIXED_EXIT = ("기준", "A", "D", "E")      # 매수 때 정한 목표·손절·60봉 그대로 / 나머지(B, C)는 손절선을 따라 올린다
TRAIL_ATR, SMA, MIN_RISK_ATR = 3.0, 200, 0.5


CACHE = b.DATA / "variants"
CACHE_VERSION = 2          # 계산 방식이 바뀌면 올린다 (예전 결과를 다시 쓰지 않게)


def run_symbol(args) -> dict | None:
    """한 종목 계산은 1분쯤 걸린다 → 결과를 종목별로 저장해 두고, 같은 일봉·같은 규칙이면 다시 계산하지 않는다."""
    symbol, candles, my = args
    if len(candles) < 500:
        return None
    key = hashlib.md5(json.dumps([CACHE_VERSION, len(candles), candles[-1], my, TRAIL_ATR, SMA, MIN_RISK_ATR],
                                 sort_keys=True).encode()).hexdigest()
    path = CACHE / f"{symbol.replace('/', '_')}.pkl"
    if path.exists():
        try:
            saved = pickle.loads(path.read_bytes())
            if saved.get("key") == key:
                return saved["result"]
        except Exception:
            pass
    result = _compute(symbol, candles, my)
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pickle.dumps({"key": key, "result": result}))
    return result


def _compute(symbol: str, candles: list[dict], my: dict) -> dict:
    n = len(candles)
    df = analysis.to_frame(candles)
    close, high = df["close"].to_numpy(), df["high"].to_numpy()
    atr = analysis.atr(df).to_numpy()
    sma = df["close"].rolling(SMA).mean().to_numpy()
    start = min(rules.LOOKBACK, n // 2)
    # 봉마다 '지금 규칙'의 판정을 한 번만 계산 (그날까지의 데이터로) → 후보들은 이걸 같이 쓴다
    sig: dict[int, tuple[float, float]] = {}          # 기준·A·B·C 가 쓰는 매수 신호 (위험 하한 없음)
    sig_d: dict[int, tuple[float, float]] = {}        # D: 손익비의 위험을 최소 ATR 0.5배로
    sig_e: dict[int, tuple[float, float]] = {}        # E: 기준 신호 중 손절까지 ATR 0.5배 이상인 것만
    old, floored = {**my, "min_risk_atr": 0}, {**my, "min_risk_atr": MIN_RISK_ATR}
    for i in range(start, n - 1):
        sub = candles[max(0, i - rules.LOOKBACK + 1): i + 1]
        chart = {"symbol": symbol, "name": symbol, "candles": sub, **analysis.lines_only(sub)}
        r = rules.evaluate(chart, old, fmt)
        if r["tone"] == "buy":
            sig[i] = (r["target"]["price"], r["stop"]["price"])
            if r["price"] - r["stop"]["price"] >= MIN_RISK_ATR * r["atr"]:
                sig_e[i] = sig[i]
        r = rules.evaluate(chart, floored, fmt)
        if r["tone"] == "buy":
            sig_d[i] = (r["target"]["price"], r["stop"]["price"])

    def simulate(variant: str) -> tuple[np.ndarray, list[dict]]:
        daily, trades, i = np.zeros(n), [], start
        trend_ok = lambda k: not np.isnan(sma[k]) and close[k] > sma[k]
        while i < n - 1:
            src = {"D": sig_d, "E": sig_e}.get(variant, sig)
            enter = trend_ok(i) if variant == "추세" else (i in src and (variant not in ("A", "C") or trend_ok(i)))
            if not enter:
                i += 1
                continue
            target, stop = src.get(i, (None, None))
            peak, exit_i, exit_price = close[i], n - 1, close[n - 1]
            last = min(i + rules.MAX_HOLD, n - 1) if variant in FIXED_EXIT else n - 1
            for j in range(i + 1, last + 1):
                if variant == "추세":
                    if not trend_ok(j):
                        exit_i, exit_price = j, close[j]
                        break
                elif variant in FIXED_EXIT:
                    if close[j] <= stop:
                        exit_i, exit_price = j, close[j]
                        break
                    if high[j] >= target:
                        exit_i, exit_price = j, target
                        break
                else:                                              # B, C: 손절선을 따라 올린다
                    if close[j] <= stop:
                        exit_i, exit_price = j, close[j]
                        break
                    peak = max(peak, close[j])
                    stop = max(stop, peak - TRAIL_ATR * atr[j])
                exit_i, exit_price = j, close[j]
            for j in range(i + 1, exit_i + 1):
                daily[j] = (exit_price if j == exit_i else close[j]) / close[j - 1] - 1
            trades.append({"symbol": symbol, "month": candles[i]["time"][:7], "bars": exit_i - i, "ret": (exit_price / close[i] - 1) * 100})
            i = exit_i + 1
        return daily, trades

    base: dict[int, float] = {}

    def any_day(h: int) -> float:
        if h not in base:
            base[h] = float(((close[start + h:] / close[start: n - h]) - 1).mean() * 100) if n - start > h else 0.0
        return base[h]

    out = {"symbol": symbol, "dates": df["time"].tolist()[start:], "returns": {}, "trades": {}}
    hold = np.zeros(n)
    hold[start + 1:] = close[start + 1:] / close[start: n - 1] - 1
    out["returns"]["보유"] = hold[start:]
    for v in VARIANTS:
        daily, trades = simulate(v)
        for t in trades:
            t["excess"] = t["ret"] - any_day(max(t["bars"], 1))
        out["returns"][v], out["trades"][v] = daily[start:], trades
    return out


def port_stats(r: np.ndarray) -> tuple[float, float, float]:
    """(연 수익 %, 최대 낙폭 %, 샤프)."""
    eq = np.cumprod(1 + r)
    cagr = (eq[-1] ** (252 / len(r)) - 1) * 100
    mdd = float(((eq / np.maximum.accumulate(eq)) - 1).min() * 100)
    return float(cagr), mdd, float(r.mean() / r.std() * np.sqrt(252)) if r.std() else 0.0


def sharpe_diff_ci(a: pd.Series, h: pd.Series, rng) -> tuple[float, float]:
    """샤프(규칙) − 샤프(그냥 보유) 의 95% 구간. 달을 통째로 다시 뽑는다."""
    months = a.index.str[:7]
    groups = [np.flatnonzero(months == mth) for mth in pd.unique(months)]
    av, hv = a.to_numpy(), h.to_numpy()
    sh = lambda x: x.mean() / x.std() * np.sqrt(252) if x.std() else 0.0
    diffs = []
    for _ in range(1000):
        idx = np.concatenate([groups[k] for k in rng.integers(0, len(groups), len(groups))])
        diffs.append(sh(av[idx]) - sh(hv[idx]))
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return float(lo), float(hi)


def main() -> None:
    from backend import main as app
    my = app.my_rules()
    mid = midcap.fetch(midcap.CANDIDATES)
    explore, conf, _ = midcap.universe(mid)
    data = {**mid, **midcap.fetch(MINE + b.US + confirm.HOLDOUT)}
    mega = [s for s in b.US + confirm.HOLDOUT if s not in near_high.ETFS and s in data]
    sp = json.loads((b.ROOT / "research" / "sp500.json").read_text())
    data.update(midcap.fetch([s for s in sp["symbols"] if s not in data]))
    sp_all = [s for s in sp["symbols"] if s in data]
    sp_old = [s for s in sp_all if sp["date_added"].get(s, "9") < "2019"]
    print(f"[S&P 500] {len(sp['symbols'])}종목 중 일봉을 받은 것 {len(sp_all)}개 (못 받은 것: "
          f"{', '.join(s for s in sp['symbols'] if s not in data) or '없음'})")
    sets = [("탐색: 중형·고변동성 50", explore), ("확증: 중형·고변동성 50", conf), ("확증: 대형주 66", mega),
            ("내 종목 9", [s for s in MINE if s in data]),
            (f"S&P 500 전 종목 ({sp['fetched']} 구성)", sp_all), ("S&P 500 중 2019년 전부터 들어 있던 종목", sp_old)]
    need = sorted({s for _, syms in sets for s in syms})
    with ProcessPoolExecutor() as ex:
        res = {r["symbol"]: r for r in ex.map(run_symbol, [(s, data[s], my) for s in need]) if r}

    rng = np.random.default_rng(0)
    out = ["# 내 규칙 고치기: 후보 비교\n",
           "- 기준 = 예전 규칙(손익비의 위험 = 손절까지 거리 그대로) / A = 200일선 위일 때만 매수 / B = 목표가 없이 손절선을 따라 올림(최고 종가 − ATR 3배) / C = A+B",
           "- D = 손익비를 셀 때 위험을 최소 ATR 0.5배로 (앱의 지금 기본값) / E = 손절까지 ATR 0.5배 미만이면 사지 않음 / 단순 추세 = 200일선 위면 보유",
           "- D·E 는 엔비디아·테슬라·애플 3년치를 본 뒤 추가했다. 이 세 종목이 든 대형주 묶음의 D·E 는 완전히 새 자료가 아니다 (중형 두 묶음은 보지 않았다)",
           "- **거래 초과수익** = 거래 수익 − 그 종목을 아무 날 사서 같은 기간 들고 있었을 때의 평균 (0 이면 아무 날과 같다)",
           "- **포트폴리오** = 종목들을 같은 비중으로, 안 들고 있는 종목은 현금. 샤프 = 위험 대비 수익 (클수록 좋다). 샤프 차이 = 규칙 − 그냥 보유",
           "- 약 7~8년 일봉, 수수료·세금 미반영, 지금 살아남은 종목만 있어 '그냥 보유'가 실제보다 좋게 나온다\n"]
    for title, syms in sets:
        syms = [s for s in syms if s in res]
        out += [f"## {title} ({len(syms)}종목)\n",
                "| 방식 | 거래 | 승률 | 평균 보유 | 거래 초과수익 (95% 구간) | 시장에 있던 비율 | 연 수익 | 최대 낙폭 | 샤프 | 샤프 차이 (95% 구간) |",
                "|---|---|---|---|---|---|---|---|---|---|"]
        frames = {v: pd.DataFrame({s: pd.Series(res[s]["returns"][v], index=res[s]["dates"]) for s in syms}).sort_index()
                  for v in VARIANTS + ["보유"]}
        hold_port = frames["보유"].mean(axis=1)
        for v in VARIANTS + ["보유"]:
            port = frames[v].mean(axis=1)                    # 상장 전인 종목은 빼고 평균
            cagr, mdd, sharpe = port_stats(port.to_numpy())
            # 상장 전(NaN)인 날은 세지 않는다: NaN != 0 이 참이라, 늦게 상장한 종목이 있으면 100% 를 넘게 나왔다
            in_mkt = float(((frames[v] != 0) & frames[v].notna()).sum().sum() / frames[v].notna().sum().sum() * 100)
            if v == "보유":
                out.append(f"| {NAMES[v]} | - | - | - | - | 100% | {cagr:+.1f}% | {mdd:.1f}% | {sharpe:.2f} | - |")
                continue
            tr = pd.DataFrame([t for s in syms for t in res[s]["trades"][v]])
            x, lo, hi, _ = m_boot(tr, rng)
            dlo, dhi = sharpe_diff_ci(port, hold_port, rng)
            out.append(f"| {NAMES[v]} | {len(tr):,} | {(tr.ret > 0).mean() * 100:.0f}% | {tr.bars.mean():.0f}봉 | {x:+.2f}%p ({lo:+.2f} ~ {hi:+.2f}) | "
                       f"{in_mkt:.0f}% | {cagr:+.1f}% | {mdd:.1f}% | {sharpe:.2f} | {sharpe - port_stats(hold_port.to_numpy())[2]:+.2f} ({dlo:+.2f} ~ {dhi:+.2f}) |")
        out.append("")
    (b.OUT / "rule_variants.md").write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))


def m_boot(tr: pd.DataFrame, rng) -> tuple[float, float, float, float]:
    g = tr.groupby("month")["excess"].agg(["sum", "count"])
    s, n = g["sum"].to_numpy(), g["count"].to_numpy()
    idx = rng.integers(0, len(g), (m.N_BOOT, len(g)))
    means = s[idx].sum(1) / np.maximum(n[idx].sum(1), 1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(tr["excess"].mean()), float(lo), float(hi), m._boot_p(means)


if __name__ == "__main__":
    main()
