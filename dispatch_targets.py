"""Explicit, user-confirmed LINE room renames; never fuzzy-match rooms."""

ADVERTISING_TARGET = "【利潤10%】自動排廣告群組"
TARGET_ALIASES = {
    "【自動排廣告群組】": ADVERTISING_TARGET,
    "自動排廣告群組": ADVERTISING_TARGET,
}


def canonical_target(name):
    name = name.strip()
    return TARGET_ALIASES.get(name, name)


def same_target(left, right):
    return bool(left.strip() and right.strip()) and canonical_target(left) == canonical_target(right)
