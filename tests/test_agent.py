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
    assert out == {"answer": "규칙 기준으로는 조건 미달이에요.", "steps": ["내 규칙 대조"], "tokens": 200, "actions": []}
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


def test_agent_only_proposes_actions(monkeypatch):
    s, _ = _fake_openai(monkeypatch, [("tool", "propose_action", '{"kind": "watch_add", "symbol": "IREN"}'), ("text", "확인 버튼을 누르면 실행돼요.")])
    proposed = {"action": {"kind": "watch_add", "symbol": "IREN", "label": "아이렌(IREN) 관심종목에 추가"}, "status": "아직 실행되지 않았어요"}
    out = agent.chat(s, {"propose_action": lambda **kw: proposed}, [{"role": "user", "content": "아이렌 관심종목에 넣어줘"}], None)
    assert out["actions"] == [proposed["action"]] and out["steps"] == ["할 일 제안"]      # 버튼으로 보여줄 것만 돌려준다


def _main_with_store(monkeypatch, tmp_path):
    from backend import main
    from backend.alerts import Store
    s = replace(get_settings(), data_dir=tmp_path, toss_client_id="", toss_client_secret="")
    monkeypatch.setattr(main, "get_settings", lambda: s)
    monkeypatch.setattr(main, "store", Store(tmp_path))
    candles = [{"time": f"2026-01-{i + 1:02d}", "open": 100, "high": 101, "low": 99, "close": 100.0, "volume": 1.0} for i in range(30)]
    monkeypatch.setattr(main, "load_stock", lambda sym: ({"name": "테스트", "currency": "USD"}, candles, False))
    main.store.set_watchlist(["AAA"])
    return main


def test_actions_change_nothing_until_confirmed(monkeypatch, tmp_path):
    main = _main_with_store(monkeypatch, tmp_path)
    p = main.propose_action("alert_line", "bbb", 95)
    assert p["action"]["label"] == "테스트(BBB) $95.00에 수평선 긋기 (닿으면 알림) + 관심종목에 추가(알림은 관심종목만 와요)"
    assert main.store.watchlist() == ["AAA"] and main.store.drawings("BBB") == []                 # 제안만으로는 그대로
    done = main.agent_act(main.ActBody(kind="alert_line", symbol="BBB", price=95))
    assert done["kind"] == "alert_line" and main.store.watchlist() == ["AAA", "BBB"]
    assert [(d["type"], d["p1"]["price"]) for d in main.store.drawings("BBB")] == [("horizontal", 95.0)]


def test_actions_reject_bad_requests(monkeypatch, tmp_path):
    main = _main_with_store(monkeypatch, tmp_path)
    assert "이미 관심종목" in main.propose_action("watch_add", "AAA")["error"]
    assert "너무 먼 가격" in main.propose_action("alert_line", "AAA", 5000)["error"]
    assert "할 수 없는 일" in main.propose_action("buy_order", "AAA", 1)["error"]                    # 주문은 종류 자체가 없다
    assert "바꿀 규칙 값이 없어요" in main.propose_action("rules_update", changes={"hack": 1})["error"]


def test_rules_update_shows_before_and_after(monkeypatch, tmp_path):
    main = _main_with_store(monkeypatch, tmp_path)
    p = main.propose_action("rules_update", changes={"stop_atr": 1, "min_rr": 2})
    assert p["action"]["label"] == "내 규칙 바꾸기: 손절 기준(ATR 배수) 0.5 → 1, 최소 손익비 1.5 → 2"
    assert main.my_rules()["stop_atr"] == 0.5
    main.agent_act(main.ActBody(kind="rules_update", changes={"stop_atr": 1, "min_rr": 2}))
    assert (main.my_rules()["stop_atr"], main.my_rules()["min_rr"]) == (1.0, 2.0)


def test_agent_stops_after_max_steps(monkeypatch):
    s, sent = _fake_openai(monkeypatch, [("tool", "market_brief", "{}")] * agent.MAX_STEPS)
    out = agent.chat(s, {"market_brief": lambda: {}}, [{"role": "user", "content": "시장"}], None)
    assert len(sent) == agent.MAX_STEPS and "정리하지 못했어요" in out["answer"]
