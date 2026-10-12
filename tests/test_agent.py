from dataclasses import replace
from types import SimpleNamespace as NS

from backend import agent
from backend.config import get_settings


def _fake_openai(monkeypatch, replies):
    """replies: 차례로 돌려줄 응답. ('tool', 이름, 인자json) 또는 ('text', 내용)."""
    import openai
    sent = []

    def create(**kw):
        sent.append(kw)
        kind, *rest = replies[len(sent) - 1]
        if kind == "tool":
            call = NS(id=f"c{len(sent)}", function=NS(name=rest[0], arguments=rest[1]))
            msg = NS(content=None, tool_calls=[call])
        else:
            msg = NS(content=rest[0], tool_calls=None)
        return NS(choices=[NS(message=msg)], usage=NS(total_tokens=100))

    class FakeClient:
        def __init__(self, **_):
            self.chat = NS(completions=NS(create=create))
    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    return replace(get_settings(), openai_api_key="k", openai_agent_model="gpt-5.4-mini"), sent


def test_agent_calls_tool_then_answers(monkeypatch):
    s, sent = _fake_openai(monkeypatch, [("tool", "check_my_rules", '{"symbol": "INTC"}'), ("text", "규칙 기준으로는 조건 미달이에요.")])
    seen = []
    tools = {"check_my_rules": lambda symbol: seen.append(symbol) or {"verdict": "매수 구간이지만 손익비 미달"}}
    out = agent.chat(s, tools, [{"role": "user", "content": "지금 어때?"}], "INTC", "실적 전 주엔 안 산다")
    assert out == {"answer": "규칙 기준으로는 조건 미달이에요.", "steps": ["내 규칙 대조"], "tokens": 200}
    assert seen == ["INTC"]
    assert "매수 구간이지만 손익비 미달" in sent[1]["messages"][-1]["content"]          # 도구 결과가 다음 요청에 들어간다
    assert "지금 보고 있는 종목: INTC" in sent[0]["messages"][1]["content"] and "실적 전 주엔" in sent[0]["messages"][1]["content"]
    assert sent[0]["reasoning_effort"] == "none"                                       # gpt-5 계열 + 도구


def test_agent_has_no_order_tool_and_survives_tool_failure(monkeypatch):
    names = {n for n, _, _ in agent.TOOLS}
    assert not any(w in n for n in names for w in ("order", "buy", "sell", "trade"))  # 주문 도구는 만들지 않는다
    s, sent = _fake_openai(monkeypatch, [("tool", "get_news", '{"symbol": "X"}'), ("text", "뉴스를 못 가져왔어요.")])
    boom = lambda symbol: (_ for _ in ()).throw(RuntimeError("막힘"))
    out = agent.chat(s, {"get_news": boom}, [{"role": "user", "content": "뉴스"}], None)
    assert out["answer"] == "뉴스를 못 가져왔어요." and "RuntimeError" in sent[1]["messages"][-1]["content"]


def test_agent_stops_after_max_steps(monkeypatch):
    s, sent = _fake_openai(monkeypatch, [("tool", "market_brief", "{}")] * agent.MAX_STEPS)
    out = agent.chat(s, {"market_brief": lambda: {}}, [{"role": "user", "content": "시장"}], None)
    assert len(sent) == agent.MAX_STEPS and "정리하지 못했어요" in out["answer"]
