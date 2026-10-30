from streamlit.testing.v1 import AppTest


APP='''
from copy import deepcopy
from unittest.mock import patch
import streamlit as st
import quote_image_source_refresh_ui as ui
from test_quote_image_source_refresh import fixture
if "store" not in st.session_state:
    sheet,store,net,plan,originals,attempt=fixture()
    st.session_state.update(sheet=sheet,store=store,net=net,plan=plan,io=0,pacing=0,clears=0,
        before={k:deepcopy(v.rows) for k,v in sheet.sheets.items()})
def get_store():
    st.session_state["io"]+=1
    return st.session_state["store"]
def pace(store,**kwargs):
    st.session_state["pacing"]+=1
    return store
def clear():st.session_state["clears"]+=1
with patch.object(ui,"paced_nas_store",pace),patch.object(ui,"clear_library_cache",clear):
    ui.render_binding_source_refresh(get_store,st.session_state["plan"])
'''

CONFIRM="我已逐張核對既有原圖仍是本款，僅更新來源指紋並保留主圖與順序"
SAVE="只刷新本款圖組來源指紋（保留主圖與順序）"
VERIFY="只唯讀核對這次來源指紋預備版本"
def widget(app,kind,label):return next(w for w in getattr(app,kind) if w.label==label)
def start():
    app=AppTest.from_string(APP,default_timeout=20).run()
    assert not app.exception
    return app


def test_open_checkbox_and_rerun_have_no_io():
    app=start()
    assert app.session_state["io"]==app.session_state["pacing"]==0
    assert widget(app,"button",SAVE).disabled
    widget(app,"checkbox",CONFIRM).check().run()
    assert not widget(app,"button",SAVE).disabled and app.session_state["io"]==0
    app.run();assert app.session_state["io"]==0 and not app.session_state["net"].sessions


def test_save_paces_once_shows_receipt_and_never_automatically_resends():
    app=start();widget(app,"checkbox",CONFIRM).check().run();widget(app,"button",SAVE).click().run()
    assert not app.exception and not app.error and app.success
    assert app.session_state["io"]==app.session_state["pacing"]==1
    assert len(app.session_state["net"].sessions)==2 and not app.session_state["net"].uploads()
    assert app.code and '"mode": "binding_source_refresh"' in app.code[0].value
    app.run()
    assert widget(app,"button",SAVE).disabled and app.session_state["io"]==1
    before={k:list(v.rows) for k,v in app.session_state["sheet"].sheets.items()}
    widget(app,"button",VERIFY).click().run()
    assert app.session_state["io"]==app.session_state["pacing"]==2
    assert {k:v.rows for k,v in app.session_state["sheet"].sheets.items()}==before
    assert len(app.session_state["net"].sessions)==4


def test_unknown_write_locks_save_and_only_explicit_readonly_recovers():
    from product_images import PRODUCT_IMAGE_SHEET
    app=start();app.session_state["sheet"].sheets[PRODUCT_IMAGE_SHEET].fail_append_after_write=True
    widget(app,"checkbox",CONFIRM).check().run();widget(app,"button",SAVE).click().run()
    assert app.error and not app.success and not app.code
    app.run()
    assert widget(app,"button",SAVE).disabled and app.session_state["io"]==1
    before={k:list(v.rows) for k,v in app.session_state["sheet"].sheets.items()}
    widget(app,"button",VERIFY).click().run()
    assert not app.error and app.success and app.code
    assert {k:v.rows for k,v in app.session_state["sheet"].sheets.items()}==before


def test_plan_reload_after_unknown_write_keeps_exact_attempt_readonly():
    from product_images import PRODUCT_IMAGE_SHEET
    from quote_image_repair import prepare_image_repair
    app=start();app.session_state["sheet"].sheets[PRODUCT_IMAGE_SHEET].fail_append_after_write=True
    widget(app,"checkbox",CONFIRM).check().run();widget(app,"button",SAVE).click().run()
    app.session_state["plan"]=prepare_image_repair(app.session_state["store"],app.session_state["plan"]["source"]["identity"])
    app.run()
    assert widget(app,"button",SAVE).disabled
    widget(app,"button",VERIFY).click().run()
    assert app.success and not app.error and app.session_state["io"]==2


def test_no_stale_hash_or_binding_does_not_offer_new_control():
    app=start();plan=app.session_state["plan"]
    plan["binding"]["source_hash"]=plan["source"]["source_hash"]
    app.session_state["plan"]=plan;app.run()
    assert not app.button and not app.checkbox and app.session_state["io"]==0


def test_source_failure_keeps_attempt_without_success_or_automatic_fallback():
    app=start();app.session_state["sheet"].sheets["G正版"].rows[1][6]="changed"
    widget(app,"checkbox",CONFIRM).check().run();widget(app,"button",SAVE).click().run()
    assert app.error and not app.success and not app.code
    assert not app.session_state["net"].sessions
    app.run();assert widget(app,"button",SAVE).disabled and app.session_state["io"]==1


APP_GATE=APP.replace('    ui.render_binding_source_refresh(get_store,st.session_state["plan"])',
    '    pending=ui.render_binding_source_refresh(get_store,st.session_state["plan"])\n    if not pending:st.button("Synthetic append entry")')


def test_pending_attempt_gates_only_this_identity_and_readonly_verified_receipt_reopens_append():
    from product_images import PRODUCT_IMAGE_SHEET
    from quote_image_repair import prepare_image_repair
    app=AppTest.from_string(APP_GATE,default_timeout=20).run()
    assert not app.exception and any(b.label=="Synthetic append entry" for b in app.button)
    identity=app.session_state["plan"]["source"]["identity"]
    app.session_state["sheet"].sheets[PRODUCT_IMAGE_SHEET].fail_append_after_write=True
    widget(app,"checkbox",CONFIRM).check().run();widget(app,"button",SAVE).click().run()
    assert not any(b.label=="Synthetic append entry" for b in app.button)
    app.session_state["plan"]=prepare_image_repair(app.session_state["store"],identity)
    app.run()
    assert not any(b.label=="Synthetic append entry" for b in app.button)
    other=app.session_state["store"].catalog()[1]["identity"]
    app.session_state["plan"]=prepare_image_repair(app.session_state["store"],other);app.run()
    assert any(b.label=="Synthetic append entry" for b in app.button) and app.session_state["io"]==1
    app.session_state["plan"]=prepare_image_repair(app.session_state["store"],identity);app.run()
    assert not any(b.label=="Synthetic append entry" for b in app.button)
    widget(app,"button",VERIFY).click().run()
    assert app.success and any(b.label=="Synthetic append entry" for b in app.button)
    assert app.session_state["io"]==2


def test_unpublished_or_malformed_receipt_never_releases_append_gate():
    from dispatch_manager import digest
    app=AppTest.from_string(APP_GATE,default_timeout=20).run()
    app.session_state["sheet"].sheets["G正版"].rows[1][6]="changed"
    widget(app,"checkbox",CONFIRM).check().run();widget(app,"button",SAVE).click().run()
    identity=app.session_state["plan"]["source"]["identity"]
    key="quote_image_source_refresh_report_"+digest(identity)
    app.session_state[key]={"結果":"已刷新","receipt":{"mode":"binding_source_refresh","digest":"wrong"}}
    app.run()
    assert not any(b.label=="Synthetic append entry" for b in app.button)
    assert not app.success and not app.code
