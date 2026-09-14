"""Post-quote image hook. Never retries or modifies the quote write itself."""
from cost_audit import block, number
from dispatch_manager import DispatchError


def save_quote_images(store, category, base_row, expected_block, assets):
    if not assets:
        return []
    expected = block(expected_block)
    products = store.catalog()
    matches = [p for p in products if p["category"] == category and p["row"] == base_row]
    if len(matches) != 1 or sum(p["identity"] == matches[0]["identity"] for p in products) != 1:
        raise DispatchError("無法唯一識別剛保存的商品，圖片未綁定")
    source = matches[0]
    formulas = block(store.spreadsheet.worksheet(category).get(f"A{base_row}:L{base_row + 5}", value_render_option="FORMULA"))
    coords = [(0, 0), (0, 1), (0, 11), (1, 1), (2, 1), (3, 1), (4, 1)]
    if any(formulas[r][c] != expected[r][c] for r, c in coords):
        raise DispatchError("保存後商品身分或來源已變更，圖片未綁定")
    if (number(formulas[1][6]) != number(expected[1][6])
            or any(formulas[1][c] != expected[1][c] for c in (2, 3, 4, 5, 7, 8, 9, 10))):
        raise DispatchError("保存後進價／公式已变更，圖片未綁定")
    return store.save_product_images([{"source": source, "assets": assets, "expected_revision": ""}],
                                    actor="報價操作者", origin="quote_save")
