"""Write a selective quote patch and its audit record in one Sheets request."""
from copy import deepcopy
import re

from cost_audit import block, fingerprint, number
from dispatch_manager import DispatchError, now
from product_correction import FINANCIAL, FORMULA_KEYS, parse_proposal, plan_correction

CORRECTION_SHEET = '_商品修正'


class CorrectionUncertain(DispatchError):
    pass


def correction_records(store, identity):
    from dispatch_storage import decode_records
    ws = store._sheet(CORRECTION_SHEET)
    if ws is None:
        return []
    index = ws.get('A2:B')
    ranges = []
    for n, row in enumerate(index, 2):
        if len(row) > 1 and row[1] == identity:
            if ranges and ranges[-1][1] == n - 1:
                ranges[-1][1] = n
            else:
                ranges.append([n, n])
    rows = []
    for offset in range(0, len(ranges), 20):
        rows.extend(row for group in ws.batch_get([f'A{a}:H{b}' for a, b in ranges[offset:offset + 20]]) for row in group)
    records = decode_records(rows)
    for record in records:
        if record['value'].get('schema') != 1 or record['value'].get('source', {}).get('identity') != identity:
            raise DispatchError('商品修正紀錄格式或識別不符')
    return records


def load_snapshot(store, expected):
    matches = [s for s in store.catalog() if s['identity'] == expected['identity']]
    if len(matches) != 1:
        raise DispatchError('品號不唯一或原商品不存在，停止修正')
    source = matches[0]
    if source['row'] != expected['row'] or source['source_hash'] != expected['source_hash']:
        raise DispatchError('原商品已被修改或移動；請重新載入、更新本款來源再核對')
    formulas = store.read_cost_source(source)
    store._evidence_index = None
    store._evidence_row_index = None
    evidence = store._evidence(source['identity'])
    return dict(source=deepcopy(source), formulas=formulas, evidence=deepcopy(evidence),
                worksheet_id=store.worksheet(source['category']).id,
                spreadsheet_id=getattr(store.spreadsheet, 'id', ''))


def read_outcome(store, plan, operation):
    if getattr(store.spreadsheet, 'id', '') != plan['spreadsheet_id']:
        raise DispatchError('目的雲表已切換，停止查詢／保存')
    found = [r for r in correction_records(store, plan['source']['identity']) if r['id'] == operation]
    if not found:
        return None
    if len(found) != 1 or found[0]['value'].get('plan_hash') != fingerprint(plan):
        raise DispatchError('修正操作識別或內容不符，不重試寫入')
    ws = store.worksheet(plan['source']['category'])
    if ws.id != plan['worksheet_id']:
        raise DispatchError('原分頁已重建，不能確認修正結果')
    source = plan['source']
    matches = [s for s in store.catalog() if s['identity'] == source['identity']]
    if len(matches) != 1 or matches[0]['row'] != source['row']:
        raise DispatchError('寫後品號重複／列移動，請人工核對，不重送')
    actual = block(ws.get(f"A{source['row']}:L{source['row'] + 5}", value_render_option='FORMULA'))
    expected = deepcopy(plan['after'])
    # Sheets may normalize a numeric 10.0 to 10; never normalize formula/text cells.
    if actual[1][6] != expected[1][6]:
        try:
            if number(actual[1][6]) == number(expected[1][6]):
                actual[1][6] = expected[1][6]
        except ValueError:
            pass
    if actual != expected:
        raise DispatchError('修正紀錄已存在，但目前內容不同或寫後讀回不符；不自動重寫')
    for impact in plan['impacts']:
        col = impact['column']
        value = matches[0].get('price', '') if col is None else matches[0]['block'][1][col]
        if number(value) != number(impact['修正後']):
            raise DispatchError('公式結果與事前驗算不符／尚未更新，請先查結果，不重送')
    return dict(record=found[0]['value'], source=matches[0])


def _check_duplicates(store, plan):
    # Same normalization as the existing quote editor; ignore only this exact ID.
    name = re.sub(r'\s+', ' ', plan['after'][0][1]).strip()
    code = re.sub(r'^貨號\s*[:：]?\s*', '', plan['after'][4][1])
    code = re.sub(r'\s+', '', code).upper()
    for other in store.catalog():
        if other['identity'] == plan['source']['identity']:
            continue
        same_name = name == re.sub(r'\s+', ' ', other['name']).strip()
        same_code = code and code == re.sub(r'\s+', '', other['supplier_code']).upper()
        if same_name or same_code:
            raise DispatchError('與其他商品貨號／品名重複，停止修正：' + (other['code'] or other['identity']))


def apply_correction(store, plan, operation):
    from dispatch_storage import encode_record
    if not re.fullmatch(r'[a-f0-9]{32}', operation):
        raise DispatchError('修正操作識別無效')
    existing = read_outcome(store, plan, operation)
    if existing is not None:
        return existing
    snapshot = load_snapshot(store, plan['source'])
    if snapshot['worksheet_id'] != plan['worksheet_id'] or block(snapshot['formulas']) != plan['before']:
        raise DispatchError('原分頁或公式已變動，停止修正')
    if fingerprint(snapshot['evidence']) != plan['evidence_hash']:
        raise DispatchError('原報價依據已變動，請重新預覽')
    tools = store.correction_tools
    proposal = parse_proposal(plan['proposal']['raw'], tools['parse'])
    rebuilt = plan_correction(snapshot, plan['fields'], plan['edits'], proposal,
                              actor=plan['actor'], basis=plan['basis'], formula_builder=tools['formulas'])
    if rebuilt != plan:
        raise DispatchError('修正內容或解析規則已變動，請重新預覽')
    _check_duplicates(store, plan)
    ledger = store._sheet(CORRECTION_SHEET, create=True)
    # Last fresh read after preparation, before the single atomic data+audit write.
    last = load_snapshot(store, plan['source'])
    if (last['worksheet_id'] != plan['worksheet_id'] or block(last['formulas']) != plan['before']
            or fingerprint(last['evidence']) != plan['evidence_hash']):
        raise DispatchError('送出前來源／公式／依據又有變動，停止修正')
    record = dict(deepcopy(plan), plan_hash=fingerprint(plan), operation=operation, at=now(),
                  state='已提交修正；實際現況以讀回為準')
    requests = []
    for change in plan['changes']:
        r, c, value = change['row'], change['col'], change['after']
        if r == 1 and c in FORMULA_KEYS and set(plan['fields']) & FINANCIAL:
            cell = {'formulaValue': value}
        elif (r, c) == (1, 6):
            cell = {'numberValue': float(value)}
        else:
            cell = {'stringValue': value}  # Literal text, including leading '='.
        requests.append({'updateCells': {'range': {'sheetId': plan['worksheet_id'],
                        'startRowIndex': plan['source']['row'] - 1 + r,
                        'endRowIndex': plan['source']['row'] + r,
                        'startColumnIndex': c, 'endColumnIndex': c + 1},
                        'rows': [{'values': [{'userEnteredValue': cell}]}], 'fields': 'userEnteredValue'}})
    rows = encode_record(plan['source']['identity'], record, record_id=operation)
    requests.append({'appendCells': {'sheetId': ledger.id,
                     'rows': [{'values': [{'userEnteredValue': {'stringValue': str(v)}} for v in row]} for row in rows],
                     'fields': 'userEnteredValue'}})
    try:
        store.spreadsheet.batch_update({'requests': requests})
    except Exception as exc:
        status = getattr(getattr(exc, 'response', None), 'status_code', None)
        if status in (400, 401, 403, 404, 413, 429):
            raise DispatchError('雲表拒絕本次修正，未套用商品更新：' + str(exc)) from exc
        raise CorrectionUncertain('商品可能已修正；保留操作識別，先查結果，不可再次套用：' + str(exc)) from exc
    try:
        result = read_outcome(store, plan, operation)
        if result is None:
            raise DispatchError('尚未讀回修正紀錄')
        return result
    except Exception as exc:
        raise CorrectionUncertain('商品可能已修正；保留操作識別，先查結果，不可再次套用：' + str(exc)) from exc
