"""Second-pass checks over the saved six-row quote block."""

from line_ad_copy import validate_sheet_pricing


def validate_saved_quote(rows):
    """Validate saved price and unit evidence; raise instead of guessing."""
    validate_sheet_pricing(rows)
    return True
