"""Bound session image caches, measured by stored base64 bytes (not raw size)."""
import hashlib
import json
from dispatch_storage import asset_bytes

ORIGINAL_PREFIX = "dispatch_asset_"
THUMB_PREFIX = "dispatch_thumb_"
LIMITS = {ORIGINAL_PREFIX: (5, 16 * 1024 * 1024), THUMB_PREFIX: (48, 8 * 1024 * 1024)}


def prune(state, prefix):
    limit, size_limit = LIMITS[prefix]
    order_key = prefix + "lru"
    keys = [k for k in state if k.startswith(prefix) and k != order_key]
    order = [k for k in state.get(order_key, []) if k in keys]
    order.extend(k for k in keys if k not in order)
    def size(k):
        value = state.get(k)
        return len(value.get("data", "")) if isinstance(value, dict) else size_limit + 1
    total = sum(size(k) for k in order)
    while order and (len(order) > limit or total > size_limit):
        key = order.pop(0)
        total -= size(key)
        state.pop(key, None)
    state[order_key] = order


def cached(state, prefix, identity, loader):
    prune(state, prefix)  # Also discards excess entries left by pre-pagination code.
    key, order_key = prefix + identity, prefix + "lru"
    if key not in state:
        asset = loader()
        asset_bytes(asset)
        state[key] = asset
    asset = state[key]
    state[order_key] = [k for k in state[order_key] if k != key] + [key]
    prune(state, prefix)
    return asset


def scope_cache(state, store):
    config = getattr(store, "nas_config", None)
    scope = (getattr(getattr(store, "spreadsheet", None), "id", None),
             getattr(config, "base_url", None), getattr(config, "root", None),
             getattr(config, "library_id", None), getattr(store, "nas_config_error", False))
    scope = hashlib.sha256(json.dumps(scope).encode()).hexdigest()
    if state.get("dispatch_image_cache_scope") != scope:
        for key in list(state):
            if key.startswith((ORIGINAL_PREFIX, THUMB_PREFIX)):
                state.pop(key, None)
        state["dispatch_image_cache_scope"] = scope
    for prefix in LIMITS:
        prune(state, prefix)
