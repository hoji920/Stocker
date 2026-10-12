"""채팅 에이전트: 질문을 받으면 앱의 기능(도구)을 골라 써서 답한다. 주문 도구는 없다 (조회·대조만).

- 도구는 main.py 가 넘겨준다 (이름 → 함수). 여기에는 '무엇을 할 수 있는지' 설명과 대화 루프만 있다.
- 숫자는 도구가 준 것만 쓴다. '내 규칙'은 rules.evaluate 가 계산한 결과를 그대로 전한다 (AI 가 판정하지 않는다).
- 사라·팔아라는 말하지 않는다: "네 규칙 기준으로는 ~" 까지만.
"""
from __future__ import annotations
import json

from backend.config import Settings

MAX_STEPS = 6

_SYSTEM = (
    "너는 개인용 주식 차트 앱 안의 조사 비서다. 사용자의 질문에 도구를 써서 답한다. 한국어 해요체, 짧고 구체적으로.\n"
    "원칙:\n"
    "1) 숫자와 사실은 도구 결과에 있는 것만 쓴다. 모르면 도구를 부르고, 도구로도 없으면 없다고 말한다.\n"
    "2) 매수·매도 판단을 묻거나 '어때?', '사도 돼?'처럼 물으면 check_my_rules 를 불러 사용자가 정한 규칙에 대조한 결과를 전한다. "
    "판정(verdict)과 진입·목표·손절·손익비는 도구가 계산한 값을 그대로 쓴다. 네가 다시 계산하거나 판정을 바꾸지 않는다.\n"
    "   '언제 팔까', '목표가', '손절'을 물어도 check_my_rules 를 부른다: 매도 목표와 손절 기준, 보유 중이면 평단 대비 "
    "목표 도달 시·손절 시 수익률(target_from_avg_pct, stop_from_avg_pct)을 숫자로 전한다.\n"
    "3) '사세요/파세요' 같은 권유나 앞으로 오른다·내린다는 전망은 하지 않는다. \"정해두신 규칙 기준으로는 ~\"까지만 말한다. "
    "사용자가 정하지 않은 매매 요령('저항을 못 넘고 되밀리면 판다' 같은 것)을 네가 덧붙이지 않는다.\n"
    "3-1) 추세를 물으면 read_chart 결과의 추세선·채널·insights 에 있는 사실만 전한다 (어느 선 위/아래인지, 채널의 어디쯤인지).\n"
    "4) 규칙 결과를 전할 때는 한 번, 짧게: 이 앱의 백테스트에서 자동 지지/저항·채널은 무작위 선보다 잘 지켜지지 않았다는 점을 덧붙인다.\n"
    "5) 왜 움직였는지 물으면 why_moved, 뉴스는 get_news, 시장 전체는 market_brief 를 쓴다. 근거 기사가 있으면 언론사 이름을 붙인다.\n"
    "6) 종목 이름만 들으면 find_symbol 로 티커를 찾는다. 사용자가 종목을 말하지 않으면 '지금 보고 있는 종목'을 쓴다.\n"
    "7) 필요한 도구만 부른다 (토큰을 아낀다). 답은 보통 3~6문장, 숫자가 여러 개면 짧은 목록으로.\n"
    "8) 도구 이름(check_my_rules 등)이나 내부 용어를 답에 쓰지 않는다. '원하시면 ~해드릴게요'라고 묻지 말고, "
    "질문에 답하는 데 필요하면 지금 도구를 불러서 답한다. 가격은 도구가 준 표기($384.56)를 그대로 쓴다."
)

_sym = {"type": "object", "properties": {"symbol": {"type": "string", "description": "티커 (예: INTC, 005930)"}}, "required": ["symbol"]}
_none = {"type": "object", "properties": {}}
TOOLS = [
    ("check_my_rules", "사용자가 정한 매매 규칙(지지 근처·채널 하단에서 매수, 저항 근처가 매도 목표)에 이 종목이 지금 해당하는지 대조한다. "
                       "판정, 지지선, 목표가, 손절 기준, 손익비, 보유 중이면 평단 대비 수치. 살 자리인지·언제 팔지·목표가·손절을 물을 때 쓴다.", _sym),
    ("read_chart", "일봉 차트 요약: 현재가·등락률, 지지/저항선, 추세선, 채널, 52주 고저, 형성 중인 패턴, 최근 신호.", _sym),
    ("get_news", "이 종목의 최근 뉴스 (한국어·영어, 무관한 기사는 걸러짐)와 3줄 요약.", _sym),
    ("why_moved", "오늘 이 종목이 왜 움직였는지: 등락률, 시장 대비, 근거 기사.", _sym),
    ("market_brief", "미국 시장 요약: 시장을 움직인 소식과 지수·금리·원자재.", _none),
    ("my_holdings", "사용자의 보유 종목: 수량, 평단, 현재가, 수익률.", _none),
    ("find_symbol", "종목 이름으로 티커 찾기.", {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}),
]
LABELS = {"check_my_rules": "내 규칙 대조", "read_chart": "차트 읽기", "get_news": "뉴스", "why_moved": "움직인 이유",
          "market_brief": "시장 요약", "my_holdings": "보유 종목", "find_symbol": "종목 찾기"}


def chat(settings: Settings, tools: dict, messages: list[dict], symbol: str | None, rules_note: str = "") -> dict:
    """messages = [{role: user|assistant, content}]. 반환 {answer, steps: [도구 이름], tokens}."""
    from openai import OpenAI
    client = OpenAI(api_key=settings.openai_api_key)
    model = settings.openai_agent_model
    # gpt-5 계열은 이 API 에서 도구와 '생각(reasoning)'을 같이 못 쓴다 → 생각 없이 (도구 고르기엔 충분, 토큰도 덜 든다)
    extra = {"temperature": 0.2} if model.startswith("gpt-4") else {"reasoning_effort": "none"}
    context = f"지금 보고 있는 종목: {symbol or '없음'}"
    if rules_note:
        context += f"\n사용자가 적어둔 원칙: {rules_note}"
    convo = [{"role": "system", "content": _SYSTEM}, {"role": "system", "content": context},
             *[{"role": m["role"], "content": str(m["content"])[:2000]} for m in messages[-10:] if m.get("role") in ("user", "assistant")]]
    specs = [{"type": "function", "function": {"name": n, "description": d, "parameters": p}} for n, d, p in TOOLS]
    steps, tokens = [], 0
    for _ in range(MAX_STEPS):
        resp = client.chat.completions.create(model=model, messages=convo, tools=specs, **extra)
        tokens += resp.usage.total_tokens if resp.usage else 0
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return {"answer": (msg.content or "").strip(), "steps": steps, "tokens": tokens}
        convo.append({"role": "assistant", "content": msg.content, "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
            for c in msg.tool_calls]})
        for call in msg.tool_calls:
            name = call.function.name
            steps.append(LABELS.get(name, name))
            try:
                result = tools[name](**json.loads(call.function.arguments or "{}"))
            except Exception as error:                       # 도구가 실패해도 대화는 이어간다 (모델이 실패를 알고 답하게)
                result = {"error": f"{type(error).__name__}: {str(error)[:200]}"}
            convo.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, ensure_ascii=False, default=str)[:6000]})
    return {"answer": "도구를 여러 번 썼는데도 답을 정리하지 못했어요. 질문을 조금 좁혀서 다시 물어봐 주세요.", "steps": steps, "tokens": tokens}
