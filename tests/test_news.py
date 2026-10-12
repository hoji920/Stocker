from dataclasses import replace
from datetime import date

from backend import news
from backend.config import get_settings


def _item(title, lang="ko"):
    return news.NewsItem(title=title, summary="", date=date.today().isoformat(), source="x", url=title, lang=lang)


def test_kr_news_about_keeps_only_titles_naming_the_stock(monkeypatch):
    found = [_item("머스크, TSMC와 협력 논의…인텔 주가 이틀간 5.7% 하락"),
             _item("초등생 혼숙 방치 무인텔 업주 입건"),                      # 앞에 한글이 붙은 다른 낱말
             _item("인텔 주가, 40.48달러 3.07% 하락 마감"),                    # 자동 시세 기사
             _item("반도체 업황 점검")]                                        # 제목에 종목명 없음
    monkeypatch.setattr(news, "fetch_kr_news", lambda *a, **k: list(found))
    titles = [it.title for it in news.fetch_kr_news_about(get_settings(), "인텔")]
    assert titles == ["머스크, TSMC와 협력 논의…인텔 주가 이틀간 5.7% 하락"]     # 두 검색에 다 나와도 한 번만


def test_get_news_drops_articles_ai_marked_irrelevant(monkeypatch):
    en, ko, gossip = _item("Tesla deliveries beat", "en"), _item("테슬라 PER 343배"), _item("테슬라 다니는 남친이랑 안 헤어졌네")
    monkeypatch.setattr(news, "fetch_us_news", lambda *a, **k: [en])
    monkeypatch.setattr(news, "fetch_kr_news_about", lambda *a, **k: [ko, gossip])

    monkeypatch.setattr(news, "filter_relevant", lambda s, name, symbol, items: [it for it in items if it is not gossip])
    summarized = []
    monkeypatch.setattr(news, "summarize", lambda s, name, items: summarized.extend(items) or {"lines": [], "tone": "중립"})
    out = news.get_news(get_settings(), "US", "TSLA", "테슬라")
    assert [(n["title"], n["lang"]) for n in out["items"]] == [(en.title, "en"), (ko.title, "ko")]
    assert gossip not in summarized                                            # 뺀 기사는 요약에도 안 들어간다


def _fake_openai(monkeypatch, content):
    import openai

    class FakeClient:
        def __init__(self, **_):
            reply = type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": content})})]})
            self.chat = type("Chat", (), {"completions": type("Comp", (), {"create": staticmethod(lambda **k: reply)})})
    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    return replace(get_settings(), openai_api_key="k")


def test_filter_drops_the_numbered_articles(monkeypatch):
    s = _fake_openai(monkeypatch, '{"drop": [{"n": 2, "why": "연예 기사"}]}')
    a, b, c = _item("a"), _item("b"), _item("c")
    assert news.filter_relevant(s, "테슬라", "TSLA", [a, b, c]) == [a, c]


def test_filter_keeps_all_when_answer_is_broken(monkeypatch):
    s = _fake_openai(monkeypatch, "not json")
    items = [_item("a"), _item("b")]
    assert news.filter_relevant(s, "테슬라", "TSLA", items) == items


def test_get_news_keeps_everything_without_summary(monkeypatch):
    monkeypatch.setattr(news, "fetch_us_news", lambda *a, **k: [_item("a", "en")])
    monkeypatch.setattr(news, "fetch_kr_news_about", lambda *a, **k: [_item("b")])
    s = replace(get_settings(), openai_api_key="")
    assert len(news.get_news(s, "US", "TSLA", "테슬라")["items"]) == 2
