from backend.minute_store import PAGE, MinuteStore


def series(start, end):
    return [{"time": t, "close": float(t)} for t in range(start, end)]


class Fake:
    """1분봉 원천 흉내: 지금까지의 봉 중 최근 pages*PAGE 개를 준다."""
    def __init__(self, n):
        self.bars = series(0, n)
        self.calls = []

    def __call__(self, symbol, pages):
        self.calls.append(pages)
        return [dict(b) for b in self.bars[-pages * PAGE:]]


def test_first_fetch_then_top_up_only_newest():
    src, clock = Fake(10_000), [0.0]
    st = MinuteStore(src, now=lambda: clock[0])
    assert len(st.get("A", 30)) == 30 * PAGE
    src.bars += series(10_000, 10_050)          # 새 봉 50개
    src.bars[-1]["close"] = -1.0
    clock[0] = 100
    out = st.get("A", 30)
    assert src.calls == [30, 1]                 # 두 번째는 최신 1페이지만
    assert out[-1]["time"] == 10_049 and out[-1]["close"] == -1.0
    assert len(out) == 30 * PAGE and len({b["time"] for b in out}) == len(out)


def test_cached_within_fresh_window_and_shallower_request_reuses():
    src, clock = Fake(10_000), [0.0]
    st = MinuteStore(src, now=lambda: clock[0])
    st.get("A", 30)
    clock[0] = 2
    assert len(st.get("A", 2)) == 2 * PAGE
    assert src.calls == [30]


def test_deeper_request_refetches_and_gap_refetches_whole():
    src, clock = Fake(200_000), [0.0]
    st = MinuteStore(src, now=lambda: clock[0])
    st.get("A", 2)
    st.get("A", 30)
    assert src.calls == [2, 30]
    src.bars += series(200_000, 200_000 + 5 * PAGE)   # 3페이지 넘게 쌓임 → 이어지지 않음
    clock[0] = 100
    out = st.get("A", 30)
    assert src.calls == [2, 30, 1, 2, 3, 30]   # 이어지지 않아서 필요한 30페이지만 다시
    assert out[-1]["time"] == 200_000 + 5 * PAGE - 1


def test_gap_on_deep_store_refetches_only_requested_depth():
    src, clock = Fake(200_000), [0.0]
    st = MinuteStore(src, now=lambda: clock[0])
    st.get("A", 800)
    src.bars += series(200_000, 200_000 + 5 * PAGE)
    clock[0] = 100
    st.get("A", 30)
    assert src.calls == [800, 1, 2, 3, 30]      # 2분짜리 800페이지를 다시 받지 않는다
