"""구조 읽기: 앱이 그어 둔 선·채널·패턴을 LLM 이 한 덩어리로 풀어 쓴다 (한눈에 보기 탭, 버튼을 눌렀을 때만).

카드 여러 장(지지선, 저항선, 추세선, 채널 …)을 따로 읽으면 '그래서 지금 어떤 모양인지'가 안 잡힌다.
- 재료는 전부 analysis 가 계산한 숫자다. LLM 은 새 숫자를 만들지 않고, 뚫렸는지도 재료에 적힌 대로만 말한다.
- '여기를 종가로 내주면 다음은 어디'처럼 조건으로만 쓴다. 오른다·내린다는 전망이나 사라·팔라는 말은 없다.
- 답에 나온 숫자가 재료에 없으면 표시한다 (unverified).
"""
from __future__ import annotations
import json
import re

from backend.config import Settings, llm_args

_SYSTEM = (
    "너는 주식 차트를 초보자에게 읽어주는 도우미다. 한국어 해요체, 짧게.\n"
    "주어진 자료(JSON)에 있는 선들을 묶어서 '지금 어떤 모양 안의 어디쯤인지'를 설명한다.\n"
    "규칙:\n"
    "1) 숫자는 자료에 있는 것만 쓴다. 새로 계산하거나 지어내지 않는다. 가격은 자료의 표기 그대로(반올림은 정수나 소수 첫째 자리까지 가능).\n"
    "2) 자료의 down 은 가격이 내려갈 때 차례로 만나는 선, up 은 올라갈 때 차례로 만나는 선이다 (이미 순서대로 줄 세워져 있다). "
    "이 순서를 그대로 따르고 선을 다른 쪽으로 옮기지 않는다.\n"
    "   state 에 '걸쳐 있음'이라고 적힌 선은 아직 뚫린 게 아니다. 그 선의 rule(종가 기준)이 돼야 뚫린 것이다. 네 눈대중으로 '이미 이탈했다'고 하지 않는다.\n"
    "3) structure: 지금 모양을 1~2문장으로. 위에 무엇, 아래에 무엇이 있는지, 걸쳐 있는 선이 있으면 그것도 "
    "(예: 위는 하락 추세선, 아래는 상승 추세선인 넓은 수렴 구간이고 지금은 $102.40 지지선에 걸쳐 있어요).\n"
    "4) below: 문장 1~2개. 첫 문장은 반드시 down 의 첫 번째 선, 둘째 문장은 '거기를 종가로 내주면 다음은 ~'로 down 의 두 번째 선. rule 이 있으면 그 조건을 같이 쓴다.\n"
    "5) above: 문장 1~2개. 첫 문장은 반드시 up 의 첫 번째 선, 둘째 문장은 up 의 두 번째 선. 같은 선을 두 문장에서 되풀이하지 않는다.\n"
    "   below·above 의 문장은 '아래쪽', '위쪽', '첫 선'이라는 말로 시작하지 않는다 (화면이 '아래쪽', '위쪽'을 앞에 붙인다). 선의 가격과 이름으로 시작한다.\n"
    "6) summary: '정리하면'으로 시작하는 한 문장. 지금 위치를 한마디로 (예: 박스 하단 쪽으로 내려가는 중).\n"
    "7) 오를 것·내릴 것이라는 전망, 사라·팔라는 말, 확률은 쓰지 않는다. note 에 '임시 선'이라고 적힌 추세선은 그렇게 밝힌다.\n"
    'JSON 으로만 답한다: {"structure": "...", "below": ["...", "..."], "above": ["...", "..."], "summary": "..."}'
)


def facts(chart: dict, price: float, fmt) -> dict:
    """LLM 에 줄 자료. chart = /api/chart 의 일봉 결과, price = 지금 가격."""
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
                (hold_down if kind == "support" else hold_up).append((l["price"], {**item, "state": "지금 가격이 이 선에 걸쳐 있음 (아직 안 뚫림)"}))
            else:
                lines.append((l["price"], item))
    for t in chart.get("trendlines", []):
        note = "아직 두 점만 이은 임시 선" if t.get("tentative") else f"{t['touches']}번 닿은 선"
        lines.append((t["price"], {"name": t["label"], "price": fmt(t["price"]), "from_now_pct": pct(t["price"]), "note": note}))
    ch, ex = chart.get("channel"), chart.get("extremes")
    if ch:                                                # 채널의 기준선은 위 추세선과 같은 선이라 반대편 평행선만 넣는다
        side = "상단" if ch["other_price"] >= ch["upper"] - 1e-9 else "하단"
        lines.append((ch["other_price"], {"name": f"{ch['label']} {side}", "price": fmt(ch["other_price"]), "from_now_pct": pct(ch["other_price"])}))
    # 아래로 내려갈 때 / 위로 올라갈 때 차례로 만나는 선. 걸쳐 있는 선이 맨 앞 (그 선의 rule 이 먼저 걸린다)
    down = [i for _, i in sorted(hold_down, key=lambda x: -x[0])] + [i for p, i in sorted(lines, key=lambda x: -x[0]) if p < price]
    up = [i for _, i in sorted(hold_up, key=lambda x: x[0])] + [i for p, i in sorted(lines, key=lambda x: x[0]) if p >= price]
    return {
        "name": chart["name"], "symbol": chart["symbol"], "price_now": fmt(price),
        "scale": "로그 눈금 (추세선은 % 기준)" if chart.get("log_scale") else "보통 눈금",
        "down": down, "up": up,
        "channel": {"name": ch["label"], "bottom": fmt(ch["lower"]), "top": fmt(ch["upper"]), "position_pct_from_bottom": ch["position_pct"]} if ch else None,
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
        model=model, response_format={"type": "json_object"}, **llm_args(model, 0.2),
        messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": json.dumps(material, ensure_ascii=False)}])
    data = json.loads(resp.choices[0].message.content or "{}")
    lines = lambda key: [str(x).strip() for x in data.get(key, []) if str(x).strip()][:2] if isinstance(data.get(key), list) else []
    out = {"structure": str(data.get("structure", "")).strip(), "below": lines("below"), "above": lines("above"),
           "summary": str(data.get("summary", "")).strip()}
    if not out["structure"] or not out["summary"]:
        raise ValueError("구조 읽기가 비어서 왔어요")
    text = " ".join([out["structure"], *out["below"], *out["above"], out["summary"]])
    return {**out, "price": fmt(price), "as_of": chart["candles"][-1]["time"], "unverified": unverified(text, material),
            "tokens": resp.usage.total_tokens if resp.usage else 0}
