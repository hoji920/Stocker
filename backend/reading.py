"""구조 읽기: 앱이 그어 둔 선·채널·패턴을 LLM 이 한 덩어리로 풀어 쓴다 (한눈에 보기 탭, 버튼을 눌렀을 때만).

카드 여러 장(지지선, 저항선, 추세선, 채널 …)을 따로 읽으면 '그래서 지금 어떤 모양인지'가 안 잡힌다.
- 재료는 전부 analysis 가 계산한 숫자다. LLM 은 새 숫자를 만들지 않고, 뚫렸는지도 재료에 적힌 대로만 말한다.
- 지금까지 벌어진 일(어디서 막혔고 무엇을 내줬는지)과 지금 그림은 단정해서 말한다. 그러려면 그 사실이 재료에 있어야 해서
  최근 고점·저점과 그때 근처의 선, 최근에 깨진 선, 지난 저점·고점, 거래량을 코드가 계산해서 준다.
- 앞으로는 '여기를 종가로 내주면 다음은 어디'처럼 조건으로만 쓴다. 오른다·내린다는 예언이나 사라·팔라는 말은 없다.
- 답에 나온 숫자가 재료에 없으면 표시한다 (unverified).
"""
from __future__ import annotations
import json
import math
import re

from backend import analysis
from backend.config import Settings, llm_args

_SYSTEM = (
    "너는 차트를 읽어주는 트레이더 출신 해설자다. 한국어 해요체, 짧고 단정하게. 돌려 말하지 않는다.\n"
    "주어진 자료(JSON)로 '최근에 무슨 일이 있었고, 그래서 지금 어떤 그림인지'를 읽어준다. 선을 나열하지 말고 이야기로 묶는다.\n"
    "규칙:\n"
    "1) 숫자는 자료에 있는 것만 쓴다. 새로 계산하거나 지어내지 않는다. 가격은 자료의 표기 그대로(반올림은 정수나 소수 첫째 자리까지 가능). "
    "가까이 붙은 선들은 '167~171 구간(하락 추세선+저항선)'처럼 묶어 말해도 된다 (양 끝은 자료의 값). "
    "날짜는 어디서든 '9월 16일'처럼 쓴다 (2026-09-16 처럼 쓰지 않는다).\n"
    "2) 자료의 down 은 가격이 내려갈 때 차례로 만나는 자리, up 은 올라갈 때 차례로 만나는 자리다 (이미 순서대로 줄 세워져 있다). "
    "이 순서를 그대로 따르고 다른 쪽으로 옮기지 않는다. 자리마다 state 가 있으면 그 말대로만 쓴다:\n"
    "   - '밀려 있음'·'올라와 있음'·'걸쳐 있음': 종가로 확정된 건 아니다. '~아래로 밀려 있어요', '~를 내주는 중이에요'라고는 해도 되지만 확정 조건은 rule(종가 기준)대로 쓴다.\n"
    "   - 이름이 '최근 내준 ~'·'최근 넘은 ~': 지지선·저항선이 아니라 방금 깨진 선이다. '되찾으면 속임수', '다시 내주면 돌파 실패' 같은 말은 state 에 적힌 쪽으로만 쓴다.\n"
    "   - '지난 저점'·'지난 고점': 선이 아니라 전에 방향이 바뀐 가격대다. 날짜와 함께 쓴다.\n"
    "3) structure: '지금 그림'. 1~2문장. recent 의 최근 고점·저점과 lines_nearby 로 '어디서 막혔는지(받쳤는지)', "
    "broken 과 state 로 '그 뒤 무엇을 내줬는지(넘었는지)'를 이어서 쓴다 "
    "(예: 167~171 구간(하락 추세선+저항선)에서 막힌 뒤 상승 추세선($159.35)과 $155.91 지지선을 한꺼번에 내주는 모양이에요). "
    "두 문장을 넘기지 않고, 자료에 없는 평가(버티는 힘이 약하다, 약한 구간이다 등)나 아래·위 자리 나열을 덧붙이지 않는다 (그건 below·above 가 한다).\n"
    "4) summary: 판단 한 문장. 단정한다. 아래 기준에서 자료에 맞는 것을 고른다 (기준 문장을 베끼지 말고 이 종목에 맞게 쓴다):\n"
    "   - '최근 내준' 상승·지지 추세선이 있고 지금 가격이 그 아래다, 또는 지지선 아래로 밀려 있다 → 단기 상승 흐름이 꺾였다\n"
    "   - '최근 넘은' 하락·저항 추세선이 있고 지금 가격이 그 위다 → 내리던 흐름에서 벗어났다\n"
    "   - state 에 '이탈 실패'·'돌파 실패'가 있다 → 그 이탈(돌파)은 실패했다\n"
    "   - 깨진 선이 없고 살아 있는 상승 추세선 위에 있다 → 상승 추세 안의 눌림(또는 진행)이다. 하락 추세선 아래에 있으면 내리는 흐름 안의 반등(또는 진행)\n"
    "   - 위아래 선 사이에서 어느 쪽도 못 넘었다 → 방향이 안 정해진 구간이다\n"
    "   단기와 중기(averages_and_rsi 의 이동평균 배열)가 엇갈리면 엇갈린다고 단정한다. '~일 수 있어요', '~로 보여요' 같은 흐린 말투를 쓰지 않는다.\n"
    "5) below: 문장 1~2개. down 순서대로. 이미 '밀려 있음'인 선은 받침이라고 하지 않고 '종가가 ~ 아래면 이탈 확정'으로 쓴다. "
    "가까이 붙은 지지선은 한 구간으로 묶는다. 둘째 문장은 '거기도 내주면 다음은 ~'. "
    "자리에 gap 이 적혀 있을 때만 '그 사이엔 받칠 자리가 없어요'라고 덧붙인다. rule 은 그 rule 이 적힌 자리에만 쓴다 (다른 자리의 rule 을 가져오지 않는다).\n"
    "6) above: 문장 1~2개. up 순서대로, 그림이 바뀌는 조건을 쓴다 (무엇을 종가로 되찾거나 넘으면 지금 판단이 뒤집히는지, 그다음 어디가 막는지). "
    "같은 자리를 두 문장에서 되풀이하지 않는다.\n"
    "   below·above 의 문장은 '아래쪽', '위쪽', '반대로'라는 말로 시작하지 않는다 (화면이 앞에 붙인다). 가격과 이름으로 시작한다.\n"
    "7) note: 거래량 1~2문장. volume.pattern 의 뜻을 말한다: '줄면서 내리는 중'이면 투매는 아직 아니다, '늘면서 내리는 중'이면 이탈에 힘이 실렸다, "
    "'늘면서 오르는 중'이면 오름에 힘이 붙었다, '줄면서 오르는 중'이면 따라붙는 힘이 약하다. 이어서 무엇을 같이 볼지 한마디 "
    "(예: 거래량이 평소보다 16% 줄면서 밀리고 있어서 아직 투매가 나온 건 아니에요. 거래량이 크게 붙으면서 깨지는지 같이 보세요). "
    "pattern 이 '평소 수준'이나 '제자리'고 times_usual 도 1.8 미만이면 빈 문자열.\n"
    "8) 단정하는 건 '지금까지 벌어진 일'과 '지금 그림'까지다. 앞으로는 '~하면 ~'의 조건으로만 쓴다. 오른다·내린다는 예언, 사라·팔라는 말, 목표가, 확률은 쓰지 않는다. "
    "note 에 '임시 선'이라고 적힌 추세선은 그렇게 밝힌다.\n"
    'JSON 으로만 답한다: {"structure": "...", "summary": "...", "below": ["...", "..."], "above": ["...", "..."], "note": "..."}'
)


def _day(t) -> str:
    """'2026-09-16' → '9월 16일'. LLM 이 날짜를 그대로 옮겨 적게 자료부터 읽는 말로 준다."""
    m = re.fullmatch(r"\d{4}-(\d{2})-(\d{2})", str(t))
    return f"{int(m[1])}월 {int(m[2])}일" if m else str(t)


def _broken_item(b: dict, price: float, fmt, pct) -> dict:
    """최근에 깨진 추세선을 '지금 가격이 어느 쪽에 있는지'까지 풀어서. 되찾으면/다시 내주면 이 어느 쪽 얘기인지 LLM 이 헷갈리지 않게."""
    sup, when = b.get("kind", "support") == "support", _day(b.get("broken_at", ""))
    inside = price >= b["price"] if sup else price <= b["price"]
    if sup:
        state = f"{when} 종가로 내준 선. " + ("지금은 다시 선 위로 올라와 있음 (이탈 실패). 다시 종가로 내주면 이탈 재확인" if inside
                                         else "지금 가격은 선 아래. 종가로 되찾으면 이번 이탈은 속임수")
    else:
        state = f"{when} 종가로 넘은 선. " + ("지금은 다시 선 아래로 내려와 있음 (돌파 실패). 다시 종가로 넘으면 돌파 재확인" if inside
                                         else "지금 가격은 선 위. 다시 종가로 내주면 돌파 실패")
    if b.get("bars_ago") == 0:
        state += " (마지막 봉 기준이라 마감 전이면 바뀔 수 있음)"
    return {"name": f"최근 {'내준' if sup else '넘은'} {b['label'].replace('깨진 ', '')}", "price": fmt(b["price"]), "from_now_pct": pct(b["price"]),
            "touches_before": b.get("touches"), "state": state}


def _line_at(tl: dict, times: list, i: int) -> float | None:
    """추세선이 i 번째 봉에서 지나던 값 (그때 아직 시작 전이면 None). 로그 선은 로그에서 잇는다."""
    (p1, p2), n = tl["points"], len(times) - 1
    if p1["time"] not in times:
        return None
    a = times.index(p1["time"])
    if i < a or n <= a:
        return None
    f = math.log if tl.get("log") else float
    v = f(p1["value"]) + (f(p2["value"]) - f(p1["value"])) * (i - a) / (n - a)
    return math.exp(v) if tl.get("log") else v


def _swing_zones(df, price: float, side: str, taken: list[tuple[float, float]], fmt, pct, bars: int = 120, gap: float = 0.04, keep: int = 3):
    """최근 bars 봉에서 방향이 바뀐 저점(고점) 중 현재가 아래(위)에 있는 것을, 가까운 가격끼리 묶어서 가까운 순으로.
    수평 지지/저항은 현재가 근처만 잡으므로, 그 선들을 다 내줬을 때의 '다음 자리'가 여기서 나온다. taken = 이미 선으로 잡힌 (가격, 허용폭)."""
    n, low = len(df), side == "low"
    pts = sorted(((p, i) for i, p, t in analysis.swing_points(df) if t == side and i >= n - bars and (p < price if low else p > price)),
                 reverse=low)
    zones: list[list[tuple[float, int]]] = []
    for p, i in pts:
        if zones and abs(p / zones[-1][0][0] - 1) <= gap:
            zones[-1].append((p, i))
        else:
            zones.append([(p, i)])
    out = []
    for z in zones:
        lo, hi = min(p for p, _ in z), max(p for p, _ in z)
        if any(lo - tol <= lp <= hi + tol for lp, tol in taken):       # 이미 지지선·저항선으로 나와 있는 자리
            continue
        edge = hi if low else lo                                       # 가격이 먼저 닿는 쪽
        out.append((edge, {"name": "지난 저점" if low else "지난 고점", "price": fmt(lo) if len(z) == 1 else f"{fmt(lo)}~{fmt(hi)}",
                           "from_now_pct": pct(edge), "dates": [_day(df["time"].iloc[i]) for i in sorted(i for _, i in z)]}))
    return out[:keep]


def _recent(df, price: float, chart: dict, fmt, pct, bars: int = 20) -> dict:
    """최근 bars 봉의 고점·저점과 그때 근처에 있던 선. '어디서 막힌 뒤 내려왔는지'를 LLM 이 지어내지 않게 사실로 준다."""
    n, times = len(df), df["time"].tolist()
    sub = df.iloc[-bars:]
    reach = lambda v: max(float(chart.get("atr") or 0), 0.02 * v)
    named = [(l["price"], word, None) for kind, word in (("support", "지지선"), ("resistance", "저항선")) for l in chart["levels"][kind]]
    named += [(None, t["label"], t) for t in chart.get("trendlines", []) + chart.get("broken_trendlines", [])]

    def spot(col: str, pick) -> dict:
        i = n - bars + int(pick(sub[col].to_numpy())) if n >= bars else int(pick(df[col].to_numpy()))
        v = float(df[col].iloc[i])
        near = []
        for p, name, tl in named:
            at = p if tl is None else _line_at(tl, times, i)
            if at is not None and abs(at - v) <= reach(v):
                near.append(f"{name.replace('깨진 ', '')} {fmt(p if tl is None else tl['price'])}")
        return {"date": _day(times[i]), "price": fmt(v), "bars_ago": n - 1 - i, "from_now_pct": pct(v), "lines_nearby": near}, i

    (high, hi), (low, lo) = spot("high", lambda a: a.argmax()), spot("low", lambda a: a.argmin())
    at = lambda s: abs(s["from_now_pct"]) <= 1.0                       # 지금 가격이 그 고점·저점에서 1% 안
    if hi > lo:
        order = "지금 최근 고점을 새로 쓰는 중" if at(high) else "고점을 찍고 내려오는 중 (고점이 저점보다 나중)"
    else:
        order = "지금 최근 저점을 새로 쓰는 중" if at(low) else "저점을 찍고 올라오는 중 (저점이 고점보다 나중)"
    return {"window": f"최근 {min(bars, n)}봉", "high": high, "low": low, "order": order}


def _volume(df) -> dict | None:
    """거래량이 평소보다 붙었나 빠졌나. 마지막 봉은 장중일 수 있어서 뺀다."""
    done = df.iloc[:-1]
    v = done["volume"].to_numpy(dtype=float)
    if len(v) < 40 or not v[-40:].all():
        return None
    usual = float(v[-65:-5].mean())
    j = len(v) - 10 + int(v[-10:].argmax())
    vol = round((float(v[-5:].mean()) / usual - 1) * 100)
    move = round((float(done["close"].iloc[-1]) / float(done["close"].iloc[-6]) - 1) * 100, 1)     # 같은 5봉 동안의 가격 변화
    pattern = (f"거래량이 {'늘면서' if vol >= 15 else '줄면서' if vol <= -15 else '평소 수준에서'} "
               f"{'오르는 중' if move >= 1 else '내리는 중' if move <= -1 else '제자리'}")
    return {"basis": "마지막 봉을 뺀 최근 5봉 평균을 그 앞 60봉 평균과 비교", "recent5_vs_usual_pct": vol, "recent5_price_change_pct": move, "pattern": pattern,
            "biggest_day_in_last_10": {"date": _day(done["time"].iloc[j]), "times_usual": round(float(v[j]) / usual, 1),
                                       "candle": "양봉" if done["close"].iloc[j] >= done["open"].iloc[j] else "음봉"}}


def facts(chart: dict, price: float, fmt) -> dict:
    """LLM 에 줄 자료. chart = /api/chart 의 일봉 결과, price = 지금 가격.
    선을 '걸쳐 있음 / 아래 / 위'로 미리 나누고 가까운 순으로 줄 세운다 — LLM 에게 맡기면 걸쳐 있는 지지선을 위쪽 선으로 읽는다."""
    pct = lambda v: round((v / price - 1) * 100, 1)
    hold_down, hold_up, lines = [], [], []                # 걸쳐 있는 지지선 / 걸쳐 있는 저항선 / 나머지 (가격, 항목)
    for kind, word, verb in (("support", "지지선", "아래로 내려가면 이탈"), ("resistance", "저항선", "위로 올라가면 돌파")):
        for l in chart["levels"][kind]:
            edge = l["price"] - l["tol"] if kind == "support" else l["price"] + l["tol"]
            item = {"name": word, "price": fmt(l["price"]), "from_now_pct": pct(l["price"]), "touches": l["touches"],
                    "rule": f"종가가 {fmt(edge)} {verb}"}
            if l.get("at_price"):
                beyond = price < l["price"] if kind == "support" else price > l["price"]
                state = (f"지금 가격이 이 선 {'아래로' if kind == 'support' else '위로'} {abs(pct(l['price']))}% "
                         f"{'밀려' if kind == 'support' else '올라와'} 있음 (종가 기준으로는 아직 확정 아님)" if beyond
                         else "지금 가격이 이 선에 걸쳐 있음 (아직 안 뚫림)")
                (hold_down if kind == "support" else hold_up).append((l["price"], {**item, "state": state}))
            else:
                lines.append((l["price"], item))
    for t in chart.get("trendlines", []):
        note = "아직 두 점만 이은 임시 선" if t.get("tentative") else f"{t['touches']}번 닿은 선"
        lines.append((t["price"], {"name": t["label"], "price": fmt(t["price"]), "from_now_pct": pct(t["price"]), "note": note}))
    ch, ex = chart.get("channel"), chart.get("extremes")
    if ch and not ch.get("other_far"):                    # 채널의 기준선은 위 추세선과 같은 선이라 반대편 평행선만 넣는다 (너무 먼 선은 뺀다)
        side = "상단" if ch["other_price"] >= ch["upper"] - 1e-9 else "하단"
        lines.append((ch["other_price"], {"name": f"{ch['label']} {side}", "price": fmt(ch["other_price"]), "from_now_pct": pct(ch["other_price"])}))
    broken = [(b["price"], _broken_item(b, price, fmt, pct)) for b in chart.get("broken_trendlines", [])]
    lines += broken                                       # 깨진 선도 순서 안에 선다 (지지·저항이 아니라 '되찾으면/다시 내주면'의 기준으로)
    # 봉이 충분하면 선 말고도: 지난 저점·고점(선을 다 내줬을 때의 다음 자리), 최근 고점·저점, 거래량
    recent = volume = None
    if len(chart["candles"]) >= 60 and "volume" in chart["candles"][-1]:
        df = analysis.to_frame(chart["candles"])
        taken = [(l["price"], l["tol"]) for kind in ("support", "resistance") for l in chart["levels"][kind]]
        lines += _swing_zones(df, price, "low", taken, fmt, pct) + _swing_zones(df, price, "high", taken, fmt, pct)
        recent, volume = _recent(df, price, chart, fmt, pct), _volume(df)
    # 아래로 내려갈 때 / 위로 올라갈 때 차례로 만나는 선. 걸쳐 있는 선이 맨 앞 (그 선의 rule 이 먼저 걸린다)
    down = [i for _, i in sorted(hold_down, key=lambda x: -x[0])] + [i for p, i in sorted(lines, key=lambda x: -x[0]) if p < price]
    up = [i for _, i in sorted(hold_up, key=lambda x: x[0])] + [i for p, i in sorted(lines, key=lambda x: x[0]) if p >= price]
    for seq, word in ((down, "받칠"), (up, "막을")):       # 다음 자리까지 비어 있는 구간 (10% 넘게)
        for here, there in zip(seq, seq[1:]):
            if abs(there["from_now_pct"] - here["from_now_pct"]) >= 10:
                here["gap"] = f"여기를 지나면 다음 자리({there['price']})까지 {word} 자리가 없음"
    return {
        "name": chart["name"], "symbol": chart["symbol"], "price_now": fmt(price),
        "scale": "로그 눈금 (추세선은 % 기준)" if chart.get("log_scale") else "보통 눈금",
        "recent": recent, "volume": volume,
        "down": down, "up": up,
        "broken": [i for _, i in broken],
        "channel":{"name": ch["label"], "bottom": fmt(ch["lower"]), "top": fmt(ch["upper"]), "position_pct_from_bottom": ch["position_pct"]} if ch else None,
        "patterns": [{"name": p["name"], "status": p["status"], "direction": "상승형" if p["dir"] == "bull" else "하락형",
                      "trigger_line": fmt(p["level"]), "invalid_line": fmt(p["invalid"])} for p in chart.get("patterns", [])],
        "week52": {"high": fmt(ex["high"]["price"]), "low": fmt(ex["low"]["price"])} if ex else None,
        "averages_and_rsi": [f"{i['title']} — {i['body']}" for i in chart.get("insights", []) if i.get("kind") in ("trend", "rsi")],
    }


_NUM = re.compile(r"\d[\d,]*\.?\d*")


def unverified(text: str, material: dict) -> list[str]:
    """답에 나온 숫자 중 자료에 없는 것. 정수나 소수 첫째 자리로 반올림한 것까지는 같은 숫자로 본다 (차이 0.5 이하).
    한 자리 숫자·개수는 넘어간다."""
    num = lambda s: float(s.replace(",", "").rstrip("."))
    known = [num(m) for m in _NUM.findall(json.dumps(material, ensure_ascii=False)) if m.strip(",.")]
    out = []
    for m in _NUM.findall(text):
        v = num(m)
        if v < 10 and "." not in m:                       # '1~2개', '두 점' 같은 개수
            continue
        if not any(abs(v - k) <= max(0.0005 * abs(k), 0.5) for k in known):
            out.append(m)
    return out


def read(settings: Settings, chart: dict, price: float, fmt) -> dict:
    """{structure, below, above, summary, price, unverified, tokens}. 형식이 어긋나면 ValueError."""
    from openai import OpenAI
    material = facts(chart, price, fmt)
    model = settings.openai_agent_model
    resp = OpenAI(api_key=settings.openai_api_key).chat.completions.create(
        model=model, response_format={"type": "json_object"}, **llm_args(model, 0.2, effort="medium"),   # 자리마다 state·rule 을 맞춰 써야 해서 한 단계 더 생각하게
        messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": json.dumps(material, ensure_ascii=False)}])
    data = json.loads(resp.choices[0].message.content or "{}")
    lines = lambda key: [str(x).strip() for x in data.get(key, []) if str(x).strip()][:2] if isinstance(data.get(key), list) else []
    out = {"structure": str(data.get("structure", "")).strip(), "below": lines("below"), "above": lines("above"),
           "summary": str(data.get("summary", "")).strip(), "note": str(data.get("note") or "").strip()}
    if not out["structure"] or not out["summary"]:
        raise ValueError("구조 읽기가 비어서 왔어요")
    text = " ".join([out["structure"], *out["below"], *out["above"], out["summary"], out["note"]])
    return {**out, "price": fmt(price), "as_of": chart["candles"][-1]["time"], "unverified": unverified(text, material),
            "tokens": resp.usage.total_tokens if resp.usage else 0}
