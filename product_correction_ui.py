"""Problem-row entry, explicit field selection and frozen correction preview."""
import json
import uuid

import streamlit as st

from cost_audit import fingerprint
from correction_storage import CorrectionUncertain, apply_correction, correction_records, load_snapshot, read_outcome
from product_correction import FIELDS, current_fields, invalidate_correction, parse_proposal, plan_correction

PENDING = 'dispatch_pending_product_correction'


def finished(plan):
    invalidate_correction(st.session_state, plan['source']['identity'])
    st.session_state['dispatch_uncertain_corrections'] = [p for p in st.session_state.get('dispatch_uncertain_corrections', [])
                                                       if fingerprint(p['plan']) != fingerprint(plan)]
    st.session_state.pop(PENDING, None)
    st.session_state['dispatch_notice'] = (
        f"{plan['source']['code']} 原商品修正及歷史已讀回確認。請更新本款來源與文案、重新驗算及核對圖片。"
        '既有問題不自動解除；未核准、未發 LINE，其他商品不變。')


def render_pending_correction(store_factory):
    pending = st.session_state[PENDING]
    st.error(pending['error'])
    st.warning('修正結果待確認，禁止直接重送。資料只暫存在本工作階段，請勿關閉／重整瀏覽器。')
    st.caption('操作識別：' + pending['operation'])
    st.dataframe(pending['plan']['diff'], hide_index=True, width='stretch')
    st.download_button('下載待確認修正備份', json.dumps(pending, ensure_ascii=False, indent=2),
                       'pending-correction.json', 'application/json', on_click='ignore')
    if st.button('只查詢修正結果（不重送）'):
        try:
            outcome = read_outcome(store_factory(), pending['plan'], pending['operation'])
            if outcome is None:
                st.warning('尚未查到此操作紀錄；不能因此判定未寫入。保留備份，稍後再查，不自動重送。')
            else:
                finished(pending['plan'])
                st.rerun()
        except Exception as exc:
            st.error(str(exc))
    if st.checkbox('我已保留備份，了解回到清單不代表修正成功或失敗'):
        if st.button('保留待查紀錄並返回清單'):
            st.session_state.setdefault('dispatch_uncertain_corrections', []).append(pending)
            invalidate_correction(st.session_state, pending['plan']['source']['identity'])
            st.session_state.pop(PENDING, None)
            st.rerun()


def render_correction_editor(store, batch, item):
    source = item['source']
    tools = getattr(store, 'correction_tools', None)
    key = 'product_fix_' + fingerprint([batch['id'], item['id'], source['source_hash']])[:20]
    unresolved = [p for p in st.session_state.get('dispatch_uncertain_corrections', [])
                  if p['plan']['source']['identity'] == item['id']]
    if unresolved:
        st.warning('本款仍有未釐清的修正，不能再次修改。')
        if st.button('返回本款修正結果查詢', key=key + '_recover'):
            st.session_state[PENDING] = unresolved[0]
            st.rerun()
        return
    if st.button('修正本款原雲表（逐欄）', key=key + '_open'):
        if not tools:
            st.error('修正工具未載入；請重新開啟正式報價工具，不改原表。')
        else:
            try:
                snapshot = load_snapshot(store, source)
                st.session_state[key] = {'snapshot': snapshot}
            except Exception as exc:
                st.error(str(exc))
    if st.button('查看本款原表修正歷史', key=key + '_history'):
        try:
            records = correction_records(store, item['id'])
            if not records:
                st.info('尚無逐欄修正紀錄；舊人工修改不會自動補登。')
            for record in records:
                value = record['value']
                st.write(f"{value['at']}｜{value['actor']}｜{value['basis']}")
                st.dataframe(value['diff'], hide_index=True, width='stretch')
                st.caption('操作識別：' + record['id'] + '；歷史紀錄不是目前商品已核准的證明。')
        except Exception as exc:
            st.error(str(exc))
    editor = st.session_state.get(key)
    if not editor or not tools:
        return
    st.markdown('#### 逐欄修正原商品')
    st.info(f"固定目標：{source['code']}｜{source['vendor']}｜{source['category']} 第 {source['row']} 列。"
            '不新增商品，不改 NO、日期、圖片、格式或其他商品。')
    st.caption('品名／貨號變更可能使舊圖片綁定失效，須重新核對原圖。廠商與參數不在本頁任意改寫範圍。')
    if st.button('關閉逐欄修正（不保存）', key=key + '_close'):
        st.session_state.pop(key, None)
        st.rerun()
    snapshot = editor['snapshot']
    if editor.get('plan'):
        plan = editor['plan']
        st.dataframe(plan['diff'], hide_index=True, width='stretch')
        with st.expander('本次原文及修正依據'):
            st.text(plan['proposal']['raw'])
            st.text(plan['actor'] + '：' + plan['basis'])
        if plan['impacts']:
            st.warning('下列成本／售價會連動更新；不是只改輸入數字。')
            st.dataframe([{k: v for k, v in row.items() if k != 'column'} for row in plan['impacts']], hide_index=True, width='stretch')
            st.write('沿用已保存原參數：匯率 ' + plan['parameters']['ex_rate'] + '；國際費率 '
                     + plan['parameters']['intl_rate'] + '；內陸費率 ' + plan['parameters']['dom_rate'])
        else:
            st.success('本次不變更成本／售價／重量／運費公式。')
        st.caption('未勾選欄位保留。保存前會重新核對資料；發現變動停止。原表與草稿分開，保存後不會自動核准或重發。')
        if st.button('返回修改選取／修正值', key=key + '_back'):
            editor.pop('plan', None)
            st.rerun()
        ack = st.checkbox('我已對照原文／原圖確認同一商品，並同意以上選定欄位及連動價格變更',
                          key=key + '_ack_' + fingerprint(plan))
        if st.button('確認套用選定欄位到原雲表', disabled=not ack, type='primary', key=key + '_apply'):
            operation = uuid.uuid4().hex
            try:
                apply_correction(store, plan, operation)
            except CorrectionUncertain as exc:
                st.session_state[PENDING] = dict(plan=plan, operation=operation, error=str(exc))
                st.session_state.pop(key, None)
                st.rerun()
            except Exception as exc:
                st.error('尚未套用商品修正：' + str(exc))
            else:
                finished(plan)
                st.session_state.pop(key, None)
                st.rerun()
        return
    raw = st.text_area('修正用廠商原文（解析只提供建議）',
                       value=(snapshot.get('evidence') or {}).get('raw_source', ''), key=key + '_raw', height=160)
    if st.button('解析原文並列出欄位差異（不寫入）', key=key + '_parse'):
        try:
            editor['proposal'] = parse_proposal(raw, tools['parse'])
        except Exception as exc:
            editor.pop('proposal', None)
            st.error(str(exc))
    proposal = editor.get('proposal')
    if not proposal or proposal['raw'] != raw:
        st.caption('先解析本款原文；改動原文後需重新解析。')
        return
    if proposal['issues']:
        st.warning('解析疑義：' + '；'.join(proposal['issues']) + '。算價欄位會停止；文字修正仍須逐欄核實。')
    old = current_fields(source, snapshot['formulas'])
    st.dataframe([{'欄位': label, '雲表原值': old[k], '原文解析值': proposal['values'].get(k, '')}
                  for k, label in FIELDS.items()], hide_index=True, width='stretch')
    revision = key + '_' + fingerprint(proposal)[:12]
    selected = st.multiselect('只選要修正的欄位（預設全不選）', list(FIELDS), format_func=FIELDS.get,
                              key=revision + '_fields')
    st.caption('改進價、裝箱、單位或重量須有版本相符的原參數；缺少時先至「報價表原文、參數與詳細算式」補存核實。')
    with st.form(revision + '_form_' + fingerprint(selected)[:8]):
        edits = {}
        for field in selected:
            value = proposal['values'].get(field, '')
            widget = st.text_area if field == 'extra' else st.text_input
            edits[field] = widget('修正值｜' + FIELDS[field], value=value)
        actor = st.text_input('本次修正人')
        basis = st.text_area('修正依據／原文位置及參數核實說明')
        if st.form_submit_button('預覽選定修正與連動價格', disabled=not selected):
            try:
                editor['plan'] = plan_correction(snapshot, selected, edits, proposal,
                                                 actor=actor, basis=basis, formula_builder=tools['formulas'])
            except Exception as exc:
                st.error(str(exc))
            else:
                st.rerun()
