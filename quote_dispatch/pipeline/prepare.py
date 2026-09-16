"""Prepare outbound copy only after the saved quote passes validation."""

from line_ad_copy import build_line_ad_copy_from_sheet_block as _build_copy
from quote_dispatch.pipeline.validate import validate_saved_quote


def build_line_ad_copy_from_sheet_block(category_name, rows, category_codes=None):
    validate_saved_quote(rows)
    return _build_copy(category_name, rows, category_codes)
