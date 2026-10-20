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
from dataclasses import asdict
from datetime import date, datetime, time as dtime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import (agent, ai_cache, analysis, bars, company, demo, judgments, market_calendar, options, peers, thesis, macro, market_brief, movers, news, patterns, reading, rules, search,
                     signals, underlying)
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


def candle_ttl(now: datetime | None = None) -> int:
    """일봉을 몇 초 동안 다시 안 받을지. 평소 5분, 새 일봉이 생기는 시간대(평일 한국 시간 08:55~10:15: 국내 개장, 미국 주간거래 시작)엔 30초.
    등락률은 '마지막 일봉의 바로 전 봉'과 비교하는데, 새 장의 첫 체결이 들어온 뒤에도 옛 일봉을 쓰고 있으면
    그 사이(최대 5분) 이틀 치 등락률이 나온다."""
    now = now or datetime.now(ZoneInfo("Asia/Seoul"))
    opening = now.weekday() < 5 and dtime(8, 55) <= now.time() <= dtime(10, 15)
    return 30 if opening else 300


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
        candles = cached(f"candles:{symbol}", candle_ttl(), lambda: toss().daily_candles(symbol))
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
    key = f"sig:{symbol}:{candles[-1]['time']}:{len(candles)}:{analysis.LOG_TRENDS}"
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
analysis.LOG_TRENDS = bool(store.settings().get("log_trends"))     # 추세선·채널을 로그 가격에서 그을지 (차트의 '로그' 버튼)
monitor = Monitor(get_settings(), store, levels_for_alert,
                  lambda symbols: toss().prices(symbols), price_formatter, signal_alerts, move_alerts)


hub = Hub(lambda: toss()._get_token())


@asynccontextmanager
async def lifespan(_: FastAPI):
    tasks = []
    if get_settings().toss_enabled:
        tasks = [asyncio.create_task(monitor.run_forever()), asyncio.create_task(hub.run_forever())]
        asyncio.create_task(asyncio.to_thread(universe))   # 검색 목록 미리 받아두기 (~7초)
        asyncio.create_task(asyncio.to_thread(lambda: judge_scored(judge_log.read())))   # 판단 기록 채점도 미리
        tasks.append(asyncio.create_task(entries_loop()))    # 판단 기록의 출발 가격 (분봉이 며칠만 남아 있어서 판단 뒤 바로 받는다)
        if get_settings().judge_auto:
            tasks.append(asyncio.create_task(judge_scheduler()))
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
        return bars.aggregate_days(long_days(symbol, bars.DAILY[interval]), interval)
    except TossApiError as error:
        raise HTTPException(status_code=502, detail=f"토스 API 오류 {error.status}: {error.payload}")


def long_days(symbol: str, pages: int) -> list[dict]:
    """일봉 pages 페이지 (200일/페이지, 1시간 캐시). 더 긴 일봉을 이미 받아뒀으면 (예: 년봉 본 뒤 월봉) 그걸 재사용 — 긴 기록은 수십 페이지라 느리다."""
    for p in sorted({v for v in (*bars.DAILY.values(), bars.DAILY_ALL) if v > pages}, reverse=True):
        hit = _cache.get(f"days:{symbol}:{p}")
        if hit and time.time() - hit[0] < 3600:
            return hit[1]
    return cached(f"days:{symbol}:{pages}", 3600, lambda: toss().daily_candles(symbol, pages))


@app.get("/api/chart/{symbol}/older")
def chart_older(symbol: str, before: str, interval: str = "1d"):
    """차트의 왼쪽을 채울 옛 봉. 화면이 차트를 그린 뒤에 따로 받아 붙인다.
    일·주·월봉은 상장 이후 전부, 분봉은 bars.MINUTE_LONG 페이지만큼 (1분봉 약 5일 ~ 60분봉 약 6주). 년봉은 처음부터 전부라 없다.
    차트의 첫 봉과 같은 기간의 봉까지 주고 replace_first 로 알린다: 첫 봉은 받은 범위의 시작에서 잘려
    덜 찬 봉일 수 있어서(분봉의 앞 몇 분, 주봉의 앞 며칠) 화면이 꽉 찬 봉으로 바꿔 낀다.
    보여주기만 한다 — 선·신호·내 규칙·판단 기록은 지금처럼 차트 응답의 봉만 쓴다."""
    symbol = symbol.strip().upper()
    if interval not in ("1d", "1w", "1M", *bars.MINUTE):
        raise HTTPException(status_code=400, detail=f"옛 봉을 붙이지 않는 봉 단위: {interval}")
    try:
        if interval in bars.MINUTE:
            if macro.is_macro(symbol) or not get_settings().toss_enabled:
                return {"candles": [], "replace_first": False}
            size, _ = bars.MINUTE[interval]
            out, key = bars.aggregate_minutes(long_minutes(symbol, bars.MINUTE_LONG[interval]), size), (lambda t: t)
            cut = int(before)
        else:
            if macro.is_macro(symbol):
                days = macro.daily_candles(symbol)
            elif not get_settings().toss_enabled:
                days = []
            else:
                days = long_days(symbol, bars.DAILY_ALL)
            out, key = bars.aggregate_days(days, interval), (lambda t: bars.period(t, interval))
            cut = key(before)
    except TossApiError as error:
        raise HTTPException(status_code=502, detail=f"토스 API 오류 {error.status}: {error.payload}")
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"옛 봉을 못 받았어요: {error}")
    keep = [c for c in out if key(c["time"]) <= cut]
    return {"candles": keep, "replace_first": bool(keep) and key(keep[-1]["time"]) == cut}


def long_minutes(symbol: str, pages: int) -> list[dict]:
    """1분봉 pages 페이지. 지난 분봉은 안 바뀌니까 30분 동안 다시 안 받고, 더 긴 걸 받아뒀으면 그걸 쓴다
    (차트 첫 봉보다 앞선 봉만 쓰므로 30분 묵어도 괜찮다)."""
    for p in sorted({v for v in bars.MINUTE_LONG.values() if v > pages}, reverse=True):
        hit = _cache.get(f"min-long:{symbol}:{p}")
        if hit and time.time() - hit[0] < 1800:
            return hit[1]
    return cached(f"min-long:{symbol}:{pages}", 1800, lambda: toss().minute_candles(symbol, pages))


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
        "log_scale": analysis.LOG_TRENDS,          # 추세선·채널을 로그 가격에서 그었나 (화면의 가격축도 로그로 맞춘다)
        **analysis.analyze(candles, price_formatter(currency), bars.UNIT_WORD[interval]),
        # 차트 신호는 '장 마감 확정 일봉' 기준이라 일봉에서만
        "signals": signals_for(symbol, daily) if interval == "1d" else None,
        # 차트 패턴(형성 중·최근 돌파)도 일봉 기준 + 검증 숫자
        "patterns": cached(f"pat:{symbol}:{daily[-1]['time']}:{daily[-1]['close']}", 120,
                           lambda: patterns.current(analysis.to_frame(daily), price_formatter(currency)))
                    if interval == "1d" else [],
        "pattern_evidence": cached("pat-evidence", 3600, patterns.evidence),
        # '채널 긋기'에서 찍을 점 추천 (비스듬히 그은 선은 일봉 전용)
        "channel_suggestions": analysis.channel_suggestions(analysis.to_frame(daily, despike=analysis.DESPIKE)) if interval == "1d" else [],
    }


class ScaleBody(BaseModel):
    log: bool


@app.put("/api/settings/scale")
def put_scale(body: ScaleBody):
    """추세선·채널을 로그 가격에서 그을지. 화면만이 아니라 신호·알림·내 규칙도 같은 선을 쓰게 서버 설정으로 둔다.
    수평 지지/저항과 내가 그은 선은 그대로다."""
    store.update_settings(log_trends=body.log)
    analysis.LOG_TRENDS = body.log
    return {"log": analysis.LOG_TRENDS}


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
ai_disk = ai_cache.DiskCache(get_settings().data_dir / "ai_cache")    # AI 결과 디스크 저장 (backend/ai_cache.py)


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
    plain = cached(f"news:{symbol}", 600, lambda: fetch(False))
    # 기사 목록이 같으면 예전에 AI 로 거르고 요약한 결과를 디스크에서 그대로 쓴다 (서버를 다시 켜도). 새 기사가 나오면 그때만 다시
    disk_key = f"news-ai:{symbol}:{ai_cache.digest([(it.get('url') or it.get('title')) for it in plain['items']])}"
    saved = ai_disk.get(disk_key, 7 * 86400)
    if saved is not None:
        _cache[f"news-ai:{symbol}"] = (time.time(), saved)
        return saved
    if ai:
        out = cached(f"news-ai:{symbol}", NEWS_AI_TTL, lambda: fetch(True))
        if out.get("summary"):
            ai_disk.put(disk_key, out)
        return out
    return plain


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
        lv = analysis.find_levels(analysis.to_frame(candles, despike=analysis.DESPIKE))
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


def live_price(symbol: str, last_close: float, given: float | None = None, batch: dict | None = None) -> float:
    """내 규칙을 대조할 '지금 가격'. 화면이 실시간 가격을 주면 그걸, 아니면 토스 현재가(15초 캐시), 그것도 안 되면 마지막 일봉 종가.
    일봉 캐시(평소 5분)만 쓰면 카드의 가격·판정이 화면의 실시간 가격과 어긋난다."""
    if given and 0.5 * last_close <= given <= 2 * last_close:       # 엉뚱한 값은 무시
        return float(given)
    if batch and symbol in batch:
        return batch[symbol]
    if not get_settings().toss_enabled:
        return last_close
    try:
        return cached(f"px1:{symbol}", 15, lambda: toss().prices([symbol])).get(symbol, last_close)
    except TossApiError:
        return last_close


def check_rules(symbol: str, price: float | None = None, batch: dict | None = None) -> dict:
    symbol = symbol.strip().upper()
    data = chart(symbol, "1d")
    if data["macro"]:
        raise HTTPException(status_code=400, detail="지수·금리 같은 지표에는 매매 규칙을 대조하지 않아요")
    now = live_price(symbol, data["candles"][-1]["close"], price, batch)
    return rules.evaluate(data, my_rules(), price_formatter(data["currency"]), holding_of(symbol), price=now)


@app.get("/api/reading/{symbol}")
def chart_reading(symbol: str, make: bool = False, price: float | None = None):
    """한눈에 보기의 '구조 읽기'. make=0 이면 오늘 이미 만든 것만 돌려주고(없으면 null), make=1 일 때만 AI 를 부른다.
    같은 종목은 그날의 마지막 봉·눈금 설정이 같으면 다시 만들지 않는다 (6시간)."""
    symbol = symbol.strip().upper()
    settings = get_settings()
    if not settings.summary_enabled:
        return {"enabled": False, "reading": None}
    data = chart(symbol, "1d")
    if data["macro"]:
        return {"enabled": False, "reading": None}
    key = f"reading2:{symbol}:{data['candles'][-1]['time']}:{analysis.LOG_TRENDS}"
    done = ai_disk.get(key)                               # 같은 날 봉이면 서버를 다시 켜도 그대로 (디스크)
    if done is None and make:
        now = live_price(symbol, data["candles"][-1]["close"], price)
        try:
            done = reading.read(settings, data, now, price_formatter(data["currency"]))
        except Exception as error:
            raise HTTPException(status_code=502, detail=f"구조 읽기를 만들지 못했어요 ({type(error).__name__})")
        ai_disk.put(key, done)
    return {"enabled": True, "reading": done}


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
def rules_check(symbol: str, price: float | None = None):
    """지금 가격이 내 규칙의 어디에 해당하는지 (계산만, AI 를 안 부른다). price = 화면에 떠 있는 실시간 가격."""
    return check_rules(symbol, price)


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


def price_check(symbol: str, price: float) -> dict:
    """에이전트 도구: 사용자가 말한 특정 가격(예: '51불에 팔까')을 차트에서 본다.
    그 가격 근처(허용폭 = max(ATR 절반, 가격의 1%))에서 방향이 바뀐 날(고점·저점), 그 뒤 10봉 동안 얼마나 밀렸나(올랐나),
    최근 1년 종가가 그 가격 위에서 끝난 날 수, 근처의 자동 지지/저항선, 보유 중이면 그 가격에 팔 때 평단 대비 수익."""
    symbol = symbol.strip().upper()
    info, daily, _ = load_stock(symbol)
    df = analysis.to_frame(daily, despike=analysis.DESPIKE)
    close, high, low = df["close"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
    times = df["time"].tolist()
    last, atr = float(close[-1]), float(analysis.atr(df).iloc[-1])
    tol = max(0.5 * atr, 0.01 * price)
    fmt = price_formatter(currency_of(info, symbol))
    turns = []
    for i, p, kind in analysis.swing_points(df):
        if abs(p - price) > tol:
            continue
        after = close[i + 1: i + 11]
        move = ((after.min() if kind == "high" else after.max()) / p - 1) * 100 if len(after) else None
        turns.append({"date": times[i], "kind": "고점 (여기서 밀림)" if kind == "high" else "저점 (여기서 반등)", "price": fmt(p),
                      "next_10_bars_pct": round(move, 1) if move is not None else None})
    yr = slice(max(0, len(close) - 252), len(close))
    recent = slice(max(0, len(close) - 60), len(close))         # 1년은 길다: 레버리지 ETF 는 1년 전 가격이 지금의 몇 배라 '위에서 마감한 날'이 부풀었다
    above = [times[i] for i in range(recent.start, recent.stop) if close[i] > price]
    near = [{"kind": "저항선" if k == "resistance" else "지지선", "price": fmt(l["price"]), "touches": l["touches"], "why": l["why"]}
            for k in ("resistance", "support") for l in analysis.find_levels(df)[k] if abs(l["price"] - price) <= 2 * tol]
    held = holding_of(symbol)
    out = {
        "symbol": symbol, "asked_price": fmt(price), "price_now": fmt(last), "from_now_pct": round((price / last - 1) * 100, 1),
        "atr": fmt(atr), "from_now_atr": round((price - last) / atr, 1), "tolerance": fmt(tol),
        "turns_near_price": turns[-6:], "turn_count": len(turns),
        "highest_high_last_year": fmt(float(high[yr].max())), "lowest_low_last_year": fmt(float(low[yr].min())),
        "closes_above_price_last_60_bars": len(above), "last_close_above_price": above[-1] if above else None,
        "auto_lines_near_price": near,
    }
    if held:
        avg = held["avg_price"]
        out["holding"] = {"quantity": held["quantity"], "avg_price": fmt(avg),
                          "pl_at_asked_price_pct": round((price / avg - 1) * 100, 1), "pl_now_pct": round((last / avg - 1) * 100, 1)}
    return out


def _news_for_agent(symbol: str) -> dict:
    n = stock_news(symbol, ai=True)
    return {"summary": n.get("summary"), "items": [{k: it[k] for k in ("date", "source", "title", "sentiment")} for it in n["items"][:12]]}


def scan_rules(scope: str = "watchlist") -> dict:
    """관심종목·보유 종목 전체를 내 규칙에 대조 (계산만). 매수 조건 충족 → 구간이지만 미달 → 목표 근처 → 해당 없음 순."""
    held = [h["symbol"] for h in holdings().get("items", [])] if scope in ("holdings", "all") else []
    symbols = list(dict.fromkeys((store.watchlist() if scope in ("watchlist", "all") else []) + held))
    batch: dict[str, float] = {}                    # 지금 가격은 한 번에 받아서 종목마다 따로 부르지 않는다
    if symbols and get_settings().toss_enabled:
        try:
            batch = cached("px-scan:" + ",".join(sorted(symbols)), 15, lambda: toss().prices(symbols))
        except TossApiError:
            pass

    def one(symbol: str) -> dict:
        try:
            r = check_rules(symbol, batch=batch)
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
    key = f"rule-bt:{symbol}:{years}:{candles[-1]['time']}:{analysis.LOG_TRENDS}:{json.dumps(mine, sort_keys=True)}"
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
              "stop_atr": "손절 기준(ATR 배수)", "min_rr": "최소 손익비", "min_risk_atr": "손익비의 최소 위험(ATR 배수)"}


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
    "fundamentals": lambda symbol: company.fundamentals(symbol.strip().upper()),
    "price_check": price_check,
    "options_move": lambda symbol: options.expected_moves(symbol.strip().upper(), _next_earnings(symbol.strip().upper())),
    "my_thesis": lambda symbol: _thesis_for_agent(symbol),
    "record_view": lambda **kw: record_view(**kw),
}


class ChatBody(BaseModel):
    messages: list[dict]
    symbol: str | None = None


@app.post("/api/agent/chat")
def agent_chat(body: ChatBody):
    settings = get_settings()
    if not settings.summary_enabled:
        raise HTTPException(status_code=400, detail="OPENAI_API_KEY 가 있어야 질문할 수 있어요")
    symbol = (body.symbol or "").strip().upper() or None
    try:
        return agent.chat(settings, AGENT_TOOLS, body.messages, symbol, my_rules()["note"], about=symbol_facts(symbol))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"답을 만들지 못했어요 ({type(error).__name__})")


def symbol_facts(symbol: str | None) -> str:
    """챗봇에 같이 넘길 '지금 보는 종목'의 기본 사실 (티커만 넘겼더니 KORU 가 한국 ETF 인지 미국 ETF 인지 되물었다).
    이미 받아둔 것만 쓴다: 토스 종목 정보 + 저장된 회사 정보. 못 받으면 빈 문자열."""
    if not symbol or macro.is_macro(symbol):
        return ""
    try:
        info = load_stock(symbol)[0]
    except Exception:
        info = {}
    path = get_settings().data_dir / "company" / f"{symbol}.json"
    c = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    kind = {"ETF": "ETF", "EQUITY": "주식"}.get(c.get("type") or "", "ETF" if info.get("securityType") == "ETF" else "")
    listed = "한국 상장" if market_of(symbol) == "KR" else "미국 상장"
    parts = [info.get("name"), info.get("englishName"),
             f"{listed} ({info['market']})" if info.get("market") else listed,
             kind, f"{info.get('leverageFactor')}배 레버리지" if info.get("leverageFactor") else "",
             f"보수(expense ratio) {c['expense_ratio'] * 100:.2f}%" if c.get("expense_ratio") is not None else "",
             f"{c.get('sector_ko')} › {c.get('industry_ko')}" if c.get("industry_ko") else "",
             c.get("summary") or ""]
    return " · ".join(p for p in parts if p)


# ── 판단 기록 (에이전트의 판단을 미리 적어 두고 나중에 시세로 채점. 추천이 아니다) ──
# 기록은 레포 안 judgments/ 에 둔다 (data/ 는 PC 마다 따로라, 두 PC 를 오가면 기록이 갈라진다 → 커밋해서 맞춘다)
judge_log = judgments.Log(Path(__file__).resolve().parent.parent / "judgments")
judge_state = {"running": False, "week": None, "done": 0, "total": 0, "tokens": 0, "errors": [], "started": None, "finished": None}
_judge_lock = threading.Lock()
ASKED_PATH = judge_log.root / "asked" / "asked.jsonl"      # 질문 에이전트의 종합 의견 (주 1회 기록과 섞이지 않게 하위 폴더)
_asked_lock = threading.Lock()


def read_asked() -> list[dict]:
    if not ASKED_PATH.exists():
        return []
    return [json.loads(line) for line in ASKED_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def record_view(symbol: str, view: str, confidence: int, reason: str, invalid_if: str) -> dict:
    """에이전트 도구: 종합 의견을 판단 기록에 남긴다 (20거래일 뒤 SPY 대비로 채점). 사용자 확인 없이 적는다 — 적기만 하고 아무것도 바꾸지 않는다."""
    symbol = symbol.strip().upper()
    if view not in judgments.VIEW_DIR:
        return {"error": "view 는 strong / neutral / weak 중 하나"}
    if macro.is_macro(symbol):
        return {"error": "지수·금리 같은 지표는 기록하지 않아요"}
    info, candles, _ = load_stock(symbol)
    rec = judgments.asked_record(symbol, info.get("name", symbol), get_settings().openai_agent_model, view, confidence, reason, invalid_if,
                                 candles, load_stock(judgments.BENCH)[1], mine=symbol in store.watchlist())
    with _asked_lock:
        first = not any(r["symbol"] == symbol and r["as_of"] == rec["as_of"] for r in read_asked())
        ASKED_PATH.parent.mkdir(parents=True, exist_ok=True)
        with ASKED_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"view_record": {"symbol": symbol, "view": view, "as_of": rec["as_of"], "counted": first},
            "note": ("판단 기록에 남겼어요. " if first else "오늘 이 종목은 이미 남긴 의견이 있어서, 채점은 처음 것으로 해요. ")
                    + f"출발 가격은 지금 이후 미국 정규장의 첫 {judgments.ENTRY_MINUTES}분 평균이고, "
                    + f"{rec['as_of']} 뒤 5·20거래일 종가에 S&P 500 대비로 채점돼요."}


# ── 판단 기록의 출발 가격: 판단 뒤 미국 정규장 첫 30분 평균 (judgments 맨 위 '바꾼 것 2026-10-15', '2026-10-19') ──
ENTRIES_PATH = judge_log.root / "entries" / "entries.jsonl"   # 두 PC 가 같이 쓰도록 레포에. 하위 폴더: 주 기록(*.jsonl)과 안 섞이게
_entries_lock = threading.Lock()
ENTRY_MAX_PAGES = 60                                      # 1분봉 60페이지 = 12,000분 ≈ 장이 열린 날 열흘쯤. 이보다 오래되면 분봉이 없다


def read_entries() -> dict[tuple[str, str], dict]:
    """{(종목, 판단 시각): 출발 가격}. 같은 키가 두 번 적혔으면 먼저 적은 것.
    지금 규칙(judgments.ENTRY_RULE)으로 적은 줄만 읽는다 — 옛 규칙('첫 체결부터 30분')으로 적은 줄은 파일에 남겨 두되 쓰지 않는다."""
    out: dict = {}
    if ENTRIES_PATH.exists():
        for line in ENTRIES_PATH.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("rule") != judgments.ENTRY_RULE:
                continue
            out.setdefault((e["symbol"], e["at"]), e)
    return out


def fill_entries(now_ts: float | None = None) -> int:
    """출발 가격이 아직 없는 기록을 채운다. 판단 시각을 덮는 1분봉을 받아 판단 뒤 정규장 첫 30분 평균 (SPY 도 같은 30분).
    그 구간이 아직 안 끝났으면 다음에 (끝날 시각 전에는 분봉을 받지도 않는다). 분봉이 이미 지워졌으면 기준일 다음 봉 시가로 대신하고
    그렇다고 적는다. 반환: 새로 적은 수."""
    if not get_settings().toss_enabled:
        return 0
    now_ts = now_ts or time.time()
    with _entries_lock:
        have = read_entries()
        todo: dict[str, dict[str, dict]] = {}
        for r in judge_log.read() + read_asked():
            key = judgments.entry_key(r) if judgments.needs_entry(r) else None
            if key and key not in have and now_ts >= judgments.entry_ready_at(datetime.fromisoformat(key[1]).timestamp()):
                todo.setdefault(r["symbol"], {})[key[1]] = r
        added = 0
        for symbol, by_at in todo.items():
            oldest = min(datetime.fromisoformat(a).timestamp() for a in by_at)
            pages = min(ENTRY_MAX_PAGES, int((now_ts - oldest) / 60 / 200) + 3)     # 1분봉 한 페이지 200개, 넉넉히
            try:
                mins = toss().minute_candles(symbol, pages)
                spy = cached(f"entry-spy:{pages}", 300, lambda: toss().minute_candles(judgments.BENCH, pages))
            except Exception as error:
                print(f"[entries] {symbol}: {type(error).__name__}: {error}")
                continue
            for at, r in by_at.items():
                at_ts = datetime.fromisoformat(at).timestamp()
                if mins and spy and mins[0]["time"] <= at_ts and spy[0]["time"] <= at_ts:     # 받은 분봉이 판단 시각을 덮는다
                    e = judgments.entry_from_minutes(mins, at_ts, now_ts)
                    b = judgments.entry_from_minutes(spy, e["t0"], now_ts) if e else None
                    if not e or not b:
                        continue                                   # 30분이 아직 안 끝났다
                    entry = {"price": e["price"], "bars": e["bars"], "bench_price": b["price"], "bench_bars": b["bars"],
                             "t0": datetime.fromtimestamp(e["t0"], judgments.KST).isoformat(timespec="minutes")}
                elif pages >= ENTRY_MAX_PAGES:                     # 분봉을 놓쳤다 → 기준일 다음 봉 시가
                    entry = judgments.entry_fallback(load_stock(symbol)[1], load_stock(judgments.BENCH)[1], r["as_of"])
                    if not entry:
                        continue
                else:
                    continue
                ENTRIES_PATH.parent.mkdir(parents=True, exist_ok=True)
                with ENTRIES_PATH.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"symbol": symbol, "at": at, "rule": judgments.ENTRY_RULE, "minutes": judgments.ENTRY_MINUTES, **entry},
                                       ensure_ascii=False) + "\n")
                added += 1
        return added


async def entries_loop():
    """10분마다 출발 가격을 채운다 (판단 뒤 정규장 첫 30분이 끝나면, 그리고 분봉이 지워지기 전에)."""
    while True:
        try:
            n = await asyncio.to_thread(fill_entries)
            if n:
                print(f"[entries] 출발 가격 {n}건")
        except Exception as error:
            print(f"[entries] {type(error).__name__}: {error}")
        await asyncio.sleep(600)


def judge_symbols() -> list[tuple[str, bool]]:
    """(종목, 내 종목인지). 고정 100종목 + 나스닥 100 + 지금의 관심·보유 종목."""
    try:
        held = [h["symbol"] for h in holdings().get("items", [])]
    except Exception:
        held = []
    mine = {s for s in store.watchlist() + held if market_of(s) == "US" and not macro.is_macro(s)}
    return [(s, s in mine) for s in dict.fromkeys(judgments.symbols_for(judgments.week_key()) + sorted(mine))]


def judge_plan() -> list[tuple[str, bool, list[tuple[str, str]]]]:
    """이번 주에 적어야 할 것: (종목, 내 종목인지, [(모델, 조건)]). 첫 번째 모델은 전 종목, 나머지는 judgments.PREMIUM 만."""
    models, week = get_settings().openai_judge_models, judgments.week_key()
    return [(s, mine, judgments.combos_for(s, models, week)) for s, mine in judge_symbols()]


def judge_left(week: str) -> int:
    """이번 주 계획 중 아직 안 적은 건수."""
    done = judge_log.done_keys(week)
    return sum((s, *c) not in done for s, _, combos in judge_plan() for c in combos)


def judge_run(only: list[str] | None = None) -> dict:
    """이번 주 판단을 남긴다. 이미 적은 (종목, 모델, 조건)은 건너뛰어서, 중간에 끊겨도 이어서 할 수 있다."""
    settings = get_settings()
    if not settings.summary_enabled or not settings.toss_enabled:
        raise HTTPException(status_code=400, detail="토스 키와 OPENAI_API_KEY 가 있어야 판단을 남길 수 있어요")
    if not _judge_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="이미 판단을 남기는 중이에요")
    try:
        week, done = judgments.week_key(), judge_log.done_keys(judgments.week_key())
        jobs = [(s, mine, [c for c in combos if (s, *c) not in done]) for s, mine, combos in judge_plan() if not only or s in only]
        jobs = [j for j in jobs if j[2]]
        judge_state.update(running=True, week=week, done=0, total=sum(len(j[2]) for j in jobs), tokens=0, errors=[],
                           started=datetime.now(judgments.KST).isoformat(timespec="seconds"), finished=None)
        _, bench, _ = load_stock(judgments.BENCH)
        _, bench2, _ = load_stock(judgments.BENCH2)

        def one(job):
            symbol, mine, todo = job
            try:
                info, candles, _ = load_stock(symbol)
                if len(candles) < 260:
                    raise ValueError("일봉이 260개보다 적어요")
                name = info.get("name", symbol)
                # 기사: 판단 전 7일, 거의 같은 제목은 하나만, 최대 30건 (W43 부터. judgments 맨 위 '바꾼 것')
                kinds = {judgments.REPEATS.get(a, a) for _, a in todo}
                items = []
                if kinds & set(judgments.USES_NEWS):
                    raw = (news._safe(news.fetch_us_news, settings, symbol, limit=200, days=judgments.NEWS_DAYS + 1)
                           + news._safe(news.fetch_kr_news_about, settings, name, limit=60, days=judgments.NEWS_DAYS + 1))
                    items = judgments.select_news([asdict(it) for it in raw], datetime.now(judgments.KST).date().isoformat())
                fund = None
                if kinds & set(judgments.USES_FUND):                       # 펀더멘탈 (W43 부터). 못 받으면 없이 판단하고 기록에 남긴다
                    try:
                        fund = company.fundamentals(symbol)
                    except Exception as error:
                        print(f"[judgments] {symbol} 펀더멘탈 못 받음: {type(error).__name__}")
                for rec in judgments.make_records(settings, week, symbol, name, mine, candles, bench, items, todo,
                                                  save_input=judge_log.append_input, bench2=bench2, fund=fund):
                    judge_log.append(rec)
                    judge_state["done"] += 1
                    judge_state["tokens"] += rec["tokens"]
            except Exception as error:
                judge_state["errors"].append(f"{symbol}: {type(error).__name__}: {str(getattr(error, 'detail', error))[:120]}")
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(one, jobs))
        return dict(judge_state)
    finally:
        judge_state.update(running=False, finished=datetime.now(judgments.KST).isoformat(timespec="seconds"))
        _judge_lock.release()


async def judge_scheduler():
    """JUDGE_AUTO=1 일 때만: 그 주 판단이 아직 다 안 적혔고 때가 됐으면(화요일 07:00 이후) 남긴다. 15분마다 확인."""
    while True:
        try:
            if judgments.is_due() and not judge_state["running"]:
                if judge_left(judgments.week_key()) and not judge_state["errors"]:
                    await asyncio.to_thread(judge_run)
        except Exception as error:
            print(f"[judgments] {type(error).__name__}: {error}")
        await asyncio.sleep(900)


class JudgeRunBody(BaseModel):
    symbols: list[str] | None = None        # 일부 종목만 (시험용). 없으면 전체


@app.post("/api/judgments/run")
def judgments_run(body: JudgeRunBody):
    only = [s.strip().upper() for s in body.symbols] if body.symbols else None
    if judge_state["running"]:
        raise HTTPException(status_code=409, detail="이미 판단을 남기는 중이에요")
    threading.Thread(target=lambda: judge_run(only), daemon=True).start()
    return {"started": True}


JUDGE_SCORE_TTL = 3600
_judge_score: dict = {}                  # {"at", "n", "value"}: 마지막 채점
_judge_score_lock = threading.Lock()


def judge_scored(records: list[dict]) -> list[dict]:
    """채점 결과. 채점은 하루에 한 번 생기는 확정 봉으로 하니까 1시간 동안 같은 값을 쓴다.
    1시간이 지났으면 지난 값을 바로 돌려주고 뒤에서 새로 계산한다 (탭을 열 때 기다리지 않게).
    기록 수가 바뀌었으면(새 주 기록) 기다려서 새로 계산한다. 동시에 여러 번 불려도 계산은 한 번만."""
    hit = dict(_judge_score)                              # 복사: 뒤에서 새로 계산한 값이 이 호출의 답을 바꾸지 않게
    n = (len(records), len(read_entries()))               # 출발 가격이 새로 들어와도 다시 계산한다
    if hit.get("n") == n:
        if time.time() - hit["at"] < JUDGE_SCORE_TTL:
            return hit["value"]
        if _judge_score_lock.acquire(blocking=False):
            def refresh():
                try:
                    _store_judge_score(records)
                finally:
                    _judge_score_lock.release()
            threading.Thread(target=refresh, daemon=True).start()
        return hit["value"]
    with _judge_score_lock:
        if _judge_score.get("n") != n:                    # 기다리는 사이 다른 요청이 이미 계산했으면 그걸 쓴다
            _store_judge_score(records)
        return _judge_score["value"]


def _store_judge_score(records: list[dict]) -> None:
    entries = read_entries()
    value = judgments.score(records, lambda s: cached(f"judge-candles:{s}", JUDGE_SCORE_TTL, lambda: load_stock(s)[1]), entries=entries)
    _judge_score.update(at=time.time(), n=(len(records), len(entries)), value=value)


@app.get("/api/judgments")
def judgments_summary(symbol: str | None = None):
    """기록 현황 + 채점 (읽을 때마다 지금 시세로 계산). symbol 을 주면 그 종목의 기록도."""
    settings = get_settings()
    records = judge_log.read()
    scored = judge_scored(records)
    week = judgments.week_key()
    plan = judge_plan()
    out = {
        "protocol": {"universe": len(judgments.UNIVERSE), "ndx": len(judgments.NDX), "ndx_from": judgments.NDX_FROM, "symbols": len(plan),
                     "premium": len(judgments.PREMIUM), "horizons": judgments.HORIZONS,
                     "primary": judgments.PRIMARY, "bench": judgments.BENCH, "models": settings.openai_judge_models,
                     "arms": judgments.arms_for(week), "auto": settings.judge_auto,
                     "bridge": [judgments.BRIDGE_FROM, judgments.BRIDGE_UNTIL], "bridge_now": judgments.in_bridge(week)},
        "week": week, "due": judgments.is_due(), "planned": sum(len(c) for _, _, c in plan), "left": judge_left(week),
        "by_model": {m: sum(sum(x[0] == m for x in c) for _, _, c in plan) for m in settings.openai_judge_models},
        "recorded_this_week": len(judge_log.done_keys(week)),
        "state": judge_state, "weeks": judgments.weeks_table(records),
        "fixed": judgments.summarize(scored, fixed_only=True),
        "ndx": judgments.summarize(judgments.in_set(scored, judgments.NDX), fixed_only=False),
        "consistency": judgments.consistency(records),
        "paired": judgments.summarize(judgments.paired(scored), fixed_only=True),      # 모델끼리 비교는 둘 다 판단한 기록만
        "mine": judgments.summarize([r for r in scored if r.get("mine")], fixed_only=False),
    }
    # 내가 물어본 판단 (질문 에이전트의 종합 의견). 종목·기준일마다 처음 것만 채점
    asked = judgments.first_per_day(read_asked())
    entries = read_entries()
    asked_scored = cached(f"asked-scored:{len(asked)}:{len(entries)}", JUDGE_SCORE_TTL,
                          lambda: judgments.score(asked, lambda s: cached(f"judge-candles:{s}", JUDGE_SCORE_TTL, lambda: load_stock(s)[1]),
                                                  entries=entries))
    out["asked"] = judgments.summarize(asked_scored, fixed_only=False, arms=(judgments.ASKED_ARM,))
    out["asked_records"] = asked_scored[-20:]
    if symbol:
        s = symbol.strip().upper()
        out["symbol_records"] = [r for r in scored if r["symbol"] == s][-12:]
    return out


@app.get("/api/company/{symbol}")
def company_info(symbol: str):
    """어떤 섹터·업종의 어떤 회사인지 (야후 프로필 + AI 한국어 요약, 30일 저장)."""
    symbol = symbol.strip().upper()
    if macro.is_macro(symbol) or market_of(symbol) == "KR":
        return {"company": None}
    try:
        info = load_stock(symbol)[0] if get_settings().toss_enabled else {}
        return {"company": company.profile(get_settings(), symbol, info)}
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"회사 정보를 못 받았어요 ({type(error).__name__})")


def _us_stock(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if macro.is_macro(symbol) or market_of(symbol) == "KR":
        raise HTTPException(status_code=400, detail="미국 종목·ETF 만 볼 수 있어요")
    return symbol


def _next_earnings(symbol: str) -> str | None:
    try:
        return company.fundamentals(symbol).get("next_earnings_date")
    except Exception:
        return None


@app.get("/api/options/{symbol}")
def options_move(symbol: str):
    """옵션이 예상하는 움직임 (등가격 스트래들, 가까운 만기들 + 다음 실적 직후 만기). 15분 캐시."""
    symbol = _us_stock(symbol)
    try:
        return options.expected_moves(symbol, _next_earnings(symbol))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"옵션 정보를 못 받았어요 ({type(error).__name__}: {str(error)[:80]})")


@app.get("/api/peers/{symbol}")
def peers_compare(symbol: str, refresh: bool = False):
    """경쟁사 비교 표. 고르기는 디스크에 저장(반년, refresh=1 이면 다시 고름), 숫자는 야후·토스 캐시."""
    symbol = _us_stock(symbol)
    s = get_settings()
    try:
        prof = company.profile(s, symbol, load_stock(symbol)[0])
        if refresh:
            _cache.pop(f"peers:{symbol}", None)
        return cached(f"peers:{symbol}", 3600, lambda: peers.compare(s, symbol, prof, lambda x: load_stock(x)[:2], refresh))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"경쟁사 비교를 못 만들었어요 ({type(error).__name__})")


# ── 투자 논리 추적 (backend/thesis.py) ──
thesis_store = thesis.Store(get_settings().data_dir / "theses.json")


def thesis_facts(symbol: str, since: str | None = None) -> dict:
    """논리 정리·점검에 줄 사실. 전부 코드가 모은 숫자와 기사 (AI 는 이것만 본다)."""
    info, daily, _ = load_stock(symbol)
    spy = load_stock(judgments.BENCH)[1]
    last = daily[-1]["close"]
    ago = lambda cs, n: round((cs[-1]["close"] / cs[-1 - n]["close"] - 1) * 100, 1) if len(cs) > n else None
    out = {"name": info.get("name"), "price_now": last, "price_date": daily[-1]["time"],
           "change_pct": {"5d": ago(daily, 5), "20d": ago(daily, 20), "60d": ago(daily, 60), "spy_20d": ago(spy, 20)}}
    if since:
        d0 = since[:10]
        base = next((c for c in daily if c["time"] >= d0), None)
        sb = next((c for c in spy if c["time"] >= d0), None)
        if base and sb:
            out["since_thesis"] = {"from": base["time"], "stock_pct": round((last / base["close"] - 1) * 100, 1),
                                   "spy_pct": round((spy[-1]["close"] / sb["close"] - 1) * 100, 1)}
    try:
        prof = company.profile(get_settings(), symbol, info)
        out["company"] = {"type": prof.get("type"), "industry": prof.get("industry_ko") or prof.get("industry"), "summary": prof.get("summary")}
    except Exception:
        pass
    # 시장·기초자산 흐름: 논리가 비트코인·반도체 같은 바깥 가격에 기대는 일이 많다 (없으면 '판단 못함'이 나왔다)
    market = {}
    for label, get in (("나스닥 100 (QQQ)", lambda: load_stock("QQQ")[1]), ("비트코인", lambda: macro.daily_candles("BTC-USD"))):
        try:
            cs = get()
            market[label] = {"5d": ago(cs, 5), "20d": ago(cs, 20), "60d": ago(cs, 60), "last": round(cs[-1]["close"], 2)}
        except Exception:
            pass
    try:
        u = underlying.resolve(symbol, info)
        if u:
            cs = load_stock(u["quote"])[1] if u.get("quote_is_stock") else macro.daily_candles(u["quote"])
            market[f"기초자산 {u.get('label', u['quote'])}"] = {"5d": ago(cs, 5), "20d": ago(cs, 20), "60d": ago(cs, 60)}
    except Exception:
        pass
    out["market"] = market
    try:
        out["fundamentals"] = {k: v for k, v in company.fundamentals(symbol).items() if k != "source"}
    except Exception:
        pass
    try:
        out["news"] = _news_for_agent(symbol)
    except Exception:
        pass
    try:
        m = options.expected_moves(symbol, (out.get("fundamentals") or {}).get("next_earnings_date"))
        out["options"] = {"expiries": [{k: e[k] for k in ("expiry", "move_pct", "atm_iv_pct", "put_call_oi")} for e in m["expiries"][:2]],
                          "earnings": m.get("earnings")}
    except Exception:
        pass
    held = holding_of(symbol)
    if held:
        out["holding"] = {"quantity": held["quantity"], "avg_price": held["avg_price"], "pl_pct": round((last / held["avg_price"] - 1) * 100, 1)}
    return out


@app.get("/api/thesis/{symbol}")
def thesis_get(symbol: str):
    symbol = _us_stock(symbol)
    t = thesis_store.get(symbol)
    return {"thesis": t, "stale": thesis.stale(t) if t else None}


class ThesisDraftBody(BaseModel):
    why: str = ""


@app.post("/api/thesis/{symbol}/draft")
def thesis_draft(symbol: str, body: ThesisDraftBody):
    """'왜 샀나'를 기둥·틀린 조건·이벤트로 나눈 초안 (저장하지 않는다)."""
    symbol = _us_stock(symbol)
    try:
        return thesis.draft(get_settings(), symbol, body.why.strip()[:2000], thesis_facts(symbol))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"정리하지 못했어요 ({type(error).__name__})")


class ThesisBody(BaseModel):
    why: str = ""
    pillars: list[str] = []
    invalid_if: list[str] = []
    catalysts: list[str] = []
    horizon: str = ""


@app.put("/api/thesis/{symbol}")
def thesis_save(symbol: str, body: ThesisBody):
    symbol = _us_stock(symbol)
    old = thesis_store.get(symbol) or {}
    pillars = thesis.clean_lines(body.pillars, 6)
    if not pillars:
        raise HTTPException(status_code=400, detail="논리 기둥을 하나 이상 적어 주세요")
    t = {**old, "why": body.why.strip()[:2000], "pillars": pillars, "invalid_if": thesis.clean_lines(body.invalid_if, 5),
         "catalysts": thesis.clean_lines(body.catalysts, 6), "horizon": body.horizon.strip()[:40], "updated_at": thesis.now_iso()}
    if not old:
        t.update(created_at=thesis.now_iso(), entry_price=load_stock(symbol)[1][-1]["close"], checks=[])
    t["earnings_watch"] = _next_earnings(symbol) or old.get("earnings_watch")
    thesis_store.put(symbol, t)
    return {"thesis": t, "stale": thesis.stale(t)}


@app.delete("/api/thesis/{symbol}")
def thesis_delete(symbol: str):
    thesis_store.put(_us_stock(symbol), None)
    return {"ok": True}


@app.post("/api/thesis/{symbol}/check")
def thesis_check(symbol: str):
    """논리가 아직 유효한지 점검하고 기록에 덧붙인다."""
    symbol = _us_stock(symbol)
    t = thesis_store.get(symbol)
    if not t:
        raise HTTPException(status_code=404, detail="먼저 투자 논리를 저장해 주세요")
    try:
        c = thesis.check(get_settings(), symbol, t, thesis_facts(symbol, t.get("created_at")))
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"점검하지 못했어요 ({type(error).__name__})")
    t = {**t, "checks": (t.get("checks") or [])[-(thesis.MAX_CHECKS - 1):] + [c], "earnings_watch": _next_earnings(symbol) or t.get("earnings_watch")}
    thesis_store.put(symbol, t)
    return {"thesis": t, "stale": thesis.stale(t), "check": c}


def _thesis_for_agent(symbol: str) -> dict:
    symbol = symbol.strip().upper()
    t = thesis_store.get(symbol)
    if not t:
        return {"thesis": None, "note": "이 종목엔 저장된 투자 논리가 없어요"}
    last = (t.get("checks") or [None])[-1]
    return {"why": t.get("why"), "pillars": t.get("pillars"), "invalid_if": t.get("invalid_if"), "catalysts": t.get("catalysts"),
            "created_at": t.get("created_at"), "entry_price": t.get("entry_price"), "stale": thesis.stale(t),
            "last_check": {k: last.get(k) for k in ("at", "overall", "summary", "pillars")} if last else None}


@app.get("/api/calendar")
def market_calendar_api(days: int = 28, back: int = 3, month: str = ""):
    """증권 달력: 오늘 기준 back 일 전부터 days 일 동안의 미국 경제지표 발표와 실적 발표 (내 관심·보유 + 주요 대형주).
    month=YYYY-MM 이면 그 달 한 달치 (한국 날짜 1일은 미국 전날 밤 발표까지 들어가게 하루 앞에서 시작). 2년 전 ~ 6달 뒤까지."""
    days, back = max(7, min(int(days), 42)), max(0, min(int(back), 14))
    try:
        held = [h["symbol"] for h in holdings().get("items", [])]
    except Exception:
        held = []
    mine = list(dict.fromkeys(store.watchlist() + held))
    today = datetime.now(ZoneInfo("America/New_York")).date()
    if month:
        try:
            first = date.fromisoformat(f"{month}-01")
        except ValueError:
            raise HTTPException(400, "month 는 YYYY-MM")
        if not (today - timedelta(days=740) <= first <= today + timedelta(days=190)):
            raise HTTPException(400, "2년 전부터 6달 뒤까지만 볼 수 있어요")
        nxt = (first + timedelta(days=32)).replace(day=1)
        start, total = first - timedelta(days=1), (nxt - first).days
        ttl = 1800 if nxt >= today - timedelta(days=7) else 86400                 # 지난 달은 바뀔 일이 거의 없다
    else:
        start, total, ttl = today - timedelta(days=back), days + back, 1800
    key = f"calendar:{start}:{total}:{','.join(sorted(mine))}"
    return cached(key, ttl, lambda: market_calendar.build(start, total, get_settings().finnhub_api_key, mine))


@app.get("/api/fx")
def fx():
    """달러→원 환율. 토스 매매기준율(1분마다 바뀜)을 1분 캐시. 토스 키가 없으면 야후 원/달러(몇 분 늦음)."""
    try:
        if get_settings().toss_enabled:
            rate = cached("usd_krw:live", 60, lambda: toss().usd_krw())
            return {"usd_krw": rate, "source": "토스 매매기준율", "at": datetime.now(ZoneInfo("Asia/Seoul")).strftime("%H:%M")}
        bars_ = cached("usd_krw:yahoo", 300, lambda: macro.daily_candles("KRW=X"))
        return {"usd_krw": bars_[-1]["close"], "source": "야후 파이낸스 (몇 분 늦음)", "at": bars_[-1]["time"]}
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"환율을 못 받았어요: {error}")


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
