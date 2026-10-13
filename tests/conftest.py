import pytest

from backend import analysis


@pytest.fixture(autouse=True)
def linear_trendlines(monkeypatch):
    """추세선 눈금은 사용자 설정(data/settings.json 의 log_trends)이라, backend.main 을 불러오는 순간 그 PC 의 값이 들어온다.
    테스트는 설정과 무관하게 기본값(선형)에서 시작한다. 로그를 보는 테스트는 직접 켠다."""
    monkeypatch.setattr(analysis, "LOG_TRENDS", False)
