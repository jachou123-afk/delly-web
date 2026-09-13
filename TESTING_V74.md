# V74 parsing and fail-closed checks

Run `python -m pip install -r requirements.txt pytest`, then `python -m pytest tests -q`.

Tests extract real functions with AST and exercise the real Streamlit UI with mocked storage. They do not load credentials, read production Sheets, or send messages. The private clipboard regression test skips when the local audit fixtures are absent; supplier messages are intentionally not published.

## Behavior

- One product per paste/save. Multiple independent quotes cannot share the first price/carton parameters.
- Normalize punctuation; recognize suffix single-item weight, explicit weight units, and single/range/general dimensions.
- Preserve supplemental packaging, materials, colors, display-box and extra-cost notes. Retain the input in diagnostics.
- Preserve sales/carton units. Different units require manual conversion and review.
- Missing price, valid weight, carton quantity/unit, conflicting weights (20% threshold), ambiguous numbers, or unresolved charges prevent saving. Derived outputs are blank, never partial costs or zero placeholders.
- Additional charges require supplier confirmation, an explanation, corrected total price/weight inputs, and explicit confirmation. No automatic assumption that a wood frame is included.
- Same code/different name and same-name/code conflicts block new records; no automatic overwrite or silent skip.
- Existing destination tabs only. Read errors fail closed. Fresh data and duplicate checks run before writing. Exact values/formulas are checked after writing. A post-write error warns that data may already exist; it is not a success or invitation to retry.
- Input changes invalidate the review checkbox. Existing price margins and 5% weight uplift are unchanged.

## Limits and scope

Existing Sheets records, images, and LINE are not changed by this release. Pending records are blocked in the tool; no new status column is introduced. Plausible incorrect source numbers still require human source comparison. Sheets has no compare-and-swap: pre-write checks narrow but cannot eliminate simultaneous writes from separate sessions. Do not have multiple operators save to the same destination at exactly the same time.
