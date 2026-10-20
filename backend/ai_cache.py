"""AI 결과 디스크 저장: 한 번 만든 것은 서버를 다시 켜도 다시 추론하지 않는다.

키에 '같은 결과가 나와야 하는 조건'(종목, 그날 봉 날짜, 기사 목록 해시 …)을 넣고, 조건이 같으면 저장한 값을 그대로 쓴다.
저장 위치: data/ai_cache/<키 해시>.json  ({key, saved_at, value}). PC 마다 따로.
"""
from __future__ import annotations
import hashlib
import json
import threading
import time
from pathlib import Path

_lock = threading.Lock()


class DiskCache:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / f"{hashlib.sha1(key.encode('utf-8')).hexdigest()[:24]}.json"

    def get(self, key: str, ttl: float | None = None):
        path = self._path(key)
        if not path.exists():
            return None
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return None
        if saved.get("key") != key or (ttl is not None and time.time() - saved.get("saved_at", 0) > ttl):
            return None
        return saved.get("value")

    def put(self, key: str, value) -> None:
        with _lock:
            self.root.mkdir(parents=True, exist_ok=True)
            self._path(key).write_text(json.dumps({"key": key, "saved_at": time.time(), "value": value}, ensure_ascii=False, default=str),
                                       encoding="utf-8")

    def remember(self, key: str, fn, ttl: float | None = None):
        """저장된 값이 있으면 그것, 없으면 fn() 을 불러 저장하고 돌려준다."""
        hit = self.get(key, ttl)
        if hit is not None:
            return hit
        value = fn()
        if value is not None:
            self.put(key, value)
        return value


def digest(items) -> str:
    """기사 목록 같은 것의 짧은 해시 (같은 목록이면 같은 값)."""
    return hashlib.sha1(json.dumps(items, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
