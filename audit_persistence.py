"""Durable calculation snapshots; restoring checks versions, never reruns math."""
from collections import Counter, defaultdict
from copy import deepcopy

from batch_cost_audit import batch_signature, result_row
from cost_audit import RULE_VERSION, block, fingerprint
from dispatch_manager import DispatchError, digest, now


def save_result(store, batch, result):
    if batch['status'] != 'draft':
        raise DispatchError('只有草稿可以保存驗算結果')
    updated = deepcopy(batch)
    previous = updated.get('cost_snapshot', {})
    entries = deepcopy(previous.get('entries', {})) if previous.get('schema') == 1 else {}
    by_id = {i['id']: i for i in batch['items']}
    for identity in result['ids']:
        source = by_id[identity]['source']
        entries[identity] = dict(source_hash=source['source_hash'], row=source['row'],
                                 at=result['at'], check=deepcopy(result['checks'][identity]))
    updated['cost_snapshot'] = dict(schema=1, rule=RULE_VERSION, at=result['at'], entries=entries)
    updated['audit'].append(dict(at=now(), actor=batch['actor'], action='保存驗算結果', items=result['ids']))
    return store.save_batch(updated, expected_revision=batch.get('_revision', ''))


def restore_result(store, batch, catalog):
    snapshot = batch.get('cost_snapshot')
    if not snapshot:
        return None
    if snapshot.get('schema') != 1 or not isinstance(snapshot.get('entries'), dict):
        raise DispatchError('已存驗算版本無法辨識；請重新驗算，不沿用舊通過結果')
    current = defaultdict(list)
    for source in catalog:
        current[source['identity']].append(source)
    checks, groups = {}, defaultdict(list)
    items = sorted((i for i in batch['items'] if i['id'] in snapshot['entries']), key=lambda i: i['order'])

    def invalid(identity, reason):
        checks[identity] = dict(error='已存驗算失效：' + reason, status='驗算已失效')

    for item in items:
        identity, source = item['id'], item['source']
        entry = snapshot['entries'][identity]
        checks[identity] = deepcopy(entry['check'])
        matches = current[identity]
        if len(matches) != 1 or any(s.get('source_hash') != entry['source_hash'] or s.get('row') != entry['row']
                                   for s in [source] + matches):
            invalid(identity, '来源內容／列位置變更或品號不唯一，請更新本款來源後重算')
        elif snapshot.get('rule') != RULE_VERSION or (
                checks[identity].get('report') and checks[identity]['report'].get('rule') != RULE_VERSION):
            invalid(identity, '計算規則版本已變更，請重算')
        elif checks[identity].get('report') and not checks[identity].get('error'):
            groups[source['category']].append(item)
    ids = [i['id'] for group in groups.values() for i in group]
    try:
        store._evidence_index = None
        store._evidence_row_index = None
        store._load_evidence(ids)
    except Exception as exc:
        for identity in ids:
            invalid(identity, f'無法確認原文／參數版本：{exc}')
        groups.clear()
    for category, group in groups.items():
        for offset in range(0, len(group), 20):
            chosen = group[offset:offset + 20]
            try:
                ws = store.spreadsheet.worksheet(category)
                ranges = [f"A{i['source']['row']}:L{i['source']['row'] + 5}" for i in chosen]
                values = ws.batch_get(ranges, value_render_option='FORMATTED_VALUE')
                formulas = ws.batch_get(ranges, value_render_option='FORMULA')
                if len(values) != len(chosen) or len(formulas) != len(chosen):
                    raise DispatchError('版本讀取不完整')
                for item, value, formula in zip(chosen, values, formulas):
                    identity = item['id']
                    report = checks[identity]['report']
                    evidence = store._evidence(identity)
                    if fingerprint(block(value)) != report.get('source_hash'):
                        invalid(identity, '原表內容已變更，請更新本款來源後重算')
                    elif fingerprint(block(formula)) != report.get('formula_hash'):
                        invalid(identity, '原表公式已變更，請重算本款')
                    elif (fingerprint(evidence) if evidence else '') != report.get('evidence_hash'):
                        invalid(identity, '原文或計算參數已變更，請重算本款')
            except Exception as exc:
                for item in chosen:
                    invalid(item['id'], f'無法確認來源版本：{exc}')
    rows = [dict(result_row(i, checks[i['id']]), **{'本款驗算時間': snapshot['entries'][i['id']]['at']}) for i in items]
    return dict(signature=batch_signature(batch), at=snapshot['at'], ids=[i['id'] for i in items],
                checks=checks, rows=rows, counts=dict(Counter(r['計算結果'] for r in rows)), persisted=True)


def restore_session(store, batch, catalog, state):
    """One version check per saved revision/session, not on every widget rerun."""
    snapshot = batch.get('cost_snapshot')
    if not snapshot:
        return
    key = 'dispatch_bulk_result_' + batch['id']
    token = digest([batch_signature(batch), snapshot, [(s['identity'], s['source_hash'], s['row']) for s in catalog]])
    if state.get(key, {}).get('restore_token') == token:
        return
    # Clear previous in-memory successes before attempting a cloud read.
    for item in batch['items']:
        if item['id'] in snapshot.get('entries', {}):
            state.pop('dispatch_cost_' + item['id'] + item['source']['source_hash'], None)
    state.pop(key, None)
    result = restore_result(store, batch, catalog)
    result['restore_token'] = token
    state[key] = result
    state.setdefault('dispatch_bulk_selected_' + batch['id'], set(result['ids']))
    for item in batch['items']:
        check = result['checks'].get(item['id'], {})
        if check.get('report') and not check.get('error'):
            state['dispatch_cost_' + item['id'] + item['source']['source_hash']] = (check['report'], check['formulas'])
