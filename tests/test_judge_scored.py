import threading

from backend import main


def test_scores_are_reused_and_refreshed_in_the_background(monkeypatch):
    calls = []
    done = threading.Event()

    def fake_score(records, candles_of, entries=None):
        calls.append(len(records))
        done.set()
        return [{"n": len(records), "call": len(calls)}]

    monkeypatch.setattr(main.judgments, "score", fake_score)
    monkeypatch.setattr(main, "_judge_score", {})
    monkeypatch.setattr(main, "read_entries", lambda: {})
    recs = [{"symbol": "A"}] * 3
    first = main.judge_scored(recs)
    assert main.judge_scored(recs) is first and calls == [3]           # 1시간 안에는 다시 계산 안 함
    main._judge_score["at"] -= main.JUDGE_SCORE_TTL + 1                # 1시간 지남
    done.clear()
    assert main.judge_scored(recs) is first                            # 일단 지난 값을 바로 주고
    assert done.wait(5) and calls == [3, 3]                            # 뒤에서 새로 계산한다
    assert main.judge_scored(recs + [{"symbol": "B"}])[0]["n"] == 4    # 기록이 늘면 기다려서 새로
