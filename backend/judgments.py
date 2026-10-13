"""판단 기록과 채점: 에이전트(LLM)가 '이 종목이 시장보다 더 오를지'를 미리 적어 두고, 기간이 지나면 시세로 채점한다.

LLM 의 판단은 과거 데이터로 검증할 수 없다 (모델이 학습하면서 그 뒤의 일을 이미 봤다) → 앞으로 쌓이는 기록으로만 본다.
그래서 판단은 한 번 적으면 고치지 않고(파일 끝에 덧붙이기만), 채점은 저장하지 않고 읽을 때마다 시세로 다시 계산한다.

미리 정한 것 (2026-10-13, 결과를 보기 전)
- 종목: UNIVERSE 100개 (research/midcap.py 가 고른 미국 중형·고변동성 종목, 고정) + 그때의 내 관심·보유 종목(mine 표시, 따로 집계)
- 주기: 주 1회. 그 주에 이미 적은 (종목, 모델, 조건)은 다시 적지 않는다
- 판단: 5거래일·20거래일 뒤까지 SPY 보다 더 오를지(outperform) / 비슷할지(neutral) / 덜 오를지(underperform) + 확신 50~100
- 조건 2가지: news = 숫자 + 최근 기사 / nonews = 숫자만  → 기사가 판단을 낫게 하는지 본다
- 모델: settings.openai_judge_models (기본 gpt-5.4-mini, gpt-5.5)
- 채점: 판단 때의 가격 → h 거래일 뒤 종가 수익률 − 같은 기간 SPY 수익률 = 초과수익
  맞음 = outperform 인데 초과수익 > 0, underperform 인데 < 0. neutral 은 '판단 안 함'으로 따로 센다
- 주된 판정: 20거래일, 고정 100종목. 맞힌 비율이 50% 를 넘는지, 방향대로 샀을 때의 평균 초과수익이 0 을 넘는지,
  그리고 '전부 outperform 이라고 찍기'(always_up)보다 나은지. 5거래일은 참고
- 확신 점검: 확신 70% 라고 한 판단이 실제로 70% 맞았는지 (구간별 맞힌 비율)

바꾼 것 (2026-10-13, 첫 주 W42 를 적은 직후. 채점은 아직 하나도 안 나왔다)
- 비용: W42 는 두 모델을 전 종목에 돌려 한 주에 약 3달러가 들었다 (거의 전부 gpt-5.5).
  → W43 부터 첫 번째 모델(gpt-5.4-mini)만 전 종목, 나머지 모델(gpt-5.5)은 PREMIUM 30종목만. 한 주 약 1달러.
- PREMIUM 은 UNIVERSE 에서 seed 20261013 으로 무작위 30개를 뽑아 고정했다 (골라 넣지 않았다). 내 종목은 포함하지 않는다.
- 모델끼리 비교할 때는 두 모델이 모두 판단한 (주, 종목, 조건)만 쓴다 (summarize 의 paired). 주된 판정(20일, 고정 100종목)은 첫 번째 모델로 본다.
- 시점: W43 부터 매주 월요일 아침(한국 시간)에 남긴다. 기준 가격은 '마지막으로 끝난 미국 정규장 종가'(보통 금요일 종가).
  W42 는 화요일 오전에 남겨서 기준 가격이 토스 주간거래 중의 가격이었다 (그 가격과 날짜는 기록에 있어 채점은 그대로 된다).
  토스는 한국 낮에 주간거래가 시작되면 그날 날짜의 일봉을 새로 만든다 → completed() 가 아직 안 끝난 봉을 빼고 본다.
- 재현: W43 부터 모델에 준 입력 원문을 judgments/inputs/<주>.jsonl 에 남기고, 기록에는 그 해시(prompt_sha)와 지시문 해시(system_sha)를 적는다.

이 기록은 추천이 아니다. 채점이 쌓이기 전의 판단은 근거 없는 의견과 같다.
"""
from __future__ import annotations
import hashlib
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from backend import analysis
from backend.config import Settings, llm_args

UNIVERSE = """ACHR AEHR AI AMC APP ASTS BBAI BE BTBT CELH CIFR CLS COHR CORZ CRSP DDOG DKNG ELF EOSE FCEL FVRR HIMS HUT IOT IREN
LCID LITE LSCC MARA MRNA MSTR MU NET NNE NVAX OPEN PLTR PTON QS RDW RIOT RKLB SEDG SMCI SNAP SNOW SOUN U UPST VRT
AA ACLS AFRM ALAB AMKR ARM BB BEAM CCJ CHPT CLF CLSK COIN CRDO CVNA DELL DUOL ENPH ESTC FSLR GTLB HOOD IONQ JOBY LEU
LMND LUNR MDB MRVL NBIS NIO NTLA OKLO PATH PLUG QBTS RBLX RGTI RIVN RUN SHOP SMR SOFI TTD UEC VKTX VST W WULF ZS""".split()
# 첫 번째 모델 말고는 이 종목들만 판단한다 (비용). random.Random(20261013).sample(sorted(UNIVERSE), 30)
PREMIUM = """ACLS AI ALAB APP BB BBAI BTBT CCJ CHPT COHR COIN CRSP DDOG DELL GTLB LSCC MRNA MRVL MU NET PLUG QBTS RBLX RDW RIOT RIVN SOFI
TTD VRT VST""".split()
HORIZONS, PRIMARY, BENCH = (5, 20), 20, "SPY"
ARMS = ("news", "nonews")
DIRECTIONS = ("outperform", "neutral", "underperform")
KST, ET = ZoneInfo("Asia/Seoul"), ZoneInfo("America/New_York")
BUCKETS = ((50, 59), (60, 69), (70, 79), (80, 89), (90, 100))

_SYSTEM = (
    "너는 주식 분석가다. 주어진 정보만으로, 이 종목이 앞으로 5거래일과 20거래일 동안 S&P 500(SPY)보다 더 오를지 판단한다.\n"
    "- outperform = SPY 보다 더 오른다, underperform = SPY 보다 덜 오른다(더 내린다), neutral = 판단할 근거가 없다.\n"
    "- 확신(c5, c20)은 그 방향이 맞을 확률을 50~100 의 정수로. 50 은 동전 던지기다. neutral 이면 50.\n"
    "- 근거가 약하면 neutral 로 둔다. 억지로 방향을 고르지 않는다. 확신을 부풀리지 않는다.\n"
    "- 주어지지 않은 사실(이 날짜 뒤의 일 포함)을 쓰지 않는다.\n"
    '- JSON 으로만 답한다: {"d5": "outperform|neutral|underperform", "c5": 50, "d20": "...", "c20": 50, '
    '"reason": "한국어 1~2문장", "invalid_if": "이 판단이 틀렸다고 볼 조건을 한국어 한 문장으로"}'
)


def models_for(symbol: str, models: tuple[str, ...]) -> tuple[str, ...]:
    """이 종목을 판단할 모델들: 첫 번째 모델은 전 종목, 나머지는 PREMIUM 종목만."""
    return tuple(m for i, m in enumerate(models) if i == 0 or symbol in PREMIUM)


def week_key(now: datetime | None = None) -> str:
    """판단을 묶는 주 (한국 시간, 월요일 시작). 예: 2026-W42"""
    y, w, _ = (now or datetime.now(KST)).isocalendar()
    return f"{y}-W{w:02d}"


def is_due(now: datetime | None = None) -> bool:
    """그 주 판단을 남길 때가 됐나: 월요일 06:00(한국) 이후. 미국 금요일 장은 토요일 새벽에 끝나 있다."""
    now = now or datetime.now(KST)
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return now >= monday + timedelta(hours=6)


def completed(candles: list[dict], now: datetime | None = None) -> list[dict]:
    """끝난 미국 정규장 봉까지만. 언제 판단을 남기든 기준 가격이 '마지막으로 끝난 장의 종가'가 되게 한다.
    뺄 것: 미국 날짜로 내일 이후의 봉(한국 낮의 주간거래가 만든 봉), 오늘 봉인데 정규장이 아직 안 끝난 것(16:10 전)."""
    now = (now or datetime.now(ET)).astimezone(ET)
    today, closed = now.date().isoformat(), (now.hour, now.minute) >= (16, 10)
    out = list(candles)
    while out and (out[-1]["time"] > today or (out[-1]["time"] == today and not closed)):
        out.pop()
    return out


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def features(candles: list[dict], bench: list[dict]) -> dict:
    """모델에 줄 숫자 (전부 마지막 봉까지의 데이터로). 차트의 선·패턴은 넣지 않는다 (검증에서 정보가 없었다)."""
    df = analysis.to_frame(candles)
    c, n = df["close"], len(df)
    last = float(c.iloc[-1])
    ret = lambda s, h: round((float(s.iloc[-1]) / float(s.iloc[-1 - h]) - 1) * 100, 1) if len(s) > h else None
    vs = lambda k: round((last / float(analysis.sma(c, k).iloc[-1]) - 1) * 100, 1) if n >= k else None
    year = df.tail(252)
    b = analysis.to_frame(bench)["close"]
    return {
        "return_pct": {f"{h}d": ret(c, h) for h in (5, 20, 60, 120)},
        "spy_return_pct": {f"{h}d": ret(b, h) for h in (5, 20, 60)},
        "vs_sma_pct": {f"sma{k}": vs(k) for k in (20, 60, 200)},
        "rsi14": round(float(analysis.rsi(c).iloc[-1]), 0),
        "atr_pct": round(float(analysis.atr(df).iloc[-1]) / last * 100, 1),
        "from_52w_high_pct": round((last / float(year["high"].max()) - 1) * 100, 1),
        "from_52w_low_pct": round((last / float(year["low"].min()) - 1) * 100, 1),
        "volume_5d_vs_60d": round(float(df["volume"].tail(5).mean() / df["volume"].tail(60).mean()), 2) if df["volume"].tail(60).mean() else None,
    }


def build_prompt(symbol: str, name: str, as_of: str, feats: dict, items: list[dict] | None) -> str:
    text = f"종목: {name} ({symbol})\n기준일: {as_of}\n숫자:\n{json.dumps(feats, ensure_ascii=False)}"
    if items is not None:
        block = "\n".join(f"- ({it['date']}, {it['source']}) {it['title']} — {str(it.get('summary', ''))[:160]}" for it in items[:12])
        text += f"\n\n최근 기사 {len(items[:12])}건:\n{block or '(없음)'}"
    return text


def judge(settings: Settings, model: str, prompt: str) -> dict:
    """LLM 한 번 호출 → {d5, c5, d20, c20, reason, invalid_if, tokens}. 형식이 어긋나면 ValueError (기록하지 않는다)."""
    from openai import OpenAI
    resp = OpenAI(api_key=settings.openai_api_key).chat.completions.create(
        model=model, response_format={"type": "json_object"}, **llm_args(model, 0.2),
        messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}])
    data = json.loads(resp.choices[0].message.content or "{}")
    out = {}
    for h in HORIZONS:
        d = data.get(f"d{h}")
        if d not in DIRECTIONS:
            raise ValueError(f"d{h} 가 없거나 틀림: {d!r}")
        out[f"d{h}"] = d
        out[f"c{h}"] = 50 if d == "neutral" else max(50, min(100, int(data.get(f"c{h}", 50))))
    out["reason"], out["invalid_if"] = str(data.get("reason", ""))[:400], str(data.get("invalid_if", ""))[:300]
    out["tokens"] = resp.usage.total_tokens if resp.usage else 0
    return out


class Log:
    """판단 기록. 주마다 파일 하나(JSON 한 줄에 판단 하나), 덧붙이기만 한다.
    두 PC 에서 같은 주를 각각 돌렸으면 (주, 종목, 모델, 조건)마다 먼저 적은 것 하나만 쓴다."""

    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.Lock()

    def append(self, record: dict) -> None:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            with (self.root / f"{record['week']}.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def append_input(self, entry: dict) -> None:
        """모델에 준 입력 원문 (재현용). 같은 종목·조건이면 두 모델이 같은 입력을 받는다."""
        with self._lock:
            (self.root / "inputs").mkdir(parents=True, exist_ok=True)
            with (self.root / "inputs" / f"{entry['week']}.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def read(self) -> list[dict]:
        rows: dict[tuple, dict] = {}
        for path in sorted(self.root.glob("*.jsonl")) if self.root.exists() else []:
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                key = (r["week"], r["symbol"], r["model"], r["arm"])
                if key not in rows or r["made_at"] < rows[key]["made_at"]:
                    rows[key] = r
        return sorted(rows.values(), key=lambda r: (r["made_at"], r["symbol"], r["model"], r["arm"]))

    def done_keys(self, week: str) -> set[tuple]:
        return {(r["symbol"], r["model"], r["arm"]) for r in self.read() if r["week"] == week}


def make_records(settings: Settings, week: str, symbol: str, name: str, mine: bool, candles: list[dict], bench: list[dict],
                 items: list[dict], todo: list[tuple[str, str]], now: datetime | None = None,
                 save_input=None) -> list[dict]:
    """한 종목에 대해 아직 안 적은 (모델, 조건)들을 판단한다. 조건이 달라도 숫자·기준일·가격은 같다.
    candles·bench 는 끝난 봉까지만(completed) 쓴다. save_input(entry) 로 모델에 준 입력 원문을 남긴다."""
    candles, bench = completed(candles, now), completed(bench, now)
    feats, as_of = features(candles, bench), candles[-1]["time"]
    base = {"week": week, "symbol": symbol, "name": name, "mine": mine, "fixed": symbol in UNIVERSE, "as_of": as_of,
            "price": candles[-1]["close"], "bench_as_of": bench[-1]["time"], "bench_price": bench[-1]["close"]}
    prompts = {arm: build_prompt(symbol, name, as_of, feats, items if arm == "news" else None) for arm in {a for _, a in todo}}
    for arm, text in prompts.items():
        if save_input:
            save_input({"week": week, "symbol": symbol, "arm": arm, "prompt_sha": sha(text), "prompt": text})
    out = []
    for model, arm in todo:
        result = judge(settings, model, prompts[arm])
        out.append({**base, "model": model, "arm": arm, "news_count": len(items[:12]) if arm == "news" else 0,
                    "prompt_sha": sha(prompts[arm]), "system_sha": sha(_SYSTEM),
                    "made_at": (now or datetime.now(KST)).isoformat(timespec="seconds"), **result})
    return out


# ── 채점 (저장하지 않는다: 읽을 때마다 지금 시세로 계산) ──
def score(records: list[dict], candles_of) -> list[dict]:
    """candles_of(symbol) -> 일봉. 기록마다 {h: {ret, bench, excess, hit}} 를 붙인다. 아직 기간이 안 지났으면 그 h 는 없다."""
    cache: dict[str, tuple[list[str], list[float]] | None] = {}

    def series(symbol: str):
        if symbol not in cache:
            try:
                cs = candles_of(symbol)
                cache[symbol] = ([c["time"] for c in cs], [c["close"] for c in cs])
            except Exception:
                cache[symbol] = None
        return cache[symbol]

    out = []
    for r in records:
        scored, s, b = {}, series(r["symbol"]), series(BENCH)
        if s and b and r["as_of"] in s[0] and r["bench_as_of"] in b[0]:
            i, j = s[0].index(r["as_of"]), b[0].index(r["bench_as_of"])
            for h in HORIZONS:
                if i + h >= len(s[0]):
                    continue
                exit_day = s[0][i + h]
                jx = b[0].index(exit_day) if exit_day in b[0] else (j + h if j + h < len(b[0]) else None)
                if jx is None:
                    continue
                ret, bench = (s[1][i + h] / r["price"] - 1) * 100, (b[1][jx] / r["bench_price"] - 1) * 100
                d = r[f"d{h}"]
                scored[h] = {"ret": round(ret, 2), "bench": round(bench, 2), "excess": round(ret - bench, 2), "exit_day": exit_day,
                             "hit": None if d == "neutral" else (ret > bench) == (d == "outperform")}
        out.append({**r, "scored": scored})
    return out


def summarize(scored: list[dict], fixed_only: bool = True) -> list[dict]:
    """(모델, 조건, 기간)별 성적. always_up = 같은 기록에 전부 outperform 이라고 찍었을 때."""
    rows = []
    recs = [r for r in scored if r.get("fixed") or not fixed_only]
    for model in sorted({r["model"] for r in recs}):
        for arm in ARMS:
            g = [r for r in recs if r["model"] == model and r["arm"] == arm]
            if not g:
                continue
            for h in HORIZONS:
                done = [r for r in g if h in r["scored"]]
                calls = [r for r in done if r["scored"][h]["hit"] is not None]
                signed = [r["scored"][h]["excess"] * (1 if r[f"d{h}"] == "outperform" else -1) for r in calls]
                mean = lambda xs: round(sum(xs) / len(xs), 2) if xs else None
                rows.append({
                    "model": model, "arm": arm, "horizon": h, "primary": h == PRIMARY,
                    "recorded": len(g), "scored": len(done), "pending": len(g) - len(done),
                    "calls": len(calls), "neutral": len(done) - len(calls),
                    "hit_rate_pct": round(sum(r["scored"][h]["hit"] for r in calls) / len(calls) * 100, 1) if calls else None,
                    "mean_signed_excess_pct": mean(signed),
                    "always_up_hit_rate_pct": round(sum(r["scored"][h]["excess"] > 0 for r in done) / len(done) * 100, 1) if done else None,
                    "always_up_mean_excess_pct": mean([r["scored"][h]["excess"] for r in done]),
                    "calibration": [{"bucket": f"{lo}~{hi}", "n": len(b), "hit_rate_pct": round(sum(r["scored"][h]["hit"] for r in b) / len(b) * 100, 1)}
                                    for lo, hi in BUCKETS if (b := [r for r in calls if lo <= r[f"c{h}"] <= hi])],
                })
    return rows


def paired(scored: list[dict]) -> list[dict]:
    """모델끼리 공정하게 비교할 기록만: 고정 종목 중, 같은 (주, 종목, 조건)을 모든 모델이 판단한 것."""
    models = {r["model"] for r in scored}
    seen: dict[tuple, set] = {}
    for r in scored:
        if r.get("fixed"):
            seen.setdefault((r["week"], r["symbol"], r["arm"]), set()).add(r["model"])
    return [r for r in scored if r.get("fixed") and seen.get((r["week"], r["symbol"], r["arm"])) == models] if len(models) > 1 else []


def weeks_table(records: list[dict]) -> list[dict]:
    """주마다 몇 건을 적었고 토큰을 얼마나 썼나 (모델별)."""
    df = pd.DataFrame(records)
    if df.empty:
        return []
    g = df.groupby(["week", "model"]).agg(records=("symbol", "size"), symbols=("symbol", "nunique"), tokens=("tokens", "sum"),
                                         first=("made_at", "min"), last=("made_at", "max")).reset_index()
    return g.sort_values(["week", "model"], ascending=[False, True]).to_dict("records")
