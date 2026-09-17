"""Recovery panel renders before any ordinary cloud reads."""
import json
import math
import time

import streamlit as st

from audit_save_recovery import PENDING_KEY, MAX_ATTEMPTS, attempt_save, auto_retry_due
from dispatch_workbench_ui import cache_saved_batch


def complete_save(saved):
    cache_saved_batch(saved)
    st.session_state.pop(PENDING_KEY, None)
    st.session_state['dispatch_notice'] = '驗算結果已保存並讀回確認；未修改商品價格、核准或發送狀態。'


@st.fragment(run_every=5)
def render_pending_save(store_factory):
    pending = st.session_state.get(PENDING_KEY)
    if not pending:
        return
    st.subheader('驗算結果已產生｜雲端保存待確認')
    st.write(f"批次：{pending['payload']['name']}｜本次 {len(pending['result']['ids'])} 款")
    st.warning('處理完成只代表每款都有結果，不代表每款通過或已保存。待保存結果暫存在本視窗；請勿關閉或重新整理瀏覽器。')
    st.dataframe(pending['result']['rows'], hide_index=True, width='stretch')
    wait = max(0, math.ceil(pending['not_before'] - time.monotonic()))
    st.error(pending['error'] or '尚未確認雲端保存結果')
    if pending['rate_limited'] and pending['attempts'] < MAX_ATTEMPTS:
        st.info(f'讀取限流：{wait} 秒後自動查詢並接續保存。等待期間本面板不讀取雲表；不重新驗算。')
    else:
        st.info('自動接續已停止，結果仍保留。可下載備份，再查詢保存狀態；版本衝突不會自動覆蓋。')
    manual = st.button('查詢保存狀態並接續保存（不重新驗算）', disabled=wait > 0,
                       key='dispatch_pending_resume')
    st.download_button('下載本次驗算備份', json.dumps(pending, ensure_ascii=False, indent=2),
                       'pending-audit.json', 'application/json', key='dispatch_pending_download', on_click='ignore')
    if manual or auto_retry_due(pending):
        saved = attempt_save(store_factory, pending)
        if saved is not None:
            complete_save(saved)
            # A delayed result must recheck current versions before it can be used.
            st.session_state.pop('dispatch_review_catalog', None)
            st.session_state.pop('dispatch_bulk_result_' + saved['id'], None)
            st.rerun(scope='app')
        st.rerun(scope='app')
    abandon = st.checkbox('我了解放棄只清除此視窗的待保存結果，雲端可能已有紀錄', key='dispatch_pending_abandon_ack')
    if st.button('放棄待保存結果並重新載入雲端', disabled=not abandon or wait > 0,
                 key='dispatch_pending_abandon'):
        st.session_state.pop(PENDING_KEY, None)
        st.session_state.pop('dispatch_history', None)
        st.session_state.pop('dispatch_review_catalog', None)
        st.session_state.pop('dispatch_bulk_result_' + pending['payload']['id'], None)
        st.rerun(scope='app')
