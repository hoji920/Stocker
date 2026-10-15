"""회사 정보: 어떤 섹터·업종의 어떤 회사(또는 ETF)인지.

- 숫자·분류·영문 사업 설명은 야후 파이낸스 프로필(quoteSummary)에서 받는다 (토스 Open API 에는 섹터·사업 설명이 없다).
- 사업 설명은 영어라서 LLM 이 한국어 2~3문장으로 줄인다. 받은 설명 안에서만 쓰고, 전망·평가는 쓰지 않는다.
- 회사 정보는 거의 안 바뀌니까 data/company/{종목}.json 에 30일 동안 둔다 (종목당 AI 호출은 한 달에 한 번).
"""
from __future__ import annotations
import json
import time
from pathlib import Path

import httpx

from backend.config import Settings, llm_args

TTL = 30 * 86400
_UA = {"User-Agent": "Mozilla/5.0"}
_crumb: dict = {}

# 야후 섹터 → 한국어 (업종은 LLM 이 옮긴다)
SECTORS = {"Technology": "기술", "Healthcare": "헬스케어", "Financial Services": "금융", "Consumer Cyclical": "경기소비재",
           "Consumer Defensive": "필수소비재", "Communication Services": "커뮤니케이션", "Industrials": "산업재",
           "Energy": "에너지", "Utilities": "유틸리티", "Real Estate": "부동산", "Basic Materials": "소재"}


def _client() -> tuple[httpx.Client, str]:
    """야후는 쿠키 + crumb 이 있어야 프로필을 준다. 한 시간 동안 같은 걸 쓴다."""
    if not _crumb or time.time() - _crumb["at"] > 3600:
        c = httpx.Client(headers=_UA, timeout=10, follow_redirects=True)
        c.get("https://fc.yahoo.com")
        _crumb.update(client=c, crumb=c.get("https://query1.finance.yahoo.com/v1/test/getcrumb").text.strip(), at=time.time())
    return _crumb["client"], _crumb["crumb"]


def fetch_yahoo(symbol: str) -> dict:
    c, crumb = _client()
    r = c.get(f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}",
              params={"modules": "assetProfile,quoteType,fundProfile,price", "crumb": crumb})
    r.raise_for_status()
    res = (r.json().get("quoteSummary", {}).get("result") or [{}])[0]
    ap, qt, fp, pr = (res.get(k) or {} for k in ("assetProfile", "quoteType", "fundProfile", "price"))
    raw = lambda d, k: (d.get(k) or {}).get("raw") if isinstance(d.get(k), dict) else d.get(k)
    return {
        "type": qt.get("quoteType"),                                  # EQUITY / ETF
        "sector": ap.get("sector"), "industry": ap.get("industry"),
        "country": ap.get("country"), "city": ap.get("city"), "state": ap.get("state"),
        "employees": ap.get("fullTimeEmployees"), "website": ap.get("website"),
        "summary_en": ap.get("longBusinessSummary") or "",
        "fund_family": fp.get("family"), "fund_category": fp.get("categoryName"),
        "expense_ratio": raw((fp.get("feesExpensesInvestment") or {}), "annualReportExpenseRatio"),
        "market_cap": raw(pr, "marketCap"),
    }


_fund: dict[str, tuple[float, dict]] = {}


def fundamentals(symbol: str) -> dict:
    """에이전트가 종합 판단할 때 보는 펀더멘탈 (야후, 6시간 캐시). 숫자는 야후가 준 그대로, 없는 건 빼고 준다.
    주식: 가치(PER·PEG·EV/EBITDA), 성장(매출·이익), 수익성(마진), 재무(부채·현금흐름), 애널리스트(목표가·의견), 다음 실적 발표일.
    ETF: 운용사·분류·보수·순자산."""
    hit = _fund.get(symbol)
    if hit and time.time() - hit[0] < 6 * 3600:
        return hit[1]
    c, crumb = _client()
    r = c.get(f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}",
              params={"modules": "quoteType,financialData,defaultKeyStatistics,summaryDetail,calendarEvents,fundProfile", "crumb": crumb})
    r.raise_for_status()
    res = (r.json().get("quoteSummary", {}).get("result") or [{}])[0]
    fd, ks, sd, ce, fp = (res.get(k) or {} for k in ("financialData", "defaultKeyStatistics", "summaryDetail", "calendarEvents", "fundProfile"))
    raw = lambda d, k: (d.get(k) or {}).get("raw") if isinstance(d.get(k), dict) else d.get(k)
    pct = lambda v: round(v * 100, 1) if isinstance(v, (int, float)) else None
    num = lambda v, n=2: round(v, n) if isinstance(v, (int, float)) else None
    kind = (res.get("quoteType") or {}).get("quoteType")
    if kind == "ETF":
        out = {"type": "ETF", "fund_family": fp.get("family"), "category": fp.get("categoryName"),
               "expense_ratio_pct": pct(raw(fp.get("feesExpensesInvestment") or {}, "annualReportExpenseRatio")),
               "total_assets": raw(sd, "totalAssets"), "beta_3y": num(raw(ks, "beta3Year"))}
    else:
        dates = [d.get("fmt") for d in ((ce.get("earnings") or {}).get("earningsDate") or []) if isinstance(d, dict)]
        out = {
            "type": kind, "market_cap": raw(sd, "marketCap"),
            "valuation": {"pe_trailing": num(raw(sd, "trailingPE")), "pe_forward": num(raw(sd, "forwardPE") or raw(ks, "forwardPE")),
                          "peg": num(raw(ks, "pegRatio")), "ev_to_ebitda": num(raw(ks, "enterpriseToEbitda")),
                          "price_to_book": num(raw(ks, "priceToBook"))},
            "growth_yoy_pct": {"revenue": pct(raw(fd, "revenueGrowth")), "earnings": pct(raw(fd, "earningsGrowth"))},
            "margins_pct": {"gross": pct(raw(fd, "grossMargins")), "operating": pct(raw(fd, "operatingMargins")), "net": pct(raw(fd, "profitMargins"))},
            "balance": {"debt_to_equity": num(raw(fd, "debtToEquity")), "total_cash": raw(fd, "totalCash"), "total_debt": raw(fd, "totalDebt"),
                        "free_cashflow": raw(fd, "freeCashflow")},
            "analysts": {"count": raw(fd, "numberOfAnalystOpinions"), "recommendation": fd.get("recommendationKey"),
                         "target_mean": num(raw(fd, "targetMeanPrice")), "target_low": num(raw(fd, "targetLowPrice")),
                         "target_high": num(raw(fd, "targetHighPrice")), "price_now": num(raw(fd, "currentPrice"))},
            "short_pct_of_float": pct(raw(ks, "shortPercentOfFloat")), "beta": num(raw(sd, "beta")),
            "dividend_yield_pct": pct(raw(sd, "dividendYield")),
            "next_earnings_date": dates[0] if dates else None,
        }
    # 큰 금액은 읽는 말로 (−22,587,523,072 같은 숫자를 LLM 이 그대로 옮겨 적었다)
    def money(v):
        if not isinstance(v, (int, float)):
            return v
        a = abs(v)
        text = f"{a / 1e12:.2f}조 달러" if a >= 1e12 else f"{a / 1e8:,.0f}억 달러" if a >= 1e8 else f"{a / 1e4:,.0f}만 달러"
        return ("-" if v < 0 else "") + text
    for k in ("market_cap", "total_assets"):
        if k in out:
            out[k] = money(out[k])
    for k in ("total_cash", "total_debt", "free_cashflow"):
        if k in out.get("balance", {}):
            out["balance"][k] = money(out["balance"][k])
    prune = lambda d: {k: (prune(v) if isinstance(v, dict) else v) for k, v in d.items() if v not in (None, {}, "")}
    out = prune(prune(out))
    out["source"] = "야후 파이낸스 (몇 시간 늦을 수 있음)"
    _fund[symbol] = (time.time(), out)
    return out


_SYSTEM = (
    "너는 미국 주식 앱의 회사 소개를 쓰는 도우미다. 주어진 영문 사업 설명만 보고 한국어로 쓴다.\n"
    "1) summary: 해요체 2~3문장. 무엇을 하는 회사(펀드)인지, 돈을 어떻게 버는지(펀드면 무엇을 따라가고 몇 배인지)를 쉽게. "
    "설명에 없는 내용, 전망, 좋다·나쁘다는 평가는 쓰지 않는다. 회사명은 영어 그대로 써도 된다.\n"
    "2) industry_ko: 업종 이름을 짧은 한국어로 (예: Software - Application → 응용 소프트웨어). 업종이 없으면 빈 문자열.\n"
    'JSON 으로만: {"summary": "...", "industry_ko": "..."}'
)


def korean(settings: Settings, info: dict) -> dict:
    if not settings.summary_enabled or not info["summary_en"]:
        return {"summary": "", "industry_ko": "", "tokens": 0}
    from openai import OpenAI
    model = settings.openai_model
    resp = OpenAI(api_key=settings.openai_api_key).chat.completions.create(
        model=model, response_format={"type": "json_object"}, **llm_args(model, 0.2),
        messages=[{"role": "system", "content": _SYSTEM},
                  {"role": "user", "content": json.dumps({"industry": info["industry"], "type": info["type"],
                                                          "description": info["summary_en"][:3000]}, ensure_ascii=False)}])
    data = json.loads(resp.choices[0].message.content or "{}")
    return {"summary": str(data.get("summary", "")).strip(), "industry_ko": str(data.get("industry_ko", "")).strip(),
            "tokens": resp.usage.total_tokens if resp.usage else 0}


def profile(settings: Settings, symbol: str, toss_info: dict | None = None) -> dict:
    """회사 정보 한 덩어리. 디스크에 30일 둔다."""
    path: Path = settings.data_dir / "company" / f"{symbol}.json"
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - saved.get("saved_at", 0) < TTL:
            return saved
    info = fetch_yahoo(symbol)
    ko = korean(settings, info)
    t = toss_info or {}
    out = {**info, **ko, "sector_ko": SECTORS.get(info["sector"] or "", info["sector"] or ""),
           "name": t.get("name"), "english_name": t.get("englishName"), "market": t.get("market"), "list_date": t.get("listDate"),
           "saved_at": time.time()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
