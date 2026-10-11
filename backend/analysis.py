"""지지/저항선 + 보조지표 + 초보자용 설명 문장.

지지/저항선 방법:
1) 스윙 고점/저점 찾기: 좌우 window 봉보다 높은(낮은) 봉
2) 가격이 가까운 스윙 포인트끼리 묶기 (허용폭 = ATR 의 절반)
3) 2번 이상 닿은 가격대 우선. 현재가 근처(4 ATR 또는 10%)에 없으면 한 번 찍은 전저점·전고점
4) 현재가 위 = 저항선, 아래 = 지지선. 단 현재가가 가격대 안에 걸쳐 있으면 아직 안 뚫린 것으로 보고 지금까지의 역할대로
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def to_frame(candles: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(candles)


def _t(df: pd.DataFrame, i: int):
    """봉 시각을 JSON 으로 보낼 수 있는 파이썬 값으로 (분봉은 numpy int → int)."""
    v = df["time"].iloc[i]
    return v.item() if hasattr(v, "item") else v


# ── 보조지표 ──
def sma(close: pd.Series, n: int) -> pd.Series:
    return close.rolling(n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0).where(gain.notna())


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["close"].shift()
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


# ── 지지/저항 ──
def swing_points(df: pd.DataFrame, window: int = 5) -> list[tuple[int, float, str]]:
    """(index, price, 'high'|'low'). 양옆 window 봉이 다 있어야 확정.
    왼쪽보다는 '엄격히' 높아야(낮아야) 한다 — 같은 가격이 이어지는 보합 구간에서 매 봉이 스윙으로 잡히는 것 방지."""
    highs, lows = df["high"].to_numpy(), df["low"].to_numpy()
    points: list[tuple[int, float, str]] = []
    for i in range(window, len(df) - window):
        left, right = slice(i - window, i), slice(i + 1, i + window + 1)
        if highs[i] > highs[left].max() and highs[i] >= highs[right].max():
            points.append((i, float(highs[i]), "high"))
        if lows[i] < lows[left].min() and lows[i] <= lows[right].min():
            points.append((i, float(lows[i]), "low"))
    return points


def _quality(touches: int, first_idx: int, last_idx: int, n: int) -> tuple[int, float, str]:
    """선 신뢰도 → (별 1~3, 점수 0~1, 한 줄 근거).
    많이 닿을수록, 오래 유지될수록, 최근에도 닿았을수록 믿을 만한 선."""
    span = max(last_idx - first_idx, 0)
    ago = n - 1 - last_idx
    # 기준 60봉(일봉이면 약 3개월): 그만큼 유지됐으면 만점, 그보다 오래 안 닿았으면 '최근' 점수 0
    t_score = 0.85 if touches >= 5 else {4: 0.7, 3: 0.5, 2: 0.25}.get(touches, 0.1)
    score = 0.5 * t_score + 0.2 * min(span / 60, 1) + 0.3 * max(0.0, 1 - ago / 60)
    stars = 3 if score >= 0.7 else 2 if score >= 0.5 else 1
    why = f"{touches}번 닿음 · {span}봉에 걸쳐 · 마지막으로 닿은 건 {ago}봉 전"
    return stars, round(score, 3), why


def find_levels(df: pd.DataFrame, window: int = 5, max_each: int = 3) -> dict:
    if len(df) < window * 2 + 15:
        return {"support": [], "resistance": []}

    last_close = float(df["close"].iloc[-1])
    last_atr = float(np.nan_to_num(atr(df).iloc[-1]))
    # 허용폭: ATR 의 절반 (가격의 0.8% 이상). 상한(예전엔 가격의 3%)은 두지 않는다 — 하루에 7%씩 움직이는 종목에선
    # 가까운 저점들이 하나도 안 묶여 전부 '1번 닿음'으로 버려지고, 1년 전 가격대가 '가장 가까운 지지선'이 됐다.
    tol = float(max(last_atr * 0.5, last_close * 0.008))
    far = _far(last_close, last_atr, atrs=4, pct=0.10)

    points = sorted(swing_points(df, window), key=lambda p: p[1])
    clusters: list[list[tuple[int, float, str]]] = []
    for p in points:
        if clusters and p[1] - np.mean([q[1] for q in clusters[-1]]) <= tol:
            clusters[-1].append(p)
        else:
            clusters.append([p])

    n = len(df)
    levels = []
    for cl in clusters:
        price = float(np.mean([q[1] for q in cl]))
        touches = len(cl)
        idxs = sorted(q[0] for q in cl)
        stars, score, why = _quality(touches, idxs[0], idxs[-1], n)
        levels.append({
            "price": round(price, 4),
            "touches": touches,
            "highs": sum(q[2] == "high" for q in cl),      # 상승이 막힌 횟수
            "lows": sum(q[2] == "low" for q in cl),        # 하락이 멈춘 횟수
            "last_touch": _t(df, idxs[-1]),
            "score": score,
            "stars": stars,
            "why": why,
            "touch_points": [{"time": _t(df, i), "value": round(price, 4)} for i in idxs],
            "tol": round(tol, 4),
            # 현재가가 이 가격대 안에 걸쳐 있다: 종가가 허용폭 넘게 벗어나야 뚫린 것
            "at_price": abs(price - last_close) <= tol,
        })

    dist = lambda lv: abs(lv["price"] - last_close)

    def is_support(lv) -> bool:
        # 가격대에 걸쳐 있는 동안은 아직 안 뚫린 것 → 지금까지 한 역할 그대로 (저점이 모인 곳이면 몇 센트 아래에 있어도 지지)
        if lv["at_price"] and lv["highs"] != lv["lows"]:
            return lv["lows"] > lv["highs"]
        return lv["price"] < last_close

    def pick(want_support: bool) -> list[dict]:
        mine = sorted((lv for lv in levels if is_support(lv) == want_support), key=dist)
        near = [lv for lv in mine if dist(lv) <= far]
        # 2번 이상 닿은 가격대가 우선 (한 번 닿은 건 노이즈가 많다). 가까이에 없으면 한 번 찍은 전저점·전고점,
        # 그것도 없으면 멀더라도 가장 가까운 것 하나 — 1년 전 가격대를 '지지선 후보'로 줄 세우지 않는다
        return ([lv for lv in near if lv["touches"] >= 2] or near)[:max_each] or mine[:1]

    support, resistance = pick(True), pick(False)

    for lv in support + resistance:
        lv["distance_pct"] = round((lv["price"] / last_close - 1) * 100, 2)
        lv["strength"] = lv["stars"] + 1            # 선 굵기 2~4
    return {"support": support, "resistance": resistance}


def _far(last_close: float, last_atr: float, atrs: float, pct: float) -> float:
    """현재가에서 이보다 멀리 있는 선은 '지금 볼 선'이 아니다 (ATR 배수와 가격 비율 중 큰 쪽)."""
    return max(atrs * last_atr, pct * last_close)


# ── 추세선 ──
def _rank_trendlines(df: pd.DataFrame, points: list[tuple[int, float]], kind: str,
                     tol: float, k: int = 3, recent: int = 90, lookback: int = 200) -> list[dict]:
    """스윙 포인트 두 개를 잇는 선 중 이후 종가가 선을 뚫지 않은 선들을, 신뢰도 순으로 k개 (서로 겹치는 선은 하나만).

    support: 저점끼리 연결, 종가가 선 아래로 tol 이상 내려가면 무효
    resistance: 고점끼리 연결, 종가가 선 위로 tol 이상 올라가면 무효
    """
    n = len(df)
    close = df["close"].to_numpy()
    xs = np.arange(n)
    pts = [p for p in points if p[0] >= n - lookback]
    # 가파른 선을 오늘까지 늘이면 현재가와 동떨어진 값이 된다 (예: 현재가 35 인데 지지 추세선 12) → 그런 선은 후보에서 뺀다
    far = _far(float(close[-1]), float(np.nan_to_num(atr(df).iloc[-1])), atrs=6, pct=0.20)
    cands = []
    for i, (a, pa) in enumerate(pts):
        for b, pb in pts[i + 1:]:
            if b - a < 10 or b < n - recent:
                continue
            slope = (pb - pa) / (b - a)
            line = pa + slope * (xs[a:] - a)
            if line[-1] <= 0 or abs(line[-1] - close[-1]) > far:
                continue
            seg = close[a:]
            if kind == "support" and (seg < line - tol).any():
                continue
            if kind == "resistance" and (seg > line + tol).any():
                continue
            touched = [j for j, pj in pts if j >= a and abs(pj - (pa + slope * (j - a))) <= tol]
            stars, score, why = _quality(len(touched), touched[0], touched[-1], n)
            cands.append((score, len(touched), a, pa, slope, touched, stars, why))
    cands.sort(key=lambda c: (c[0], c[1]), reverse=True)

    chosen: list[tuple] = []
    for c in cands:
        a, pa, slope = c[2], c[3], c[4]
        now = pa + slope * (n - 1 - a)
        # 시작점과 오늘 값이 둘 다 이미 고른 선과 허용폭 안이면 사실상 같은 선
        if any(abs(now - (d[3] + d[4] * (n - 1 - d[2]))) < tol and abs(pa - (d[3] + d[4] * (a - d[2]))) < tol * 1.5
               for d in chosen):
            continue
        chosen.append(c)
        if len(chosen) >= k:
            break

    out = []
    for rank, (score, touches, a, pa, slope, touched, stars, why) in enumerate(chosen):
        now = pa + slope * (n - 1 - a)
        rising = slope > 0
        label = ("상승 추세선" if rising else "지지 추세선") if kind == "support" \
            else ("하락 추세선" if not rising else "저항 추세선")
        out.append({
            "id": f"auto-{kind}" if rank == 0 else f"auto-{kind}-{rank + 1}",
            "rank": rank + 1,
            "kind": kind,
            "label": label,
            "touches": touches,
            "tentative": touches < 3,       # 두 점은 언제나 선이 된다 → 세 번째로 닿아야 확인된 선
            "stars": stars,
            "score": score,
            "why": why + (" · 아직 두 점만 이은 임시 선" if touches < 3 else ""),
            "points": [{"time": _t(df, a), "value": round(pa, 4)},
                       {"time": _t(df, -1), "value": round(now, 4)}],
            "touch_points": [{"time": _t(df, j), "value": round(pa + slope * (j - a), 4)} for j in touched],
            "price": round(now, 4),
            "slope_pct": round(slope / now * 100, 3),     # 하루당 기울기 %
            "distance_pct": round((now / close[-1] - 1) * 100, 2),
        })
    return out


def _trend_tol(df: pd.DataFrame) -> float:
    last_close = float(df["close"].iloc[-1])
    return float(np.clip(float(atr(df).iloc[-1]) * 0.5, last_close * 0.005, last_close * 0.03))


def trendline_candidates(df: pd.DataFrame, window: int = 5, k: int = 3) -> dict:
    """방향별 후보 k개. {"support": [...], "resistance": [...]} — 각 목록의 첫 번째가 가장 믿을 만한 선."""
    if len(df) < 60:
        return {"support": [], "resistance": []}
    tol = _trend_tol(df)
    swings = swing_points(df, window)
    lows = [(i, p) for i, p, t in swings if t == "low"]
    highs = [(i, p) for i, p, t in swings if t == "high"]
    return {"support": _rank_trendlines(df, lows, "support", tol, k),
            "resistance": _rank_trendlines(df, highs, "resistance", tol, k)}


def find_trendlines(df: pd.DataFrame, window: int = 5) -> list[dict]:
    """방향별 1등 추세선만 (알림·신호·패턴 판단용)."""
    cands = trendline_candidates(df, window, k=1)
    return cands["support"] + cands["resistance"]


def find_channel(df: pd.DataFrame, trendlines: list[dict]) -> dict | None:
    """추세 채널: 더 믿을 만한 추세선을 기준으로, 반대편 스윙 고점(저점)들이 닿는 평행선을 긋는다.

    튀는 꼬리 하나 때문에 채널이 넓어지지 않도록, 가장 먼 점이 두 번째보다 허용폭 넘게 멀면 그 점은 무시한다.
    """
    if not trendlines:
        return None
    base = max(trendlines, key=lambda t: (t.get("score", 0), t["touches"], t["kind"] == "support"))
    times = df["time"].tolist()
    a = times.index(base["points"][0]["time"])
    n = len(df)
    pa, now = base["points"][0]["value"], base["points"][1]["value"]
    slope = (now - pa) / (n - 1 - a)
    line_at = lambda i: pa + slope * (i - a)
    tol = _trend_tol(df)
    opp = "high" if base["kind"] == "support" else "low"
    sign = 1 if opp == "high" else -1                   # 반대편이 위쪽이면 +, 아래쪽이면 -
    opp_pts = [(i, p) for i, p, t in swing_points(df) if t == opp and i >= a]
    offs = sorted((sign * (p - line_at(i)) for i, p in opp_pts), reverse=True)
    if not offs:
        col = df[opp].to_numpy()[a:]
        offs = [float(max(sign * (col[j] - line_at(a + j)) for j in range(len(col))))]
    off = offs[0]
    if len(offs) >= 2 and offs[0] - offs[1] > tol:
        off = offs[1]                                   # 튀는 점 하나 무시
    if off <= 1e-9:
        return None
    offset = sign * off
    other_touched = [i for i, p in opp_pts if abs(p - (line_at(i) + offset)) <= tol]
    lower_now, upper_now = sorted([now, now + offset])
    last = float(df["close"].iloc[-1])
    base_idx = [times.index(tp["time"]) for tp in base.get("touch_points", [])]
    all_touch = sorted(set(base_idx + other_touched)) or [a]
    stars, score, why = _quality(len(all_touch), all_touch[0], all_touch[-1], n)
    if len(other_touched) < 2:                           # 반대편이 한 점뿐이면 그 점을 지나게 그은 것일 뿐
        stars = min(stars, 2 if other_touched else 1)
    return {
        "label": "상승 채널" if slope > 0 else "하락 채널",
        "base_kind": base["kind"],
        "base": base["points"],
        "other": [{"time": base["points"][0]["time"], "value": round(pa + offset, 4)},
                  {"time": base["points"][1]["time"], "value": round(now + offset, 4)}],
        "other_kind": "resistance" if opp == "high" else "support",
        "other_price": round(now + offset, 4),
        "upper": round(upper_now, 4),
        "lower": round(lower_now, 4),
        "width_pct": round((upper_now / lower_now - 1) * 100, 2),
        "position_pct": round((last - lower_now) / (upper_now - lower_now) * 100, 1),   # 0=바닥, 100=천장
        "stars": stars,
        "score": score,
        "why": f"기준선 {base['touches']}번 + 반대편 {len(other_touched)}번 닿음 · {why.split(' · ', 1)[1]}",
        "touch_points": base.get("touch_points", []) + [
            {"time": _t(df, i), "value": round(line_at(i) + offset, 4)} for i in other_touched],
    }


def channel_suggestions(df: pd.DataFrame, window: int = 5) -> list[dict]:
    """'채널 긋기'에서 찍을 점 3개 추천 (기준선 두 점 + 평행선이 지나는 점). 사용자가 그대로 쓰거나 직접 찍는다.

    ① 큰 흐름: 추세가 시작된 저점(조회 기간 최저)에서, 그 뒤 어떤 저점도 밑으로 안 내려가는 가장 완만한 선.
       폭은 두 점 사이에서 선과 가장 먼 고점까지. 하락 추세면 위아래를 뒤집어 고점끼리 잇는다.
       같은 폭으로 층을 쌓았을 때 지금까지의 가격이 1.5~8층에 걸칠 때만 추천한다
       (1층뿐이면 그냥 넓은 채널, 너무 많으면 폭이 잔물결 수준이라 층이 의미 없다).
    ② 최근 흐름: 자동 추세선 + 평행선(find_channel)을 점 3개로 바꾼 것.
    """
    n = len(df)
    if n < 60:
        return []
    out = []
    high, low, close = (df[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
    tol = _trend_tol(df)
    up = int(low.argmin()) < int(high.argmax())          # 저점이 먼저 나왔으면 상승 추세
    sign = 1.0 if up else -1.0
    base_px, far_px = (low, high) if up else (-high, -low)   # 뒤집으면 '고점끼리 잇기'가 '저점끼리 잇기'와 같은 계산
    i1 = int(base_px.argmin())
    swings = [i for i, _, t in swing_points(df, window) if t == ("low" if up else "high") and i - i1 >= 20]
    if swings:
        slope, i2 = min(((base_px[j] - base_px[i1]) / (j - i1), j) for j in swings)
        line = base_px[i1] + slope * (np.arange(n) - i1)
        offs = far_px - line
        i3 = i1 + int(offs[i1: i2 + 1].argmax())
        width = float(offs[i3])
        layers = float(offs[i1:].max() / width) if width > tol else 0.0
        intact = not (sign * close[i1:] < line[i1:] - tol).any()      # 기준선이 아직 안 깨졌어야
        if intact and 1.5 <= layers <= 8:
            pt = lambda i, v: {"time": _t(df, i), "price": round(float(sign * v), 4)}
            out.append({
                "label": "큰 흐름", "direction": "up" if up else "down",
                "p1": pt(i1, base_px[i1]), "p2": pt(i2, base_px[i2]), "p3": pt(i3, far_px[i3]),
                "layers": round(layers, 1),
                "why": (f"상승이 시작된 저점({_t(df, i1)})과, 그 뒤 어떤 저점도 밑으로 내려가지 않는 저점({_t(df, i2)})을 이었어요. "
                        f"폭은 그 사이 가장 높았던 고점({_t(df, i3)})까지예요." if up else
                        f"하락이 시작된 고점({_t(df, i1)})과, 그 뒤 어떤 고점도 위로 넘지 않는 고점({_t(df, i2)})을 이었어요. "
                        f"폭은 그 사이 가장 낮았던 저점({_t(df, i3)})까지예요."),
            })

    trendlines = find_trendlines(df, window)
    ch = find_channel(df, trendlines)
    if ch:
        base = next(t for t in trendlines if t["kind"] == ch["base_kind"])
        tps = sorted(base["touch_points"], key=lambda q: q["time"])
        on_other = [q for q in ch["touch_points"] if q not in base["touch_points"]]
        p3 = on_other[0] if on_other else ch["other"][0]
        pt = lambda q: {"time": q["time"], "price": q["value"]}
        if tps[0]["time"] != tps[-1]["time"]:
            word = "저점" if ch["base_kind"] == "support" else "고점"
            out.append({
                "label": "최근 흐름", "direction": "up" if ch["label"] == "상승 채널" else "down",
                "p1": pt(tps[0]), "p2": pt(tps[-1]), "p3": pt(p3), "layers": None,
                "why": f"자동 추세선이 닿은 {word} 두 곳을 잇고, 반대편에서 가장 먼 점에 평행선을 맞췄어요.",
            })
    return out


def drawing_value(times: list[str], drawing: dict) -> float | None:
    """사용자가 그은 선(채널이면 기준선)의 오늘 값. trend/channel 은 봉 개수 기준으로 연장."""
    vals = drawing_values(times, drawing)
    return vals[0][1] if vals else None


def drawing_values(times: list[str], drawing: dict, last: float | None = None) -> list[tuple[str, float]]:
    """[(라벨, 오늘 값)]. 채널은 기준선 + 평행선. last(현재가)를 주면, 현재가가 그 두 선 밖에 있을 때
    같은 폭으로 복사한 층 선 중 현재가 바로 위·아래 선도 넣는다 (화면의 층 채널과 같은 선에 알림이 오게)."""
    if drawing["type"] == "horizontal":
        return [("내 수평선", float(drawing["p1"]["price"]))]
    idx = {t: i for i, t in enumerate(times)}
    i1, i2 = idx.get(drawing["p1"]["time"]), idx.get(drawing["p2"]["time"])
    if i1 is None or i2 is None or i1 == i2:
        return []
    slope = (drawing["p2"]["price"] - drawing["p1"]["price"]) / (i2 - i1)
    base = float(drawing["p1"]["price"] + slope * (len(times) - 1 - i1))
    if drawing["type"] == "trend":
        return [("내 추세선", base)]
    i3 = idx.get((drawing.get("p3") or {}).get("time"))
    if i3 is None:
        return [("내 채널 기준선", base)]
    offset = drawing["p3"]["price"] - (drawing["p1"]["price"] + slope * (i3 - i1))
    out = [("내 채널 기준선", base), ("내 채널 평행선", base + offset)]
    if last is not None and abs(offset) > 1e-9:
        k = int(np.floor((last - base) / offset))        # 현재가가 있는 층: 선 k 와 k+1 사이
        out += [("내 채널 층 선", base + j * offset) for j in (k, k + 1) if j not in (0, 1)]
    return out


def trend_pattern(trendlines: list[dict], last_close: float) -> dict | None:
    """저점 추세선과 고점 추세선의 관계: 평행(채널) / 수렴(삼각형·쐐기) / 확산."""
    by = {t["kind"]: t for t in trendlines}
    if "support" not in by or "resistance" not in by:
        return None
    sup, res = by["support"], by["resistance"]
    s_sup = sup["slope_pct"] / 100 * sup["price"]      # 하루당 가격 변화
    s_res = res["slope_pct"] / 100 * res["price"]
    gap = res["price"] - sup["price"]
    diff = s_res - s_sup                                 # 음수면 두 선이 가까워지는 중
    if gap <= 0:
        return None
    # 두 선이 만나거나(수렴) 폭이 두 배가 되는 데(확산) 150거래일(약 7개월) 넘게 걸리면 사실상 평행
    if abs(diff) < 1e-12 or gap / abs(diff) > 150:
        kind, body = "평행 채널", "저점선과 고점선이 거의 평행해요. 이 통로 안에서 오르내리는 흐름이에요."
    elif diff < 0:
        days = int(gap / -diff)
        kind = "삼각 수렴" if s_sup > 0 > s_res or abs(s_sup) < 1e-9 or abs(s_res) < 1e-9 else "쐐기형 수렴"
        body = (f"저점선과 고점선이 점점 모이고 있어요. 지금 속도면 약 {days}거래일 뒤 만나요. "
                "폭이 좁아지면 곧 한쪽으로 크게 움직일 거라고 보는 사람이 많아요.")
    else:
        kind, body = "확산", "저점선과 고점선이 점점 벌어지고 있어요. 출렁임이 커지는 구간이에요."
    return {
        "kind": "trendline",
        "title": f"두 추세선 모양: {kind}",
        "body": body + f" 지금 폭 {gap / last_close * 100:.1f}%.",
    }


# ── 초보자용 설명 ──
_TREND_BODY = {
    "상승 추세선": "저점이 점점 높아지고 있어요. 이 선 위에 있는 동안은 오르는 흐름이 유지된다고 보는 사람이 많아요. "
                   "다만 이 앱의 과거 검증에선 무작위로 그은 선보다 잘 지켜지지 않았어요 (오히려 조금 더 자주 깨졌어요).",
    "하락 추세선": "고점이 점점 낮아지고 있어요. 이 선을 뚫고 올라가면 흐름이 바뀌는 신호로 많이 봐요.",
    "지지 추세선": "저점들을 이은 선이에요. 기울기가 아래쪽이라 저점도 조금씩 낮아지고 있어요.",
    "저항 추세선": "고점들을 이은 선이에요. 기울기가 위쪽이라 고점도 조금씩 높아지고 있어요.",
}


def channel_insight(ch: dict, fmt) -> dict:
    pos = ch["position_pct"]
    rising = ch["label"] == "상승 채널"
    if pos > 100:
        where = ("채널 위로 벗어났어요. 오르는 속도가 평소보다 빨라졌다는 뜻으로 많이 봐요." if rising
                 else "채널 위로 벗어났어요. 내리던 흐름이 깨졌다는 신호로 많이 봐요.")
    elif pos < 0:
        where = ("채널 아래로 벗어났어요. 오르던 흐름이 깨졌다는 신호로 많이 봐요." if rising
                 else "채널 아래로 벗어났어요. 내리는 속도가 더 빨라졌다는 뜻으로 많이 봐요.")
    elif pos >= 80:
        where = "채널 천장 근처예요. 여기서 상승이 막힐 거라고 보는 사람이 많지만, 과거 검증에선 무작위로 그은 선과 차이가 없었어요."
    elif pos <= 20:
        where = "채널 바닥 근처예요. 여기서 반등을 기대하는 사람이 많지만, 과거 검증에선 무작위로 그은 선과 차이가 없었어요."
    else:
        where = "채널 가운데쯤이에요."
    return {
        "kind": "channel",
        "title": f"{ch['label']} 안 {pos:.0f}% 위치",
        "body": f"바닥 {fmt(ch['lower'])} ~ 천장 {fmt(ch['upper'])} (폭 {ch['width_pct']:.1f}%). "
                f"0%=바닥, 100%=천장. {where}",
    }


def level_history(lv: dict, kind: str) -> str:
    """수평선이 과거에 한 일을 사실대로: 지지선이라고 늘 '반등했던 자리'는 아니다 (막혔던 가격을 뚫고 올라온 경우)."""
    highs, lows = lv.get("highs", 0), lv.get("lows", 0)
    if lv.get("at_price"):                                  # 아직 어느 쪽으로도 안 벗어났으니 '전환' 얘기는 이르다
        return (f"과거 여기서 {lows}번 하락이 멈췄어요." if not highs else f"과거 여기서 {highs}번 상승이 막혔어요." if not lows
                else f"과거 여기서 {lows}번 하락이 멈추고 {highs}번 상승이 막혔어요.")
    if kind == "support":
        if highs and not lows:
            return (f"전에 {highs}번 상승이 막혔던 가격대예요. 지금은 그 위로 올라와 있어서, "
                    "거꾸로 지지선이 된다고 보는 사람이 많아요 (저항→지지 전환).")
        if highs:
            return f"과거 이 가격대에서 {lows}번 하락이 멈추고 {highs}번 상승이 막혔어요."
        return f"과거 이 가격대에서 {lows}번 하락이 멈췄어요."
    if lows and not highs:
        return (f"전에 {lows}번 하락이 멈췄던 가격대예요. 지금은 그 아래로 내려와 있어서, "
                "거꾸로 저항선이 된다고 보는 사람이 많아요 (지지→저항 전환).")
    if lows:
        return f"과거 이 가격대에서 {highs}번 상승이 막히고 {lows}번 하락이 멈췄어요."
    return f"과거 이 가격대에서 {highs}번 상승이 막혔어요."


def extremes(df: pd.DataFrame, unit: str = "일") -> dict:
    """최근 1년(일봉 252개) 또는 조회 기간의 최고·최저. 지지/저항이 비어 있을 때도 기준이 되는 가격."""
    span = min(len(df), 252) if unit == "일" else len(df)
    sub = df.iloc[-span:]
    hi, lo = int(sub["high"].to_numpy().argmax()), int(sub["low"].to_numpy().argmin())
    last = float(df["close"].iloc[-1])
    label = "52주" if unit == "일" and span == 252 else f"최근 {span}{unit}"
    return {
        "label": label,
        "high": {"price": float(sub["high"].iloc[hi]), "time": _t(sub, hi),
                 "distance_pct": round((float(sub["high"].iloc[hi]) / last - 1) * 100, 2)},
        "low": {"price": float(sub["low"].iloc[lo]), "time": _t(sub, lo),
                "distance_pct": round((float(sub["low"].iloc[lo]) / last - 1) * 100, 2)},
    }


def build_insights(df: pd.DataFrame, levels: dict, fmt, trendlines: list[dict] | None = None,
                   unit: str = "일") -> list[dict]:
    """사실 서술만 한다. 매수/매도 권유 문장 금지."""
    close = df["close"]
    last = float(close.iloc[-1])
    out: list[dict] = []

    if levels["support"]:
        s = levels["support"][0]
        out.append({
            "kind": "support",
            "title": f"가장 가까운 지지선 {fmt(s['price'])} ({s['distance_pct']:+.1f}%)",
            "body": (f"현재가가 지금 이 가격대에 걸쳐 있어요. {level_history(s, 'support')} "
                     f"종가가 {fmt(s['price'] - s['tol'])} 아래로 내려가면 뚫린 걸로 봐요." if s.get("at_price") else
                     f"{level_history(s, 'support')} 깨고 내려가면 다음 지지선을 보는 사람이 많아요."),
        })
    else:
        out.append({"kind": "support", "title": "아래쪽 지지선 없음",
                    "body": f"지금 보는 {len(df)}{unit} 안에서는 가장 낮은 가격대예요. "
                            "더 오래된 저점은 주·월 봉에서 볼 수 있어요."})

    if levels["resistance"]:
        r = levels["resistance"][0]
        out.append({
            "kind": "resistance",
            "title": f"가장 가까운 저항선 {fmt(r['price'])} ({r['distance_pct']:+.1f}%)",
            "body": (f"현재가가 지금 이 가격대에 걸쳐 있어요. {level_history(r, 'resistance')} "
                     f"종가가 {fmt(r['price'] + r['tol'])} 위로 올라가면 뚫린 걸로 봐요." if r.get("at_price") else
                     f"{level_history(r, 'resistance')} 뚫고 올라가면 이 선이 거꾸로 지지선이 된다고 보는 사람이 많아요."),
        })
    else:
        out.append({"kind": "resistance", "title": "위쪽 저항선 없음",
                    "body": f"지금 보는 {len(df)}{unit} 안에서는 가장 높은 가격대예요. "
                            "더 오래된 고점이 위에 있을 수 있으니 주·월 봉에서 확인해 보세요."})

    for lv in levels["support"][:1] + levels["resistance"][:1]:
        if abs(lv["distance_pct"]) <= 1.5:
            side = "지지선" if lv in levels["support"] else "저항선"
            out.insert(0, {"kind": "alert", "title": f"{side} 바로 근처예요",
                           "body": f"현재가가 {side}과 {abs(lv['distance_pct']):.1f}% 차이예요. "
                                   "위로 튕길지 뚫고 갈지 많은 사람이 지켜보는 자리예요."})

    for tl in trendlines or []:
        out.append({
            "kind": "trendline",
            "title": f"{tl['label']} {fmt(tl['price'])} ({tl['distance_pct']:+.1f}%)",
            "body": f"{_TREND_BODY[tl['label']]} 과거 {tl['touches']}번 닿았어요."
                    + (" 아직 두 점만 이은 임시 선이라, 한 번 더 닿아야 확인된 추세선으로 봐요." if tl.get("tentative") else ""),
        })

    ex = extremes(df, unit)
    out.append({
        "kind": "extremes",
        "title": f"{ex['label']} 최고 {fmt(ex['high']['price'])} ({ex['high']['distance_pct']:+.1f}%) · "
                 f"최저 {fmt(ex['low']['price'])} ({ex['low']['distance_pct']:+.1f}%)",
        "body": (f"최고가는 {ex['high']['time']}, 최저가는 {ex['low']['time']}에 나왔어요. "
                 if isinstance(ex["high"]["time"], str) else "") + "많은 사람이 기준으로 삼는 가격이에요.",
    })

    ma20, ma60 = sma(close, 20).iloc[-1], sma(close, 60).iloc[-1]
    if not np.isnan(ma20) and not np.isnan(ma60):
        if last > ma20 > ma60:
            trend = ("상승 흐름", f"현재가가 20{unit}·60{unit} 평균보다 위에 있고, 단기 평균이 장기 평균보다 높아요.")
        elif last < ma20 < ma60:
            trend = ("하락 흐름", f"현재가가 20{unit}·60{unit} 평균보다 아래에 있고, 단기 평균이 장기 평균보다 낮아요.")
        else:
            trend = ("방향 탐색 중", "이동평균선들이 엇갈려 있어요. 뚜렷한 추세가 없는 구간이에요.")
        out.append({"kind": "trend", "title": f"추세: {trend[0]}", "body": trend[1]})

    r = rsi(close).iloc[-1]
    if not np.isnan(r):
        if r >= 70:
            body = "최근 짧은 기간에 많이 올랐어요. 단기 과열로 보는 사람이 많은 구간이에요."
        elif r <= 30:
            body = "최근 짧은 기간에 많이 내렸어요. 단기 과매도로 보는 사람이 많은 구간이에요."
        else:
            body = "과열도 과매도도 아닌 보통 구간이에요. (70 이상 과열, 30 이하 과매도)"
        out.append({"kind": "rsi", "title": f"RSI {r:.0f}", "body": body})

    return out


def analyze(candles: list[dict], fmt, unit: str = "일") -> dict:
    """보조지표 선은 프론트(indicators.js)에서 사용자가 고른 기간으로 계산한다."""
    df = to_frame(candles)
    levels = find_levels(df)
    candidates = trendline_candidates(df)
    trendlines = candidates["support"][:1] + candidates["resistance"][:1]
    channel = find_channel(df, trendlines)
    insights = build_insights(df, levels, fmt, trendlines, unit)
    # 추세선 카드 바로 뒤, 이평/RSI 카드 앞
    at = next((i for i, c in enumerate(insights) if c["kind"] in ("extremes", "trend", "rsi")), len(insights))
    extra = [trend_pattern(trendlines, float(df["close"].iloc[-1])),
             channel_insight(channel, fmt) if channel else None]
    insights[at:at] = [c for c in extra if c]
    return {
        "levels": levels,
        "trendlines": trendlines,
        "trend_candidates": candidates,     # 방향별 후보 (1번 = trendlines 에 있는 선)
        "channel": channel,
        "extremes": extremes(df, unit),
        "insights": insights,
    }


def alert_lines(candles: list[dict], drawings: list[dict]) -> dict:
    """알림 감시용: 수평 지지/저항 + 자동 추세선 + 내가 그은 선의 '오늘 값'을 한 목록으로."""
    df = to_frame(candles)
    last = float(df["close"].iloc[-1])
    out = find_levels(df)
    trendlines = find_trendlines(df)
    for tl in trendlines:
        out[tl["kind"]].append({"id": tl["id"], "price": tl["price"], "touches": tl["touches"], "label": tl["label"]})
    ch = find_channel(df, trendlines)
    if ch:   # 기준선은 위 추세선과 같으니 반대편 평행선만 추가
        side = "천장" if ch["other_kind"] == "resistance" else "바닥"
        kind = "support" if ch["other_price"] < last else "resistance"
        out[kind].append({"id": "auto-channel", "price": ch["other_price"], "touches": 0, "label": f"{ch['label']} {side}"})
    times = df["time"].tolist()
    for d in drawings:
        for i, (label, value) in enumerate(drawing_values(times, d, last)):
            if value <= 0:
                continue
            kind = "support" if value < last else "resistance"
            out[kind].append({"id": f"{d['id']}-{i}" if i else d["id"], "price": round(value, 4),
                              "touches": 0, "label": label})
    return out
