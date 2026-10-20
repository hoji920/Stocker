"""투자 논리 추적 (anthropics/financial-services 의 thesis-tracker 스킬을 개인용으로 옮긴 것, Apache-2.0).

종목마다 '왜 샀나'를 적어 두고(기둥 2~4개 + 틀렸다고 볼 조건 + 기다리는 이벤트), 새 뉴스·실적·가격이 나오면
'논리가 아직 유효한가'를 기둥마다 점검해 기록으로 쌓는다.

- 저장: data/theses.json (관심종목·내가 그은 선처럼 PC 마다 따로).
- 정리(draft): 사용자가 자유롭게 쓴 이유를 AI 가 기둥·틀린 조건·이벤트로 나눈다. 사용자가 고쳐서 저장한다.
- 점검(check): 재료는 코드가 모은 사실(가격 변화, 뉴스, 펀더멘탈, 실적일, 옵션 예상 움직임, 보유)뿐이다.
  AI 는 기둥마다 유지·약화·깨짐·판단 못함과 근거(자료의 날짜·출처)를 쓴다. 사라·팔라는 말은 하지 않는다.
- 점검이 필요할 때: 마지막 점검 뒤 7일이 지났거나, 그 사이 실적 발표일이 지났을 때.
"""
from __future__ import annotations
import json
import threading
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.config import Settings, llm_args

KST = ZoneInfo("Asia/Seoul")
STATUS = ("유지", "약화", "깨짐", "판단 못함")
OVERALL = ("유지", "흔들림", "깨짐")
MAX_CHECKS = 30
_lock = threading.Lock()


class Store:
    def __init__(self, path: Path):
        self.path = path

    def all(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def get(self, symbol: str) -> dict | None:
        return self.all().get(symbol)

    def put(self, symbol: str, value: dict | None) -> None:
        with _lock:
            data = self.all()
            if value is None:
                data.pop(symbol, None)
            else:
                data[symbol] = value
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def clean_lines(v, cap: int = 6) -> list[str]:
    items = v if isinstance(v, list) else str(v or "").splitlines()
    return [str(x).strip(" -•\t") for x in items if str(x).strip(" -•\t")][:cap]


def stale(t: dict, today: str | None = None) -> str | None:
    """점검이 필요하면 그 이유, 아니면 None. earnings_watch = 저장·점검 때 알던 다음 실적일 (지나면 야후는 다음 분기로 넘어가 버려서 적어 둔다)."""
    today = today or date.today().isoformat()
    if not t.get("checks"):
        return "아직 한 번도 점검하지 않았어요"
    last = t["checks"][-1]["at"][:10]
    e = t.get("earnings_watch")
    if e and last < e <= today:
        return f"마지막 점검 뒤 실적 발표({e})가 있었어요"
    if (date.fromisoformat(today) - date.fromisoformat(last)).days >= 7:
        return "마지막 점검 뒤 7일이 지났어요"
    return None


_DRAFT = (
    "너는 개인 투자자의 투자 논리를 정리해 주는 애널리스트다. 한국어 해요체.\n"
    "사용자가 쓴 '왜 샀나'(비어 있을 수 있다)와 회사 자료를 보고 아래로 나눈다. 사용자가 쓴 이유를 우선하고, 없는 이유를 지어내지 않는다.\n"
    "- pillars: 이 투자가 맞으려면 사실이어야 하는 것 2~4개. 각각 한 문장, 나중에 뉴스·숫자로 확인할 수 있게 구체적으로.\n"
    "- invalid_if: 이게 보이면 논리가 틀렸다고 볼 조건 2~3개. 가능하면 숫자나 사건으로 (예: 다음 실적에서 데이터센터 매출 성장률이 20% 아래).\n"
    "- catalysts: 앞으로 확인할 이벤트 2~4개. 각각 '날짜 무엇' 꼴로 (예: 2026-10-29 3분기 실적 발표). 자료에 있는 날짜는 그대로, 날짜를 모르면 무엇만.\n"
    "- horizon: 이 논리가 맞는지 보려면 필요한 기간 (예: 3~6개월).\n"
    "사용자가 이유를 안 썼으면 자료로 흔히 드는 논리를 제안하되 'why_note'에 '사용자 이유 없음 — 제안'이라고 적는다.\n"
    'JSON 으로만: {"pillars": [], "invalid_if": [], "catalysts": [], "horizon": "", "why_note": ""}'
)

_CHECK = (
    "너는 개인 투자자의 투자 논리가 아직 유효한지 점검하는 애널리스트다. 한국어 해요체, 짧고 구체적으로.\n"
    "주어진 자료(JSON)만 쓴다. 숫자·날짜·출처는 자료에 있는 것만. 자료로 판단이 안 되면 '판단 못함'이라고 쓴다.\n"
    "자료의 market 에는 나스닥 100·비트코인·기초자산의 5·20·60일 변화가 있다. 기둥이 그런 바깥 가격에 기대면 여기서 확인한다.\n"
    "- pillars: 기둥마다 status(유지/약화/깨짐/판단 못함) + evidence(자료의 날짜·출처를 붙인 한두 문장).\n"
    "- invalid_if: 조건마다 hit(true/false) + evidence. 확실히 일어났을 때만 true.\n"
    "- overall: 유지(기둥이 대체로 유지) / 흔들림(약화가 있거나 조건에 가까워짐) / 깨짐(핵심 기둥이 깨졌거나 틀린 조건이 일어남).\n"
    "- summary: 지난번 점검 이후 무엇이 바뀌었는지 2문장. 가격만 움직였고 논리와 상관없으면 그렇게 말한다.\n"
    "- watch_next: 다음에 확인할 것 2~3개 (자료의 이벤트 날짜를 쓴다).\n"
    "사라·팔라·목표가 같은 말은 하지 않는다. 가격 하락만으로 논리가 깨졌다고 하지 않는다.\n"
    'JSON 으로만: {"overall": "", "summary": "", "pillars": [{"text": "", "status": "", "evidence": ""}], '
    '"invalid_if": [{"text": "", "hit": false, "evidence": ""}], "watch_next": []}'
)


def _ask(settings: Settings, system: str, payload: dict, effort: str = "low") -> tuple[dict, int]:
    from openai import OpenAI
    model = settings.openai_agent_model
    resp = OpenAI(api_key=settings.openai_api_key).chat.completions.create(
        model=model, response_format={"type": "json_object"}, **llm_args(model, 0.2, effort),
        messages=[{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}])
    return json.loads(resp.choices[0].message.content or "{}"), (resp.usage.total_tokens if resp.usage else 0)


def draft(settings: Settings, symbol: str, why: str, facts: dict) -> dict:
    data, tokens = _ask(settings, _DRAFT, {"symbol": symbol, "user_why": why, "facts": facts})
    return {"why": why, "pillars": clean_lines(data.get("pillars"), 4), "invalid_if": clean_lines(data.get("invalid_if"), 3),
            "catalysts": clean_lines(data.get("catalysts"), 4), "horizon": str(data.get("horizon") or "").strip(),
            "why_note": str(data.get("why_note") or "").strip(), "tokens": tokens}


def check(settings: Settings, symbol: str, t: dict, facts: dict) -> dict:
    last = (t.get("checks") or [None])[-1]
    data, tokens = _ask(settings, _CHECK, {"symbol": symbol, "thesis": {k: t.get(k) for k in ("why", "pillars", "invalid_if", "catalysts", "horizon",
                                                                                                  "created_at", "entry_price")},
                                            "last_check": {k: last.get(k) for k in ("at", "overall", "summary")} if last else None,
                                            "facts": facts}, effort="medium")
    by_text = {str(p.get("text", "")).strip(): p for p in data.get("pillars") or []}
    pillars = []
    for i, text in enumerate(t.get("pillars") or []):
        p = by_text.get(text) or ((data.get("pillars") or [])[i] if i < len(data.get("pillars") or []) else {})
        st = p.get("status") if p.get("status") in STATUS else "판단 못함"
        pillars.append({"text": text, "status": st, "evidence": str(p.get("evidence") or "").strip()})
    inv = []
    for i, text in enumerate(t.get("invalid_if") or []):
        x = (data.get("invalid_if") or [])[i] if i < len(data.get("invalid_if") or []) else {}
        inv.append({"text": text, "hit": bool(x.get("hit")), "evidence": str(x.get("evidence") or "").strip()})
    overall = data.get("overall") if data.get("overall") in OVERALL else ("깨짐" if any(x["hit"] for x in inv) else "흔들림")
    return {"at": now_iso(), "overall": overall, "summary": str(data.get("summary") or "").strip(), "pillars": pillars, "invalid_if": inv,
            "watch_next": clean_lines(data.get("watch_next"), 3), "price": facts.get("price_now"), "tokens": tokens}
