"""Compatibility imports for explicit, user-confirmed LINE room renames."""

from quote_dispatch.config import (
    advertising_target,
    canonical_target,
    get_common_rules,
    same_target,
)


ADVERTISING_TARGET = advertising_target()
TARGET_ALIASES = dict(get_common_rules().target_aliases)

__all__ = ["ADVERTISING_TARGET", "TARGET_ALIASES", "canonical_target", "same_target"]
