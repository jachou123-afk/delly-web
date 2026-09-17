"""Selective correction plans. Parsing proposes; only explicit selections write."""
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
import re

from cost_audit import RULE_VERSION, INPUT_LABELS, UNITS, block, calculate, fingerprint, fmt, legacy_inputs, number
from dispatch_manager import DispatchError

FIELDS = {'name': '商品名稱', 'supplier_code': '廠商貨號', 'price': '進價 RMB',
          'qty': '每箱數量', 'unit': '計價／裝箱單位', 'carton_kg': '整箱毛重 kg',
          'unit_g': '單位重量 g', 'prod_size': '產品尺寸', 'color_size': '彩盒尺寸',
          'outer_size': '外箱尺寸', 'extra': '其他商品資訊（含包裝／備註）'}
FINANCIAL = {'price', 'qty', 'unit', 'carton_kg', 'unit_g'}
DETAILS = {'unit': ('計價單位：', r'^計價單位\s*[：:]\s*'),
           'prod_size': ('尺寸 ', r'^(?:產品尺寸|尺寸)\s*[：:]?\s*'),
           'color_size': ('彩盒尺寸 ', r'^彩盒尺寸\s*[：:]?\s*'),
           'outer_size': ('外箱尺寸 ', r'^外箱尺寸\s*[：:]?\s*')}
FORMULA_KEYS = {2: 'quote_10', 3: 'quote_13', 4: 'quote_15', 5: 'quote_20',
                7: 'weight', 8: 'domestic', 9: 'international', 10: 'cost'}
IMPACT_LABELS = {'quote_10': '10% 報價', 'quote_13': '13% 報價', 'quote_15': '15% 報價',
                 'quote_20': '20% 報價', 'weight': '計費重量 g', 'domestic': '內陸運費 RMB',
                 'international': '國際運費 RMB', 'cost': '到手成本 TWD'}


def detail_parts(text):
    found, extra = {k: [] for k in DETAILS}, []
    for line in text.splitlines():
        key = next((k for k, (_, pattern) in DETAILS.items() if re.match(pattern, line)), None)
        if key:
            found[key].append(re.sub(DETAILS[key][1], '', line))
        else:
            extra.append(line)
    return found, extra


def current_fields(source, formulas):
    saved = block(source['block'])
    values = legacy_inputs(source, formulas)
    found, extra = detail_parts(saved[1][1])
    values.update(name=saved[0][1], supplier_code=source.get('supplier_code', ''), extra='\n'.join(extra))
    for key in ('prod_size', 'color_size', 'outer_size'):
        values[key] = '\n'.join(found[key])
    return {key: str(values.get(key, '')) for key in FIELDS}


def parse_proposal(raw, parser):
    if not raw.strip() or len(raw) > 50000:
        raise DispatchError('請提供本款原文，最多 50000 字')
    common, products = parser(raw)
    if len(products) != 1:
        raise DispatchError('解析必須只有一款商品；不以第一款代替多款來源')
    aliases = {'unit': 'qty_unit', 'carton_kg': 'weight', 'unit_g': 'unit_weight_g'}
    proposed = {k: str(common.get(aliases.get(k, k), '')) for k in FIELDS}
    proposed.update(name=products[0]['name'], supplier_code=products[0]['code'])
    return dict(values=proposed, parsed=deepcopy(common), issues=list(common.get('issues', [])), raw=raw)


def retained_parameters(source, formulas, evidence):
    if (not evidence or evidence.get('schema') != 1 or evidence.get('rule') != RULE_VERSION
            or evidence.get('identity') != source['identity']
            or evidence.get('source_hash') != source['source_hash']
            or evidence.get('formula_hash') != fingerprint(block(formulas))
            or not evidence.get('raw_source', '').strip() or not evidence.get('notes', '').strip()):
        raise DispatchError('缺少版本相符的原報價依據；先補存並核實當時參數，不套用今天的預設值')
    inputs = evidence.get('inputs', {})
    result = {}
    for key in ('dom_rate', 'intl_rate', 'ex_rate'):
        result[key] = fmt(number(inputs.get(key, '')))
    if number(result['ex_rate']) <= 0:
        raise DispatchError('原報價匯率無效')
    return result


def replace_details(text, selected, values):
    found, _ = detail_parts(text)
    for key in selected & DETAILS.keys():
        if len(found[key]) > 1:
            raise DispatchError(FIELDS[key] + '有多行，需先釐清，不能自行刪除合併')
    lines = []
    written = set()
    for line in text.splitlines():
        key = next((k for k, (_, pattern) in DETAILS.items() if re.match(pattern, line)), None)
        if key in selected:
            if values[key]:
                lines.append(DETAILS[key][0] + values[key])
            written.add(key)
        elif key or 'extra' not in selected:
            lines.append(line)
    for key in DETAILS:
        if key in selected and key not in written and values[key]:
            lines.append(DETAILS[key][0] + values[key])
    if 'extra' in selected and values['extra']:
        if any(re.match(pattern, line) for line in values['extra'].splitlines() for _, pattern in DETAILS.values()):
            raise DispatchError('備註不可夾帶計價單位或尺寸欄位；請分別選取對應欄位')
        lines.extend(values['extra'].splitlines())
    return '\n'.join(lines)


def plan_correction(snapshot, selected, edits, proposal, *, actor, basis, formula_builder):
    selected = set(selected)
    if not selected or not selected <= FIELDS.keys():
        raise DispatchError('請明確勾選要修正的欄位，預設不修改任何欄位')
    if not actor.strip() or not basis.strip() or not proposal.get('raw', '').strip():
        raise DispatchError('請填操作者、原文及修正依據')
    source = snapshot['source']
    before = block(snapshot['formulas'])
    old = current_fields(source, before)
    values = {**old, **{k: str(edits.get(k, '')).strip() for k in selected}}
    for key in selected:
        if len(values[key]) > 5000 or (key != 'extra' and '\n' in values[key]):
            raise DispatchError(FIELDS[key] + '內容過長或有多行')
        if key in {'name', 'supplier_code', 'unit'} and not values[key]:
            raise DispatchError(FIELDS[key] + '不可空白')
    after = deepcopy(before)
    if 'name' in selected:
        after[0][1] = values['name']
    if 'supplier_code' in selected:
        after[4][1] = '貨號 ' + re.sub(r'\s+', '', values['supplier_code']).upper()
    if selected & (DETAILS.keys() | {'extra'}):
        after[1][1] = replace_details(before[1][1], selected, values)
    impacts, params = [], {}
    if selected & FINANCIAL:
        if values['unit'] != old['unit'] and not {'price', 'qty'} <= selected:
            raise DispatchError('變更計價單位時，必須一併勾選核實進價及每箱數量（即使數字不變），不能只改單位名稱')
        if proposal.get('issues'):
            raise DispatchError('原文解析仍有疑義，請先釐清：' + '；'.join(proposal['issues']))
        params = retained_parameters(source, before, snapshot.get('evidence'))
        inputs = {**{k: values[k] for k in FINANCIAL}, **params}
        computed = calculate(inputs, source['vendor'])
        numbers = {k: float(number(v)) for k, v in inputs.items() if k != 'unit'}
        formulas = formula_builder(source['row'] + 1, numbers['carton_kg'], numbers['unit_g'],
                                   numbers['qty'], numbers['dom_rate'], numbers['intl_rate'], numbers['ex_rate'],
                                   source['vendor'], final_price=numbers['price'])
        if any(not formulas.get(k) for k in FORMULA_KEYS.values()):
            raise DispatchError('無法產生完整安全公式，不更新價格')
        if 'price' in selected:
            after[1][6] = fmt(number(values['price']))
        if selected & {'qty', 'unit'}:
            after[2][1] = f"裝箱 {fmt(number(values['qty']))}{values['unit']}/箱"
        if 'unit' in selected:
            if values['unit'] not in UNITS:
                raise DispatchError('單位未確認')
            after[0][7] = '重量g/' + values['unit']
        if selected & {'carton_kg', 'unit_g'}:
            kg, g = number(values['carton_kg']), number(values['unit_g'])
            after[3][1] = '／'.join(([f'整箱毛重 {fmt(kg)}KG'] if kg else []) + ([f'單個重量 {fmt(g)}g'] if g else []))
        for col, key in FORMULA_KEYS.items():
            after[1][col] = formulas[key]
            calc_key = 'quote' if key == 'quote_10' else key
            if key.startswith('quote_') and key != 'quote_10':
                divisor = {'quote_13': '0.87', 'quote_15': '0.85', 'quote_20': '0.8'}[key]
                value = (computed['cost'][0] / Decimal(divisor)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)
                equation = f"成本 ÷ {divisor} → 四捨五入 1 位"
            else:
                value, equation = computed[calc_key]
            impacts.append({'項目': IMPACT_LABELS[key], '原值': source['block'][1][col], '修正後': fmt(value), '算式': equation, 'column': col})
        impacts.append({'項目': '廣告售價', '原值': source.get('price', ''),
                        '修正後': fmt(computed['sale'][0]), '算式': computed['sale'][1], 'column': None})
    changes = [dict(row=r, col=c, before=before[r][c], after=after[r][c])
               for r in range(6) for c in range(12) if before[r][c] != after[r][c]]
    if not changes:
        raise DispatchError('沒有實際差異，不重複寫入')
    return dict(schema=1, source=deepcopy(source), spreadsheet_id=snapshot['spreadsheet_id'],
                worksheet_id=snapshot['worksheet_id'], before=before, after=after, changes=changes,
                fields=sorted(selected), edits={k: values[k] for k in selected},
                diff=[{'欄位': FIELDS[k], '雲表原值': old[k], '原文解析值': proposal['values'].get(k, ''),
                       '修正值': values[k]} for k in FIELDS if k in selected],
                impacts=impacts, parameters=params, evidence_hash=fingerprint(snapshot.get('evidence')),
                actor=actor.strip(), basis=basis.strip(), proposal=deepcopy(proposal), rule=RULE_VERSION)


def invalidate_correction(state, identity):
    for key in list(state):
        if key in {'dispatch_catalog', 'dispatch_review_catalog', 'dispatch_history'}:
            state.pop(key, None)
        elif key.startswith('dispatch_cost_' + identity):
            state.pop(key, None)
        elif key.startswith('dispatch_bulk_result_') and identity in state[key].get('ids', []):
            state.pop(key, None)
    state.pop('dispatch_workbench_run', None)
