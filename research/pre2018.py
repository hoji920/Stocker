"""내 규칙을 '한 번도 안 본 기간'(2017년 말까지)에서 다시 본다 + 어디서 지는지 쪼개 보고 고칠 곳을 찾는다.

왜: 지금까지의 검증(rule_variants, midcap, near_high …)은 전부 토스 일봉 10페이지 = 약 2018년 10월 이후였다.
앱이 상장 이후 일봉을 다 받게 되면서, 그 전 기간은 규칙을 고르고 다듬을 때 한 번도 안 본 자료가 됐다.
같은 규칙을 숫자 하나 안 바꾸고 그 기간에 돌리면 깨끗한 사후 검증이 된다.

미리 정한 것 (2026-10-14, 돌리기 전)
- 기간: 상장일 ~ 2017-12-31 (그 뒤 봉은 버린다). 2018년 전 일봉이 1,000개(약 4년) 이상인 종목만
- 규칙: 앱의 지금 '내 규칙' 숫자 그대로 (결과 파일에 적는다). 선은 직선(로그 아님) — 이전 검증과 같게
- 후보: rule_variants 와 같은 7개 (기준·A·B·C·D·E·단순 추세) + 그냥 보유. 판정 지표도 같다
- 종목 묶음
    ① S&P 500 (2026-10-12 구성, research/sp500.json) 중 조건에 맞는 것        ← 주 판정
    ② ① 중 2018년 전에 이미 지수에 들어 있던 것                               ← 생존 편향이 덜한 쪽
    ③ 중형·고변동성 후보(midcap.CANDIDATES) 중 조건에 맞는 것                  ← 참고
    ④ 내 종목(MINE) 중 조건에 맞는 것                                           ← 참고
- 고칠 곳 찾기: ①을 알파벳순으로 번갈아 탐색 반 / 확인 반으로 나눈다
    탐색 반에서 지금 규칙(D)의 거래를 진입 이유·추세·청산 이유·손익비·손절 거리별로 쪼개 본다 (phase=explore)
    → 수정안을 최대 2개 골라 이 파일의 FIXES 에 적고(돌리기 전에), 확인 반에서 한 번만 돌린다 (phase=confirm)
    확인 반에서도 지금 규칙(D)보다 거래 초과수익이 높고 샤프 차이가 나아져야 '고치면 나아진다'고 쓴다

한계: 지금 살아남은 종목만 있다 (2000년대에 망한 종목은 없다) → '그냥 보유'가 실제보다 좋게 나온다.
      수수료·세금·호가 차이는 안 뺐다. 종가에 사고판다고 가정. 토스 일봉은 분할 조정된 가격.

실행: python -m research.pre2018 [explore|confirm]      결과: research/results/pre2018.md (+ pre2018_explore.md, pre2018_confirm.md)
"""
from __future__ import annotations
import hashlib
import json
import pickle
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

import numpy as np
import pandas as pd

from backend import analysis, rules
from research import backtest_lines as b
from research import midcap
from research.my_rules_backtest import MINE, fmt
from research.rule_variants import NAMES, VARIANTS, m_boot, port_stats, sharpe_diff_ci

END = "2018-01-01"                      # 이 날짜 '전' 봉만 쓴다
MIN_BARS = 1000
FULL = b.DATA / "full"                  # 상장 이후 전부 (60페이지)
CACHE = b.DATA / "pre2018"
CACHE_VERSION = 1
TRAIL_ATR, SMA, MIN_RISK_ATR = 3.0, 200, 0.5
FIXED_EXIT = ("기준", "A", "D", "E")

# 탐색 반을 본 뒤 여기에 적는다 (확인 반을 돌리기 전에). 형식: 이름 → (설명, 진입 조건 함수, 청산 방식)
# 2026-10-14 탐색 반(218종목, D 거래 77,586건)을 보고 정함:
# - 진입 쪽 어떤 묶음도 거래 초과수익이 0 보다 확실히 크지 않았다 (지지/채널/추세선/손익비/손절·목표 거리 전부 −0.27 ~ +0.12%p).
#   진입 조건을 더 조이는 수정은 잡음을 고르는 것이라 하나만, 방향이 가장 뚜렷했던 것만 고른다:
#   200일선 위에서 산 거래 −0.17%p (구간이 0 을 안 걸침) vs 아래 −0.06%p → F1 '200일선 아래(눌림)에서만 산다'
# - 이익은 청산 쪽에서만 났다: 단순 추세(200일선 이탈 때 판다)만 거래 초과수익이 + 였다.
#   → F2 '200일선 위에서 내 규칙 자리가 오면 사서, 목표·손절 없이 200일선 이탈 때 판다' (200일선 아래에서 사면 바로 다음 날 팔게 되므로 위에서만).
#   단순 추세(200일선 위로 올라오면 바로 삼)와 비교하면 '선으로 고른 진입 시점'이 보탬이 되는지 알 수 있다
FIXES: dict[str, tuple] = {
    "F1": ("F1 내 규칙 + 200일선 아래에서만 삼", lambda info, c, sma: not np.isnan(sma) and c < sma, "fixed"),
    "F2": ("F2 200일선 위 + 내 규칙 자리에서 사고 200일선 이탈 때 팖", lambda info, c, sma: not np.isnan(sma) and c > sma, "trend"),
}


# ── 데이터 ──
def fetch_full(symbols: list[str]) -> dict[str, list[dict]]:
    """토스 일봉 상장 이후 전부 (research/data/full 캐시, 7일). 4개씩 동시에 받는다."""
    FULL.mkdir(parents=True, exist_ok=True)
    from backend.config import get_settings
    from backend.toss_client import TossClient
    client = TossClient(get_settings())

    def one(s: str):
        path = FULL / f"{s.replace('/', '_')}.json"
        if path.exists() and time.time() - path.stat().st_mtime < 7 * 86400:
            return s, json.loads(path.read_text())
        try:
            c = client.daily_candles(s, 60)
        except Exception as error:
            print(f"[data] {s} 건너뜀: {type(error).__name__}", flush=True)
            return s, None
        path.write_text(json.dumps(c))
        return s, c

    with ThreadPoolExecutor(4) as ex:
        return {s: c for s, c in ex.map(one, symbols) if c}


# ── 봉마다 규칙 판정 (무겁다 → 종목별로 저장) ──
def signals(args) -> dict | None:
    symbol, candles, my, *rest = args                  # rest[0] = 저장 폴더 (post2018 이 따로 쓴다)
    cache = rest[0] if rest else CACHE
    analysis.LOG_TRENDS = False
    analysis.DESPIKE = False                            # 이전 검증과 같은 선 (튀는 꼬리 무시는 2026-10-15 에 앱에 넣었다)
    key = hashlib.md5(json.dumps([CACHE_VERSION, len(candles), candles[-1], my, MIN_RISK_ATR], sort_keys=True).encode()).hexdigest()
    path = cache / f"{symbol.replace('/', '_')}.pkl"
    if path.exists():
        try:
            saved = pickle.loads(path.read_bytes())
            if saved.get("key") == key:
                return saved["out"]
        except Exception:
            pass
    n = len(candles)
    start = min(rules.LOOKBACK, n // 2)
    old, floored = {**my, "min_risk_atr": 0}, {**my, "min_risk_atr": MIN_RISK_ATR}
    sig, sig_e, sig_d = {}, {}, {}
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
            ok = {c["label"]: c["ok"] for c in r["checks"]}
            sig_d[i] = {"target": r["target"]["price"], "stop": r["stop"]["price"], "rr": r["rr"],
                        "risk_atr": (r["price"] - r["stop"]["price"]) / r["atr"], "target_atr": (r["target"]["price"] - r["price"]) / r["atr"],
                        "support": bool(ok.get("지지 근처")), "channel": bool(ok.get("채널 하단")),
                        "sup_name": (r["support"] or {}).get("name"), "target_name": r["target"]["name"],
                        "ch_label": (r["channel"] or {}).get("label"), "ch_pos": (r["channel"] or {}).get("position_pct")}
    out = {"symbol": symbol, "start": start, "sig": sig, "sig_e": sig_e, "sig_d": sig_d}
    cache.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pickle.dumps({"key": key, "out": out}))
    return out


# ── 사고팔기 흉내 (가볍다) ──
def simulate(candles: list[dict], s: dict, variant: str) -> tuple[np.ndarray, list[dict]]:
    df = analysis.to_frame(candles)
    close, high = df["close"].to_numpy(), df["high"].to_numpy()
    atr = analysis.atr(df).to_numpy()
    sma = df["close"].rolling(SMA).mean().to_numpy()
    n, start = len(close), s["start"]
    trend_ok = lambda k: not np.isnan(sma[k]) and close[k] > sma[k]
    fix = FIXES.get(variant)
    src = s["sig_d"] if variant in ("D",) or fix else s["sig_e"] if variant == "E" else s["sig"]
    exit_mode = fix[2] if fix else ("trend" if variant == "추세" else "fixed" if variant in FIXED_EXIT else "trail")
    daily, trades, i = np.zeros(n), [], start
    while i < n - 1:
        if variant == "추세":
            enter = trend_ok(i)
        elif fix:
            enter = i in src and fix[1](src[i], close[i], sma[i])
        else:
            enter = i in src and (variant not in ("A", "C") or trend_ok(i))
        if not enter:
            i += 1
            continue
        info = src.get(i)
        target, stop = (info["target"], info["stop"]) if isinstance(info, dict) else (info or (None, None))
        peak, exit_i, exit_price, why = close[i], n - 1, close[n - 1], "end"
        last = min(i + rules.MAX_HOLD, n - 1) if exit_mode == "fixed" else n - 1
        for j in range(i + 1, last + 1):
            if exit_mode == "trend":
                if not trend_ok(j):
                    exit_i, exit_price, why = j, close[j], "trend"
                    break
            elif exit_mode == "fixed":
                if close[j] <= stop:
                    exit_i, exit_price, why = j, close[j], "stop"
                    break
                if high[j] >= target:
                    exit_i, exit_price, why = j, target, "target"
                    break
            else:
                if close[j] <= stop:
                    exit_i, exit_price, why = j, close[j], "stop"
                    break
                peak = max(peak, close[j])
                stop = max(stop, peak - TRAIL_ATR * atr[j])
            exit_i, exit_price = j, close[j]
            why = "time" if exit_mode == "fixed" and j == last and last < n - 1 else why
        for j in range(i + 1, exit_i + 1):
            daily[j] = (exit_price if j == exit_i else close[j]) / close[j - 1] - 1
        t = {"symbol": s["symbol"], "month": candles[i]["time"][:7], "bars": exit_i - i, "ret": (exit_price / close[i] - 1) * 100,
             "exit": why, "above200": bool(trend_ok(i))}
        if isinstance(info, dict):
            t.update({k: info[k] for k in ("rr", "risk_atr", "target_atr", "support", "channel", "sup_name", "target_name", "ch_label", "ch_pos")})
        trades.append(t)
        i = exit_i + 1
    return daily[start:], trades


def any_day_excess(candles: list[dict], start: int, trades: list[dict]) -> None:
    close = np.array([c["close"] for c in candles])
    cache: dict[int, float] = {}
    for t in trades:
        h = max(t["bars"], 1)
        if h not in cache:
            cache[h] = float(((close[start + h:] / close[start: len(close) - h]) - 1).mean() * 100) if len(close) - start > h else 0.0
        t["excess"] = t["ret"] - cache[h]


def run_variants(data, sig, syms, variants):
    res = {}
    for s in syms:
        c, sg = data[s], sig[s]
        hold = np.zeros(len(c))
        cl = np.array([x["close"] for x in c])
        hold[sg["start"] + 1:] = cl[sg["start"] + 1:] / cl[sg["start"]:-1] - 1
        out = {"dates": [x["time"] for x in c][sg["start"]:], "returns": {"보유": hold[sg["start"]:]}, "trades": {}}
        for v in variants:
            daily, trades = simulate(c, sg, v)
            any_day_excess(c, sg["start"], trades)
            out["returns"][v], out["trades"][v] = daily, trades
        res[s] = out
    return res


def table(res, syms, variants, rng) -> list[str]:
    rows = ["| 방식 | 거래 | 승률 | 평균 보유 | 거래 초과수익 (95% 구간) | 시장에 있던 비율 | 연 수익 | 최대 낙폭 | 샤프 | 샤프 차이 (95% 구간) |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    frames = {v: pd.DataFrame({s: pd.Series(res[s]["returns"][v], index=res[s]["dates"]) for s in syms}).sort_index()
              for v in variants + ["보유"]}
    hold_port = frames["보유"].mean(axis=1)
    hold_sharpe = port_stats(hold_port.to_numpy())[2]
    for v in variants + ["보유"]:
        port = frames[v].mean(axis=1)
        cagr, mdd, sharpe = port_stats(port.to_numpy())
        in_mkt = float(((frames[v] != 0) & frames[v].notna()).sum().sum() / frames[v].notna().sum().sum() * 100)
        name = NAMES.get(v) or FIXES[v][0]
        if v == "보유":
            rows.append(f"| {name} | - | - | - | - | 100% | {cagr:+.1f}% | {mdd:.1f}% | {sharpe:.2f} | - |")
            continue
        tr = pd.DataFrame([t for s in syms for t in res[s]["trades"][v]])
        if tr.empty:
            rows.append(f"| {name} | 0 | - | - | - | - | - | - | - | - |")
            continue
        x, lo, hi, _ = m_boot(tr, rng)
        dlo, dhi = sharpe_diff_ci(port, hold_port, rng)
        rows.append(f"| {name} | {len(tr):,} | {(tr.ret > 0).mean() * 100:.0f}% | {tr.bars.mean():.0f}봉 | {x:+.2f}%p ({lo:+.2f} ~ {hi:+.2f}) | "
                    f"{in_mkt:.0f}% | {cagr:+.1f}% | {mdd:.1f}% | {sharpe:.2f} | {sharpe - hold_sharpe:+.2f} ({dlo:+.2f} ~ {dhi:+.2f}) |")
    return rows


def breakdown(tr: pd.DataFrame, rng) -> list[str]:
    """지금 규칙(D) 거래를 쪼개 본다: 어디서 지는가."""
    def row(label, part):
        if len(part) < 30:
            return f"| {label} | {len(part):,} | - | - | - |"
        x, lo, hi, _ = m_boot(part, rng)
        return f"| {label} | {len(part):,} | {(part.ret > 0).mean() * 100:.0f}% | {part.ret.mean():+.2f}% | {x:+.2f}%p ({lo:+.2f} ~ {hi:+.2f}) |"
    head = ["| 묶음 | 거래 | 승률 | 평균 수익 | 거래 초과수익 (95% 구간) |", "|---|---|---|---|---|"]
    out = []
    groups = [
        ("진입 이유", [("지지 근처만", tr.support & ~tr.channel), ("채널 하단만", ~tr.support & tr.channel), ("둘 다", tr.support & tr.channel)]),
        ("지지선 종류", [("수평 지지선", tr.sup_name == "지지선"), ("추세선", tr.sup_name.fillna("").str.contains("추세선"))]),
        ("200일선", [("위에서 삼", tr.above200), ("아래에서 삼", ~tr.above200)]),
        ("채널 방향", [("상승 채널", tr.ch_label == "상승 채널"), ("하락 채널", tr.ch_label == "하락 채널"), ("채널 없음", tr.ch_label.isna())]),
        ("청산 이유", [("목표 도달", tr.exit == "target"), ("손절", tr.exit == "stop"), ("60봉 정리", tr.exit == "time"), ("기간 끝", tr.exit == "end")]),
        ("손익비", [("1.5~2", tr.rr < 2), ("2~3", (tr.rr >= 2) & (tr.rr < 3)), ("3~5", (tr.rr >= 3) & (tr.rr < 5)), ("5 이상", tr.rr >= 5)]),
        ("손절까지 거리 (ATR)", [("0.5 미만", tr.risk_atr < 0.5), ("0.5~1", (tr.risk_atr >= 0.5) & (tr.risk_atr < 1)),
                              ("1~2", (tr.risk_atr >= 1) & (tr.risk_atr < 2)), ("2 이상", tr.risk_atr >= 2)]),
        ("목표까지 거리 (ATR)", [("1 미만", tr.target_atr < 1), ("1~2", (tr.target_atr >= 1) & (tr.target_atr < 2)),
                              ("2~4", (tr.target_atr >= 2) & (tr.target_atr < 4)), ("4 이상", tr.target_atr >= 4)]),
    ]
    for title, parts in groups:
        out += [f"### {title}\n", *head, *[row(l, tr[mask]) for l, mask in parts], ""]
    return out


def main(phase: str = "all") -> None:
    from backend import main as app
    analysis.LOG_TRENDS = False                        # 이 PC 의 화면 설정(로그)과 무관하게 이전 검증과 같은 직선 선
    my = app.my_rules()
    sp = json.loads((b.ROOT / "research" / "sp500.json").read_text())
    want = sorted(set(sp["symbols"]) | set(midcap.CANDIDATES) | set(MINE))
    t0 = time.time()
    raw = fetch_full(want)
    print(f"[data] {len(raw)}/{len(want)}종목 받음 ({time.time() - t0:.0f}초)", flush=True)
    data = {s: [c for c in cs if c["time"] < END] for s, cs in raw.items()}
    data = {s: cs for s, cs in data.items() if len(cs) >= MIN_BARS}
    g1 = sorted(s for s in sp["symbols"] if s in data)
    g2 = [s for s in g1 if sp["date_added"].get(s, "9") < END]
    g3 = sorted(s for s in midcap.CANDIDATES if s in data)
    g4 = sorted(s for s in MINE if s in data)
    explore, confirm = g1[0::2], g1[1::2]
    print(f"[universe] ① {len(g1)} ② {len(g2)} ③ {len(g3)} ④ {len(g4)} · 탐색 {len(explore)} / 확인 {len(confirm)}", flush=True)

    need = sorted(set(g1) | set(g3) | set(g4))
    t0 = time.time()
    sig = {}
    with ProcessPoolExecutor() as ex:
        for k, r in enumerate(ex.map(signals, [(s, data[s], my) for s in need], chunksize=1), 1):
            sig[r["symbol"]] = r
            if k % 25 == 0:
                print(f"[signals] {k}/{len(need)} ({time.time() - t0:.0f}초)", flush=True)
    rng = np.random.default_rng(0)
    rule_note = ", ".join(f"{k} {v}" for k, v in my.items() if k != "note")
    span = lambda syms: f"{min(data[s][0]['time'] for s in syms)} ~ {max(data[s][-1]['time'] for s in syms)}"

    if phase in ("all", "explore"):
        out = ["# 2017년 말까지(처음 보는 기간)에서 내 규칙 다시 보기\n",
               f"- 기간: 상장일 ~ 2017-12-31, 2018년 전 일봉 {MIN_BARS:,}개 이상인 종목 · 규칙 숫자: {rule_note}",
               "- 방식·지표는 rule_variants.md 와 같다. 선은 직선. 수수료·세금 미반영, 지금 살아남은 종목만 (그냥 보유가 실제보다 좋게 나온다)",
               "- 이전 검증(2018년 10월 이후)은 rule_variants.md\n"]
        res = run_variants(data, sig, need, VARIANTS)
        for title, syms in [("① S&P 500 (지금 구성)", g1), ("② S&P 500 중 2018년 전에 이미 지수에 있던 종목", g2),
                            ("③ 중형·고변동성 후보 (참고)", g3), ("④ 내 종목 (참고)", g4)]:
            if syms:
                out += [f"## {title} ({len(syms)}종목, {span(syms)})\n", *table(res, syms, VARIANTS, rng), ""]
        (b.OUT / "pre2018.md").write_text("\n".join(out), encoding="utf-8")
        print("\n".join(out), flush=True)
        tr = pd.DataFrame([t for s in explore for t in res[s]["trades"]["D"]])
        ex_out = [f"# 탐색 반 ({len(explore)}종목): 지금 규칙(D) 거래 쪼개 보기\n",
                  "- 거래 초과수익 = 거래 수익 − 같은 종목을 아무 날 사서 같은 기간 들고 있었을 때 평균. 95% 구간은 매수한 달 묶음 부트스트랩",
                  "- 여기서 고를 수정안은 최대 2개. 확인 반에서 한 번만 돌린다\n", *breakdown(tr, rng)]
        (b.OUT / "pre2018_explore.md").write_text("\n".join(ex_out), encoding="utf-8")
        print("\n".join(ex_out), flush=True)

    if phase == "confirm":
        assert FIXES, "탐색 반을 보고 FIXES 를 먼저 적는다"
        variants = ["D", *FIXES, "추세"]
        res = run_variants(data, sig, confirm, variants)
        out = [f"# 확인 반 ({len(confirm)}종목): 수정안 한 번만\n",
               *[f"- {k}: {v[0]}" for k, v in FIXES.items()], "", *table(res, confirm, variants, rng), ""]
        (b.OUT / "pre2018_confirm.md").write_text("\n".join(out), encoding="utf-8")
        print("\n".join(out), flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "all")
