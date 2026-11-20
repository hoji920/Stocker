"""조건 알림: 챗봇 대화에서 정한 '그때 알려줘'를 지켜본다 (확인 버튼을 눌러야 만들어진다).

- zone (가격 구간 low~high): 도착(실시간 가격이 구간 안) · 꺾임(구간에 닿은 뒤 일봉 종가가 구간 아래로) · 돌파(일봉 종가가 구간 위로)
- rsi (일봉 RSI 14, 기준 level): 위로 넘음(종가 기준 RSI ≥ level) · 근처에서 꺾임(만든 뒤 RSI 최고가 level−2 이상까지 왔다가
  그 최고보다 3 넘게 내려오고 level 아래)
종가로 보는 것은 '끝난 일봉'만 쓴다. 꺾임·돌파가 나면 그 조건은 끝, 60일이 지나도 끝.
알림 기록은 alerts.Store 와 같은 곳에 남고 화면·브라우저·텔레그램으로 간다 (alerts.Monitor 가 1분마다 check 를 부른다).
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from backend import analysis

EXPIRE_DAYS = 60


class Conditions:
    def __init__(self, path: Path):
        self.path = path

    def all(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return []

    def save(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")

    def add(self, item: dict) -> dict:
        item = {"id": f"c{int(time.time() * 1000)}", "created": date.today().isoformat(), "state": {}, "done": False, **item}
        self.save(self.all() + [item])
        return item

    def remove(self, cid: str) -> None:
        self.save([c for c in self.all() if c["id"] != cid])

    def active(self) -> list[dict]:
        today = date.today()
        return [c for c in self.all() if not c.get("done") and date.fromisoformat(c["created"]) + timedelta(days=EXPIRE_DAYS) >= today]


def describe(c: dict, fmt=lambda v: f"{v:,.2f}") -> str:
    if c["type"] == "zone":
        return f"{fmt(c['low'])}~{fmt(c['high'])} 구간 (도착 · 꺾임 · 돌파)"
    return f"일봉 RSI {c['level']:g} (위로 넘음 · 근처에서 꺾임)"


def evaluate(c: dict, closed: list[dict], live: float | None, fmt=lambda v: f"{v:,.2f}") -> list[dict]:
    """새로 생긴 알림 목록. c['state'] 를 고친다 (부른 쪽이 저장). closed = 끝난 일봉 (time 'YYYY-MM-DD')."""
    st, out, name = c.setdefault("state", {}), [], c.get("name", c["symbol"])
    after = [x for x in closed if str(x["time"]) >= c["created"]]          # 만든 날부터
    ev = lambda what, title, body: out.append({"key": f"cond:{c['id']}:{what}", "kind": "condition", "symbol": c["symbol"],
                                               "title": f"{name} {title}", "body": body})
    if c["type"] == "zone":
        lo, hi = c["low"], c["high"]
        touch_bar = next((x for x in after if x["high"] >= lo and x["low"] <= hi), None)
        if not st.get("reached") and (touch_bar or (live is not None and lo <= live <= hi)):
            st["reached"] = True
            # 꺾임은 닿은 뒤의 봉으로만 본다: 끝난 봉에서 닿았으면 그 봉부터, 실시간으로 닿았으면 아직 안 끝난 다음 봉부터
            st["from"] = str(touch_bar["time"]) if touch_bar else (str(closed[-1]["time"]) + "~" if closed else c["created"])
            ev("reach", "구간 도착", f"{fmt(lo)}~{fmt(hi)} 구간에 들어왔어요" + (f" (지금 {fmt(live)})" if live else "") + ". 여기서 막히는지 뚫는지가 갈림길이에요.")
        if after:
            x = after[-1]
            if x["close"] > hi:
                c["done"] = True
                ev("break", "구간 돌파", f"{x['time']} 종가 {fmt(x['close'])}로 {fmt(lo)}~{fmt(hi)} 구간 위에서 마감했어요. 거래량이 같이 붙었는지도 보세요.")
            elif st.get("reached") and str(x["time"]) >= st.get("from", "") and x["close"] < lo:
                c["done"] = True
                ev("reject", "구간에서 꺾임", f"{fmt(lo)}~{fmt(hi)}에 닿은 뒤 {x['time']} 종가 {fmt(x['close'])}로 구간 아래에서 마감했어요. 데드캣 쪽 신호로 많이 봐요.")
        return out
    # rsi
    level = float(c["level"])
    if len(closed) < 30 or not after:
        return out
    rsi = analysis.rsi(analysis.to_frame(closed)["close"]).tolist()
    tail = rsi[-len(after):]
    peak = max(tail)
    now = tail[-1]
    st["peak"] = round(peak, 1)
    if now >= level and not st.get("up"):
        st["up"] = True
        ev("up", f"RSI {level:g} 위로", f"{after[-1]['time']} 일봉 RSI {now:.1f} — {level:g} 위로 올라왔어요 (힘이 붙는 쪽).")
    if peak >= level - 2 and now <= peak - 3 and now < level:
        c["done"] = True
        ev("turn", f"RSI {level:g} 근처에서 꺾임", f"RSI 가 {peak:.1f}까지 올랐다가 {after[-1]['time']} {now:.1f}로 꺾였어요. 반등이 힘을 잃는 쪽 신호로 많이 봐요.")
    return out


def stamp(events: list[dict]) -> list[dict]:
    at = datetime.now().isoformat(timespec="seconds")
    return [{**e, "at": at} for e in events]
