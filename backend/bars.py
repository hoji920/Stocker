"""봉 단위 변환. 토스 API 는 1분봉·일봉만 주므로 나머지는 여기서 묶는다.

- 분봉: time = 유닉스 초. 차트가 시각을 UTC 로 그리기 때문에, 한국 시간으로 보이도록 +9시간 해서 내보낸다
  (토스 앱처럼 미국주식도 한국 시간 축).
- 일·주·월·년봉: time = 'YYYY-MM-DD' (그 기간 첫 거래일).
"""
from __future__ import annotations
from datetime import date

KST_SHIFT = 9 * 3600

# interval → (원천, 묶는 크기(분), 원천 페이지 수)
MINUTE = {"1m": (1, 2), "3m": (3, 3), "5m": (5, 5), "15m": (15, 10), "30m": (30, 15), "60m": (60, 30), "240m": (240, 30)}
# 차트를 그린 뒤 왼쪽에 붙일 옛 분봉: 1분봉 페이지 수 (페이지당 200분, 받는 데 0.1초쯤)
# 1분봉 약 5일, 3분봉 약 10일, 5분봉 약 3주, 15·30·60분봉 약 6주 (200페이지 = 20초쯤, 차트를 먼저 띄우고 뒤에서 받는다)
# 4시간봉은 약 12주 (400페이지 = 1분쯤. 2026-10-28 에 토스 1분봉이 12주 넘게 남아 있는 걸 확인)
MINUTE_LONG = {"1m": 30, "3m": 60, "5m": 100, "15m": 200, "30m": 200, "60m": 200, "240m": 400}
DAILY_ALL = 60          # 일봉 전체 (상장 이후, 60페이지 = 약 47년). 일·주·월봉의 옛 봉을 여기서 붙인다
DAILY = {"1d": 2, "1w": 10, "1M": 25, "1y": 60}   # 일봉 페이지 수 (200일/페이지)
INTERVALS = list(MINUTE) + list(DAILY)
UNIT_WORD = {"1m": "분", "3m": "봉", "5m": "봉", "15m": "봉", "30m": "봉", "60m": "봉", "240m": "봉",
             "1d": "일", "1w": "주", "1M": "개월", "1y": "년"}


def _merge(group: list[dict], time) -> dict:
    return {
        "time": time,
        "open": group[0]["open"],
        "high": max(b["high"] for b in group),
        "low": min(b["low"] for b in group),
        "close": group[-1]["close"],
        "volume": sum(b["volume"] for b in group),
    }


def aggregate_minutes(bars: list[dict], size: int) -> list[dict]:
    """1분봉(유닉스 초) → size 분봉, 한국 시간으로 이동."""
    step = size * 60
    out: list[dict] = []
    group: list[dict] = []
    key = None
    for b in bars:
        t = b["time"] + KST_SHIFT
        k = t - t % step
        if key is not None and k != key:
            out.append(_merge(group, key))
            group = []
        key = k
        group.append(b)
    if group:
        out.append(_merge(group, key))
    return out


def period(day: str, interval: str) -> str:
    """그 날짜가 속한 봉의 이름 (주봉 '2026-W42', 월봉 '2026-10', 년봉 '2026', 일봉은 날짜 그대로). 문자열 크기 순서 = 시간 순서."""
    d = date.fromisoformat(day)
    if interval == "1w":
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}"
    if interval == "1M":
        return day[:7]
    if interval == "1y":
        return day[:4]
    return day


def aggregate_days(bars: list[dict], interval: str) -> list[dict]:
    """일봉 → 주/월/년봉. time 은 그 기간 첫 거래일."""
    if interval == "1d":
        return bars
    out: list[dict] = []
    group: list[dict] = []
    key = None
    for b in bars:
        k = period(b["time"], interval)
        if key is not None and k != key:
            out.append(_merge(group, group[0]["time"]))
            group = []
        key = k
        group.append(b)
    if group:
        out.append(_merge(group, group[0]["time"]))
    return out
