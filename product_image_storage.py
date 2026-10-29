"""Append-only image library; no quote cells, approvals, or LINE operations."""
from collections import Counter

from dispatch_manager import DispatchError, now
from image_limits import MAX_PRODUCT_ORIGINALS
from product_images import PRODUCT_IMAGE_SHEET, checked_assets, source_problem, subject_key


def checked_image_diagnostic(value):
    """Only fixed categories and bounded integers may leave storage failures."""
    if (not isinstance(value, dict) or not {"kind", "origin"} <= set(value)
            or set(value) - {"kind", "origin", "http_status", "api_code"}
            or value.get("kind") not in {"google_sheets_api", "http", "network_timeout", "network_connection", "nas", "read_budget_wait"}
            or value.get("origin") != ("local" if value.get("kind") == "read_budget_wait" else "typed")
            or (value.get("kind") == "read_budget_wait" and set(value) != {"kind", "origin"})):
        return None
    result = {"kind": value["kind"], "origin": value["origin"]}
    for key, low, high in (("http_status", 100, 599), ("api_code", 0, 999999)):
        if key in value:
            if type(value[key]) is not int or not low <= value[key] <= high:
                return None
            result[key] = value[key]
    return result


def image_error_diagnostic(exc):
    """Inspect native exception metadata/cause chains, never remote text or JSON."""
    from gspread.exceptions import APIError
    import requests
    from synology_image_store import NasDiagnosticError
    from batch_read_pacing import ReadBudgetWaitExceeded
    seen, fallback = set(), None
    while exc is not None and id(exc) not in seen and len(seen) < 16:
        seen.add(id(exc))
        if isinstance(exc, ReadBudgetWaitExceeded):
            return {"kind": "read_budget_wait", "origin": "local"}
        kind = ("google_sheets_api" if isinstance(exc, APIError) else
                "nas" if isinstance(exc, NasDiagnosticError) else
                "http" if isinstance(exc, requests.exceptions.HTTPError) else None)
        if kind:
            result = {"kind": kind, "origin": "typed"}
            status = (exc.http_status if isinstance(exc, NasDiagnosticError) else
                      getattr(getattr(exc, "response", None), "status_code", None))
            code = exc.api_code if isinstance(exc, NasDiagnosticError) else getattr(exc, "code", None)
            if type(status) is int and 100 <= status <= 599:
                result["http_status"] = status
            if type(code) is int and 0 <= code <= 999999:
                result["api_code"] = code
            return result
        if isinstance(exc, requests.exceptions.Timeout):
            fallback = {"kind": "network_timeout", "origin": "typed"}
        elif isinstance(exc, requests.exceptions.ConnectionError):
            fallback = {"kind": "network_connection", "origin": "typed"}
        exc = exc.__cause__ or exc.__context__
    return fallback


def image_error_summary(diagnostic):
    value = checked_image_diagnostic(diagnostic)
    if value is None:
        return "未分類保存錯誤；詳細內容已隱藏"
    if value["kind"] == "read_budget_wait":
        return "批次讀取等待已達上限；尚未送出請求，請稍後明確接續"
    parts = [{"google_sheets_api": "Google Sheets API 回應失敗", "http": "服務 HTTP 回應失敗",
              "network_timeout": "服務連線或讀取逾時", "network_connection": "服務連線中斷",
              "nas": "NAS 回應失敗"}[value["kind"]]]
    if "http_status" in value:
        parts.append(f"HTTP {value['http_status']}")
    if "api_code" in value:
        parts.append(f"API 代碼 {value['api_code']}")
    return "；".join(parts)


class ProductImageStoreMixin:
    def product_image_bindings(self):
        from dispatch_storage import decode_records
        ws = self._sheet(PRODUCT_IMAGE_SHEET)
        latest = {}
        for record in decode_records(ws.get_all_values()[1:]) if ws else []:
            identity, value = record["entity"], record["value"]
            previous = latest.get(identity)
            if record["parent"] != (previous["_revision"] if previous else ""):
                raise DispatchError("商品圖片對應有同時修改衝突，請先核對圖庫紀錄")
            if (value.get("schema") != 1 or value.get("identity") != identity
                    or not value.get("subject") or not isinstance(value.get("assets"), list)
                    or not 1 <= len(value["assets"]) <= MAX_PRODUCT_ORIGINALS
                    or len(set(value["assets"])) != len(value["assets"])
                    or any(not isinstance(a, str) or len(a) != 64 for a in value["assets"])):
                raise DispatchError("商品圖片對應內容不完整，停止自動帶入")
            latest[identity] = {**value, "_revision": record["id"]}
        return latest

    def product_image_refs(self, products):
        """Read the small binding index only; images load for the focused item."""
        bindings = self.product_image_bindings()
        counts = Counter(p["identity"] for p in products)
        refs, warnings = {}, []
        for source in products:
            identity = source["identity"]
            refs[identity] = []
            saved = bindings.get(identity)
            if not saved:
                continue
            if counts[identity] != 1 or saved["subject"] != subject_key(source):
                warnings.append(f"{source.get('code') or identity}：商品身分變動／NO 重複，舊圖未自動帶入")
                continue
            refs[identity] = [{"sha256": asset, "stored_id": asset,
                               "name": f"已綁定原圖 {n}", "binding_revision": saved["_revision"],
                               "reference": "商品圖庫原圖（已唯一綁定，仍須核對是否同款）"}
                              for n, asset in enumerate(saved["assets"], 1)]
        return {"images": refs, "warnings": warnings, "bindings": bindings}

    def put_assets(self, assets):
        """Store original bytes in bounded requests; never split a record write."""
        from dispatch_storage import IMAGE_SHEET, asset_bytes, encode_record, require_legacy_image_size, validate_image
        unique = {}
        for asset in assets:
            raw = asset_bytes(asset)
            require_legacy_image_size(raw)
            clean = validate_image(raw, asset["name"])
            unique[clean["sha256"]] = clean
        if len(unique) > 750 or sum(len(a["data"]) for a in unique.values()) > 70 * 1024 * 1024:
            raise DispatchError("本次原圖總量太大，請將圖片包分批保存")
        if not unique:
            return []
        ws = self._sheet(IMAGE_SHEET, create=True)
        self._image_index = None
        # Loading the index does not load unrelated image payloads.
        self._asset_rows(ws, set())
        present = {row[1] for row in self._image_index if len(row) > 1} & set(unique)
        if present:
            self.get_assets(present)  # Corruption must not be mistaken for a hit.
        groups, pending = [], []
        for identity, asset in unique.items():
            if identity in present:
                continue
            rows = encode_record(identity, asset)
            if pending and len(pending) + len(rows) > 80:
                groups.append(pending)
                pending = []
            pending.extend(rows)
        if pending:
            groups.append(pending)
        for rows in groups:
            try:
                ws.append_rows(rows, value_input_option="RAW", table_range="A:H")
            except Exception as exc:
                self._image_index = None
                raise DispatchError("原圖保存結果待確認；請重新載入後只重試圖片，不要重複新增商品") from exc
        self._image_index = None
        self.get_assets(unique)  # Verify original bytes before publishing bindings.
        return list(unique)

    def save_product_images(self, assignments, *, actor, origin, replace=False):
        """Per-item outcomes; explicit revisions prevent silent photo replacement."""
        from dispatch_storage import encode_record
        if not actor.strip() or not origin.strip() or not 1 <= len(assignments) <= 150:
            raise DispatchError("請填圖片保存人，且每次選擇 1～150 款商品")
        identities = [a["source"]["identity"] for a in assignments]
        if len(set(identities)) != len(identities):
            raise DispatchError("圖片保存範圍含重複商品")
        fresh, bindings = self.catalog(), self.product_image_bindings()
        output, prepared = [], []
        for assignment in assignments:
            source, identity = assignment["source"], assignment["source"]["identity"]
            row = {"品號": source.get("code") or identity, "商品": source["name"], "結果": "待處理", "原因": ""}
            output.append(row)
            try:
                problem = source_problem(source, fresh)
                if problem:
                    raise DispatchError(problem)
                assets = checked_assets(assignment["assets"])
                ids, saved = [a["sha256"] for a in assets], bindings.get(identity)
                if saved and saved["subject"] == subject_key(source) and saved["assets"] == ids:
                    self.get_assets(ids)
                    row.update(結果="已存在", 原因="已綁定同一組原圖，不重複寫入")
                    continue
                if (saved.get("_revision", "") if saved else "") != assignment["expected_revision"]:
                    raise DispatchError("圖片對應已在其他視窗更新，請重新載入")
                if saved and not replace:
                    raise DispatchError("已有不同圖片，請逐款確認替換，不自動覆蓋")
                prepared.append((assignment, assets, row))
            except (ValueError, KeyError) as exc:
                row["原因"] = str(exc)
        if not prepared:
            return output
        try:
            self.put_assets([asset for _, assets, _ in prepared for asset in assets])
            # An upload can take time: re-read identity and binding revisions.
            fresh, bindings = self.catalog(), self.product_image_bindings()
        except Exception as exc:
            diagnostic = image_error_diagnostic(exc)
            detail = image_error_summary(diagnostic) if diagnostic is not None else str(exc)
            for _, _, row in prepared:
                row["原因"] = "圖庫未完成：" + detail
                row["diagnostic"] = diagnostic
            return output
        pending = []
        for assignment, assets, row in prepared:
            source, identity = assignment["source"], assignment["source"]["identity"]
            try:
                problem = source_problem(source, fresh)
                if problem:
                    raise DispatchError(problem)
                saved = bindings.get(identity)
                parent = saved.get("_revision", "") if saved else ""
                if parent != assignment["expected_revision"]:
                    raise DispatchError("圖片對應已更新，未套用這次配對")
                value = {"schema": 1, "identity": identity, "subject": subject_key(source),
                         "source_hash": source["source_hash"], "assets": [a["sha256"] for a in assets],
                         "actor": actor.strip(), "origin": origin.strip(), "at": now()}
                rows = encode_record(identity, value, parent)
                pending.append((identity, value, rows, row))
            except Exception as exc:
                row["原因"] = str(exc)
        if pending:
            try:
                ws = self._sheet(PRODUCT_IMAGE_SHEET, create=True)
                # Append this batch's compact mappings together, not 150 writes.
                ws.append_rows([r for _, _, rows, _ in pending for r in rows],
                               value_input_option="RAW", table_range="A:H")
                observed = self.product_image_bindings()
                for identity, value, rows, row in pending:
                    saved = observed.get(identity)
                    if not saved or saved["_revision"] != rows[0][0] or any(saved[k] != v for k, v in value.items()):
                        row["原因"] = "圖片對應寫後核對不符，請重新載入"
                    else:
                        row.update(結果="已綁定", 原因="下次開啟本商品會自動帶入；未標記已核對")
            except Exception as exc:
                diagnostic = image_error_diagnostic(exc)
                detail = image_error_summary(diagnostic) if diagnostic is not None else str(exc)
                for _, _, _, row in pending:
                    row["原因"] = "對應未完成或結果待確認：" + detail + "；重新載入後核對，不要重複新增商品"
                    row["diagnostic"] = diagnostic
        return output
