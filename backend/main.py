"""개인용 주식 차트 도우미 서버. 반드시 127.0.0.1 에서만 띄운다 (run.ps1 참고).

토스 시세는 본인 투자 목적으로만 쓸 수 있으므로 외부 공개/배포 금지.
"""
from __future__ import annotations
import asyncio
import base64
import hmac
import json
import mimetypes
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import agent, analysis, bars, demo, macro, market_brief, movers, news, patterns, rules, search, signals, underlying
from backend.realtime import Hub
from backend.alerts import Monitor, Store
from backend.config import get_settings, market_of
from backend.toss_client import TossApiError, TossClient

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

_cache: dict[str, tuple[float, object]] = {}


def cached(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    value = fn()
    _cache[key] = (time.time(), value)
    return value


def cache_peek(key: str, ttl: float):
    """저장된 값이 아직 쓸 만하면 그 값, 아니면 None (새로 만들지는 않는다)."""
    hit = _cache.get(key)
    return hit[1] if hit and time.time() - hit[0] < ttl else None


@lru_cache
def toss() -> TossClient:
    return TossClient(get_settings())


def price_formatter(currency: str):
    if currency == "KRW":
        return lambda p: f"{p:,.0f}원"
    if currency == "PT":                      # 지수 포인트
        return lambda p: f"{p:,.2f}"
    if currency == "PCT":                     # 금리
        return lambda p: f"{p:.3f}%"
    return lambda p: f"${p:,.2f}"


MACRO_CURRENCY = {"pt": "PT", "pct": "PCT", "usd": "USD", "krw": "KRW"}


def load_stock(symbol: str) -> tuple[dict, list[dict], bool]:
    """(종목정보, 일봉, 데모여부). 지수·선물·금리·원자재(매크로)는 야후 일봉."""
    market = market_of(symbol)
    if macro.is_macro(symbol):
        name, unit, group = macro.BY_SYMBOL[symbol]
        try:
            candles = macro.daily_candles(symbol)[-400:]
        except Exception as error:
            raise HTTPException(status_code=502, detail=f"지표 데이터를 못 받았어요: {error}")
        return {"name": name, "currency": MACRO_CURRENCY[unit], "macro": True, "group": group}, candles, False
    if not get_settings().toss_enabled:
        return demo.demo_info(symbol, market), demo.demo_candles(symbol, market), True
    try:
        info = cached(f"info:{symbol}", 3600, lambda: toss().stock_info(symbol))
        candles = cached(f"candles:{symbol}", 300, lambda: toss().daily_candles(symbol))
    except TossApiError as error:
        hint = " (허용 IP에 내 공인 IP를 등록했는지 확인)" if error.status == 403 else ""
        raise HTTPException(status_code=502, detail=f"토스 API 오류 {error.status}{hint}: {error.payload}")
    return info, candles, False


def currency_of(info: dict, symbol: str) -> str:
    return info.get("currency", "KRW" if market_of(symbol) == "KR" else "USD")


def levels_for_alert(symbol: str) -> tuple[str, str, dict]:
    info, candles, _ = load_stock(symbol)
    lines = analysis.alert_lines(candles, store.drawings(symbol))
    return info.get("name", symbol), currency_of(info, symbol), lines


def signals_for(symbol: str, candles: list[dict]) -> dict:
    """확정 봉 기준 신호. 봉 날짜가 같으면 결과도 같아서 날짜로 캐시."""
    key = f"sig:{symbol}:{candles[-1]['time']}:{len(candles)}"
    return cached(key, 600, lambda: signals.detect(candles, market_of(symbol)))


def alert_types() -> list[str]:
    return store.settings().get("signal_alert_types", signals.DEFAULT_ALERT_TYPES)


def stat_sentence(st: dict | None) -> str:
    if not st or not st.get("n20"):
        return "이 종목에선 과거 기록이 부족해요."
    if st["n20"] < signals.MIN_SAMPLE:
        return f"이 종목에선 과거 {st['n20']}번뿐이라 숫자로 판단하기 어려워요."
    base = f", 아무 날 샀을 때는 {st['base20']:+.1f}%" if st.get("base20") is not None else ""
    return (f"과거 이 종목에서 {st['count']}번, 20거래일 뒤 평균 {st['avg20']:+.1f}%{base} "
            f"(오른 경우 {st['up20']}/{st['n20']}). 과거 통계일 뿐 앞으로를 보장하진 않아요.")


def signal_alerts(symbol: str) -> list[dict]:
    """가장 최근 확정 봉에서 난 신호 중 알림 켠 종류만. 확정 봉이 5일보다 오래됐으면(연휴 등) 안 보냄."""
    info, candles, _ = load_stock(symbol)
    sig = signals_for(symbol, candles)
    latest = sig["confirmed_through"]
    if not latest or (date.today() - date.fromisoformat(latest)).days > 5:
        return []
    wanted = set(alert_types())
    name = info.get("name", symbol)
    out = []
    for ev in sig["events"]:
        if ev["time"] != latest or ev["type"] not in wanted:
            continue
        cat = signals.CATALOG[ev["type"]]
        note = f" ({ev['note']})" if ev["note"] else ""
        out.append({
            "key": f"{symbol}:sig:{ev['type']}:{latest}",
            "type": f"signal-{cat['dir']}",
            "kind": "signal",
            "symbol": symbol,
            "name": name,
            "title": f"{symbol} {cat['name']}{note}",
            "body": f"{latest} 종가 기준 확정. {cat['desc']} {stat_sentence(sig['stats'].get(ev['type']))}",
            "signal_time": latest,
        })
    return out


def prev_close_of(symbol: str, daily: list[dict]) -> float:
    """전일 종가 = 마지막 일봉의 바로 전 봉. 토스 앱의 '오늘 등락'과 같은 기준.
    (날짜로 '오늘 봉인지' 따지면 틀린다: 미국 주식 주간거래는 미국 날짜로 전날인데 이미 새 일봉이 생겨 있다)"""
    return daily[-2]["close"] if len(daily) > 1 else daily[-1]["close"]


def change_now(symbol: str, price: float | None = None) -> tuple[float, float]:
    """(현재가, 전일 대비 %)."""
    _, daily, _ = load_stock(symbol)
    if price is None:
        price = cached(f"px:{symbol}", 30, lambda: toss().prices([symbol])).get(symbol, daily[-1]["close"])
    return price, (price / prev_close_of(symbol, daily) - 1) * 100


def market_change(market: str) -> float | None:
    try:
        return change_now(movers.MARKET_PROXY[market][0])[1]
    except Exception:
        return None


def underlying_change(u: dict) -> float | None:
    """기초자산의 전일 대비 %: 개별 주식이면 토스, 지수·코인·원자재면 매크로(yfinance)."""
    try:
        return change_now(u["quote"])[1] if u.get("quote_is_stock") else macro.change_pct(u["quote"])
    except Exception:
        return None


def why_moved(symbol: str, price: float | None = None) -> dict:
    info, _, _ = load_stock(symbol)
    name, market = info.get("name", symbol), market_of(symbol)
    price, chg = change_now(symbol, price)
    u = underlying.resolve(symbol, info)                 # ETF 면 기초자산 (비트코인, 반도체, TSLA ...)
    items = movers.recent_news(get_settings(), market, symbol, name, underlying=u)
    why = movers.explain(get_settings(), symbol=symbol, name=name, market=market, change_pct=chg,
                         market_change_pct=market_change(market), items=items,
                         underlying=u, underlying_change_pct=underlying_change(u) if u else None)
    return {"symbol": symbol, "name": name, "price": price, "change_pct": round(chg, 2), **why}


def move_alerts(symbol: str, price: float, seen) -> list[dict]:
    """관심종목이 전일 대비 ±ALERT_MOVE_PCT 단계를 새로 넘으면 → 뉴스로 이유를 찾아 알림 1개."""
    s = get_settings()
    _, chg = change_now(symbol, price)
    step = movers.move_step(chg, s.alert_move_pct)
    if step < 1:
        return []
    day = signals.market_today(market_of(symbol))
    if seen(movers.move_event_key(symbol, chg, step, day)):
        return []
    why = cached(f"why:{symbol}:{step}:{chg > 0}", 600, lambda: why_moved(symbol, price))
    info, _, _ = load_stock(symbol)
    return [movers.event(symbol, why["name"], chg, step, day, why, price_formatter(currency_of(info, symbol))(price))]


store = Store(get_settings().data_dir)
monitor = Monitor(get_settings(), store, levels_for_alert,
                  lambda symbols: toss().prices(symbols), price_formatter, signal_alerts, move_alerts)


hub = Hub(lambda: toss()._get_token())


@asynccontextmanager
async def lifespan(_: FastAPI):
    tasks = []
    if get_settings().toss_enabled:
        tasks = [asyncio.create_task(monitor.run_forever()), asyncio.create_task(hub.run_forever())]
        asyncio.create_task(asyncio.to_thread(universe))   # 검색 목록 미리 받아두기 (~7초)
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Stocking", lifespan=lifespan)


LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@app.middleware("http")
async def require_password(request, call_next):
    """집 와이파이로 열었을 때: 이 PC(127.0.0.1)가 아닌 기기는 비밀번호(HTTP Basic, 아이디는 아무거나)가 있어야 한다.
    비밀번호를 안 정했으면 다른 기기 접속을 아예 막는다 (계좌·키가 걸린 앱이라 '실수로 열림'을 막기 위해)."""
    client = request.client.host if request.client else ""
    # 리버스 프록시(Caddy 등) 뒤에서는 모든 요청이 127.0.0.1 로 들어온다 → 프록시가 붙인 헤더가 있으면 외부 요청으로 본다
    proxied = "x-forwarded-for" in request.headers or "forwarded" in request.headers
    if (client in LOOPBACK and not proxied) or request.url.path == "/healthz":      # 서버 상태 확인(Render)은 비밀번호 없이
        return await call_next(request)
    pw = get_settings().app_password
    if not pw:
        return PlainTextResponse("다른 기기에서 접속하려면 .env 에 APP_PASSWORD 를 정해주세요.", status_code=403)
    auth = request.headers.get("authorization", "")
    ok = False
    if auth.lower().startswith("basic "):
        try:
            _, _, given = base64.b64decode(auth[6:]).decode("utf-8").partition(":")
            ok = hmac.compare_digest(given.encode(), pw.encode())
        except (ValueError, UnicodeDecodeError):
            ok = False
    if not ok:
        return PlainTextResponse("비밀번호가 필요해요", status_code=401,
                                 headers={"WWW-Authenticate": 'Basic realm="Stocking", charset="UTF-8"'})
    return await call_next(request)


@app.middleware("http")
async def no_stale_frontend(request, call_next):
    """화면 파일(html/js/css)은 매번 바뀌었는지 확인하게 — 안 그러면 브라우저가 옛 JS 모듈을 계속 써서
    고친 게 반영 안 된다 (실제로 indicators.js 옛 버전이 남아 있었음). 바뀌지 않았으면 304 라 비용은 거의 없음."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def load_bars(symbol: str, interval: str, daily: list[dict]) -> list[dict]:
    """interval 에 맞는 봉. 일봉(2페이지)은 이미 받아둔 걸 쓰고, 나머지는 원천을 더 받아 묶는다."""
    if interval == "1d":
        return daily
    if macro.is_macro(symbol):               # 지수·선물·금리: 야후 일봉(10년)을 묶음. 분봉은 없음
        if interval in bars.MINUTE:
            raise HTTPException(status_code=400, detail="지수·선물·금리·원자재는 일·주·월·년 봉만 볼 수 있어요")
        return bars.aggregate_days(macro.daily_candles(symbol), interval)
    if not get_settings().toss_enabled:      # 데모: 분봉은 없음, 주/월/년은 데모 일봉을 묶음
        if interval in bars.MINUTE:
            raise HTTPException(status_code=400, detail="분봉은 토스 API 키가 있어야 볼 수 있어요")
        return bars.aggregate_days(daily, interval)
    try:
        if interval in bars.MINUTE:
            size, pages = bars.MINUTE[interval]
            raw = cached(f"min:{symbol}:{pages}", 60, lambda: toss().minute_candles(symbol, pages))
            return bars.aggregate_minutes(raw, size)
        pages = bars.DAILY[interval]
        # 더 긴 일봉을 이미 받아뒀으면 (예: 년봉 본 뒤 월봉) 그걸 재사용 — 긴 기록은 수십 페이지라 느리다
        for p in sorted({v for v in bars.DAILY.values() if v > pages}, reverse=True):
            hit = _cache.get(f"days:{symbol}:{p}")
            if hit and time.time() - hit[0] < 3600:
                return bars.aggregate_days(hit[1], interval)
        long_daily = cached(f"days:{symbol}:{pages}", 3600, lambda: toss().daily_candles(symbol, pages))
        return bars.aggregate_days(long_daily, interval)
    except TossApiError as error:
        raise HTTPException(status_code=502, detail=f"토스 API 오류 {error.status}: {error.payload}")


# ── 차트 / 뉴스 ──
@app.get("/api/chart/{symbol}")
def chart(symbol: str, interval: str = "1d"):
    symbol = symbol.strip().upper()
    if interval not in bars.INTERVALS:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 봉 단위: {interval}")
    info, daily, is_demo = load_stock(symbol)
    if len(daily) < 30:
        raise HTTPException(status_code=404, detail="일봉 데이터가 너무 적어요 (신규 상장 종목?)")
    candles = load_bars(symbol, interval, daily)
    if len(candles) < 2:
        raise HTTPException(status_code=404, detail="이 봉 단위로 볼 데이터가 부족해요")
    currency = currency_of(info, symbol)
    prev_close = prev_close_of(symbol, daily)      # 등락률 계산용
    return {
        "symbol": symbol,
        "name": info.get("name", symbol),
        "market": market_of(symbol),
        "currency": currency,
        "demo": is_demo,
        "interval": interval,
        "intraday": interval in bars.MINUTE,
        "macro": bool(info.get("macro")),          # 지수·선물·금리·원자재: 실시간·관심종목·보유 없음
        "prev_close": prev_close,
        "candles": candles,
        **analysis.analyze(candles, price_formatter(currency), bars.UNIT_WORD[interval]),
        # 차트 신호는 '장 마감 확정 일봉' 기준이라 일봉에서만
        "signals": signals_for(symbol, daily) if interval == "1d" else None,
        # 차트 패턴(형성 중·최근 돌파)도 일봉 기준 + 검증 숫자
        "patterns": cached(f"pat:{symbol}:{daily[-1]['time']}:{daily[-1]['close']}", 120,
                           lambda: patterns.current(analysis.to_frame(daily), price_formatter(currency)))
                    if interval == "1d" else [],
        "pattern_evidence": cached("pat-evidence", 3600, patterns.evidence),
        # '채널 긋기'에서 찍을 점 추천 (비스듬히 그은 선은 일봉 전용)
        "channel_suggestions": analysis.channel_suggestions(analysis.to_frame(daily)) if interval == "1d" else [],
    }


@app.get("/api/live")
async def live(symbols: str):
    """실시간 체결 스트림 (SSE). symbols=AAPL,NVDA (최대 50개). 0.3초마다 {종목: 최신 체결} 묶음."""
    wanted = {s.strip().upper() for s in symbols.split(",") if s.strip()}
    wanted = set(sorted(wanted)[:50])
    if not get_settings().toss_enabled or not wanted:
        raise HTTPException(status_code=400, detail="토스 API 키가 있어야 실시간 시세를 받을 수 있어요")
    sid, q = hub.add(wanted)

    async def gen():
        try:
            yield ": connected\n\n"
            while True:
                try:
                    batch = await asyncio.wait_for(q.get(), timeout=20)
                    yield f"data: {json.dumps(batch)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            hub.remove(sid)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/why/{symbol}")
def why(symbol: str):
    """'왜 움직였지?' — 지금 등락률 + 최근 뉴스로 찾은 이유 (5분 캐시)."""
    symbol = symbol.strip().upper()
    if not get_settings().toss_enabled:
        raise HTTPException(status_code=400, detail="토스 API 키가 있어야 해요")
    return cached(f"why-manual:{symbol}", 300, lambda: why_moved(symbol))


@app.get("/api/signals/settings")
def get_signal_settings():
    return {"catalog": signals.CATALOG, "alert_types": alert_types()}


class SignalSettings(BaseModel):
    alert_types: list[str]


@app.put("/api/signals/settings")
def put_signal_settings(body: SignalSettings):
    types = [t for t in body.alert_types if t in signals.CATALOG]
    store.update_settings(signal_alert_types=types)
    return {"catalog": signals.CATALOG, "alert_types": types}


NEWS_AI_TTL = 3600       # AI 로 거르고 요약한 뉴스는 종목당 1시간 그대로 쓴다 (토큰 아끼기)


@app.get("/api/news/{symbol}")
def stock_news(symbol: str, ai: bool = False):
    """ai=0(차트를 열 때): 기사 목록만, AI 를 안 부른다. ai=1(뉴스 탭을 열 때): 무관한 기사 거르기 + 3줄 요약.
    1시간 안에 이미 AI 로 정리한 게 있으면 ai=0 이어도 그걸 준다."""
    symbol = symbol.strip().upper()
    settings = get_settings()
    info, _, _ = load_stock(symbol)
    name = info.get("name", symbol).replace("(데모)", "")
    if info.get("macro"):
        return {"items": [], "summary": None, "source_enabled": True, "summary_enabled": settings.summary_enabled,
                "ai_pending": False}
    done = cache_peek(f"news-ai:{symbol}", NEWS_AI_TTL)
    if done is not None:
        return done
    fetch = lambda use_ai: news.get_news(settings, market_of(symbol), symbol, name, ai=use_ai)
    if ai:
        return cached(f"news-ai:{symbol}", NEWS_AI_TTL, lambda: fetch(True))
    return cached(f"news:{symbol}", 600, lambda: fetch(False))


@app.get("/api/macro")
def macro_quotes():
    """시장 탭: 미국 지수·선물·국채 금리·원자재·환율·코인·국내 지수 (야후, 1분 캐시)."""
    groups = []
    if get_settings().toss_enabled:
        groups.append({"group": "실시간 ETF", "live": True, "items": cached("live-etfs", 20, live_etf_quotes)})
    try:
        groups += [{**g, "live": False} for g in macro.quotes()]
    except Exception as error:
        if not groups:
            raise HTTPException(status_code=502, detail=f"시장 지표를 못 받았어요: {error}")
    return {"groups": groups}


_brief_lock = threading.Lock()


@app.get("/api/macro/summary")
def macro_summary(refresh: bool = False):
    """시장 탭 맨 위: 미국 시장을 움직인 소식 + 지수·금리·원자재 3줄.
    하루에 한 번만 만든다 (그날 처음 열 때 만들고 파일에 저장 → 다음 날 처음 열 때까지 그대로). refresh=1 이면 지금 다시."""
    settings = get_settings()
    if not settings.summary_enabled:
        return {"enabled": False, "summary": None}
    day = market_brief.brief_day()
    with _brief_lock:                          # 만드는 데 20초쯤 걸린다 → 그 사이 온 요청이 또 만들지 않게
        summary = None if refresh else market_brief.load_saved(settings.data_dir, day)
        if summary is None:
            try:
                groups = macro.quotes()
                summary = market_brief.build(settings, groups, market_brief.headlines(settings, market_brief.as_of(groups)))
            except Exception as error:
                raise HTTPException(status_code=502, detail=f"시장 요약을 못 만들었어요 ({type(error).__name__})")
            market_brief.save(settings.data_dir, day, summary)
    return {"enabled": True, "summary": summary}


def live_etf_quotes() -> list[dict]:
    """토스로 보는 실시간 ETF: 현재가 한 번에 + 전일 종가·미니차트는 일봉에서."""
    symbols = [s for s, _ in macro.LIVE_ETFS]
    prices = toss().prices(symbols)
    out = []
    for s, name in macro.LIVE_ETFS:
        try:
            _, daily, _ = load_stock(s)
        except HTTPException:
            out.append({"symbol": s, "name": name, "unit": "usd", "error": True})
            continue
        prev = prev_close_of(s, daily)
        last = prices.get(s, daily[-1]["close"])
        out.append({"symbol": s, "name": name, "unit": "usd", "live": True, "last": last, "prev": prev,
                    "change": last - prev, "change_pct": (last / prev - 1) * 100, "change_bp": None,
                    "date": daily[-1]["time"], "spark": [c["close"] for c in daily[-22:]]})
    return out


# ── 검색 / 보유종목 ──
_DEMO_UNIVERSE = [{"symbol": s, "name": n, "market": "DEMO", "type": "STOCK"} for s, n in
                  [("AAPL", "애플"), ("NVDA", "엔비디아"), ("TSLA", "테슬라"), ("MSFT", "마이크로소프트"),
                   ("005930", "삼성전자"), ("000660", "SK하이닉스")]]


def universe() -> list[dict]:
    if not get_settings().toss_enabled:
        return _DEMO_UNIVERSE
    return cached("universe", 86400, lambda: search.build_universe(toss().listed_stocks))


@app.get("/api/search")
def search_stocks(q: str, limit: int = 12):
    # '나스닥', '금리', '원유' 같은 지표도 검색되게 맨 앞에
    qn = q.replace(" ", "").lower()
    macros = [{"symbol": s, "name": n, "market": "지표", "type": "MACRO"} for s, (n, _, _) in macro.BY_SYMBOL.items()
              if qn and (qn in n.replace(" ", "").lower() or qn == s.lower())][:4]
    return {"items": (macros + search.search(universe(), q, min(limit, 30)))[:limit]}


@app.get("/api/resolve")
def resolve_stocks(q: str):
    """'AAPL, 엔비디아, 테슬라' 처럼 붙여넣은 목록 → 종목들. 못 찾은 건 따로."""
    found, missing = [], []
    for token in (t.strip() for t in q.replace("\n", ",").split(",")):
        if not token:
            continue
        hit = search.resolve(universe(), token)
        (found.append(hit) if hit else missing.append(token))
    return {"items": found, "missing": missing}


def _num(x) -> float:
    return float(x) if x not in (None, "") else 0.0


@app.get("/api/holdings")
def holdings():
    if not get_settings().toss_enabled:
        return {"enabled": False, "items": []}

    def fetch():
        seq = toss().brokerage_account_seq()
        if seq is None:
            return {"enabled": True, "items": []}
        raw = toss().holdings(seq)
        rate = cached("usd_krw", 600, lambda: toss().usd_krw())
        to_krw = lambda v, cur: v * rate if cur == "USD" else v
        items = []
        for it in raw.get("items", []):
            cur = it["currency"]
            mv, pl = _num(it["marketValue"]["amount"]), _num(it["profitLoss"]["amount"])
            daily = _num(it["dailyProfitLoss"]["amount"])
            items.append({
                "symbol": it["symbol"],
                "name": it["name"],
                "market": it["marketCountry"],
                "currency": cur,
                "quantity": _num(it["quantity"]),
                "avg_price": _num(it["averagePurchasePrice"]),
                "last_price": _num(it["lastPrice"]),
                "market_value": mv,
                "market_value_krw": to_krw(mv, cur),
                "pl_amount": pl,
                "pl_amount_krw": to_krw(pl, cur),
                "pl_rate": round(_num(it["profitLoss"]["rate"]) * 100, 2),
                "daily_amount_krw": to_krw(daily, cur),
                "daily_rate": round(_num(it["dailyProfitLoss"]["rate"]) * 100, 2),
            })
        items.sort(key=lambda h: -h["market_value_krw"])   # 평가금 큰 순
        # 계좌 합계. 토스의 krw/usd 는 '원화로 거래하는 국내 종목 합' / '달러로 거래하는 해외 종목 합'이라
        # 그대로 쓰면 해외만 가진 계좌의 원화 합계가 0 이 된다 → 둘을 환율로 합쳐 원화 총액·달러 총액으로
        def price(p):
            k, u = _num(p.get("krw")), _num(p.get("usd"))
            return {"krw": k + u * rate, "usd": u + k / rate}
        summary = {
            "purchase": price(raw["totalPurchaseAmount"]),
            "market_value": price(raw["marketValue"]["amount"]),
            "profit": price(raw["profitLoss"]["amount"]),
            "profit_rate": round(_num(raw["profitLoss"]["rate"]) * 100, 2),
            "daily": price(raw["dailyProfitLoss"]["amount"]),
            "daily_rate": round(_num(raw["dailyProfitLoss"]["rate"]) * 100, 2),
        }
        return {"enabled": True, "items": items, "summary": summary, "usd_krw": rate}

    try:
        return cached("holdings", 30, fetch)
    except TossApiError as error:
        raise HTTPException(status_code=502, detail=f"보유종목 조회 실패 {error.status}: {error.payload}")


# ── 관심종목 / 알림 ──
class Watchlist(BaseModel):
    symbols: list[str]


@app.get("/api/watchlist")
def get_watchlist():
    return {"symbols": store.watchlist()}


@app.put("/api/watchlist")
def put_watchlist(body: Watchlist):
    symbols = list(dict.fromkeys(s.strip().upper() for s in body.symbols if s.strip()))[:50]
    store.set_watchlist(symbols)
    return {"symbols": symbols}


class Point(BaseModel):
    time: str
    price: float


class Drawing(BaseModel):
    id: str
    type: Literal["trend", "horizontal", "channel"]
    p1: Point
    p2: Point | None = None
    p3: Point | None = None        # channel: 평행선이 지나는 점


class Drawings(BaseModel):
    items: list[Drawing]


@app.get("/api/drawings/{symbol}")
def get_drawings(symbol: str):
    return {"items": store.drawings(symbol.strip().upper())}


@app.put("/api/drawings/{symbol}")
def put_drawings(symbol: str, body: Drawings):
    items = [d.model_dump() for d in body.items
             if d.type == "horizontal" or (d.p2 and (d.type != "channel" or d.p3))][:30]
    store.set_drawings(symbol.strip().upper(), items)
    return {"items": items}


@app.get("/api/watchlist/summary")
def watchlist_summary():
    """관심종목 탭용: 현재가, 전일 대비, 가장 가까운 지지/저항까지 거리."""
    symbols = store.watchlist()
    if not symbols:
        return {"items": []}
    prices: dict[str, float] = {}
    if get_settings().toss_enabled:
        try:
            prices = cached("wl-prices", 20, lambda: toss().prices(symbols))
        except TossApiError:
            pass
    items = []
    for s in symbols:
        try:
            info, candles, _ = load_stock(s)
        except HTTPException:
            items.append({"symbol": s, "name": s, "error": True})
            continue
        last = prices.get(s, candles[-1]["close"])
        prev = prev_close_of(s, candles)
        lv = analysis.find_levels(analysis.to_frame(candles))
        near = lambda xs: round((xs[0]["price"] / last - 1) * 100, 2) if xs else None
        items.append({
            "symbol": s, "name": info.get("name", s), "currency": currency_of(info, s),
            "price": last, "change_pct": round((last / prev - 1) * 100, 2),
            "support_pct": near(lv["support"]), "resistance_pct": near(lv["resistance"]),
        })
    return {"items": items}


@app.get("/api/alerts")
def get_alerts(limit: int = 50):
    return {"items": store.alerts()[:limit]}


@app.post("/api/alerts/check")
async def check_now():
    """주기 기다리지 않고 지금 한 번 검사."""
    if not get_settings().toss_enabled:
        raise HTTPException(status_code=400, detail="토스 API 키가 있어야 알림이 동작해요")
    return {"new": await monitor.tick()}


@app.get("/api/alerts/stream")
async def alert_stream():
    q = monitor.subscribe()

    async def gen():
        try:
            yield ": connected\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=25)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"           # 연결 유지
        finally:
            monitor.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ── 내 규칙 + 채팅 에이전트 (조회·대조만. 주문 기능은 없다) ──
def my_rules() -> dict:
    return rules.clean(store.settings().get("rules"))


def holding_of(symbol: str) -> dict | None:
    try:
        return next((h for h in holdings().get("items", []) if h["symbol"] == symbol), None)
    except Exception:                       # 계좌 조회가 안 돼도 규칙 대조는 한다
        return None


def check_rules(symbol: str) -> dict:
    symbol = symbol.strip().upper()
    data = chart(symbol, "1d")
    if data["macro"]:
        raise HTTPException(status_code=400, detail="지수·금리 같은 지표에는 매매 규칙을 대조하지 않아요")
    return rules.evaluate(data, my_rules(), price_formatter(data["currency"]), holding_of(symbol))


@app.get("/api/rules")
def get_rules():
    return {"rules": my_rules(), "defaults": rules.DEFAULT}


class RulesBody(BaseModel):
    rules: dict


@app.put("/api/rules")
def put_rules(body: RulesBody):
    saved = rules.clean(body.rules)
    store.update_settings(rules=saved)
    return {"rules": saved, "defaults": rules.DEFAULT}


@app.get("/api/rules/check/{symbol}")
def rules_check(symbol: str):
    """지금 가격이 내 규칙의 어디에 해당하는지 (계산만, AI 를 안 부른다)."""
    return check_rules(symbol)


def _chart_summary(symbol: str) -> dict:
    """에이전트에게 줄 차트 요약 (봉 데이터·좌표는 빼고 읽을 것만)."""
    d = chart(symbol.strip().upper(), "1d")
    last = d["candles"][-1]["close"]
    line = lambda l: {"price": round(l["price"], 2), "distance_pct": l["distance_pct"], "why": l["why"]}
    ch = d.get("channel")
    return {
        "symbol": d["symbol"], "name": d["name"], "currency": d["currency"], "price": last,
        "change_pct": round((last / d["prev_close"] - 1) * 100, 2),
        "support": [line(l) for l in d["levels"]["support"][:3]], "resistance": [line(l) for l in d["levels"]["resistance"][:3]],
        "trendlines": [{"label": t["label"], **line(t), "tentative": t.get("tentative")} for t in d["trendlines"]],
        "channel": {k: ch[k] for k in ("label", "lower", "upper", "position_pct", "why")} if ch else None,
        "week52": d.get("extremes"),
        "insights": [f"{i['title']} — {i['body']}" for i in d.get("insights", [])],
        "patterns": [{"name": p["name"], "status": p["status"], "why": p["why"]} for p in d.get("patterns", [])],
        "recent_signals": [{"date": e["time"], "name": e["name"], "dir": e["dir"]} for e in (d.get("signals") or {}).get("events", [])[-5:]],
    }


def _news_for_agent(symbol: str) -> dict:
    n = stock_news(symbol, ai=True)
    return {"summary": n.get("summary"), "items": [{k: it[k] for k in ("date", "source", "title", "sentiment")} for it in n["items"][:12]]}


def scan_rules(scope: str = "watchlist") -> dict:
    """관심종목·보유 종목 전체를 내 규칙에 대조 (계산만). 매수 조건 충족 → 구간이지만 미달 → 목표 근처 → 해당 없음 순."""
    held = [h["symbol"] for h in holdings().get("items", [])] if scope in ("holdings", "all") else []
    symbols = list(dict.fromkeys((store.watchlist() if scope in ("watchlist", "all") else []) + held))

    def one(symbol: str) -> dict:
        try:
            r = check_rules(symbol)
        except Exception as error:
            return {"symbol": symbol, "error": getattr(error, "detail", type(error).__name__)}
        pick = lambda k: {"price": r[k]["text"], "pct": round(r[k]["pct"], 1)} if r.get(k) else None
        return {"symbol": symbol, "name": r["name"], "price": r["price_text"], "verdict": r["verdict"], "tone": r["tone"], "rr": r["rr"],
                "support": pick("support"), "target": pick("target"), "stop": pick("stop"),
                "holding_pl_pct": r["holding"]["pl_rate"] if r.get("holding") else None}
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(one, symbols))
    order = {"buy": 0, "wait": 1, "sell": 2, "none": 3}
    rows.sort(key=lambda r: (order.get(r.get("tone"), 9), -(r.get("rr") or 0)))
    return {"scope": scope, "count": len(rows), "matched": sum(r.get("tone") == "buy" for r in rows), "items": rows,
            "caution": "내가 정한 규칙에 맞는지만 본 거예요. 추천이 아니에요."}


def backtest_rules(symbol: str, years: int = 3) -> dict:
    """내 규칙을 이 종목의 과거에 적용했다면 (같은 규칙·같은 날이면 결과가 같아서 1시간 캐시)."""
    symbol = symbol.strip().upper()
    info, _, _ = load_stock(symbol)
    if info.get("macro") or not get_settings().toss_enabled:
        raise HTTPException(status_code=400, detail="토스에서 일봉을 받을 수 있는 종목만 과거에 대조할 수 있어요")
    years = max(1, min(int(years), 5))
    pages = (years * 252 + rules.LOOKBACK) // 200 + 1
    candles = cached(f"days:{symbol}:{pages}", 3600, lambda: toss().daily_candles(symbol, pages))
    mine = my_rules()
    key = f"rule-bt:{symbol}:{years}:{candles[-1]['time']}:{json.dumps(mine, sort_keys=True)}"
    return cached(key, 3600, lambda: rules.backtest(candles, mine, price_formatter(currency_of(info, symbol)), symbol,
                                                    info.get("name", symbol), bars=years * 252))


def portfolio() -> dict:
    """보유 종목을 비중·오늘 손익 기여로 정리 (원화 기준)."""
    h = holdings()
    items, s = h.get("items", []), h.get("summary")
    if not items or not s:
        return {"items": [], "note": "보유 종목이 없거나 계좌를 조회하지 못했어요"}
    total = sum(i["market_value_krw"] for i in items)
    rows = [{"symbol": i["symbol"], "name": i["name"], "weight_pct": round(i["market_value_krw"] / total * 100, 1),
             "value_krw": round(i["market_value_krw"]), "quantity": i["quantity"], "avg_price": i["avg_price"], "last_price": i["last_price"],
             "pl_pct": i["pl_rate"], "pl_krw": round(i["pl_amount_krw"]), "today_pct": i["daily_rate"], "today_krw": round(i["daily_amount_krw"])}
            for i in sorted(items, key=lambda i: i["market_value_krw"], reverse=True)]
    return {"total_value_krw": round(s["market_value"]["krw"]), "total_pl_krw": round(s["profit"]["krw"]), "total_pl_pct": s["profit_rate"],
            "today_krw": round(s["daily"]["krw"]), "today_pct": s["daily_rate"], "count": len(rows),
            "top_weight": {"symbol": rows[0]["symbol"], "name": rows[0]["name"], "weight_pct": rows[0]["weight_pct"]},
            "top3_weight_pct": round(sum(r["weight_pct"] for r in rows[:3]), 1), "items": rows}


# 에이전트가 '대신 실행'하는 일은 전부 여기를 거친다: 에이전트는 제안(propose)만 하고, 사용자가 화면에서 확인을 눌러야 실행(act)된다
RULE_NAMES = {"buy_support_atr": "지지 근처(ATR 배수)", "buy_channel_pct": "채널 하단(%)", "target_near_atr": "목표 근처(ATR 배수)",
              "stop_atr": "손절 기준(ATR 배수)", "min_rr": "최소 손익비"}


def normalize_action(kind: str, symbol: str | None = None, price: float | None = None, changes: dict | None = None) -> dict:
    """제안·실행 양쪽에서 같은 검사를 한다. 반환 {kind, …, label}. 잘못된 요청은 ValueError."""
    if kind == "rules_update":
        cur, new = my_rules(), rules.clean({**my_rules(), **{k: v for k, v in (changes or {}).items() if k in rules.LIMITS}})
        diff = {k: new[k] for k in rules.LIMITS if new[k] != cur[k]}
        if not diff:
            raise ValueError("바꿀 규칙 값이 없어요 (바꿀 수 있는 것: " + ", ".join(RULE_NAMES.values()) + ")")
        return {"kind": kind, "changes": diff,
                "label": "내 규칙 바꾸기: " + ", ".join(f"{RULE_NAMES[k]} {cur[k]:g} → {v:g}" for k, v in diff.items())}
    if kind not in ("watch_add", "watch_remove", "alert_line"):
        raise ValueError(f"할 수 없는 일이에요: {kind}")
    symbol = (symbol or "").strip().upper()
    try:
        info, daily, _ = load_stock(symbol)
    except HTTPException:
        raise ValueError(f"종목을 찾지 못했어요: {symbol or '(없음)'}")
    name = info.get("name", symbol)
    if info.get("macro"):
        raise ValueError("지수·금리 같은 지표는 관심종목·알림 대상이 아니에요")
    if kind == "watch_add":
        if symbol in store.watchlist():
            raise ValueError(f"{name}은(는) 이미 관심종목이에요")
        return {"kind": kind, "symbol": symbol, "label": f"{name}({symbol}) 관심종목에 추가"}
    if kind == "watch_remove":
        if symbol not in store.watchlist():
            raise ValueError(f"{name}은(는) 관심종목이 아니에요")
        return {"kind": kind, "symbol": symbol, "label": f"{name}({symbol}) 관심종목에서 빼기"}
    try:
        price = float(price)
    except (TypeError, ValueError):
        raise ValueError("선을 그을 가격이 필요해요")
    last = daily[-1]["close"]
    if not 0.2 * last <= price <= 5 * last:
        raise ValueError(f"현재가({last:g})와 너무 먼 가격이에요: {price:g}")
    text = price_formatter(currency_of(info, symbol))(price)
    extra = "" if symbol in store.watchlist() else " + 관심종목에 추가(알림은 관심종목만 와요)"
    return {"kind": kind, "symbol": symbol, "price": price, "label": f"{name}({symbol}) {text}에 수평선 긋기 (닿으면 알림){extra}"}


def propose_action(kind: str, symbol: str | None = None, price: float | None = None, changes: dict | None = None) -> dict:
    try:
        action = normalize_action(kind, symbol, price, changes)
    except ValueError as error:
        return {"error": str(error)}
    return {"action": action, "status": "확인 버튼을 사용자에게 보여줬어요. 아직 실행되지 않았어요. 사용자가 확인을 눌러야 실행돼요."}


class ActBody(BaseModel):
    kind: str
    symbol: str | None = None
    price: float | None = None
    changes: dict | None = None


@app.post("/api/agent/act")
def agent_act(body: ActBody):
    """채팅에서 확인 버튼을 눌렀을 때만 온다. 할 수 있는 일은 관심종목·수평선(알림)·내 규칙뿐 (주문 없음)."""
    try:
        a = normalize_action(body.kind, body.symbol, body.price, body.changes)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    if a["kind"] == "rules_update":
        store.update_settings(rules=rules.clean({**my_rules(), **a["changes"]}))
    elif a["kind"] == "watch_add":
        store.set_watchlist(store.watchlist() + [a["symbol"]])
    elif a["kind"] == "watch_remove":
        store.set_watchlist([s for s in store.watchlist() if s != a["symbol"]])
    else:
        _, daily, _ = load_stock(a["symbol"])
        line = {"id": f"agent-{int(time.time() * 1000)}", "type": "horizontal", "p1": {"time": daily[-1]["time"], "price": a["price"]},
                "p2": None, "p3": None}
        store.set_drawings(a["symbol"], (store.drawings(a["symbol"]) + [line])[:30])
        if a["symbol"] not in store.watchlist():
            store.set_watchlist(store.watchlist() + [a["symbol"]])
    return {"done": a["label"], "kind": a["kind"], "symbol": a.get("symbol")}


AGENT_TOOLS = {
    "check_my_rules": check_rules,
    "scan_my_rules": scan_rules,
    "backtest_my_rules": backtest_rules,
    "read_chart": _chart_summary,
    "get_news": _news_for_agent,
    "why_moved": lambda symbol: why(symbol),
    "market_brief": lambda: macro_summary().get("summary") or {"error": "시장 요약을 만들 수 없어요"},
    "my_portfolio": portfolio,
    "find_symbol": lambda query: search_stocks(query, 5)["items"],
    "propose_action": propose_action,
}


class ChatBody(BaseModel):
    messages: list[dict]
    symbol: str | None = None


@app.post("/api/agent/chat")
def agent_chat(body: ChatBody):
    settings = get_settings()
    if not settings.summary_enabled:
        raise HTTPException(status_code=400, detail="OPENAI_API_KEY 가 있어야 질문할 수 있어요")
    try:
        return agent.chat(settings, AGENT_TOOLS, body.messages, (body.symbol or "").strip().upper() or None, my_rules()["note"])
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"답을 만들지 못했어요 ({type(error).__name__})")


@app.get("/healthz")
def healthz():
    """배포 서버 상태 확인용. 아무 정보도 내보내지 않는다."""
    return {"ok": True}


@app.get("/api/status")
def status():
    s = get_settings()
    return {"toss": s.toss_enabled, "naver": s.naver_enabled, "finnhub": s.finnhub_enabled,
            "summary": s.summary_enabled, "telegram": s.telegram_enabled,
            "alert": {"poll_sec": s.alert_poll_sec, "near_pct": s.alert_near_pct,
                      "last_run": monitor.last_run, "last_error": monitor.last_error},
            "realtime": {"connected": hub.connected, "codes": hub.desired(), "rejected": hub.rejected,
                         "last_error": hub.last_error}}


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


# Windows 는 레지스트리 설정에 따라 .js 를 text/plain 으로 내보내서 module 스크립트가 막힌다
mimetypes.add_type("text/javascript", ".js")
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
