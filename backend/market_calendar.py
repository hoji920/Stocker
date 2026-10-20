"""증권 달력: 미국 경제지표 발표(CPI·고용·FOMC …)와 실적 발표.

- 경제지표: 나스닥 경제 일정 API (날짜별, 예상치·이전치·발표치). 2주쯤 앞까지 자세히, 그 뒤는 FOMC 같은 큰 일정만 있다.
  시각은 미국 동부 시간으로 와서 한국 시간으로 바꾼다. 미국 것만, 시장이 보는 지표만 고른다 (연설·주택담보대출 지수 같은 건 뺀다).
- 실적: Finnhub 실적 달력 (무료). 내 관심·보유 종목 + 주요 대형주만.
- 날짜별 캐시: 지난 날은 하루, 앞으로의 날은 3시간.
"""
from __future__ import annotations
import html
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

ET, KST = ZoneInfo("America/New_York"), ZoneInfo("Asia/Seoul")
_H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36",
      "Accept": "application/json, text/plain, */*", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}

# (영문 이름에 들어 있는 말, 한국어 이름, 중요도 2=시장이 크게 보는 것 / 1=참고). 위에서부터 먼저 맞는 것
EVENTS = [
    ("Fed Interest Rate Decision", "FOMC 금리 결정", 2), ("FOMC Meeting Minutes", "FOMC 회의록", 2), ("FOMC Press Conference", "FOMC 기자회견", 2),
    ("Core CPI", "근원 소비자물가(CPI)", 2), ("CPI", "소비자물가(CPI)", 2),
    ("Core PPI", "근원 생산자물가(PPI)", 2), ("PPI", "생산자물가(PPI)", 2),
    ("Core PCE Price Index", "근원 PCE 물가", 2), ("PCE Price Index", "PCE 물가", 2),
    ("Nonfarm Payrolls", "비농업 고용", 2), ("Unemployment Rate", "실업률", 2), ("Average Hourly Earnings", "시간당 임금", 2),
    ("GDP", "GDP 성장률", 2), ("Core Retail Sales", "근원 소매판매", 2), ("Retail Sales", "소매판매", 2),
    ("ISM Manufacturing PMI", "ISM 제조업 PMI", 1), ("ISM Non-Manufacturing PMI", "ISM 서비스업 PMI", 1), ("ISM Services PMI", "ISM 서비스업 PMI", 1),
    ("JOLTS Job Openings", "JOLTS 구인", 1), ("ADP Nonfarm Employment", "ADP 민간고용", 1), ("Initial Jobless Claims", "신규 실업수당 청구", 1),
    ("Michigan Consumer Sentiment", "미시간 소비자심리", 1), ("Michigan 1-Year Inflation Expectations", "미시간 1년 기대인플레", 1),
    ("CB Consumer Confidence", "컨퍼런스보드 소비자신뢰", 1), ("Durable Goods Orders", "내구재 주문", 1),
    ("Industrial Production", "산업생산", 1), ("Existing Home Sales", "기존주택 판매", 1), ("New Home Sales", "신규주택 판매", 1),
    ("Building Permits", "건축 허가", 1), ("Housing Starts", "주택 착공", 1), ("Trade Balance", "무역수지", 1),
    ("Philadelphia Fed Manufacturing", "필라델피아 연은 제조업", 1), ("NY Empire State Manufacturing", "뉴욕 연은 제조업", 1),
    ("Crude Oil Inventories", "원유 재고", 1),
]
# 달력 칸에 쓰는 짧은 이름 (중요 지표만)
SHORT = {"FOMC 금리 결정": "FOMC", "FOMC 회의록": "FOMC 회의록", "FOMC 기자회견": "FOMC", "근원 소비자물가(CPI)": "CPI", "소비자물가(CPI)": "CPI",
         "근원 생산자물가(PPI)": "PPI", "생산자물가(PPI)": "PPI", "근원 PCE 물가": "PCE", "PCE 물가": "PCE", "비농업 고용": "고용",
         "실업률": "고용", "시간당 임금": "고용", "GDP 성장률": "GDP", "근원 소매판매": "소매판매", "소매판매": "소매판매"}
SKIP = ("Core Durable", "Durables Excluding", "Goods Orders Non Defense", "4-Week Avg", "Continuing Jobless", "Retail Inventories",
        "Goods Trade Balance", "Retail Control", "GDPNow", "Cleveland", "Nowcast", "Wholesale")

# 실적: 내 종목 말고도 시장 전체가 보는 대형주
MAJOR = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "BRK.B", "JPM", "V", "MA", "LLY", "UNH", "XOM", "WMT",
         "COST", "NFLX", "AMD", "ORCL", "CRM", "ADBE", "INTC", "QCOM", "MU", "PLTR", "BAC", "GS", "DIS", "NKE"]

_day_cache: dict[str, tuple[float, list[dict]]] = {}


def _clean(v) -> str:
    v = html.unescape(str(v or "")).replace("\xa0", " ").strip()
    return "" if v in ("", "-") else v


def _match(name: str):
    if any(s in name for s in SKIP):
        return None
    for key, ko, level in EVENTS:
        if key in name:
            return ko, level
    return None


def econ_day(day: str, today: str) -> list[dict]:
    """그날 미국 경제지표. 같은 지표가 단위만 다르게 여러 줄(전월비·전년비)이면 한 줄로 묶는다."""
    hit = _day_cache.get(day)
    ttl = 86400 if day < today else 3 * 3600
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    # 이 API 는 date=D 로 물으면 D 하루 전 일정을 준다 (date=2026-10-15 → 10/14(수) CPI, date=2026-10-29 → 10/28 FOMC 결정).
    # 그래서 그날 일정은 다음 날 날짜로 묻는다
    ask = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    r = httpx.get("https://api.nasdaq.com/api/calendar/economicevents", params={"date": ask}, headers=_H, timeout=15)
    r.raise_for_status()
    rows = ((r.json() or {}).get("data") or {}).get("rows") or []
    out: dict[tuple, dict] = {}
    for x in rows:
        if x.get("country") != "United States":
            continue
        m = _match(x.get("eventName", ""))
        if not m:
            continue
        ko, level = m
        t = _clean(x.get("gmt"))
        kst, kst_date, kst_time = "", day, ""
        if re.fullmatch(r"\d{1,2}:\d{2}", t):                    # 이 API 의 시각은 미국 동부 시간이다 (CPI 08:30, FOMC 14:00)
            hh, mm = map(int, t.split(":"))
            dt = datetime.fromisoformat(day).replace(hour=hh, minute=mm, tzinfo=ET).astimezone(KST)
            kst, kst_date, kst_time = dt.strftime("%m-%d %H:%M"), dt.date().isoformat(), dt.strftime("%H:%M")
        key = (ko, t)
        e = out.setdefault(key, {"name": ko, "short": SHORT.get(ko, ko), "name_en": x.get("eventName"), "level": level, "time_et": t,
                                 "time_kst": kst, "kst_date": kst_date, "kst_time": kst_time, "values": []})
        val = {"actual": _clean(x.get("actual")), "consensus": _clean(x.get("consensus")), "previous": _clean(x.get("previous"))}
        if any(val.values()) and val not in e["values"]:
            e["values"].append(val)
    for e in out.values():                                        # 같은 지표의 지수 값(338.90 같은 것)은 빼고 % 두 개(전월비·전년비)까지만
        pcts = [v for v in e["values"] if any("%" in x for x in v.values())]
        e["values"] = (pcts or e["values"])[:2]
    events = sorted(out.values(), key=lambda e: (e["time_et"] or "99", -e["level"]))
    _day_cache[day] = (time.time(), events)
    return events


def earnings(start: str, end: str, token: str, mine: list[str]) -> dict[str, list[dict]]:
    """날짜 → 실적 발표 목록 (내 종목 먼저). hour: bmo=장 시작 전, amc=장 마감 후.
    Finnhub 은 한 번에 1,500건까지만 줘서(실적 시즌엔 한 주에 800건) 일주일씩 나눠 받는다.
    같은 종목이 날짜만 다르게 두 번 오는 일이 있다 (옛 예정일이 안 지워짐) → 먼저 온 것 하나만."""
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    weeks = [(a + timedelta(7 * k), min(b, a + timedelta(7 * k + 6))) for k in range((b - a).days // 7 + 1) if a + timedelta(7 * k) <= b]

    def get(w):
        r = httpx.get("https://finnhub.io/api/v1/calendar/earnings", params={"from": w[0].isoformat(), "to": w[1].isoformat(), "token": token},
                      timeout=20)
        r.raise_for_status()
        return r.json().get("earningsCalendar") or []

    with ThreadPoolExecutor(4) as ex:
        rows = [x for part in ex.map(get, weeks) for x in part]
    want, mine_set, seen = set(mine) | set(MAJOR), set(mine), set()
    out: dict[str, list[dict]] = {}
    for x in rows:
        s = x.get("symbol")
        if s not in want or s in seen:
            continue
        seen.add(s)
        out.setdefault(x["date"], []).append({
            "symbol": s, "mine": s in mine_set,
            "when": {"bmo": "장 전", "amc": "장 후", "dmh": "장중"}.get(x.get("hour") or "", ""),
            "eps_estimate": x.get("epsEstimate"), "eps_actual": x.get("epsActual"),
            "revenue_estimate": x.get("revenueEstimate"), "revenue_actual": x.get("revenueActual"),
            "quarter": f"{x.get('year')} Q{x.get('quarter')}" if x.get("quarter") else ""})
    for v in out.values():
        v.sort(key=lambda e: (not e["mine"], e["symbol"]))
    return out


def build(start: date, days: int, token: str, mine: list[str]) -> dict:
    """start 부터 days 일, **한국 날짜** 기준으로 묶는다 (주말 포함: 미국 금요일 장 후 실적은 한국 토요일 새벽).
    경제지표는 한국 시각으로 바꾼 날짜에, 실적은 장 후면 다음 날(한국 새벽)에, 장 전이면 같은 날(한국 밤)에 둔다."""
    today = datetime.now(ET).date().isoformat()
    span = [start + timedelta(d) for d in range(days)]
    weekdays = [d.isoformat() for d in span if d.weekday() < 5]
    errors = []

    def one(day):
        try:
            return day, econ_day(day, today)
        except Exception as error:
            errors.append(f"{day} 경제지표: {type(error).__name__}")
            return day, []

    with ThreadPoolExecutor(6) as ex:
        econ = dict(ex.map(one, weekdays))
    try:
        earn = earnings(span[0].isoformat(), span[-1].isoformat(), token, mine) if token else {}
    except Exception as error:
        earn, errors = {}, errors + [f"실적: {type(error).__name__}"]
    by: dict[str, dict] = {}
    slot = lambda d: by.setdefault(d, {"econ": [], "earnings": []})
    for us, evs in econ.items():
        for e in evs:
            slot(e.get("kst_date") or us)["econ"].append({**e, "us_date": us})
    nxt = lambda d: (date.fromisoformat(d) + timedelta(days=1)).isoformat()
    for us, es in earn.items():
        for x in es:
            after = x["when"] == "장 후"
            note = {"장 후": "미국 장 마감 후 → 한국 새벽", "장 전": "미국 장 시작 전 → 한국 밤"}.get(x["when"], "시각 미정 (미국 날짜)")
            slot(nxt(us) if after else us)["earnings"].append({**x, "us_date": us, "kst_note": note})
    for v in by.values():
        v["econ"].sort(key=lambda e: (e["kst_time"] or "99", -e["level"]))
        v["earnings"].sort(key=lambda e: (not e["mine"], e["symbol"]))
    kst_today = datetime.now(KST).date().isoformat()
    span_kst = [start + timedelta(d) for d in range(days + 1)]
    return {"today": kst_today, "errors": errors[:5], "mine": mine, "timezone": "KST",
            "days": [{"date": d.isoformat(), "weekday": "월화수목금토일"[d.weekday()], **by.get(d.isoformat(), {"econ": [], "earnings": []})}
                     for d in span_kst]}
