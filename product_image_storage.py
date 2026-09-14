"""Append-only image library; no quote cells, approvals, or LINE operations."""
from collections import Counter

from dispatch_manager import DispatchError, now
from product_images import PRODUCT_IMAGE_SHEET, checked_assets, source_problem, subject_key


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
                    or not 1 <= len(value["assets"]) <= 5
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
        from dispatch_storage import IMAGE_SHEET, asset_bytes, encode_record, validate_image
        unique = {}
        for asset in assets:
            clean = validate_image(asset_bytes(asset), asset["name"])
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
            for _, _, row in prepared:
                row["原因"] = f"圖庫未完成：{exc}"
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
                for _, _, _, row in pending:
                    row["原因"] = f"對應未完成或結果待確認：{exc}；重新載入後核對，不要重複新增商品"
        return output
