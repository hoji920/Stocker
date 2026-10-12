"""시장(매크로) 요약: '어떤 소식이 시장을 움직였나'(뉴스) + 시장 탭의 숫자(지수·금리·원자재·환율·코인) 3줄.

- 소식은 주어진 기사 안에서만 찾고, 근거 기사를 반드시 단다. 근거가 없으면 그 항목은 버린다.
- 숫자는 주어진 것만 쓴다 (LLM 이 숫자를 지어내지 않게, 화면에도 같은 숫자가 바로 아래 있다).
- 매수/매도 권유·전망 금지. 공부용 읽기 도구.

기사: 네이버 '뉴욕증시 마감' 기사(이유가 제목·요약에 들어 있다) + Finnhub 시장 속보(로이터·CNBC).
야후 파이낸스(yfinance)는 뉴스가 빈 목록으로 와서 못 쓴다.
"""
from __future__ import annotations
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from backend import news
from backend.config import Settings

MOODS = ("위험 선호", "중립", "위험 회피")
EFFORT = "low"

_SYSTEM = (
    "너는 주식 초보자에게 '미국 시장이 왜 이렇게 움직였는지'를 정리해주는 도우미다. 한국어 정중체, 쉬운 말로 쓴다.\n"
    "대상은 미국 시장(뉴욕증시·미국 국채·달러·원자재)이다. 코스피·코스닥 같은 한국 증시 얘기는 쓰지 않는다.\n"
    "규칙:\n"
    "1) drivers: 시장을 움직인 소식 2~4개, 영향이 큰 순서로. 반드시 주어진 기사 안에서만 찾는다.\n"
    "   - 서로 다른 원인은 나눠 쓴다. 기사 제목이 '기술주 반등·경유 공급 합의에 상승'이면 기술주 반등과 경유 공급 합의는 "
    "각각 다른 항목이다. 한 항목에 뭉치지 않는다.\n"
    "   - what: 무슨 일이 있었는지 한 문장. 기사에 그 일이 생긴 까닭이 있으면 같이 쓴다 "
    "(예: '오픈AI 매출 우려가 누그러지며 전날 급락한 기술주에 반발 매수가 들어왔어요').\n"
    "   - effect: 그래서 시장의 무엇이 어떻게 움직였는지 한 문장 (주식·금리·유가·달러 중 해당하는 것). 표나 기사에 있는 숫자를 쓴다.\n"
    "   - sources: 근거가 된 기사 번호 1~3개. 근거 기사가 없는 소식은 쓰지 않는다.\n"
    "   - after_close: 표의 미국 지수 기준일 장이 끝난 뒤에 나온 소식이면 true (아직 지수에 반영되지 않은 주말·밤사이 소식). 아니면 false.\n"
    "   - 기준일 장의 소식을 먼저, 마감 뒤 소식은 시장에 영향을 줄 만한 것만 뒤에. 같은 소식은 하나로 묶는다. 개별 종목 한두 개의 얘기는 뺀다.\n"
    "   - 기사가 한국 증시를 같이 다뤄도 미국 시장에 해당하는 내용만 쓴다.\n"
    "   - 시장을 설명하는 기사가 없으면 drivers 는 빈 배열.\n"
    "2) lines 는 정확히 3줄, 각 줄은 짧은 한 문장:\n"
    "   ① 주식: 나스닥 종합·S&P 500 과 VIX. 선물의 기준일이 지수보다 뒤면 '지금 선물은 …'으로 덧붙인다.\n"
    "   ② 금리·달러: 10년 금리, 달러인덱스, 원/달러.\n"
    "   ③ 원자재·코인: WTI 원유, 금, 비트코인.\n"
    "   각 줄에서 가장 크게 움직인 것 위주로 숫자 2~3개를 넣는다. 표에 없는 항목은 건너뛴다. 이유는 쓰지 않는다 (drivers 에 쓴다).\n"
    "3) 숫자는 주어진 표나 기사에 있는 것만 쓴다. 금리는 bp(0.01%p)로, 나머지는 % 로 말한다. "
    "VIX 는 오르면 불안이 커진 것, 내리면 잦아든 것이라고 풀어 쓴다.\n"
    "4) headline 은 주어진 '분위기'와 어긋나지 않게, 가장 큰 이유와 움직임을 한 문장으로.\n"
    "5) 매수/매도 권유, 앞으로의 전망은 쓰지 않는다.\n"
    'JSON 으로만 답한다: {"headline": "...", "drivers": [{"what": "...", "effect": "...", "sources": [1, 2], "after_close": false}], '
    '"lines": ["...", "...", "..."]}'
)

_US_TITLE = re.compile(r"뉴욕\s?증시|뉴욕\s?마감|월가|[美미]\s?증시")
# 네이버에서 '뉴욕증시'로 찾으면 한국 장 기사가 많이 섞인다 ('뉴욕증시 올랐지만 코스피는…') → 제목이 한국 시장 얘기면 뺀다
_KR_TITLE = re.compile(r"코스피|코스닥|국내\s?증시|한국\s?증시|삼성전자|하이닉스|삼전|개미|특징주|마켓뷰|오늘의\s?증시")
US_GROUPS_OFF = ("국내",)          # 미국 시장 요약이라 표에서도 뺀다


def headlines(settings: Settings, since: str | None, limit_kr: int = 10, limit_en: int = 8) -> list[news.NewsItem]:
    """미국 시장 요약의 재료가 될 기사. since(미국 지수 기준일) 장을 다룬 것만 — 그 전 기사는 다른 날 장 얘기다.
    한국 기사는 미국 장이 끝난 뒤(한국 날짜로 다음 날)에 나오므로 기준일 '다음 날'부터, 영어 속보는 기준일부터.
    한쪽이 막혀도 다른 쪽으로 만든다."""
    since = since or (date.today() - timedelta(days=3)).isoformat()
    seen, found = set(), []
    for it in news._safe(news.fetch_kr_news, settings, "뉴욕증시 마감", limit=40, sort="sim"):
        key = it.title.replace(" ", "")
        if it.date >= since and _US_TITLE.search(it.title) and not _KR_TITLE.search(it.title) and key not in seen:
            seen.add(key)
            found.append(it)
    # 미국 장이 열려 있는 동안엔 그 장의 마감 기사가 아직 없다 → 그땐 기준일 당일 기사(직전 장 얘기)라도 쓴다
    kr = [it for it in found if it.date > since] or found
    en = [it for it in news._safe(news.fetch_market_news, settings, "general", None, limit=30, days=4) if it.date >= since]
    return kr[:limit_kr] + en[:limit_en]


def _find(groups: list[dict], symbol: str) -> dict | None:
    return next((it for g in groups for it in g["items"] if it["symbol"] == symbol and not it.get("error")), None)


def mood(groups: list[dict]) -> str:
    """분위기는 LLM 에 맡기지 않고 숫자로: S&P 500 이 0.3% 넘게 오르고 VIX 가 내리면 위험 선호, 반대면 위험 회피, 나머지는 중립."""
    spx, vix = _find(groups, "^GSPC"), _find(groups, "^VIX")
    if not spx:
        return "중립"
    vix_chg = vix["change_pct"] if vix else 0.0
    if spx["change_pct"] > 0.3 and vix_chg <= 0:
        return "위험 선호"
    if spx["change_pct"] < -0.3 and vix_chg >= 0:
        return "위험 회피"
    return "중립"


def table(groups: list[dict]) -> str:
    """LLM 에 줄 숫자 표. 금리는 bp, 나머지는 %. 묶음마다 기준일이 다르다 (지수는 마지막 장 종가, 선물·환율·코인은 지금)."""
    out = []
    for g in groups:
        if g["group"] in US_GROUPS_OFF:
            continue
        cells, dates = [], []
        for it in g["items"]:
            if it.get("error"):
                continue
            chg = f"{it['change_bp']:+.1f}bp" if it["unit"] == "pct" else f"{it['change_pct']:+.2f}%"
            last = f"{it['last']:.3f}%" if it["unit"] == "pct" else f"{it['last']:,.2f}"
            cells.append(f"{it['name']} {last} ({chg})")
            dates.append(it["date"])
        if cells:
            out.append(f"[{g['group']} · {max(dates)} 기준] " + ", ".join(cells))
    return "\n".join(out)


KST = ZoneInfo("Asia/Seoul")
DAY_STARTS_AT = 7          # 한국 시간 아침 7시에 '오늘'이 바뀐다: 미국 장이 끝나고(5~6시) 마감 기사가 나온 뒤


def brief_day(now: datetime | None = None) -> str:
    """요약은 하루 한 번만 만든다. 그 '하루'의 이름 (한국 시간 07시 ~ 다음 날 07시)."""
    now = now or datetime.now(KST)
    return (now - timedelta(hours=DAY_STARTS_AT)).date().isoformat()


def load_saved(data_dir: Path, day: str) -> dict | None:
    """오늘 이미 만든 요약 (서버를 껐다 켜도 다시 만들지 않게 파일에 둔다)."""
    try:
        saved = json.loads((data_dir / "market_brief.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return saved.get("summary") if saved.get("day") == day else None


def save(data_dir: Path, day: str, summary: dict) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "market_brief.json").write_text(json.dumps({"day": day, "summary": summary}, ensure_ascii=False), encoding="utf-8")


def as_of(groups: list[dict]) -> str | None:
    """미국 지수 기준일 (마지막으로 끝난 장). 화면에 '미국 지수 ○○ 종가 기준'으로, 기사도 이날 이후 것만 쓴다."""
    spx = _find(groups, "^GSPC")
    return spx["date"] if spx else None


def _drivers(raw, items: list[news.NewsItem]) -> list[dict]:
    """LLM 이 준 '시장을 움직인 소식'에서 근거 기사가 실제로 있는 것만 남긴다."""
    out = []
    for d in raw if isinstance(raw, list) else []:
        if not isinstance(d, dict) or not str(d.get("what", "")).strip():
            continue
        idx = [i for i in d.get("sources", []) if isinstance(i, int) and 1 <= i <= len(items)]
        if not idx:
            continue
        out.append({"what": str(d["what"]).strip(), "effect": str(d.get("effect", "")).strip(),
                    "after_close": d.get("after_close") is True,
                    "sources": [{"title": items[i - 1].title, "url": items[i - 1].url, "source": items[i - 1].source,
                                 "date": items[i - 1].date} for i in dict.fromkeys(idx[:3])]})
    return out[:4]


def build(settings: Settings, groups: list[dict], items: list[news.NewsItem]) -> dict | None:
    """{mood, headline, drivers, lines, as_of, made_at}. 키가 없으면 None, 실패하면 예외 (실패한 걸 저장하지 않으려고)."""
    if not settings.summary_enabled:
        return None
    from openai import OpenAI
    block = "\n".join(f"[{i}] ({it.date}, {it.source}) {it.title}\n    {it.summary[:260]}"
                      for i, it in enumerate(items, 1)) or "(없음)"
    now = mood(groups)
    model = settings.openai_brief_model
    # gpt-5 계열은 temperature 를 못 바꾸고, 답하기 전에 '생각'하는 데 출력 토큰을 쓴다 → 생각을 적게 (기본값이면 출력의 대부분이 생각)
    extra = {"temperature": 0.2} if model.startswith("gpt-4") else {"reasoning_effort": EFFORT}
    resp = OpenAI(api_key=settings.openai_api_key).chat.completions.create(
        model=model, response_format={"type": "json_object"}, **extra,
        messages=[{"role": "system", "content": _SYSTEM},
                  {"role": "user", "content": f"분위기: {now}\n\n전일 대비 움직임:\n{table(groups)}\n\n기사 {len(items)}건:\n{block}"}],
    )
    data = json.loads(resp.choices[0].message.content or "{}")
    lines = [str(l).strip() for l in data.get("lines", []) if str(l).strip()][:3]
    if not lines:
        raise ValueError("요약이 비어서 왔어요")
    return {"mood": now, "headline": str(data.get("headline", "")).strip(), "drivers": _drivers(data.get("drivers"), items),
            "lines": lines, "as_of": as_of(groups), "made_at": datetime.now(KST).strftime("%m-%d %H:%M")}
