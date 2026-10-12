"""미국 중형·고변동성 종목에서도 선·근접도·패턴이 안 통하나 (마지막 종목군 검증).

지금까지의 검증은 초대형주 68개(애플·JP모건 …)였다. 실제로 거래하는 건 스트래티지·아이렌·블룸에너지 같은
중형·고변동성 종목이라, 그 종목군으로 한 번만 더 본다. 종목군을 바꿔가며 '되는 곳'을 찾는 것도 우연을 고르는 방법이라
가설·기준·확인용 종목을 돌리기 전에 정해두고, 이번이 마지막이다.

종목 (결과를 보기 전에 정한 규칙)
- 후보: 아래 CANDIDATES (개인 투자자가 많이 거래하는 미국 중형·성장·테마주, ETF 제외, 기존 80종목 제외)
- 일봉 500개 이상인 것 중 최근 250봉 변동성(ATR ÷ 종가 중앙값)이 큰 순서로 100개
- 알파벳순으로 번갈아 탐색 50 / 확증 50. 내 보유·관심 종목(MINE)은 확증 쪽에 넣는다

가설 (미리 정함, 판정은 확증 50종목에서 Holm 3개 기준, 방향까지 맞아야 '확인됨')
  H1  자동 수평 지지/저항선이 대조군보다 잘 지켜진다          (backtest_lines 와 같은 판정·대조군)
  H2  52주 최고가에 가까운 종목(≥0.95)이 먼 종목(<0.80)보다 20봉 뒤 더 오른다   (near_high 와 같은 방식)
  H3  상승형 패턴 5종 돌파 뒤 20봉 초과수익이 0 보다 크다       (patterns_backtest 와 같은 방식)

한계: 지금 거래되는 종목만 골랐다 (상장폐지·급락 후 사라진 종목 없음, 최근 인기 종목 쏠림). 상장 기간이 짧은 종목이 많다.

실행: python -m research.midcap      (토스에서 일봉을 새로 받는다)
결과: research/results/midcap.md
"""
from __future__ import annotations
import json
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from backend import analysis, patterns
from research import backtest_lines as b
from research import confirm, methods as m, near_high, patterns_backtest as pb

CANDIDATES = """
MSTR COIN MARA RIOT CLSK IREN HUT CIFR WULF BITF HOOD SOFI CORZ BTBT
SMCI MU MRVL ON LSCC ARM ASTS IONQ RGTI QBTS SOUN BBAI AI PLTR PATH NBIS CRWV ALAB CRDO AMKR ACLS COHR LITE AEHR VRT CLS
SNOW DDOG NET CRWD ZS OKTA MDB TWLO DOCU ZM ROKU SHOP PINS SNAP U RBLX DKNG TTD APP UPST AFRM LMND OPEN CVNA W ETSY CHWY
PTON FVRR UBER LYFT DASH ABNB SPOT SE HIMS DUOL TOST BILL ESTC GTLB S IOT CFLT
BE PLUG FCEL ENPH SEDG RUN FSLR CHPT QS RIVN LCID NIO XPEV LI EOSE OKLO SMR NNE VST CCJ UEC LEU
RKLB LUNR ACHR JOBY RDW MRNA NVAX CRSP BNTX VKTX NTLA BEAM
GME AMC BB CCL NCLH RCL AAL UAL AA CLF FCX BABA JD PDD BIDU TME DELL ANET CELH ELF ONON
""".split()
MINE = ["MSTR", "BE", "IREN", "SNOW"]            # 내 보유·관심 종목 → 확증 쪽
N_UNIVERSE, MIN_BARS = 100, 500
BULL = [t for t, c in patterns.CATALOG.items() if c.get("dir") != "bear"]


def fetch(symbols: list[str], pages: int = 10) -> dict[str, list[dict]]:
    """토스 일봉 (research/data 캐시). 못 받는 종목(상장폐지·티커 변경)은 건너뛴다."""
    b.DATA.mkdir(parents=True, exist_ok=True)
    from backend.config import get_settings
    from backend.toss_client import TossClient
    client, out = None, {}
    for s in symbols:
        path = b.DATA / f"{s}.json"
        if path.exists() and time.time() - path.stat().st_mtime < 7 * 86400:
            out[s] = json.loads(path.read_text())
            continue
        client = client or TossClient(get_settings())
        try:
            out[s] = client.daily_candles(s, pages)
        except Exception as error:
            print(f"[data] {s} 건너뜀: {type(error).__name__}")
            continue
        path.write_text(json.dumps(out[s]))
    return out


def volatility(candles: list[dict]) -> float:
    """최근 250봉의 ATR ÷ 종가 중앙값 (%)."""
    df = analysis.to_frame(candles)
    return float((analysis.atr(df) / df["close"]).tail(250).median() * 100)


def universe(data: dict[str, list[dict]]) -> tuple[list[str], list[str], dict[str, float]]:
    old = set(b.US + b.KR + confirm.HOLDOUT)
    vol = {s: volatility(c) for s, c in data.items() if s not in old and len(c) >= MIN_BARS}
    picked = sorted(vol, key=vol.get, reverse=True)[:N_UNIVERSE]
    mine = [s for s in MINE if s in picked]
    rest = sorted(s for s in picked if s not in mine)
    conf = mine + rest[1::2][: len(picked) // 2 - len(mine)]
    return sorted(s for s in picked if s not in conf), sorted(conf), vol


def h1_levels(data: dict[str, list[dict]], rng) -> dict:
    with ProcessPoolExecutor() as ex:
        real = pd.DataFrame([r for p in ex.map(b.eval_symbol, data.items()) for r in p])
    ctrl = b.add_controls(real, data)
    for df in (real, ctrl):
        df["month"] = df["date"].str[:7]
    rl, cl = real[real.type == "level"], ctrl[ctrl.type == "level"]
    rr, cr, lo, hi, p = m.boot_bounce(rl, cl, rng)
    return {"n": int(rl.outcome.isin(["bounce", "break"]).sum()), "a": rr * 100, "b": cr * 100, "diff": (rr - cr) * 100,
            "lo": lo * 100, "hi": hi * 100, "p": p}


def h2_near_high(symbols: list[str], rng) -> dict:
    r = near_high.boot_diff(near_high.panel(symbols), "ex20", rng)
    return {"n": r["n_near"] + r["n_far"], "a": r["near"], "b": r["far"], "diff": r["diff"], "lo": r["lo"], "hi": r["hi"], "p": r["p"]}


def h3_patterns(data: dict[str, list[dict]], rng) -> dict:
    with ProcessPoolExecutor() as ex:
        ev = pd.DataFrame([r for rows, _ in ex.map(pb.symbol_events, data.items()) for r in rows])
    g = ev[(ev.group == "pattern") & ev.type.isin(BULL)]
    mean, lo, hi, p = pb.boot_mean(g, "x20", rng)
    return {"n": len(g), "a": float(g.r20.mean()), "b": float(g.r20.mean() - mean), "diff": mean, "lo": lo, "hi": hi, "p": p}


HYPOTHESES = [("H1 자동 수평선이 대조군보다 잘 지켜진다", "지켜진 비율", "대조군", "%p"),
              ("H2 52주 최고가 근처 종목이 20봉 뒤 더 오른다", "가까움 초과수익", "멂 초과수익", "%p"),
              ("H3 상승형 패턴 돌파 뒤 20봉 초과수익 > 0", "돌파 뒤 20봉", "그 종목 평소 20봉", "%p")]


def evaluate(symbols: list[str], data: dict[str, list[dict]], seed: int) -> list[dict]:
    sub = {s: data[s] for s in symbols}
    rng = np.random.default_rng(seed)
    rows = [h1_levels(sub, rng), h2_near_high(symbols, rng), h3_patterns(sub, rng)]
    for r, pa in zip(rows, m.holm([r["p"] for r in rows])):
        r["holm"] = pa
    return rows


def main() -> None:
    t0 = time.time()
    data = fetch(CANDIDATES)
    explore, conf, vol = universe(data)
    old_vol = [volatility(json.loads((b.DATA / f"{s}.json").read_text())) for s in b.US + confirm.HOLDOUT
               if (b.DATA / f"{s}.json").exists()]
    print(f"[data] 후보 {len(CANDIDATES)} → 받은 것 {len(data)} → 탐색 {len(explore)} / 확증 {len(conf)} ({time.time() - t0:.0f}s)")
    out = ["# 미국 중형·고변동성 종목 검증 (가설 3개, 미리 정함)\n",
           f"- 후보 {len(CANDIDATES)}개 중 일봉 {MIN_BARS}개 이상 {len(vol)}개 → 변동성 큰 순 {len(explore) + len(conf)}개 → 탐색 {len(explore)} / 확증 {len(conf)}",
           f"- 하루 변동폭(ATR÷종가) 중앙값: 이 종목군 {np.median([vol[s] for s in explore + conf]):.1f}% / 기존 미국 대형주 {np.median(old_vol):.1f}%",
           f"- 일봉 개수 중앙값: {int(np.median([len(data[s]) for s in explore + conf]))}",
           "- 판정·대조군·월 묶음 부트스트랩 2000회는 기존 검증과 같다. Holm p 는 가설 3개 기준, 결론은 확증 종목에서만\n"]
    for title, symbols, seed in (("확증 (결론은 여기서)", conf, 1), ("탐색 (참고)", explore, 0)):
        rows = evaluate(symbols, data, seed)
        print(f"[{title}] 완료 ({time.time() - t0:.0f}s)")
        out += [f"## {title}: {len(symbols)}종목\n", f"{', '.join(symbols)}\n",
                "| 가설 | 표본 | 값 | 비교 기준 | 차이 | 95% 구간 | Holm p | 결론 |", "|---|---|---|---|---|---|---|---|"]
        for (text, la, lb, unit), r in zip(HYPOTHESES, rows):
            verdict = "확인됨" if r["holm"] < 0.05 and r["diff"] > 0 else ("반대로 나옴" if r["holm"] < 0.05 else "확인 안 됨")
            out.append(f"| {text} | {r['n']:,} | {la} {r['a']:+.2f}% | {lb} {r['b']:+.2f}% | {r['diff']:+.2f}{unit} | "
                       f"{r['lo']:+.2f} ~ {r['hi']:+.2f} | {r['holm']:.3g} | {verdict} |")
        out.append("")
    (b.OUT / "midcap.md").write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
