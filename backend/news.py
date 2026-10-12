"""종목 뉴스 수집 + LLM 요약.

- 국내: 네이버 뉴스 검색 API (종목명으로 검색)
- 미국: Finnhub company-news (yfinance 뉴스는 2026-10 기준 빈 리스트만 와서 교체)
- 요약: OpenAI. 기사별 호재/악재/중립 태그 + 3줄 요약. 키 없으면 요약 생략.
"""
from __future__ import annotations
import html
import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

from backend.config import Settings


@dataclass
class NewsItem:
    title: str
    summary: str
    date: str          # YYYY-MM-DD
    source: str
    url: str
    sentiment: str = "중립"
    lang: str = "en"   # ko = 한국어 기사(네이버), en = 영어 기사(Finnhub) → 화면에서 탭으로 나눈다


_TAG = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    return html.unescape(_TAG.sub("", text or "")).strip()


def fetch_kr_news(settings: Settings, query: str, limit: int = 15, sort: str = "date") -> list[NewsItem]:
    if not settings.naver_enabled:
        return []
    # 키 발급처가 두 곳이고 주소·헤더만 다르다 (응답 형식은 같다): 네이버 클라우드 NAVER API HUB(Secret 40자) / 네이버 개발자센터(10자)
    if len(settings.naver_client_secret) > 20:
        url = "https://naverapihub.apigw.ntruss.com/search/v1/news"
        headers = {"X-NCP-APIGW-API-KEY-ID": settings.naver_client_id, "X-NCP-APIGW-API-KEY": settings.naver_client_secret}
    else:
        url = "https://openapi.naver.com/v1/search/news.json"
        headers = {"X-Naver-Client-Id": settings.naver_client_id, "X-Naver-Client-Secret": settings.naver_client_secret}
    resp = httpx.get(url, params={"query": query, "display": limit, "sort": sort}, headers=headers, timeout=10.0)
    resp.raise_for_status()
    items = []
    for it in resp.json().get("items", []):
        link = it.get("originallink") or it.get("link", "")
        items.append(NewsItem(
            title=_clean(it.get("title", "")),
            summary=_clean(it.get("description", "")),
            date=parsedate_to_datetime(it["pubDate"]).date().isoformat(),
            source=re.sub(r"^https?://(www\.)?([^/]+).*$", r"\2", link),
            url=link,
            lang="ko",
        ))
    return items


def fetch_us_news(settings: Settings, symbol: str, limit: int = 15, days: int = 30) -> list[NewsItem]:
    if not settings.finnhub_enabled:
        return []
    today = date.today()
    resp = httpx.get(
        "https://finnhub.io/api/v1/company-news",
        params={"symbol": symbol, "from": (today - timedelta(days=days)).isoformat(), "to": today.isoformat()},
        headers={"X-Finnhub-Token": settings.finnhub_api_key},
        timeout=10.0,
    )
    resp.raise_for_status()
    raw = sorted(resp.json(), key=lambda it: it.get("datetime", 0), reverse=True)
    return [
        NewsItem(
            title=it["headline"],
            summary=it.get("summary", ""),
            date=datetime.fromtimestamp(it["datetime"], tz=timezone.utc).date().isoformat(),
            source=it.get("source", ""),
            url=it.get("url", ""),
        )
        for it in raw[:limit] if it.get("headline")
    ]


def fetch_market_news(settings: Settings, category: str, keywords: list[str] | None, limit: int = 10,
                      days: int = 3) -> list[NewsItem]:
    """Finnhub 시장 뉴스(general / crypto) 중 키워드가 들어간 것만 (ETF 기초자산 뉴스용). keywords 가 없으면 전부 (시장 요약용)."""
    if not settings.finnhub_enabled:
        return []
    resp = httpx.get("https://finnhub.io/api/v1/news", params={"category": category},
                     headers={"X-Finnhub-Token": settings.finnhub_api_key}, timeout=10.0)
    resp.raise_for_status()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    out = []
    for it in sorted(resp.json(), key=lambda x: x.get("datetime", 0), reverse=True):
        text = f"{it.get('headline', '')} {it.get('summary', '')}".lower()
        if it.get("datetime", 0) < cutoff or (keywords and not any(k in text for k in keywords)):
            continue
        out.append(NewsItem(title=it["headline"], summary=it.get("summary", ""),
                            date=datetime.fromtimestamp(it["datetime"], tz=timezone.utc).date().isoformat(),
                            source=it.get("source", ""), url=it.get("url", "")))
        if len(out) >= limit:
            break
    return out


_SYSTEM = (
    "너는 주식 초보자를 돕는 뉴스 정리 도우미다. 주어진 기사만 근거로 한국어로 답한다.\n"
    "원칙: 없는 사실 창작 금지. 매수/매도 권유 금지. 쉬운 말로, 전문용어는 풀어서.\n"
    "JSON 으로만 답한다: {\"lines\": [3줄 요약 문자열 3개], "
    "\"tone\": \"긍정|중립|부정\", \"sentiments\": [기사 번호 순서대로 \"호재|악재|중립\"]}"
)

# 거르기는 요약과 따로 묻는다 (한 번에 시키면 거의 안 거른다)
_FILTER_SYSTEM = (
    "너는 주식 뉴스 편집자다. 종목 하나와 기사 목록을 받아, 그 종목(회사)의 주가를 움직일 만한 내용인지 기사마다 판정한다.\n"
    "남긴다: 그 회사에 생긴 일이다 — 실적·제품·투자·경영진·규제·소송·증권가 평가·주가 움직임과 그 이유. "
    "다른 회사와 맺은 계약·제휴·인수·공급도 그 회사에 생긴 일이다 "
    "(예: 종목이 '아이렌'일 때 '앤트로픽, 아이렌과 클라우드 계약'은 아이렌이 계약 당사자이므로 남긴다).\n"
    "뺀다: 회사 이름이 스치듯 나올 뿐 그 회사에 생긴 일이 아니다 — 연예·생활·사건사고 기사, 할인 행사나 앱·게임 순위 목록, "
    "그 회사와 상관없는 다른 회사 분석 기사, 이 종목 얘기가 없는 일반 투자 칼럼·종목 추천 목록.\n"
    "확실히 무관할 때만 뺀다. 애매하면 남긴다.\n"
    "JSON 으로만 답한다: {\"drop\": [{\"n\": 뺄 기사 번호, \"why\": \"뺀 이유 한 마디\"}]}  (뺄 게 없으면 빈 배열)"
)


def filter_relevant(settings: Settings, name: str, symbol: str, items: list[NewsItem]) -> list[NewsItem]:
    """이 종목 주가와 무관한 기사를 AI 로 뺀다. 키가 없거나 실패하면 전부 그대로 둔다."""
    if not settings.summary_enabled or not items:
        return items
    from openai import OpenAI
    block = "\n".join(f"[{i}] {it.title} — {it.summary[:120]}" for i, it in enumerate(items, 1))
    try:
        resp = OpenAI(api_key=settings.openai_api_key).chat.completions.create(
            model=settings.openai_model, temperature=0, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": _FILTER_SYSTEM},
                      {"role": "user", "content": f"종목: {name} ({symbol})\n기사 {len(items)}건:\n{block}"}],
        )
        drop = {int(d["n"]) for d in json.loads(resp.choices[0].message.content or "{}").get("drop", [])}
    except Exception as error:
        print(f"[news] 관련 기사 거르기 실패: {type(error).__name__}")
        return items
    return [it for i, it in enumerate(items, 1) if i not in drop]


def summarize(settings: Settings, name: str, items: list[NewsItem]) -> dict | None:
    if not settings.summary_enabled or not items:
        return None
    from openai import OpenAI
    block = "\n".join(f"[{i}] ({it.date}, {it.source}) {it.title}\n    {it.summary[:300]}"
                      for i, it in enumerate(items, 1))
    try:
        resp = OpenAI(api_key=settings.openai_api_key).chat.completions.create(
            model=settings.openai_model,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": _SYSTEM},
                      {"role": "user", "content": f"종목: {name}\n기사 {len(items)}건:\n{block}"}],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
    except Exception as error:
        return {"lines": [f"요약 실패 ({type(error).__name__})"], "tone": "중립"}
    for it, s in zip(items, data.get("sentiments", [])):
        if s in ("호재", "악재", "중립"):
            it.sentiment = s
    return {"lines": data.get("lines", [])[:3], "tone": data.get("tone", "중립")}


def fetch_kr_news_about(settings: Settings, name: str, limit: int = 8, days: int = 14) -> list[NewsItem]:
    """미국 종목의 한국어 기사: 한글 종목명으로 관련도순 검색, 최근 days 일 안에서 제목에 종목명이 들어간 것만.
    - 종목명만 검색 + '종목명 주가' 검색을 합친다 ('주가'만 붙이면 계약·실적 같은 진짜 뉴스가 빠지고 시세 기사만 남는다)
    - 최신순으로 찾으면 '인텔' → '무인텔', '애플' → 앱스토어 순위 기사처럼 주식과 무관한 기사가 섞여서 관련도순으로
    - '○○ 주가, 40.48달러 3.07% 하락 마감' 같은 자동 시세 기사는 뺀다 (앱에 이미 있는 숫자)"""
    if not name or not re.search(r"[가-힣]", name):
        return []
    key = re.compile(r"(?<![가-힣])" + re.escape(name.replace(" ", "")), re.I)     # 앞에 한글이 붙으면 다른 낱말
    recap = re.compile(r"주가,.*(상승|하락|보합)마감$")
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    found = fetch_kr_news(settings, name, limit=100, sort="sim") + _safe(fetch_kr_news, settings, f"{name} 주가", limit=100, sort="sim")
    seen, out = set(), []
    for it in found:
        title = it.title.replace(" ", "")
        if it.date >= cutoff and key.search(title) and not recap.search(title) and title not in seen:   # 같은 제목의 재전송 기사는 한 번만
            seen.add(title)
            out.append(it)
    out.sort(key=lambda it: it.date, reverse=True)
    return out[:limit]


def _safe(fn, *args, **kwargs) -> list[NewsItem]:
    try:
        return fn(*args, **kwargs)
    except Exception as error:                               # 한쪽 뉴스가 막혀도 다른 쪽은 보여준다
        print(f"[news] {fn.__name__}: {type(error).__name__}: {error}")
        return []


def get_news(settings: Settings, market: str, symbol: str, name: str, ai: bool = True) -> dict:
    """ai=False 면 기사 목록만 (AI 를 안 부른다). 차트를 열 때마다 AI 를 부르면 토큰이 많이 들어서,
    거르기·요약은 뉴스 탭을 열 때만 한다 → ai_pending 이 True 면 화면이 ai=True 로 다시 부른다."""
    if market == "KR":
        items = fetch_kr_news(settings, name)
    else:                                                    # 미국 종목: 영어(Finnhub) + 한국어(네이버), 최신순
        items = _safe(fetch_us_news, settings, symbol, limit=12) + _safe(fetch_kr_news_about, settings, name, limit=12)
        items.sort(key=lambda it: it.date, reverse=True)
    summary = None
    if ai:
        items = filter_relevant(settings, name, symbol, items)      # 이 종목 주가와 무관한 기사는 뺀 뒤에 요약
        summary = summarize(settings, name, items)
    return {
        "items": [asdict(it) for it in items],
        "summary": summary,
        "source_enabled": settings.naver_enabled if market == "KR" else (settings.finnhub_enabled or settings.naver_enabled),
        "summary_enabled": settings.summary_enabled,
        "ai_pending": not ai and settings.summary_enabled and bool(items),
    }
