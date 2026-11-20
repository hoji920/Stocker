"""토스 1분봉을 종목별로 모아 두고, 다음부터는 새 페이지만 받는다 (메모리에만, 서버를 끄면 사라짐).

토스 캔들 API 는 한 번에 200분씩, 앞 페이지 커서로만 거슬러 올라가서 페이지를 동시에 받을 수 없다
(페이지당 약 0.2초 → 1시간봉 30페이지 6초, 옛 봉 800페이지 2분 남짓). 지난 분봉은 안 바뀌니까
한 번 받은 건 두고, 다시 볼 때는 최신 1~3페이지만 받아 이어 붙인다. 이어지지 않으면(오래 안 본 종목)
이번에 필요한 만큼만 새로 받는다. 마지막 봉은 진행 중일 수 있어서 새로 받은 값으로 덮는다.

받는 동안 잠그지 않는다: 옛 봉 800페이지(2분)를 받는 중에도 같은 종목의 다른 봉 차트는 바로 떠야 해서.
같은 걸 두 번 받는 일이 가끔 생겨도, 저장할 때 더 깊은 쪽을 남긴다.
"""
from __future__ import annotations
import threading
import time
from typing import Callable

PAGE = 200          # 토스 1분봉 한 페이지
FRESH_SEC = 5       # 이 안에 다시 물으면 받지 않고 그대로 (차트·옛 봉 요청이 연달아 올 때)
TOP_UP_PAGES = 3    # 최신 쪽을 이만큼까지 받아 보고도 이어지지 않으면 필요한 만큼 다시


class MinuteStore:
    def __init__(self, fetch: Callable[[str, int], list[dict]], now: Callable[[], float] = time.time):
        self._fetch = fetch            # (종목, 페이지 수) → 1분봉 (time 오름차순)
        self._now = now
        self._data: dict[str, dict] = {}   # 종목 → {"bars": [...], "pages": 받은 깊이, "at": 마지막으로 받은 시각}
        self._lock = threading.Lock()      # 저장할 때만 잠근다

    def get(self, symbol: str, pages: int) -> list[dict]:
        """최근 pages 페이지 분량의 1분봉."""
        hit = self._data.get(symbol)
        if hit is not None and hit["pages"] >= pages and self._now() - hit["at"] <= FRESH_SEC:
            return hit["bars"][-pages * PAGE:]
        if hit is None or hit["pages"] < pages:
            bars, depth = self._fetch(symbol, pages), pages
        else:
            bars, depth = self._top_up(symbol, hit["bars"], hit["pages"], pages)
        with self._lock:
            cur = self._data.get(symbol)
            # 그사이 다른 요청이 더 깊게 받아 저장했으면 그것에 최신 봉만 얹는다
            if cur is not None and cur is not hit and cur["pages"] > depth and bars and cur["bars"]:
                bars = [b for b in cur["bars"] if b["time"] < bars[0]["time"]] + bars
                depth = cur["pages"]
            self._data[symbol] = {"bars": bars, "pages": depth, "at": self._now()}
        return bars[-pages * PAGE:]

    def _top_up(self, symbol: str, bars: list[dict], depth: int, pages: int) -> tuple[list[dict], int]:
        last = bars[-1]["time"] if bars else None
        if last is not None:
            for n in range(1, TOP_UP_PAGES + 1):
                new = self._fetch(symbol, n)
                if not new:
                    break
                if new[0]["time"] <= last:                   # 가진 봉과 겹친다 → 이어 붙인다
                    return [b for b in bars if b["time"] < new[0]["time"]] + new, depth
        return self._fetch(symbol, pages), pages             # 이어지지 않음 → 이번에 필요한 만큼만 (옛 봉 깊이는 버린다)
