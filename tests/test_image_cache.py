from types import SimpleNamespace

from image_cache import cached, prune, scope_cache, ORIGINAL_PREFIX, THUMB_PREFIX
from test_product_image_library import picture


def test_original_count_lru_and_derivatives_never_replace_originals():
    state, reads = {}, []
    def load(n):
        reads.append(n)
        return picture(n)
    for n in range(6):
        cached(state, ORIGINAL_PREFIX, str(n), lambda: load(n))
    assert ORIGINAL_PREFIX + "0" not in state
    cached(state, ORIGINAL_PREFIX, "1", lambda: load(1))
    cached(state, ORIGINAL_PREFIX, "6", lambda: load(6))
    assert ORIGINAL_PREFIX + "2" not in state and ORIGINAL_PREFIX + "1" in state
    cached(state, THUMB_PREFIX, "1", lambda: picture(55))
    assert state[ORIGINAL_PREFIX + "1"] != state[THUMB_PREFIX + "1"]
    assert reads == [0, 1, 2, 3, 4, 5, 6]


def test_limits_cover_base64_bytes_and_old_unbounded_cache():
    state = {ORIGINAL_PREFIX + str(n): {"data": "x" * (4 * 1024 * 1024)} for n in range(20)}
    prune(state, ORIGINAL_PREFIX)
    assert len(state[ORIGINAL_PREFIX + "lru"]) == 4
    state.update({THUMB_PREFIX + str(n): {"data": "x" * 1000} for n in range(100)})
    prune(state, THUMB_PREFIX)
    assert len(state[THUMB_PREFIX + "lru"]) == 48


def test_library_change_clears_both_caches_without_saving_credentials():
    config = SimpleNamespace(base_url="https://nas.test", root="/library", library_id="one", password="do-not-cache")
    store = SimpleNamespace(spreadsheet=SimpleNamespace(id="sheet"), nas_config=config)
    state = {}
    scope_cache(state, store)
    cached(state, ORIGINAL_PREFIX, "one", picture)
    cached(state, THUMB_PREFIX, "one", picture)
    scope_cache(state, store)
    assert ORIGINAL_PREFIX + "one" in state
    config.library_id = "two"
    scope_cache(state, store)
    assert ORIGINAL_PREFIX + "one" not in state and THUMB_PREFIX + "one" not in state
    assert "do-not-cache" not in str(state) and "https://nas.test" not in str(state)
