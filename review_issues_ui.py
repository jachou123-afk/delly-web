"""Explicit, previewed issue import and evidence-backed resolution UI."""
import streamlit as st

from review_issues import parse_import, source_key


def render_review_issues(store, batch, registry):
    sources = {source_key(i['source']): i['source'] for i in batch['items']}
    relevant = [r for r in registry['issues'].values() if r['source_key'] in sources]
    opened = [r for r in relevant if r['status'] == 'open']
    with st.expander(f"既有問題紀錄（未解決 {len(opened)} 項／含已解決 {len(relevant)} 項）"):
        st.caption('依品號＋供應商＋來源日期帶入所有相關批次。只保存問題，不新增商品、不改報價、不代表已核對或允許發送。')
        if relevant:
            st.dataframe([{'品號': r['code'], '狀態': '待處理' if r['status'] == 'open' else '已登記解決',
                           '欄位': r['field'], '具體問題': r['detail'], '原紀錄': r['current'],
                           '核對依據': r['expected'], '證據': r['evidence']} for r in relevant],
                         hide_index=True, width='stretch')
        with st.expander('匯入已整理的問題清單'):
            st.caption('JSON 陣列，每筆填 code、field、detail、current、expected、evidence、action。只有本批唯一品號可匯入；重複清單不會重建或重開已解決問題。')
            text = st.text_area('問題清單 JSON', key='review_issue_json_' + batch['id'])
            actor = st.text_input('問題登記人', value=batch['actor'], key='review_issue_actor_' + batch['id'])
            if text.strip():
                try:
                    records = parse_import(text, batch, actor)
                except Exception as exc:
                    st.error(str(exc))
                else:
                    st.write(f'預覽：{len(records)} 項；新增 {sum(r["id"] not in registry["issues"] for r in records)} 項')
                    for record in records:
                        st.write(f"{record['code']}｜{record['field']}：{record['detail']}")
                    if st.button('保存既有問題（不修改商品）', key='review_issue_import_' + batch['id']):
                        try:
                            store.import_review_issues(text, batch, actor, registry['_revision'])
                        except Exception as exc:
                            st.error(str(exc))
                        else:
                            st.session_state['dispatch_notice'] = '問題清單已保存並讀回；未解決問題會列入阻擋原因。'
                            st.rerun()
        if relevant:
            selected = st.selectbox('查看／解除問題', relevant,
                                    format_func=lambda r: f"{r['code']}｜{r['field']}｜{r['detail']}",
                                    key='review_issue_select_' + batch['id'])
            st.write('處理方式：' + selected['action'])
            st.write('原核對證據：' + selected['evidence'])
            if selected['status'] == 'resolved':
                st.write('解除紀錄：', selected['resolution'])
            else:
                with st.form('review_issue_resolve_' + selected['id'] + registry['_revision']):
                    actor = st.text_input('問題解除核對人')
                    evidence = st.text_area('實際修正／核對證據（必填）')
                    ack = st.checkbox('已處理此項差異並核對實際來源，不只是表內重算一致')
                    if st.form_submit_button('登記此問題已解決'):
                        try:
                            if not ack:
                                raise ValueError('請先確認已處理此項差異並核對實際來源')
                            store.resolve_review_issue(selected['id'], sources[selected['source_key']], actor, evidence, registry['_revision'])
                        except Exception as exc:
                            st.error(str(exc))
                        else:
                            st.session_state['dispatch_notice'] = '此問題已登記解決並讀回；其他驗算、圖片及發送前檢查仍保留。'
                            st.rerun()
