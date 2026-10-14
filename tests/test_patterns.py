import numpy as np
import pandas as pd

from backend import patterns


def _frame(closes, vols=None):
    t = pd.date_range("2024-01-01", periods=len(closes), freq="B")
    vols = vols if vols is not None else [1e6] * len(closes)
    return pd.DataFrame([{"time": t[i].date().isoformat(), "open": c, "high": c + 0.5, "low": c - 0.5,
                          "close": c, "volume": vols[i]} for i, c in enumerate(closes)])


def _seg(a, b, n):
    return list(np.linspace(a, b, n, endpoint=False))


def test_double_bottom_detected_on_neckline_break_and_not_before():
    # 하락 130→100, 반등 →112, 재하락 →100.5, 넥라인(112) 돌파 →118
    closes = [130.0] * 20 + _seg(130, 100, 40) + _seg(100, 112, 12) + _seg(112, 100.5, 12) + _seg(100.5, 118, 20) + [118] * 5
    df = _frame(closes)
    ev = [e for e in patterns.events(df) if e[1] == "pat_db"]
    assert len(ev) == 1
    k = ev[0][0]
    assert df.close[k] > 112 >= df.close[k - 1]           # 넥라인을 처음 넘은 그날
    # 미래 정보 없음: 돌파 전날까지만 잘라서 보면 돌파 이벤트가 없어야 한다
    assert not [e for e in patterns.events(df.iloc[:k]) if e[1] == "pat_db"]


def test_bull_flag_with_volume():
    # 이벤트는 60번째 봉부터 찾으니 앞에 평평한 구간을 충분히
    closes = [100.0] * 70 + _seg(100, 130, 8) + [130, 129, 128.5, 128, 127.5, 127.8, 127.2, 126.8, 126.5, 126.2] + [131, 133, 134]
    vols = [1e6] * 70 + [3e6] * 8 + [0.6e6] * 10 + [3e6, 2e6, 2e6]
    df = _frame(closes, vols)
    ev = [e for e in patterns.events(df) if e[1] == "pat_flag"]
    assert ev and ev[0][2] == "거래량 동반"


def test_current_reports_forming_pattern_with_drawable_lines():
    closes = [130.0] * 20 + _seg(130, 100, 40) + _seg(100, 112, 12) + _seg(112, 100.5, 12) + _seg(100.5, 108, 8)
    cur = patterns.current(_frame(closes), lambda p: f"{p:.2f}")
    db = [p for p in cur if p["type"] == "pat_db"]
    assert db and db[0]["status"] == "형성 중" and db[0]["distance_pct"] > 0
    assert all(len(ln["points"]) >= 2 for ln in db[0]["lines"])


def test_double_top_is_mirror_of_double_bottom():
    # 상승 70→100, 눌림 →88, 재상승 →99.5, 넥라인(88) 이탈 →82
    closes = [70.0] * 20 + _seg(70, 100, 40) + _seg(100, 88, 12) + _seg(88, 99.5, 12) + _seg(99.5, 82, 20) + [82] * 5
    df = _frame(closes)
    ev = [e for e in patterns.events(df) if e[1] == "pat_dt"]
    assert len(ev) == 1
    k = ev[0][0]
    assert df.close[k] < 88 <= df.close[k - 1]            # 넥라인 아래로 처음 내려간 그날
    assert not [e for e in patterns.events(df) if e[1] == "pat_db"]


def test_forming_double_top_reports_bear_side_and_invalid_line():
    closes = [70.0] * 20 + _seg(70, 100, 40) + _seg(100, 88, 12) + _seg(88, 99.5, 12) + _seg(99.5, 92, 8)
    cur = [p for p in patterns.current(_frame(closes), lambda p: f"{p:.2f}") if p["type"] == "pat_dt"]
    assert cur and cur[0]["status"] == "형성 중" and cur[0]["dir"] == "bear" and cur[0]["distance_pct"] < 0
    assert cur[0]["invalid"] > 100                         # 두 고점 위 = 무효선
    assert any(ln["label"] == "무효선" for ln in cur[0]["lines"])


def _cup(prior):
    """prior 에서 100 까지 오른 뒤(앞선 상승) 컵 100→80→100, 손잡이 →96, 테두리 돌파."""
    return ([prior] * 30 + _seg(prior, 100, 40) + [100 - 20 * np.sin(np.pi * i / 60) for i in range(1, 60)]
            + [100.0] + _seg(100, 96, 6) + _seg(96, 99, 4) + [102, 103, 104])


def test_cup_and_handle_needs_prior_uptrend():
    has = lambda closes: [e for e in patterns.events(_frame(closes)) if e[1] == "pat_cup"]
    assert has(_cup(70.0))            # 70 → 100 (43% 상승) 뒤의 컵
    assert not has(_cup(95.0))        # 앞선 상승이 거의 없으면 컵앤핸들이 아님
