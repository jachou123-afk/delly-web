"""Versioned additive category configuration shared by quote and dispatch UI."""
from category_codes import CATEGORY_SHEET, DEFAULT_CATEGORY_CODES, add_code, validate_codes
from dispatch_manager import DispatchError, now


class CategoryCodeStoreMixin:
    def category_settings(self):
        from dispatch_storage import decode_records
        ws = self._sheet(CATEGORY_SHEET)
        state = {"codes": dict(DEFAULT_CATEGORY_CODES), "_revision": ""}
        for record in decode_records(ws.get_all_values()[1:]) if ws else []:
            value = record["value"]
            if (record["entity"] != "category_codes" or value.get("schema") != 1
                    or record["parent"] != state["_revision"]):
                raise DispatchError("分類設定有版本衝突，請先核對設定紀錄")
            codes = validate_codes(value.get("codes"))
            if any(codes.get(k) != v for k, v in state["codes"].items()):
                raise DispatchError("既有分類代碼被改動，停止產生新廣告")
            state = {**value, "codes": codes, "_revision": record["id"]}
        return state

    def save_category_code(self, category, code, *, actor, expected_revision):
        from dispatch_storage import encode_record
        if not actor.strip():
            raise DispatchError("請填分類設定人")
        if category not in {ws.title for ws in self.spreadsheet.worksheets()}:
            raise DispatchError("找不到指定商品分頁，不會自動建立分頁")
        state = self.category_settings()
        if state["_revision"] != expected_revision:
            raise DispatchError("分類設定已更新，請重新載入")
        codes = add_code(state["codes"], category, code)
        if codes == state["codes"]:
            return state
        value = {"schema": 1, "codes": codes, "actor": actor.strip(), "at": now()}
        rows = encode_record("category_codes", value, state["_revision"])
        ws = self._sheet(CATEGORY_SHEET, create=True)
        try:
            ws.append_rows(rows, value_input_option="RAW", table_range="A:H")
            saved = self.category_settings()
            if saved["_revision"] != rows[0][0] or saved["codes"] != codes:
                raise DispatchError("分類設定寫後核對不符")
        except Exception as exc:
            raise DispatchError("分類設定保存結果待確認，請重新載入；不會自動重試") from exc
        return saved
