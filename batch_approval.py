"""Read-only batch preparation followed by one explicit, revalidated decision."""
from copy import deepcopy

from dispatch_manager import DispatchError, approve_batch, item_errors, prior_activity, source_changes


def binding_signature(references):
    if not references or not all(r.get("stored_id") and r.get("binding_revision") for r in references):
        return None
    return [(r["stored_id"], r["binding_revision"]) for r in references]


def prepare_batch(batch, references, reports):
    """Use only saved image bindings and inspected reports, without saving anything."""
    result, adopted = deepcopy(batch), {}
    for item in result["items"]:
        if item["excluded"]:
            continue
        identity = item["id"]
        if not item["images"]:
            signature = binding_signature(references.get(identity, []))
            if signature:
                item["images"] = [asset for asset, _ in signature]
                adopted[identity] = signature
        if identity in reports:
            item["cost_audit"] = deepcopy(reports[identity])
    return result, adopted


def preparation_issues(batch, fresh_products, history):
    issues = {}
    for item in batch["items"]:
        problems = item_errors(item, require_review=False, require_source=False)
        problems += source_changes({**batch, "items": [item]}, fresh_products)
        if not item["excluded"] and prior_activity(batch, item, history) and not item["duplicate_note"].strip():
            problems.append("同聊天室已有安排／發送紀錄，請填寫再次安排原因")
        if problems:
            issues[item["id"]] = list(dict.fromkeys(problems))
    return issues


def confirm_prepared_batch(store, batch, adopted, actor, *, acknowledge_missing_source=False):
    """Re-read cloud evidence and exact assets. Caller alone saves the returned batch."""
    if adopted:
        sources = [i["source"] for i in batch["items"] if i["id"] in adopted and not i["excluded"]]
        current = store.product_image_refs(sources)["images"]
        for identity, signature in adopted.items():
            if binding_signature(current.get(identity, [])) != signature:
                raise DispatchError(f"{identity}：圖片配對已變更，請重新載入雲端再確認")
    store.verify_cost_checks(batch, require_source=False)
    identities = list(dict.fromkeys(h for i in batch["items"] if not i["excluded"] for h in i["images"]))
    # Validate originals, not thumbnails, without retaining a whole batch in RAM.
    for offset in range(0, len(identities), 5):
        store.get_assets(identities[offset:offset + 5])
    fresh = store.catalog()
    return approve_batch(batch, fresh, store.list_batches(), actor, batch_review=True,
                         acknowledge_missing_source=acknowledge_missing_source)
