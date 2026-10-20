"""채팅 에이전트: 질문을 받으면 앱의 기능(도구)을 골라 써서 답한다. 주문 도구는 없다.

- 조회·대조는 바로 한다. 무언가를 바꾸는 일(관심종목·수평선 알림·내 규칙)은 '제안'만 하고, 사용자가 확인 버튼을 눌러야 실행된다.

- 도구는 main.py 가 넘겨준다 (이름 → 함수). 여기에는 '무엇을 할 수 있는지' 설명과 대화 루프만 있다.
- 숫자는 도구가 준 것만 쓴다. '내 규칙'은 rules.evaluate 가 계산한 결과를 그대로 전한다 (AI 가 판정하지 않는다).
- 사라·팔아라는 말하지 않는다. 매수·매도를 물으면 내 규칙·뉴스·시장·펀더멘탈을 같이 보고 '20거래일 동안 시장보다 강함·중립·약함' 의견을 낸다.
  그 의견은 판단 기록에 남겨서 나중에 채점한다 (record_view) — 맞는지 모르는 의견을 쌓아 두고 성적으로 믿을지 정한다.
"""
from __future__ import annotations
import json

from backend.config import Settings, llm_args

MAX_STEPS = 8              # 종합 검토는 도구 4개 + 기록 1개를 부른다
MAX_RESULT_CHARS = 6000


def fit_result(result, limit: int = MAX_RESULT_CHARS) -> str:
    """도구 결과를 모델에 줄 글자 수 안으로. 그냥 자르면 깨진 JSON 이 되고 모델은 뒤가 빠진 줄 모른다
    (관심·보유 종목이 많으면 '전체 규칙 대조'의 뒤쪽 종목이 조용히 사라졌다) → 가장 긴 목록의 뒤쪽을 덜어내고 그 사실을 적는다."""
    dump = lambda v: json.dumps(v, ensure_ascii=False, default=str)
    text = dump(result)
    if len(text) <= limit:
        return text
    box = dict(result) if isinstance(result, dict) else {"items": result} if isinstance(result, list) else None
    lists = {k: v for k, v in (box or {}).items() if isinstance(v, list) and len(v) > 1}
    if lists:
        key = max(lists, key=lambda k: len(dump(lists[k])))
        total, keep = len(lists[key]), len(lists[key])
        while keep > 1:
            keep = max(1, keep * 3 // 4)
            box[key] = lists[key][:keep]
            box["truncated"] = f"'{key}' {total}개 중 앞의 {keep}개만 담았어요. 나머지는 빠졌다고 사용자에게 알려주세요."
            if len(dump(box)) <= limit:
                return dump(box)
    return dump({"truncated": "결과가 너무 길어 앞부분만 담았어요.", "text": text[: limit - 80]})

_SYSTEM = (
    "너는 개인용 주식 차트 앱 안의 조사 비서다. 사용자의 질문에 도구를 써서 답한다. 한국어 해요체, 짧고 구체적으로.\n"
    "원칙:\n"
    "1) 숫자와 사실은 도구 결과에 있는 것만 쓴다. 모르면 도구를 부르고, 도구로도 없으면 없다고 말한다.\n"
    "2) 매수·매도·보유 판단을 묻거나 '어때?', '사도 돼?', '팔까?'처럼 물으면 '종합 검토'를 한다. 한 종목에 대해:\n"
    "   check_my_rules(내 규칙) + get_news(종목 뉴스) + market_brief(시장) + fundamentals(가치·성장·실적일·애널리스트)를 모두 부른다 "
    "(한 번에 같이 불러도 된다). 그다음 답을 이 순서로 쓴다:\n"
    "   **올라갈 근거** 2~3개 / **내려갈 근거** 2~3개 (뉴스·실적·가치·시장·추세에서, 도구 결과에 있는 사실만. "
    "근거 끝에 괄호로 출처를 한국어로: 뉴스면 '뉴스·' 뒤에 그 기사의 언론사 이름을 get_news 결과에 적힌 그대로 (모르면 '뉴스'만), "
    "그 밖엔 (실적·가치 지표) / (시장) / (내 규칙). "
    "영어 도구 이름은 쓰지 않는다) / "
    "**곧 있을 일** (다음 실적 발표일 등, 없으면 뺀다) / **내 규칙** 한 줄 (판정·목표·손절은 도구 값 그대로) / "
    "**종합 의견**: 앞으로 20거래일 동안 S&P 500 보다 '강함·중립·약함' 중 하나 + 확신 0~100 + 이유 한 줄 + '틀렸다고 볼 조건' 한 줄.\n"
    "   모양: 섹션 제목을 굵게 한 줄로 쓰고, 그 아래 근거마다 '- '로 시작하는 줄 하나. '사도 된다/어렵다' 같은 첫 문장 없이 바로 **올라갈 근거**부터 시작한다.\n"
    "   의견을 정했으면 답을 쓰기 전에 record_view 를 같은 내용으로 꼭 부른다 (view: strong/neutral/weak). 근거가 엇갈리면 neutral 이 맞다. "
    "답의 이유와 '틀렸다고 볼 조건'은 record_view 에 적은 문장과 같은 뜻이어야 한다. "
    "답 끝에 '판단 기록에 남겼어요 (20거래일 뒤 채점)'와 '이 의견은 아직 채점 전이라 맞는지 몰라요'를 한 줄로 붙인다.\n"
    "   ● 질문에 특정 가격이 있으면('51불에 팔까', '45에 사도 돼?') price_check 도 같이 부르고, 답의 맨 앞에 **그 가격 ($51)** 섹션을 둔다: "
    "현재가에서 몇 %, 그 가격 근처에서 방향이 바뀐 날짜와 횟수(turns_near_price)와 그 뒤 얼마나 밀렸는지/올랐는지, "
    "최근 60봉 동안 그 가격 위에서 마감한 날 수와 마지막 날, 근처 자동 선, 보유 중이면 그 가격에 팔 때 평단 대비 수익률. 사용자가 짚은 근거(예: '두 번 저항')가 "
    "자료와 맞는지 짚는다. 그다음에 나머지 종합 검토를 짧게.\n"
    "   ● 보유 중인 종목이면(check_my_rules 의 holding) 평단·수량·지금 수익률을 꼭 말한다.\n"
    "   ● 종합 검토 때 my_thesis 도 부른다. 저장된 투자 논리가 있으면 '내 논리' 섹션을 두고 기둥이 지금 자료로 유지되는지 한 줄씩 짚는다.\n"
    "   ● 실적 발표나 큰 이벤트가 20거래일 안에 있으면 options_move 도 불러 '곧 있을 일'에 옵션이 예상하는 움직임(±%)을 붙인다.\n"
    "   ● 손익비(rr)는 '지금 살 때'의 숫자다. 매도·보유를 묻는 질문에서는 손익비를 아예 꺼내지 않는다 (근거에도, 내 규칙 줄에도).\n"
    "   ● 종합 의견은 근거와 같은 방향이어야 한다: 올라갈 근거가 더 무거우면 강함, 내려갈 근거가 더 무거우면 약함, 비슷하면 중립. "
    "이유 문장이 반대 방향 근거만 말하면 안 된다. 근거의 출처 괄호는 그 사실이 실제로 나온 곳만 쓴다.\n"
    "   ● 인버스(-1배·-3배) ETF 는 기초 지수가 내려갈 때 오른다. 근거를 쓸 때 '기초 지수(예: 반도체)가 내리면 이 ETF 는 오른다'를 한 번 밝히고, "
    "레버리지 ETF 는 오래 들고 있으면 매일 다시 맞추는 구조 때문에 손실이 쌓일 수 있다는 점을 짧게 덧붙인다.\n"
    "   '언제 팔까', '목표가', '손절'만 묻는 거면 종합 검토 없이 check_my_rules 로 매도 목표와 손절 기준, 보유 중이면 평단 대비 "
    "목표 도달 시·손절 시 수익률(target_from_avg_pct, stop_from_avg_pct)을 숫자로 전한다.\n"
    "3) '사세요/파세요' 같은 지시는 하지 않는다. 종합 의견은 '시장보다 강할 것 같다/약할 것 같다'는 상대적인 판단이지 매매 지시가 아니다. "
    "목표가는 도구에 있는 값(내 규칙의 목표, 애널리스트 목표가)만 쓰고 네가 만들지 않는다. "
    "사용자가 정하지 않은 매매 요령('저항을 못 넘고 되밀리면 판다' 같은 것)을 네가 덧붙이지 않는다.\n"
    "3-1) 추세를 물으면 read_chart 결과의 추세선·채널·insights 에 있는 사실만 전한다 (어느 선 위/아래인지, 채널의 어디쯤인지).\n"
    "4) 규칙 결과를 전할 때는 한 번, 짧게: 이 앱의 백테스트에서 자동 지지/저항·채널은 무작위 선보다 잘 지켜지지 않았다는 점을 덧붙인다. "
    "그래서 종합 의견에서 선(지지·저항)은 근거로 가볍게만 쓰고, 뉴스·실적·가치·시장을 더 무겁게 본다.\n"
    "5) 왜 움직였는지 물으면 why_moved, 뉴스는 get_news, 시장 전체는 market_brief 를 쓴다. 근거 기사가 있으면 언론사 이름을 붙인다.\n"
    "6) 종목 이름만 들으면 find_symbol 로 티커를 찾는다. 사용자가 종목을 말하지 않으면 '지금 보고 있는 종목'을 쓴다.\n"
    "6-1) 여러 종목을 한꺼번에 물으면('관심종목 중 규칙에 맞는 것', '손절에 가까운 보유 종목') scan_my_rules 를 한 번 부른다. "
    "종목마다 check_my_rules 를 따로 부르지 않는다. 결과는 해당하는 종목만 추려서 전한다.\n"
    "6-2) 계좌 전체(비중, 쏠림, 오늘 손익이 어느 종목 때문인지)는 my_portfolio 를 쓴다.\n"
    "6-3) '내 규칙이 과거에 어땠어?'는 backtest_my_rules 를 쓴다. 거래 횟수, 승률, 평균 수익, 누적 수익을 전하고 "
    "'같은 기간 그냥 보유'(buy_and_hold_pct)와 '아무 날 사서 같은 기간 보유'(any_day_same_hold_pct)를 반드시 나란히 말한다. "
    "한 번 거래의 평균 수익(avg_return_pct)이 '아무 날 사서 같은 기간 보유'보다 높은지·비슷한지·낮은지를 한 문장으로 짚는다 "
    "(차이가 0.3%p 안이면 '비슷하다'). 거래가 10번 미만이면 우연일 수 있다고 말한다. 결과가 좋아도 규칙이 통한다고 결론 내리지 않는다.\n"
    "6-4) 사용자가 무언가 해달라고 하면(관심종목 추가·빼기, 가격 알림·선 긋기, 내 규칙 숫자 바꾸기) propose_action 을 부른다. "
    "이 도구는 실행하지 않고 확인 버튼만 띄운다. 그러니 '했어요'라고 말하지 말고 '아래 확인 버튼을 누르면 실행돼요'라고 말한다. "
    "도구가 error 를 주면 그 이유를 전한다.\n"
    "6-5) '사줘', '팔아줘', '주문 넣어줘'처럼 매매를 시키면 답의 첫 문장에서 \"주문은 제가 할 수 없어요. 직접 하셔야 해요.\"라고 말한다. "
    "그 뒤에 도움이 되면 규칙 대조 결과를 덧붙인다.\n"
    "7) 필요한 도구만 부른다 (토큰을 아낀다). 답은 보통 3~6문장, 숫자가 여러 개면 짧은 목록으로.\n"
    "8) 도구 이름(check_my_rules 등)이나 내부 용어를 답에 쓰지 않는다. '원하시면 ~해드릴게요'라고 묻지 말고, "
    "질문에 답하는 데 필요하면 지금 도구를 불러서 답한다. 가격은 도구가 준 표기($384.56)를 그대로 쓴다.\n"
    "9) 용어·제도 질문인데 답이 경우마다 달라지면(예: ETF 보수 — 한국 ETF는 운용·판매·수탁·사무관리보수로 나눠 보이고 "
    "미국 ETF는 총보수율(expense ratio) 하나로 보인다 / 세금·환전 — 국내 주식과 해외 주식이 다르다), "
    "먼저 공통되는 핵심을 2~3문장으로 답하고, 경우마다 어떻게 다른지 한 줄로 짚은 뒤, "
    "마지막에 사용자의 경우를 묻는 짧은 질문 하나로 끝낸다 (예: \"어떤 ETF 보고 계세요? 한국 ETF예요, 미국 ETF예요?\"). "
    "   - '지금 보고 있는 종목'의 사실(상장 시장, ETF 인지, 보수 …)은 아래에 주어진다. 그걸로 갈림길이 풀리면 묻지 말고 "
    "그 종목 기준으로 답한다 (예: KORU 를 보며 보수를 물으면 'KORU 는 미국 ETF 라 총보수율 하나로 보고, 1.32%예요'). "
    "주어진 사실을 사용자에게 되묻는 건 금지. 그 종목으로도 안 풀리는 갈림길만 묻는다.\n"
    "   - 질문이나 대화에 이미 나온 조건은 다시 묻지 않고, 아직 갈리는 것만 묻는다 "
    "(예: '해외주식 세금'을 물었으면 국내·해외는 정해졌으니 '팔아서 남은 이익이에요, 배당이에요?'를 묻는다). 더 갈리는 게 없으면 묻지 않는다.\n"
    "   - 이 되묻기는 8)의 '원하시면' 금지와 다르다: 해 줄 일을 제안하는 게 아니라, 맞는 답을 하려고 사용자의 상황을 묻는 것이다. 한 번에 하나만."
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
    ("my_portfolio", "사용자의 계좌: 종목별 비중(원화 기준), 수량, 평단, 현재가, 수익률, 오늘 손익과 그 금액, 전체 합계, 비중 쏠림.", _none),
    ("find_symbol", "종목 이름으로 티커 찾기.", {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}),
    ("scan_my_rules", "관심종목·보유 종목 전체를 사용자의 매매 규칙에 한 번에 대조한다. 종목별 판정, 손익비, 목표·손절까지 거리.",
     {"type": "object", "properties": {"scope": {"type": "string", "enum": ["watchlist", "holdings", "all"],
                                                 "description": "watchlist=관심종목, holdings=보유 종목, all=둘 다"}}, "required": ["scope"]}),
    ("backtest_my_rules", "사용자의 매매 규칙을 이 종목의 과거 일봉에 그대로 적용했을 때의 결과 (거래 수, 승률, 평균·누적 수익, 그냥 보유와 비교).",
     {"type": "object", "properties": {"symbol": {"type": "string"}, "years": {"type": "integer", "description": "1~5, 기본 3"}},
      "required": ["symbol"]}),
    ("propose_action", "사용자 대신 할 일을 제안한다 (실행은 사용자가 확인 버튼을 눌러야 된다). "
                       "watch_add/watch_remove=관심종목 추가/빼기(symbol), alert_line=그 가격에 수평선을 긋고 닿으면 알림(symbol, price), "
                       "rules_update=내 규칙 숫자 바꾸기(changes).",
     {"type": "object", "properties": {
         "kind": {"type": "string", "enum": ["watch_add", "watch_remove", "alert_line", "rules_update"]},
         "symbol": {"type": "string"}, "price": {"type": "number"},
         "changes": {"type": "object", "description": "바꿀 값만. 키: buy_support_atr(지지 근처 ATR 배수), buy_channel_pct(채널 하단 %), "
                                                     "target_near_atr(목표 근처 ATR 배수), stop_atr(손절 ATR 배수), min_rr(최소 손익비), "
                                                     "min_risk_atr(손익비를 셀 때 위험의 최소 ATR 배수)"}},
      "required": ["kind"]}),
    ("fundamentals", "펀더멘탈: 시가총액, PER·선행 PER·PEG·EV/EBITDA, 매출·이익 성장률(전년 대비), 마진, 부채·현금·잉여현금흐름, "
                     "애널리스트 수·의견·목표가(평균·최저·최고), 공매도 비율, 베타, 배당, 다음 실적 발표일. ETF 는 운용사·분류·보수·순자산.", _sym),
    ("price_check", "사용자가 말한 특정 가격을 차트에서 본다: 현재가에서 몇 %·ATR 몇 배, 그 가격 근처에서 방향이 바뀐 날(고점·저점)과 "
                    "그 뒤 10봉 동안 움직임, 최근 1년 그 가격 위에서 마감한 날 수, 근처 자동 지지/저항선, 보유 중이면 그 가격에 팔 때 평단 대비 수익.",
     {"type": "object", "properties": {"symbol": {"type": "string"}, "price": {"type": "number"}}, "required": ["symbol", "price"]}),
    ("options_move", "옵션이 예상하는 움직임: 가까운 만기들의 등가격 스트래들로 본 '만기까지 ±몇 %'와 범위, 내재변동성, 풋/콜 비율, "
                     "다음 실적 직후 만기의 예상 움직임.", _sym),
    ("my_thesis", "사용자가 이 종목에 적어 둔 투자 논리(왜 샀나, 기둥, 틀렸다고 볼 조건, 기다리는 이벤트)와 마지막 점검 결과.", _sym),
    ("record_view", "종합 의견을 판단 기록에 남긴다 (20거래일 뒤 S&P 500 대비로 자동 채점). 종합 검토를 했으면 답하기 전에 꼭 부른다.",
     {"type": "object", "properties": {
         "symbol": {"type": "string"},
         "view": {"type": "string", "enum": ["strong", "neutral", "weak"], "description": "20거래일 동안 S&P 500 대비 강함/중립/약함"},
         "confidence": {"type": "integer", "description": "0~100"},
         "reason": {"type": "string", "description": "의견의 핵심 이유 한두 문장"},
         "invalid_if": {"type": "string", "description": "틀렸다고 볼 조건 한 문장"}},
      "required": ["symbol", "view", "confidence", "reason", "invalid_if"]}),
]
LABELS = {"check_my_rules": "내 규칙 대조", "read_chart": "차트 읽기", "get_news": "뉴스", "why_moved": "움직인 이유",
          "market_brief": "시장 요약", "my_portfolio": "내 계좌", "find_symbol": "종목 찾기", "scan_my_rules": "전체 규칙 대조",
          "backtest_my_rules": "과거 대조", "propose_action": "할 일 제안", "fundamentals": "펀더멘탈", "record_view": "판단 기록",
          "price_check": "그 가격 보기", "options_move": "옵션 예상 움직임", "my_thesis": "내 투자 논리"}


def chat(settings: Settings, tools: dict, messages: list[dict], symbol: str | None, rules_note: str = "", about: str = "") -> dict:
    """messages = [{role: user|assistant, content}]. 반환 {answer, steps: [도구 이름], tokens}."""
    from openai import OpenAI
    client = OpenAI(api_key=settings.openai_api_key)
    model = settings.openai_agent_model
    # gpt-5 계열은 이 API 에서 도구와 '생각(reasoning)'을 같이 못 쓴다 → 생각 없이 (도구 고르기엔 충분, 토큰도 덜 든다)
    extra = llm_args(model, 0.2, "none")
    context = f"지금 보고 있는 종목: {symbol or '없음'}" + (f" — {about}" if about else "")
    if rules_note:
        context += f"\n사용자가 적어둔 원칙: {rules_note}"
    convo = [{"role": "system", "content": _SYSTEM}, {"role": "system", "content": context},
             *[{"role": m["role"], "content": str(m["content"])[:2000]} for m in messages[-10:] if m.get("role") in ("user", "assistant")]]
    specs = [{"type": "function", "function": {"name": n, "description": d, "parameters": p}} for n, d, p in TOOLS]
    steps, tokens, actions = [], 0, []            # actions = 사용자에게 확인 버튼으로 보여줄 일 (여기서는 실행하지 않는다)
    views = []                                    # 판단 기록에 남긴 종합 의견
    for _ in range(MAX_STEPS):
        resp = client.chat.completions.create(model=model, messages=convo, tools=specs, **extra)
        tokens += resp.usage.total_tokens if resp.usage else 0
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return {"answer": (msg.content or "").strip(), "steps": steps, "tokens": tokens, "actions": actions, "views": views}
        convo.append({"role": "assistant", "content": msg.content, "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
            for c in msg.tool_calls]})
        for call in msg.tool_calls:
            name = call.function.name
            steps.append(LABELS.get(name, name))
            try:
                result = tools[name](**json.loads(call.function.arguments or "{}"))
            except Exception as error:                       # 도구가 실패해도 대화는 이어간다 (모델이 실패를 알고 답하게)
                result = {"error": f"{type(error).__name__}: {str(getattr(error, 'detail', error))[:200]}"}
            if isinstance(result, dict) and isinstance(result.get("action"), dict) and result["action"] not in actions:
                actions.append(result["action"])
            if isinstance(result, dict) and isinstance(result.get("view_record"), dict):
                views.append(result["view_record"])
            convo.append({"role": "tool", "tool_call_id": call.id, "content": fit_result(result)})
    return {"answer": "도구를 여러 번 썼는데도 답을 정리하지 못했어요. 질문을 조금 좁혀서 다시 물어봐 주세요.", "steps": steps,
            "tokens": tokens, "actions": actions, "views": views}
