from backend import ai_cache


def test_disk_cache_reuses_saved_results_and_keys_by_content(tmp_path):
    c = ai_cache.DiskCache(tmp_path)
    calls = []
    make = lambda: calls.append(1) or {"summary": "요약"}
    assert c.remember("news:A:1", make) == {"summary": "요약"}
    assert ai_cache.DiskCache(tmp_path).remember("news:A:1", make) == {"summary": "요약"} and len(calls) == 1   # 다시 켜도 그대로
    c.remember("news:A:2", make)                                                                             # 기사가 바뀌면 새로
    assert len(calls) == 2
    assert c.get("news:A:1", ttl=-1) is None                                                                 # 기한이 지나면 없다
    assert ai_cache.digest(["a", "b"]) == ai_cache.digest(["a", "b"]) != ai_cache.digest(["a", "c"])
