"""Streamlit page for preparing and visually reconciling LINE adverts."""
import csv
import re
from copy import deepcopy
from datetime import datetime
from io import StringIO
from zoneinfo import ZoneInfo

import streamlit as st

from dispatch_manager import (
    DispatchError, digest, edit_item,
    finish_batch, item_errors, item_status, new_batch, now, prior_activity,
    reconciliation, record_observation, record_observation_batch, sequence_gaps, source_changes,
)
from dispatch_storage import asset_bytes, validate_image
from dispatch_review import comparison_rows, hydrate_draft, unit_confirmed
from cost_audit_ui import render_cost_review
from supplier_names import vendor_filter_label
from batch_cost_ui import render_batch_cost_tools
from product_image_ui import render_source_images, render_save_current_images
from category_ui import render_category_settings
from batch_image_preview import render_batch_image_preview
from batch_approval import prepare_batch, preparation_issues, confirm_prepared_batch
from image_cache import cached, ORIGINAL_PREFIX, scope_cache
from dispatch_targets import ADVERTISING_TARGET, canonical_target, same_target
from dispatch_workflow import progress_detail, pending_items, selected_pending, combined_copy, build_dispatch_package

STATUS = {"draft": "草稿・待核對", "approved": "已確認・待發送",
          "in_progress": "發送核對中", "completed": "已完成對帳"}
CHAT_TARGETS = ["周俊安", ADVERTISING_TARGET]


def _next_batch_name(history):
    numbers = [int(match[1]) for batch in history
               if (match := re.fullmatch(r"商品批次\s*(\d+)", batch["name"].strip()))]
    return f"商品批次 {max([len(history), *numbers]) + 1:03d}"


def _batch_label(batch):
    try:
        created = datetime.fromisoformat(batch.get("created_at", ""))
        taipei = ZoneInfo("Asia/Taipei")
        created = created.replace(tzinfo=taipei) if created.tzinfo is None else created.astimezone(taipei)
        date = created.strftime("%Y/%m/%d")
    except (TypeError, ValueError):
        date = "日期未記錄"
    return f"{date}｜{batch['name']}｜{batch['target']}｜{STATUS[batch['status']]}"


def _select_item(label, items, batch, mode, format_func):
    options = [item["id"] for item in items]
    focus_key = f"dispatch_focus_{mode}_{batch['id']}"
    focus = st.session_state.get(focus_key, options[0])
    selected = st.selectbox(label, options, index=options.index(focus) if focus in options else 0,
                            key=focus_key + batch.get("_revision", ""), format_func=format_func)
    st.session_state[focus_key] = selected
    return selected


def _save(store, batch, original=None):
    try:
        saved = store.save_batch(batch, expected_revision=original.get("_revision", "") if original else "")
    except Exception as exc:
        st.session_state.pop("dispatch_history", None)
        st.error(str(exc))
        st.info("本次畫面不會把動作當成成功。請按「重新載入雲端」確認最新狀態。")
        return False
    st.session_state["dispatch_active"] = saved["id"]
    st.session_state.pop("dispatch_history", None)
    st.session_state["dispatch_notice"] = "已儲存到雲端。"
    st.rerun()


def _get_asset(store, asset_id):
    scope_cache(st.session_state, store)
    reader = getattr(store, "get_display_asset", store.get_asset)
    return cached(st.session_state, ORIGINAL_PREFIX, asset_id, lambda: reader(asset_id))


def _show_images(store, images, key, download=False):
    ok = True
    for position, asset_id in enumerate(images, 1):
        try:
            asset = _get_asset(store, asset_id)
            data = asset_bytes(asset)
            if asset.get("_cloud_backup"):
                st.warning("NAS 網址仍會轉址；這張圖暫由雲表中校驗相符的原圖顯示。")
            st.image(data, caption=f"圖片 {position} · {asset['name']}", width="stretch")
            if download:
                st.download_button(f"下載圖片 {position}", data, asset["name"], asset["mime"],
                                   key=f"download_{key}_{asset_id}")
        except Exception as exc:
            ok = False
            st.error(f"圖片無法載入：{exc}")
    return ok


def _create(store, history):
    st.subheader("建立新的廣告批次")
    st.caption("從報價表選商品建立草稿，看圖文、處理異常後整批確認一次。未選商品與排除原因會保留。")
    if "dispatch_catalog" not in st.session_state:
        if st.button("載入報價表商品", type="primary", key="dispatch_load_catalog"):
            try:
                st.session_state["dispatch_catalog"] = store.catalog()
                st.rerun()
            except Exception as exc:
                st.error(f"讀取報價表失敗：{exc}")
        return
    products = st.session_state["dispatch_catalog"]
    if not products:
        st.info("報價表內尚無可辨識的商品。")
        return
    c1, c2, c3 = st.columns(3)
    dates = sorted({p["date"] for p in products}, reverse=True)
    dates_selected = c1.multiselect("商品日期", dates, default=dates[:1], key="dispatch_dates")
    vendors = sorted({vendor_filter_label(p["vendor"]) for p in products})
    vendors_selected = c2.multiselect("供應商", vendors, key="dispatch_vendors", placeholder="全部供應商")
    categories = sorted({p["category"] for p in products})
    categories_selected = c3.multiselect("商品分頁", categories, key="dispatch_categories", placeholder="全部分頁")
    scope = [p for p in products if (not dates_selected or p["date"] in dates_selected)
             and (not vendors_selected or vendor_filter_label(p["vendor"]) in vendors_selected)
             and (not categories_selected or p["category"] in categories_selected)]
    if not scope:
        st.info("這組篩選條件沒有商品。")
        return
    st.write(f"本次範圍：{len(scope)} 款")
    if len(scope) > 150:
        st.warning("每批最多 150 款，請先縮小日期、供應商或分頁範圍。")
        return
    st.dataframe([{"品號": p["code"] or p["no"], "商品": p["name"], "供應商": vendor_filter_label(p["vendor"]),
                   "日期": p["date"], "來源": f"{p['category']}!A{p['row']}",
                   "資料檢查": "；".join(p["errors"]) or "通過，仍需核對原文及圖片"} for p in scope],
                 hide_index=True, width="stretch")
    gaps = sequence_gaps(scope)
    if gaps:
        st.warning("編號中間有缺口：" + "；".join(gaps[:15]))
        st.caption("缺號可能來自日期或供應商篩選，不自動補選，也不直接當成錯誤；請核對本次商品範圍。")
    scope_key = digest([p["key"] for p in scope])[:16]
    by_key = {p["key"]: p for p in scope}
    selected = st.multiselect("本批要發的商品（預設全部，可搜尋品號／品名）", list(by_key),
                              default=list(by_key), key="dispatch_selected_" + scope_key,
                              format_func=lambda k: f"{by_key[k]['code'] or by_key[k]['no']}｜{by_key[k]['name']}")
    excluded_reason = ""
    if len(selected) < len(scope):
        excluded_reason = st.text_input("未選取商品的本批排除原因", key="dispatch_scope_reason")
    automatic_name = _next_batch_name(history)
    if ("dispatch_name" not in st.session_state
            or st.session_state["dispatch_name"] == st.session_state.get("dispatch_auto_name")):
        st.session_state["dispatch_name"] = automatic_name
    st.session_state["dispatch_auto_name"] = automatic_name
    name = st.text_input("批次名稱", key="dispatch_name")
    target = st.selectbox("目標聊天室", CHAT_TARGETS, index=None,
                          placeholder="請選擇目標聊天室", key="dispatch_destination_v876")
    actor = st.text_input("核對人", key="dispatch_actor")
    st.caption("同名聊天室請先在 LINE 確認群組成員。圖片與文案的已發狀態，依這個目標聊天室分開記錄。")
    if st.button("建立雲端草稿", type="primary", disabled=not selected or not name.strip() or not target or not actor.strip() or (len(selected) < len(scope) and not excluded_reason.strip())):
        try:
            fresh = store.catalog()
            if name == automatic_name:
                name = _next_batch_name(store.list_batches())
            batch = new_batch(name, target, scope, actor, selected, excluded_reason)
            changes = source_changes(batch, fresh)
            if changes:
                raise DispatchError("來源已變動，請重新載入：" + "；".join(changes))
            _save(store, batch)
        except Exception as exc:
            st.error(str(exc))


def _source_image_tools(store, batch):
    return render_source_images(store, batch)


def _draft_item(store, batch, item, history, source_images=None):
    prefix = f"di_{batch['id']}_{item['id']}_{batch.get('_revision', '')}"
    source = item["source"]
    fresh_problems = source_changes({**batch, "items": [item]}, st.session_state.get("dispatch_review_catalog", []))
    st.subheader(f"第 {item['order']} 款｜{source['code'] or item['id']}")
    st.write(source["name"])
    st.caption(f"{source['vendor']} · {source['category']}!A{source['row']} · 僅需儲存有修改的商品，不必逐款確認")
    hits = prior_activity(batch, item, history)
    if hits:
        st.warning("這款在同一聊天室已有安排／發送紀錄：" + "、".join(hits))
    if source["errors"]:
        for problem in source["errors"]:
            st.error("原表需處理：" + problem)
        st.info("下方保留原始資料供核對。先修正原報價表，再按「更新本款來源與文案」；不會把尚未產生的文案誤判成品號錯誤。")
    if fresh_problems:
        st.warning("原報價表與這份草稿不同，請先按下方「更新本款來源與文案」，再重新核對。")
    with st.expander("報價表原文、參數與詳細算式"):
        cost_report = render_cost_review(store, source, prefix, batch["actor"]) if source.get("cost_audit_required") else None
        st.table(comparison_rows(item, item["copy"]))
    image_ok = True
    candidates = {}
    for reference in source_images or []:
        try:
            asset = ({**_get_asset(store, reference["stored_id"]),
                      "reference": reference["reference"], "binding_revision": reference["binding_revision"]}
                     if reference.get("stored_id") else reference)
            candidates[asset["sha256"]] = asset
        except Exception as exc:
            image_ok = False
            st.error(f"本款圖庫原檔無法讀取：{exc}；未改用其他商品圖片。")
    left, right = st.columns([1, 1.4])
    with left:
        st.markdown("#### 原始資料／原圖")
        if source.get("source_url"):
            st.link_button("開啟這款原報價表（含原圖）", source["source_url"])
        st.caption("以下是原報價表保存的內容，不是完整廠商聊天原文。")
        st.text(source.get("details") or "此舊草稿尚無原始備註，請更新本款來源。")
        st.text(source.get("carton") or "裝箱資訊尚未讀取")
        if candidates:
            for candidate in candidates.values():
                if candidate.get("_cloud_backup"):
                    st.warning("NAS 網址仍會轉址；這張圖暫由雲表中校驗相符的原圖顯示。")
                st.image(asset_bytes(candidate), caption=candidate.get("reference", candidate["name"]), width="stretch")
        elif item["images"]:
            st.caption("本批先前保存的核對圖片；可開啟原表再次對照。")
            image_ok = _show_images(store, item["images"], prefix)
        else:
            st.info("尚未載入這款原圖。可先按上方「載入原報價表圖片」，或在此加入原圖。")
        keep = st.multiselect("保留的圖片", item["images"], default=item["images"],
                              format_func=lambda h: f"圖片 {item['images'].index(h) + 1}", key=prefix + "_keep") if item["images"] else []
        saved_set = bool(candidates and all(a.get("binding_revision") for a in candidates.values()))
        use_candidates = st.multiselect("採用的原表／圖片包圖片", list(candidates),
                                        default=list(candidates) if (len(candidates) == 1 or saved_set) and not keep else [],
                                        format_func=lambda h: candidates[h]["name"], key=prefix + "_candidates_" + digest(list(candidates))[:12]) if candidates else []
        uploads = st.file_uploader("加入商品圖片", type=["jpg", "jpeg", "png", "webp"],
                                   accept_multiple_files=True, max_upload_size=2, key=prefix + "_uploads",
                                   help="每張最多 2 MB、每款最多 5 張。加入後仍須核對圖片中的款式、品名與本款相符。")
        prepared = [(asset_bytes(candidates[h]), candidates[h]) for h in use_candidates]
        for upload in uploads or []:
            try:
                data = upload.getvalue()
                asset = validate_image(data, upload.name)
                prepared.append((data, asset))
                st.image(data, caption="新圖片 · " + upload.name, width="stretch")
            except Exception as exc:
                image_ok = False
                st.error(str(exc))
    with right:
        st.markdown("#### 準備發出的圖片與文案")
        for asset_id in keep:
            image_ok = _show_images(store, [asset_id], prefix + "_out") and image_ok
        for data, asset in prepared:
            st.image(data, caption="將採用 · " + asset["name"], width="stretch")
        if not keep and not prepared:
            st.caption("圖片待加入；不代表原報價表缺圖。")
        if source.get("unit_mode") == "legacy" and not unit_confirmed(item):
            st.warning("以下為預覽：計價單位尚待確認，不能直接列為可發送。")
        text = st.text_area("LINE 文案", item["copy"], height=300, key=prefix + "_copy")
        st.caption("售價與裝箱單位需和報價表一致。修改後請重新核對。")
    actor = batch["actor"]
    library_assets = {a["sha256"]: a for _, a in prepared}
    for identity in keep:
        try:
            library_assets[identity] = _get_asset(store, identity)
        except Exception:
            image_ok = False
    render_save_current_images(store, source, list(library_assets.values()), actor, prefix,
                               disabled=not image_ok or bool(fresh_problems))
    unit_value, unit_note, unit_change = None, "", False
    if source.get("unit_mode") == "legacy":
        st.info(f"舊資料未明示計價單位。原表寫「{source.get('unit_evidence') or '未提供裝箱'}」，僅能提出候選「{source.get('unit') or '待確認'}」，不能證明售價按此單位計算。")
        previous = item.get("unit_confirmation") or {}
        agreed = st.checkbox(f"我已確認售價按「每{source.get('unit') or '？'}」計價，與裝箱單位相同",
                             value=unit_confirmed(item), disabled=not source.get("unit"), key=prefix + "_unit_confirm")
        unit_note = st.text_input("單位確認依據", value=previous.get("evidence", ""),
                                  placeholder="例如：已對照廠商原文，每盒售價與每箱盒數一致", key=prefix + "_unit_evidence")
        st.caption("若售價按套、裝箱按個等不同單位，請先回原報價表換算；不能只改這裡的文字。")
        unit_change = agreed != unit_confirmed(item) or unit_note.strip() != previous.get("evidence", "")
        if unit_change:
            unit_value = source.get("unit", "") if agreed else ""
    excluded = st.checkbox("本批暫緩／排除這款", value=item["excluded"], key=prefix + "_exclude")
    reason = st.text_input("暫緩／排除原因", value=item["reason"], key=prefix + "_reason") if excluded else ""
    duplicate_note = st.text_input("再次安排原因", value=item["duplicate_note"], key=prefix + "_again") if hits else item["duplicate_note"]
    image_ids = list(dict.fromkeys(keep + [a["sha256"] for _, a in prepared]))
    # Auto-displayed library choices / cost reads are not unsaved user edits.
    default_candidates = list(candidates) if (len(candidates) == 1 or saved_set) and not item["images"] else []
    baseline_images = list(dict.fromkeys(item["images"] + default_candidates))
    unchanged = (text.strip() == item["copy"] and image_ids == baseline_images
                 and excluded == item["excluded"] and reason == item["reason"] and not unit_change)
    pending = deepcopy(item)
    pending.update(copy=text.strip(), images=image_ids)
    if source.get("cost_audit_required"):
        pending["cost_audit"] = cost_report
    if unit_change:
        pending["unit_confirmation"] = {"unit": unit_value, "source_hash": source["source_hash"], "actor": actor, "evidence": unit_note.strip()}
    substantive_errors = item_errors(pending, require_review=False, require_source=False)
    substantive_errors.extend(fresh_problems)
    if len(image_ids) > 5:
        substantive_errors.append("每款最多 5 張圖片")
    if substantive_errors and not excluded:
        st.caption("本款尚需：" + "；".join(substantive_errors))
    save = st.button("儲存本款修改", type="primary", key=prefix + "_save",
                           disabled=not actor.strip() or (not excluded and (not image_ok or len(image_ids) > 5)))
    if save:
        try:
            updated = edit_item(batch, item["id"], text=text, images=image_ids,
                                excluded=excluded, reason=reason, reviewed=False, actor=actor,
                                duplicate_note=duplicate_note, confirmed_unit=unit_value, unit_evidence=unit_note,
                                cost_audit=cost_report)
            for data, asset in prepared:
                store.put_asset(data, asset["name"])
            _save(store, updated, batch)
        except Exception as exc:
            st.error(str(exc))
    with st.expander("來源已修改？重新載入本款"):
        st.caption("會改用最新報價表文案並清除本款確認；已配對的圖片保留，仍需重新核對。")
        if st.button("更新本款來源與文案", key=prefix + "_refresh"):
            try:
                matches = [p for p in store.catalog() if p["identity"] == item["id"]]
                if len(matches) != 1:
                    raise DispatchError("來源已刪除或 NO 不唯一，請先檢查報價表")
                updated = deepcopy(batch)
                chosen = next(i for i in updated["items"] if i["id"] == item["id"])
                chosen.update(source=deepcopy(matches[0]),
                              copy=matches[0]["copy"], review=None)
                chosen.pop("unit_confirmation", None)
                chosen.pop("cost_audit", None)
                st.session_state.pop("dispatch_review_catalog", None)
                st.session_state.pop("dispatch_source_images_" + batch["id"], None)
                updated["audit"].append({"at": now(), "actor": actor, "action": "更新本款來源", "item": item["id"]})
                _save(store, updated, batch)
            except Exception as exc:
                st.error(str(exc))
    return not unchanged or duplicate_note != item["duplicate_note"]


def _draft(store, batch, history):
    st.caption("看圖文 → 驗算並處理異常 → 整批確認一次。正常商品不用逐款勾選，不會自動發 LINE。")
    try:
        if "dispatch_review_catalog" not in st.session_state:
            with st.spinner("讀取原報價資料供左右對照…"):
                st.session_state["dispatch_review_catalog"] = store.catalog()
        batch = hydrate_draft(batch, st.session_state["dispatch_review_catalog"])
    except Exception as exc:
        st.error(f"原資料讀取失敗，暫停核對：{exc}")
        return
    items = sorted(batch["items"], key=lambda i: i["order"])
    # Legacy drafts must still expose cost checks when a stale source prevented
    # hydration; do not silently hide the panel or relax confirmation gates.
    for item in items:
        item["source"]["cost_audit_required"] = True
    with st.expander("修改批次名稱或目標聊天室"):
        with st.form("dispatch_details_" + batch["id"] + batch.get("_revision", "")):
            name = st.text_input("批次名稱", value=batch["name"])
            current_target = canonical_target(batch["target"])
            targets = list(dict.fromkeys(CHAT_TARGETS + [current_target]))
            target = st.selectbox("目標聊天室", targets, index=targets.index(current_target))
            actor = st.text_input("修改人", value=batch["actor"])
            if st.form_submit_button("儲存批次設定"):
                if not name.strip() or not target.strip() or not actor.strip():
                    st.error("批次名稱、目標聊天室及修改人不可空白。")
                else:
                    updated = deepcopy(batch)
                    updated.update(name=name.strip(), target=target.strip(), actor=actor.strip())
                    if not same_target(target, batch["target"]):
                        for i in updated["items"]:
                            i["duplicate_note"] = ""
                    updated["audit"].append({"at": now(), "actor": actor.strip(), "action": "修改批次設定"})
                    _save(store, updated, batch)
    search = st.text_input("搜尋本批品號／品名", key="dispatch_review_search_" + batch["id"])
    visible = [i for i in items if search.strip().lower() in (i["source"]["code"] + " " + i["source"]["name"] + " " + i["source"].get("supplier_code", "")).lower()]
    def row_state(item):
        if item["excluded"]:
            return "暫緩／排除"
        if source_changes({**batch, "items": [item]}, st.session_state["dispatch_review_catalog"]):
            return "來源已變動"
        if item["source"]["errors"]:
            return "原表待修正"
        if not unit_confirmed(item):
            return "請確認單位"
        return "待處理" if initial_issues.get(item["id"]) else "待整批確認"
    source_images = _source_image_tools(store, batch)
    def prepare():
        reports = {i["id"]: st.session_state["dispatch_cost_" + i["id"] + i["source"]["source_hash"]][0]
                   for i in items if "dispatch_cost_" + i["id"] + i["source"]["source_hash"] in st.session_state}
        from batch_cost_audit import batch_signature
        last_result = st.session_state.get("dispatch_bulk_result_" + batch["id"])
        if last_result and last_result["signature"] == batch_signature(batch):
            for identity, check in last_result["checks"].items():
                if check.get("error"):
                    reports[identity] = None
        return prepare_batch(batch, source_images, reports)
    initial, _ = prepare()
    initial_issues = preparation_issues(initial, st.session_state["dispatch_review_catalog"], history)
    render_batch_image_preview(store, visible, source_images, batch["id"])
    cached_candidates = source_images
    render_batch_cost_tools(store, batch, items, visible, row_state, cached_candidates)
    st.subheader("③ 處理異常與最後確認")
    st.caption("只處理需修改的商品；原文、參數及詳細算式收在下方，不必每款重複確認。")
    with st.expander("查看／修改單款（需要時再展開）"):
        selected = _select_item("查看商品", items, batch, "draft",
                            lambda k: next(f"{i['order']}. {i['source']['code'] or i['id']}｜{i['source']['name']}" for i in items if i["id"] == k))
        item = next(i for i in items if i["id"] == selected)
        unsaved = _draft_item(store, batch, item, history, source_images.get(item["id"], []))
    if unsaved:
        st.warning("本款有尚未儲存的修改，請先按「儲存本款修改」，再確認整批。")
    with st.expander("調整發送順序"):
        with st.form("dispatch_order_" + batch["id"]):
            position = st.number_input("把目前商品移到第幾款", min_value=1, max_value=len(items), value=item["order"])
            if st.form_submit_button("儲存順序"):
                ordered = [i["id"] for i in items if i["id"] != selected]
                ordered.insert(position - 1, selected)
                updated = deepcopy(batch)
                for i in updated["items"]:
                    i["order"] = ordered.index(i["id"]) + 1
                _save(store, updated, batch)
    prepared, adopted = prepare()
    issues = preparation_issues(prepared, st.session_state["dispatch_review_catalog"], history)
    ready = sum(not i["excluded"] and i["id"] not in issues for i in items)
    excluded = sum(i["excluded"] for i in items)
    blocked = len(issues)
    st.write(f"確認發送到：**{batch['target']}**")
    a, b, c = st.columns(3)
    a.metric("待整批確認", ready)
    b.metric("需處理", blocked)
    c.metric("已排除", excluded)
    if issues:
        st.dataframe([{"品號": i["source"]["code"] or i["id"], "需處理": "；".join(issues[i["id"]])}
                      for i in items if i["id"] in issues], hide_index=True, width="stretch", height=200)
        st.caption("尚未驗算：到上方全選後驗算。缺圖、單位不明、價格差異或來源變動：展開單款修改，或暫緩該款。")
    missing = [i["source"]["code"] or i["id"] for i in prepared["items"] if not i["excluded"]
               and i.get("cost_audit") and not i["cost_audit"].get("source_ready")]
    if missing:
        st.warning(f"有 {len(missing)} 款缺少有效廠商原文；表內重算不能證明原文正確。可補存原文，或在最後確認時知悉此限制；不會標成原文已核對。")
        with st.expander("查看缺原文的品號"):
            st.write("、".join(missing))
    with st.form("dispatch_approve_" + batch["id"] + batch.get("_revision", "")):
        actor = st.text_input("批次確認人", value=batch["actor"])
        checked = st.checkbox("我已確認整批商品、圖文內容、順序及目標聊天室"
                              + (f"，並知悉 {len(missing)} 款缺廠商原文、僅完成表內重算" if missing else ""))
        if st.form_submit_button("確認本批內容，建立待發清單", type="primary", disabled=blocked > 0 or ready == 0 or unsaved):
            if not checked:
                st.error("請先勾選整批確認。")
            else:
                try:
                    approved = confirm_prepared_batch(store, prepared, adopted, actor,
                                                       acknowledge_missing_source=checked and bool(missing))
                    _save(store, approved, batch)
                except Exception as exc:
                    st.error(str(exc))


def _export(batch, history=()):
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["順序", "品號", "商品", "目標聊天室", "狀態", "圖片確認時間", "文案確認時間", "排除原因", "具體情況", "下一步", "同聊天室其他批次紀錄"])
    for i in sorted(batch["items"], key=lambda i: i["order"]):
        values = [i["order"], i["source"]["code"], i["source"]["name"], batch["target"], item_status(i),
                  "; ".join(r["at"] for r in i["image_receipts"]), "; ".join(r["at"] for r in i["text_receipts"]), i["reason"]]
        detail = progress_detail(batch, i, history)
        values.extend(detail[k] for k in ("具體情況", "下一步", "同聊天室其他批次紀錄"))
        writer.writerow(["'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v for v in values])
    st.download_button("下載本批核對清單", output.getvalue().encode("utf-8-sig"),
                       f"廣告批次-{batch['id'][:8]}.csv", "text/csv", key="dispatch_csv_" + batch["id"])


def _batch_actions(store, batch):
    pending = pending_items(batch)
    if not pending:
        return
    st.subheader("整批處理，不必逐款切換")
    st.caption("先確認 LINE 是否已發過；下列選取只影響這次下載／登記，不會取消排除，也不會傳送 LINE。")
    prefix = "dispatch_bulk_send_" + batch["id"] + batch.get("_revision", "")
    by_id = {i["id"]: i for i in pending}
    selection = st.multiselect("本次一起處理的商品", list(by_id), default=list(by_id), key=prefix + "_items",
                               format_func=lambda k: f"{by_id[k]['source']['code']}｜{by_id[k]['source']['name']}")
    selected = [i for i in pending if i["id"] in selection]
    st.write(f"本次 {len(selected)} 款｜目標：{batch['target']}")
    with st.expander("一次複製整批文案／下載圖文包", expanded=True):
        st.caption("圖文包依順序分好每款原圖與文案；已登記的部分不再打包。產生前會整批檢查雲表是否變更。")
        package_key = digest([batch, selection])
        if st.button("核對雲表並準備整批圖文", disabled=not selected, key=prefix + "_prepare"):
            st.session_state.pop("dispatch_download_package", None)
            try:
                selected_pending(batch, selection)
                latest = next((b for b in store.list_batches() if b["id"] == batch["id"]), None)
                if latest is None or digest(latest) != digest(batch):
                    raise DispatchError("另一台裝置已更新本批，請先重新載入雲端")
                changes = source_changes({**batch, "items": selected}, store.catalog())
                if changes:
                    raise DispatchError("；".join(changes))
                with st.spinner("整批讀取原圖中…"):
                    data = build_dispatch_package(batch, selection, lambda identity: _get_asset(store, identity))
                st.session_state["dispatch_download_package"] = (package_key, data)
            except Exception as exc:
                st.error(f"尚未產生圖文包：{exc}")
        package = st.session_state.get("dispatch_download_package")
        if package and package[0] == package_key:
            st.download_button("一次下載所選原圖＋文案", package[1], f"廣告圖文-{batch['id'][:8]}.zip",
                               "application/zip", key=prefix + "_zip")
            text = combined_copy(selected)
            if text:
                st.caption("下方右上角可一次複製所選文案。內容較長時，LINE 仍可能需要分段貼上。")
                st.code(text, language=None)
            st.success("圖文包已準備好；尚未發送，也未更改雲端紀錄。")
    with st.expander("已在 LINE 發完？一次登記所選商品", expanded=True):
        with st.form(prefix + "_receipt"):
            actor = st.text_input("整批登記人", value=batch["actor"])
            evidence = st.text_input("本次 LINE 訊息時間／核對依據", placeholder="例如：9/17 15:20～15:30，已對照所選品號的圖文")
            checked = st.checkbox(f"我已查看「{batch['target']}」，所選商品的圖片和文案都已出現")
            close = st.checkbox("若全批已核對且無異常，同時完成對帳", value=True)
            if st.form_submit_button("一次保存所選商品的發送紀錄", type="primary", disabled=not selected):
                try:
                    if not checked:
                        raise DispatchError("請先確認所選商品確實已在指定聊天室出現")
                    selected_pending(batch, selection)
                    updated = record_observation_batch(batch, item_ids=selection, evidence=evidence,
                                                        actor=actor, target=batch["target"])
                    if close and reconciliation(updated)["can_finish"]:
                        updated = finish_batch(updated, actor, evidence)
                    _save(store, updated, batch)
                except Exception as exc:
                    st.error(str(exc))


def _progress(store, batch, history=()):
    report = reconciliation(batch)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("本批應發", report["expected"])
    c2.metric("已確認完成", report["complete"])
    c3.metric("尚未完成", report["expected"] - report["complete"])
    c4.metric("已排除", report["excluded"])
    if report["expected"]:
        st.progress(report["complete"] / report["expected"])
    st.caption("已確認＝核對人已在 LINE 畫面查驗並登記；不是 LINE 系統回執。網站不會代按傳送。")
    items = sorted(batch["items"], key=lambda i: i["order"])
    view = st.radio("查看範圍", ["待處理", "本批排除", "已完成", "全部"], horizontal=True,
                    key="dispatch_progress_view_" + batch["id"])
    visible = [i for i in items if view == "全部"
               or (view == "本批排除" and i["excluded"])
               or (view == "已完成" and item_status(i) == "已確認完成")
               or (view == "待處理" and not i["excluded"] and item_status(i) != "已確認完成")]
    rows = [progress_detail(batch, i, history) for i in visible]
    if rows:
        st.dataframe([{k: r[k] for k in ("順序", "品號", "商品", "狀態", "具體情況", "下一步")} for r in rows],
                     hide_index=True, width="stretch", row_height=72,
                     height=min(600, 40 + 72 * len(rows)),
                     column_config={"具體情況": st.column_config.TextColumn(width="large"),
                                    "下一步": st.column_config.TextColumn(width="large")})
        if any(r["狀態"] == "原因待釐清" for r in rows):
            st.warning("舊紀錄只寫「已發或有疑點」，沒有逐款說明；不代表每款都有問題，也不能據此認定已發送。")
        with st.expander("查看完整原因與其他批次紀錄"):
            chosen = st.selectbox("查看哪款的原因", range(len(rows)),
                                  format_func=lambda n: rows[n]["品號"] + "｜" + rows[n]["商品"],
                                  key="dispatch_reason_" + batch["id"] + view)
            for key in ("具體情況", "下一步", "原始排除備註", "同聊天室其他批次紀錄"):
                st.write(f"{key}：{rows[chosen][key] or '未填寫'}")
            st.caption("其他批次紀錄僅供查找，不代表本次相同圖文已發送；不自動沿用完成狀態。")
    else:
        st.info("此範圍沒有商品。")
    with st.expander("下載完整核對清單"):
        _export(batch, history)
    if report["integrity_error"]:
        st.error("已確認的批次內容遭變更，停止後續操作。")
        return
    if batch["status"] == "completed":
        st.success("本批已完成逐款對帳。")
        st.write(batch["reconciliation"])
        return
    active = [i for i in items if not i["excluded"]]
    _batch_actions(store, batch)
    if active and st.checkbox("只處理單款／部分圖文／異常", key="dispatch_single_" + batch["id"]):
        _single_progress_item(store, batch, active)
    with st.expander("個別異常與最後對帳", expanded=bool(report["uncertain"] or report["duplicates"] or report["unexpected"])):
        _progress_finish(store, batch, report)


def _single_progress_item(store, batch, active):
    item_id = _select_item("目前要處理的商品", active, batch, "progress",
                           lambda k: next(f"{i['order']}. {i['source']['code']}｜{item_status(i)}｜{i['source']['name']}" for i in active if i["id"] == k))
    item = next(i for i in active if i["id"] == item_id)
    st.subheader(item["source"]["name"])
    st.write(f"發送目標：**{batch['target']}** · {item['source']['code']}")
    if st.button("發送前重新核對本款雲表", key="dispatch_fresh_" + item_id):
        try:
            changes = source_changes({**batch, "items": [item]}, store.catalog())
            if changes:
                raise DispatchError("；".join(changes))
            st.success("目前雲表與本批確認版本一致，請使用下方圖文。")
        except Exception as exc:
            st.error(f"請先停止本款發送：{exc}")
    left, right = st.columns([1, 1.4])
    with left:
        _show_images(store, item["images"], batch["id"] + item_id, download=True)
    with right:
        st.code(item["copy"], language=None)
        if item["image_receipts"]:
            st.success("圖片已確認；如文案尚缺，只處理文案。")
        if item["text_receipts"]:
            st.success("文案已確認；如圖片尚缺，只處理圖片。")
        if item.get("uncertain"):
            st.warning("結果待確認：請先查 LINE 紀錄，釐清後再補缺少部分。")
        prefix = batch["id"] + item_id + batch.get("_revision", "")
        with st.form("dispatch_receipt_" + prefix):
            choices = {"圖片已在正確聊天室出現（本款全部圖片）": "image", "文案已在正確聊天室出現": "text",
                       "操作中斷／結果不明，先列待確認": "uncertain", "已查明待確認結果（保留先前確認紀錄）": "resolve"}
            action = st.selectbox("登記核對結果", list(choices))
            observed_target = st.text_input("請輸入實際核對的聊天室名稱")
            actor = st.text_input("核對人", value=batch["actor"])
            evidence = st.text_input("LINE 訊息日期時間／核對依據", placeholder="例如：9/14 20:35，已逐張對照本款圖片與品號")
            checked = st.checkbox("我已實際查看 LINE 紀錄，以上是查驗結果")
            if st.form_submit_button("儲存核對結果", type="primary"):
                if not checked:
                    st.error("請先實際核對 LINE 紀錄並勾選確認。")
                else:
                    try:
                        updated = record_observation(batch, item_id=item_id, part=choices[action], actor=actor,
                                                     evidence=evidence, target=observed_target)
                        _save(store, updated, batch)
                    except Exception as exc:
                        st.error(str(exc))


def _progress_finish(store, batch, report):
    st.divider()
    st.subheader("本批發送後對帳")
    for field, label in (("missing", "尚無發送確認紀錄"), ("partial", "圖文紀錄不完整"), ("uncertain", "結果待確認"),
                         ("duplicates", "重複紀錄"), ("unexpected", "其他異常")):
        if report[field]:
            st.warning(label + "：" + "、".join(report[field]))
    with st.expander("登記額外／重複發送等異常"):
        with st.form("dispatch_exception_" + batch["id"]):
            note = st.text_input("異常品號及說明")
            actor = st.text_input("異常登記人", value=batch["actor"])
            if st.form_submit_button("登記異常，暫停結案"):
                try:
                    _save(store, record_observation(batch, item_id="", part="unexpected", evidence=note,
                                                    actor=actor, target=batch["target"]), batch)
                except Exception as exc:
                    st.error(str(exc))
        resolved = {e["item"] for e in batch["observations"] if e["part"] == "resolve_unexpected"}
        unresolved = [e for e in batch["observations"] if e["part"] == "unexpected" and e["id"] not in resolved]
        if unresolved:
            with st.form("dispatch_resolve_exception_" + batch["id"]):
                exception_id = st.selectbox("已處理的異常", [e["id"] for e in unresolved],
                                            format_func=lambda k: next(e["evidence"] for e in unresolved if e["id"] == k))
                actor = st.text_input("異常處理人", value=batch["actor"])
                note = st.text_input("實際處理方式與查驗依據")
                if st.form_submit_button("保存異常處理結果"):
                    try:
                        _save(store, record_observation(batch, item_id=exception_id, part="resolve_unexpected",
                                                        evidence=note, actor=actor, target=batch["target"]), batch)
                    except Exception as exc:
                        st.error(str(exc))
    with st.form("dispatch_finish_" + batch["id"] + batch.get("_revision", "")):
        actor = st.text_input("最後對帳人", value=batch["actor"])
        evidence = st.text_input("逐款對帳依據", placeholder="記錄核對範圍、時間及異常處理結果")
        checked = st.checkbox("已逐款比對 LINE 圖片、文案與品號，確認無漏發、重複或多發")
        if st.form_submit_button("完成本批對帳", disabled=not report["can_finish"], type="primary"):
            try:
                if not checked:
                    raise DispatchError("請先完成逐款對帳並勾選確認")
                _save(store, finish_batch(batch, actor, evidence), batch)
            except Exception as exc:
                st.error(str(exc))


def render_dispatch_manager(store_factory):
    st.header("📣 發送管理")
    st.caption("選商品 → 預覽與核對 → 確認待發清單 → 登記 LINE 結果 → 逐款對帳")
    notice = st.session_state.pop("dispatch_notice", "")
    if notice:
        st.success(notice)
    if st.button("重新載入雲端", key="dispatch_reload"):
        st.session_state.pop("dispatch_history", None)
        st.session_state.pop("dispatch_catalog", None)
        st.session_state.pop("dispatch_review_catalog", None)
        for key in list(st.session_state):
            if key.startswith(("dispatch_source_images_", "dispatch_cost_", "dispatch_bulk_result_",
                               "dispatch_library_", "dispatch_category_", "image_binding_revision_")):
                st.session_state.pop(key, None)
        st.rerun()
    try:
        store = store_factory()
        spreadsheet = getattr(store, "spreadsheet", None)
        if spreadsheet is not None:
            st.caption(
                f"發送紀錄雲表：{getattr(spreadsheet, 'title', '未命名')} · "
                f"{getattr(spreadsheet, 'id', '無識別碼')}"
            )
        if "dispatch_history" not in st.session_state:
            st.session_state["dispatch_history"] = store.list_batches()
        history = st.session_state["dispatch_history"]
    except Exception as exc:
        st.error(f"無法讀取發送紀錄：{exc}")
        st.info("請確認雲端連線後重新載入，讀取失敗不會被當成沒有已發紀錄。")
        return
    from nas_image_ui import render_nas_image_management
    from thumbnail_ui import render_thumbnail_management
    scope_cache(st.session_state, store)
    with st.sidebar.expander("圖片維護設定"):
        if st.checkbox("顯示維護工具", key="dispatch_show_maintenance"):
            render_nas_image_management(store)
            render_thumbnail_management(store)
            render_category_settings(store)
    options = [""] + [b["id"] for b in history]
    active = st.session_state.get("dispatch_active", "")
    history_revision = digest([(b["id"], b.get("_revision", "")) for b in history])[:16]
    chosen = st.selectbox("廣告批次", options, index=options.index(active) if active in options else 0,
                          format_func=lambda k: "＋ 建立新批次" if not k else next(_batch_label(b) for b in history if b["id"] == k),
                          key="dispatch_batch_selector_" + active + history_revision)
    if chosen != active:
        st.session_state["dispatch_active"] = chosen
        st.rerun()
    if not chosen:
        _create(store, history)
        st.caption("既有 LINE 歷史不會自動匯入；首次安排舊商品時，請先核對是否已發過。")
        st.caption("儲存草稿時才會建立專用雲端紀錄表。商品報價表的原有列與公式不會被發送管理改寫。")
        return
    batch = next(b for b in history if b["id"] == chosen)
    st.write(f"**{batch['name']}** · {batch['target']} · {STATUS[batch['status']]}")
    st.caption(f"批次 {batch['id'][:8]} · 建立於 {batch['created_at']}")
    if batch["status"] == "draft":
        _draft(store, batch, history)
    else:
        _progress(store, batch, history)
    with st.expander("核對與操作紀錄"):
        st.json({"操作": batch["audit"], "LINE 畫面核對": batch["observations"], "最終對帳": batch["reconciliation"]})
