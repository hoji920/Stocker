"""경쟁사 비교: 비교할 회사를 고르고 같은 숫자로 나란히 놓는다.

- 고르기: Finnhub 경쟁사 목록(같은 업종 분류)은 MSTR 을 소프트웨어 회사로 묶는 식으로 틀릴 때가 있다
  → LLM 이 회사 설명 + Finnhub 후보를 보고 '실제로 같은 것으로 돈을 버는' 미국 상장 종목 4~6개를 고르고 이유를 한 줄 단다.
  토스에서 종목 정보가 안 나오는 티커는 뺀다. 30일 저장 (data/company/peers_<종목>.json).
- 숫자: 야후 펀더멘탈(가치·성장·마진) + 토스 일봉 수익률(1·3·12개월). 숫자는 코드가 만든다 (LLM 은 고르기만).
"""
from __future__ import annotations
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from backend import company
from backend.config import Settings, llm_args

TTL = 180 * 86400    # 한 번 고르면 반년 동안 다시 안 고른다 (화면의 '다시 고르기'로만)
VERSION = 2          # 고르는 방식을 바꾸면 올린다 (예전에 저장한 고르기는 버리고 다시 고른다)
# v2 (2026-10-20): gpt-4o-mini 가 IREN 에 시총 880만 달러짜리 석유 신탁(MARPS)·헬스케어 보험사(CLOV)를 골랐다
#   → 질문 에이전트 모델 + 생각 켬, 우리가 보는 종목 목록을 후보로 줌, 너무 작은 회사는 숫자로 거른다
MIN_CAP_USD, MIN_CAP_SHARE = 5e8, 0.05

_SYSTEM = (
    "너는 미국 주식 비교 대상을 고르는 애널리스트다. 주어진 회사와 '지금 실제로 같은 것으로 돈을 버는' 미국 상장 종목(주식 또는 ETF) 5~6개를 고른다.\n"
    "- 업종 분류보다 지금 사업의 실체와 최근 전환을 본다 (예: 비트코인 채굴에서 AI GPU 클라우드로 옮겨 가는 회사면 "
    "AI 클라우드 회사와 같은 전환을 하는 채굴사를 섞어서. 비트코인을 대량 보유한 회사면 비트코인 관련 기업·채굴사·비트코인 현물 ETF).\n"
    "- 투자자들이 실제로 나란히 놓고 보는 이름 있는 종목을 고른다. 시가총액이 아주 작은 종목은 고르지 않는다.\n"
    "- tracked 목록(이 앱이 보는 종목)에 맞는 것이 있으면 우선 쓰고, 거기 없어도 확실히 아는 대표 종목은 넣어도 된다. "
    "finnhub_candidates(업종 분류)는 참고만, 맞지 않으면 쓰지 않는다.\n"
    "- 티커만 정확히. 확실하지 않은 티커는 넣지 않는다. 주어진 회사 자신은 넣지 않는다.\n"
    'JSON 으로만: {"peers": [{"symbol": "CRWV", "why": "한국어 한 줄: 무엇이 같은지"}]}'
)


def tracked() -> list[str]:
    """이 앱이 이미 보는 미국 종목들 (판단 기록 고정 100 + 나스닥 100 + 중형·고변동성 후보). 고르기의 후보로 준다."""
    from backend import judgments
    try:
        from research import midcap
        extra = midcap.CANDIDATES
    except Exception:
        extra = []
    return sorted(set(judgments.UNIVERSE) | set(judgments.NDX) | set(extra))


def _path(settings: Settings, symbol: str) -> Path:
    return settings.data_dir / "company" / f"peers_{symbol}.json"


def choose(settings: Settings, symbol: str, profile: dict, valid, refresh: bool = False) -> list[dict]:
    """[{symbol, why}]. valid(티커) → 토스에서 종목 정보가 나오면 True. 30일 저장."""
    path = _path(settings, symbol)
    if path.exists() and not refresh:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved.get("version") == VERSION and time.time() - saved.get("saved_at", 0) < TTL:
            return saved["peers"]
    hints = []
    if settings.finnhub_api_key:
        try:
            hints = httpx.get("https://finnhub.io/api/v1/stock/peers", params={"symbol": symbol, "token": settings.finnhub_api_key},
                              timeout=15).json()
        except Exception:
            hints = []
    from openai import OpenAI
    model = settings.openai_agent_model
    resp = OpenAI(api_key=settings.openai_api_key).chat.completions.create(
        model=model, response_format={"type": "json_object"}, **llm_args(model, 0.2, effort="medium"),
        messages=[{"role": "system", "content": _SYSTEM},
                  {"role": "user", "content": json.dumps({
                      "symbol": symbol, "name": profile.get("english_name") or profile.get("name"), "type": profile.get("type"),
                      "sector": profile.get("sector"), "industry": profile.get("industry"),
                      "description": (profile.get("summary_en") or "")[:1500], "finnhub_candidates": [h for h in hints if h != symbol],
                      "tracked": [t for t in tracked() if t != symbol]}, ensure_ascii=False)}])
    picked = json.loads(resp.choices[0].message.content or "{}").get("peers") or []
    own = cap(symbol)
    seen, out = {symbol}, []
    for p in picked:
        s = str(p.get("symbol", "")).strip().upper()
        if not s or s in seen or not valid(s):
            continue
        c = cap(s)
        # 시총이 너무 작은 회사 (ETF 는 순자산). 비율 기준은 200억 달러를 넘지 않게 (초대형주는 5% 도 수천억이라 큰 경쟁사까지 빠졌다)
        if c is not None and (c < MIN_CAP_USD or (own and c < min(own * MIN_CAP_SHARE, 2e10))):
            continue
        seen.add(s)
        out.append({"symbol": s, "why": str(p.get("why", "")).strip()})
    out = out[:6]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": VERSION, "saved_at": time.time(), "peers": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def cap(symbol: str) -> float | None:
    """시가총액(ETF 는 순자산), 달러. 못 받으면 None (거르지 않는다)."""
    try:
        f = company.fundamentals(symbol)
        return f.get("market_cap_usd") or f.get("total_assets_usd")
    except Exception:
        return None


def _returns(daily: list[dict]) -> dict:
    c = [x["close"] for x in daily]
    r = lambda n: round((c[-1] / c[-1 - n] - 1) * 100, 1) if len(c) > n else None
    return {"1m": r(21), "3m": r(63), "12m": r(252)}


def row(symbol: str, name: str, daily: list[dict]) -> dict:
    """한 종목의 비교 줄: 펀더멘탈(야후) + 수익률(토스 일봉)."""
    try:
        f = company.fundamentals(symbol)
    except Exception:
        f = {}
    v, g, m, a = (f.get(k) or {} for k in ("valuation", "growth_yoy_pct", "margins_pct", "analysts"))
    return {"symbol": symbol, "name": name, "type": f.get("type"), "market_cap": f.get("market_cap") or f.get("total_assets"),
            "pe_forward": v.get("pe_forward"), "peg": v.get("peg"), "ev_to_ebitda": v.get("ev_to_ebitda"),
            "revenue_growth": g.get("revenue"), "gross_margin": m.get("gross"), "net_margin": m.get("net"),
            "target_upside": round((a["target_mean"] / a["price_now"] - 1) * 100, 1) if a.get("target_mean") and a.get("price_now") else None,
            "expense_ratio": f.get("expense_ratio_pct"), **_returns(daily)}


def compare(settings: Settings, symbol: str, profile: dict, load, refresh: bool = False) -> dict:
    """load(티커) → (종목 정보, 일봉). 종목 자신 + 고른 경쟁사의 비교 표."""
    def valid(s):
        try:
            return bool(load(s)[1])
        except Exception:
            return False
    peers = choose(settings, symbol, profile, valid, refresh)
    why = {p["symbol"]: p["why"] for p in peers}

    def one(s):
        info, daily = load(s)
        return {**row(s, info.get("name", s), daily), "why": why.get(s, ""), "self": s == symbol}

    with ThreadPoolExecutor(6) as ex:
        rows = list(ex.map(one, [symbol] + [p["symbol"] for p in peers]))
    return {"symbol": symbol, "rows": rows, "source": "고르기: AI (한 번 고르면 저장, '다시 고르기'로만 바뀜) · 숫자: 야후 펀더멘탈 + 토스 일봉"}
