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

def international_rate_for_vendor(vendor, default_rate):
    """Apply confirmed supplier freight terms without changing global defaults."""
    canonical = normalize_vendor(vendor)
    if canonical == "v方小姐":
        return 0.0  # Taiwan delivery is already included in this supplier's price.
    return 9.0 if canonical == "v多品村" else default_rate


__all__ = [
    "ALIASES", "DEFAULT_VENDORS", "normalize_vendor", "vendor_options",
    "vendor_filter_label", "international_rate_for_vendor",
]
