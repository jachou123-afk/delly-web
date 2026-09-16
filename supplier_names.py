"""Compatibility imports for canonical supplier labels.

The actual rules live under ``quote_dispatch/rules``.  Only supplier fields
should call these helpers; raw evidence and historical records stay unchanged.
"""

from quote_dispatch.config import (
    get_common_rules,
    normalize_vendor,
    vendor_filter_label,
    vendor_options,
)


ALIASES = dict(get_common_rules().vendor_aliases)
DEFAULT_VENDORS = get_common_rules().default_vendors

__all__ = [
    "ALIASES", "DEFAULT_VENDORS", "normalize_vendor", "vendor_options",
    "vendor_filter_label",
]
