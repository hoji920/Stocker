from dataclasses import replace

from backend import company
from backend.config import get_settings


def test_profile_is_saved_and_reused_for_a_month(monkeypatch, tmp_path):
    s = replace(get_settings(), data_dir=tmp_path)
    calls = []
    monkeypatch.setattr(company, "fetch_yahoo", lambda sym: calls.append(sym) or {
        "type": "EQUITY", "sector": "Technology", "industry": "Semiconductors", "summary_en": "Makes chips."})
    monkeypatch.setattr(company, "korean", lambda settings, info: {"summary": "칩을 만들어요.", "industry_ko": "반도체", "tokens": 10})
    p = company.profile(s, "NVDA", {"name": "엔비디아", "listDate": "1999-01-22"})
    assert (p["sector_ko"], p["industry_ko"], p["summary"], p["name"]) == ("기술", "반도체", "칩을 만들어요.", "엔비디아")
    assert company.profile(s, "NVDA")["summary"] == "칩을 만들어요." and calls == ["NVDA"]     # 두 번째는 저장한 걸 쓴다 (AI 안 부름)
    path = tmp_path / "company" / "NVDA.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"saved_at": ', '"saved_at": -'), encoding="utf-8")   # 오래된 것
    company.profile(s, "NVDA")
    assert calls == ["NVDA", "NVDA"]
