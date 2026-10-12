from datetime import datetime
from zoneinfo import ZoneInfo

from backend import main
from backend.config import llm_args

KST = ZoneInfo("Asia/Seoul")


def test_candle_cache_is_short_only_around_session_open():
    at = lambda day, h, m: datetime(2026, 10, day, h, m, tzinfo=KST)
    assert main.candle_ttl(at(13, 9, 0)) == 30          # 화요일 09:00 국내 개장
    assert main.candle_ttl(at(13, 10, 5)) == 30         # 미국 주간거래 시작 직후
    assert main.candle_ttl(at(13, 14, 0)) == 300        # 그 밖의 시간
    assert main.candle_ttl(at(11, 9, 0)) == 300         # 일요일


def test_llm_args_follow_model_family():
    assert llm_args("gpt-4o-mini", 0) == {"temperature": 0}
    assert llm_args("gpt-5.4-mini", 0.2) == {"reasoning_effort": "low"}
    assert llm_args("gpt-5.5", 0.2, "none") == {"reasoning_effort": "none"}
