import streamlit as st
import pandas as pd
import re
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import zhconv
import datetime
import math
import unicodedata
import hashlib
from zoneinfo import ZoneInfo
from license_markers import (
    has_affirmative_license_marker,
    strip_affirmative_license_markers,
)
from quote_dispatch.pipeline.prepare import build_line_ad_copy_from_sheet_block
from category_codes import QUOTE_CATEGORIES
from product_image_ui import render_quote_images, clear_library_cache
from dispatch_storage import CloudDispatchStore
from dispatch_ui import render_dispatch_manager
from supplier_names import normalize_vendor, vendor_options as canonical_vendor_options
from nas_connection_check import render_nas_connection_check
# --- 1. 網頁基本設定 ---
st.set_page_config(page_title="半自動 - 採購報價彙整表", layout="wide")
st.title("🪐 半自動 - 採購報價彙整表 V87.11")
st.caption("報價整理與廣告發送管理，集中在同一個工具。")
with st.sidebar.expander("連線設定"):
    if st.toggle("顯示連線檢查", key="show_connection_check"):
        render_nas_connection_check()
# --- 2. Google Sheets 連線功能 ---
SHEET_NAME = "半自動 - 採購報價彙整表BGD"
SETTINGS_WS = "_設定"
def clean_str(v):
    return (v
        .replace('\u200b', '')
        .replace('\ufeff', '')
        .replace('\u00a0', '')
        .replace('\u3000', '')
        .replace('\r', '')
        .replace('\t', '')
        .strip()
    )
def get_credentials():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    if st.secrets.get("gcp_service_account"):
        s_dict = dict(st.secrets["gcp_service_account"])
        clean_dict = {}
        for k, v in s_dict.items():
            if isinstance(v, str):
                if k == "private_key":
                    clean_dict[k] = v
                else:
                    clean_dict[k] = clean_str(v)
            else:
                clean_dict[k] = v
        clean_dict['token_uri'] = b"https://oauth2.googleapis.com/token".decode('ascii')
        clean_dict['auth_uri'] = b"https://accounts.google.com/o/oauth2/auth".decode('ascii')
        clean_dict['auth_provider_x509_cert_url'] = b"https://www.googleapis.com/oauth2/v1/certs".decode('ascii')
        return ServiceAccountCredentials.from_json_keyfile_dict(clean_dict, scope)
    else:
        return ServiceAccountCredentials.from_json_keyfile_name("giraffe-495919-b7d55659973d.json", scope)

def open_spreadsheet(client):
    """優先使用固定試算表 ID，未設定時才以正式檔名開啟。"""
    spreadsheet_id = clean_str(str(st.secrets.get("spreadsheet_id", "")))
    if spreadsheet_id:
        return client.open_by_key(spreadsheet_id)
    return client.open(SHEET_NAME)

@st.cache_data(ttl=15)
def get_all_sheets_data():
    try:
        creds = get_credentials()
        client = gspread.authorize(creds)
        spreadsheet = open_spreadsheet(client)
        all_data = {}
        for ws in spreadsheet.worksheets():
            if ws.title.startswith("_"):
                continue
            all_data[ws.title] = ws.get_all_values()
        return all_data
    except Exception as e:
        st.error(f"讀取雲端失敗:{e}")
        return None

# --- 2.5 成本參數預設值(存於 Google Sheets 的 _設定 分頁)---
def load_settings():
    defaults = {"ex_rate": 4.7, "intl_rate": 8.5, "dom_rate": 1.5}
    try:
        creds = get_credentials()
        client = gspread.authorize(creds)
        spreadsheet = open_spreadsheet(client)
        try:
            ws = spreadsheet.worksheet(SETTINGS_WS)
        except gspread.exceptions.WorksheetNotFound:
            return defaults
        for row in ws.get_all_values():
            if len(row) >= 2 and row[0] in defaults:
                try:
                    defaults[row[0]] = float(row[1])
                except ValueError:
                    raise ValueError(f"雲端設定 {row[0]} 不是有效數字")
    except Exception as e:
        st.sidebar.error(f"讀取雲端預設失敗:{type(e).__name__}: {e}")
        return None
    return defaults

def save_settings(s):
    try:
        creds = get_credentials()
        client = gspread.authorize(creds)
        spreadsheet = open_spreadsheet(client)
        try:
            ws = spreadsheet.worksheet(SETTINGS_WS)
        except gspread.exceptions.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(title=SETTINGS_WS, rows="10", cols="2")
        rows = [[k, v] for k, v in s.items()]
        ws.update(
            values=rows,
            range_name="A1:B" + str(len(rows)),
            value_input_option="USER_ENTERED",
        )
        return True
    except Exception as e:
        st.sidebar.error(f"儲存預設失敗:{e}")
        return False

@st.cache_data(ttl=300)
def get_settings_cached():
    return load_settings()

def normalize_code(value):
    """統一貨號格式，避免大小寫或空白造成錯誤判斷。"""
    return re.sub(r"\s+", "", str(value or "")).upper()

def normalize_name(value):
    """統一商品名稱的外圍空白，保留名稱內容。"""
    return re.sub(r"\s+", " ", str(value or "")).strip()

def is_free_shipping_vendor(vendor):
    """多品村由廣州出貨，陸運費固定包郵。"""
    from quote_dispatch.config import supplier_rule
    return supplier_rule(vendor).domestic_shipping == "free"

def build_carton_note_row(final_qty, vendor, qty_unit="個"):
    """建立裝箱備註列，並把多品村包郵註記放在大陸運費欄下方。"""
    from quote_dispatch.config import supplier_rule
    row = [""] * 12
    row[1] = f"裝箱 {final_qty}{qty_unit}/箱"
    rule = supplier_rule(vendor)
    if rule.domestic_shipping == "free":
        row[8] = rule.domestic_shipping_note
    return row

def extract_saved_products(sheet_rows):
    """從既有的 6 列商品區塊擷取名稱與貨號。"""
    products = []
    for i, row in enumerate(sheet_rows):
        if not row or not re.fullmatch(r"no\d+", str(row[0]).strip(), re.IGNORECASE):
            continue

        name = normalize_name(row[1]) if len(row) > 1 else ""
        code = ""
        code_row_index = i + 4
        if code_row_index < len(sheet_rows) and len(sheet_rows[code_row_index]) > 1:
            code_match = re.match(
                r"^貨號\s*(.+)$",
                str(sheet_rows[code_row_index][1]).strip(),
            )
            if code_match:
                code = normalize_code(code_match.group(1))

        products.append({
            "no": str(row[0]).strip(),
            "code": code,
            "name": name,
            "row_index": i + 1,
        })
    return products


def protect_user_entered_rows(rows, block_size=6):
    """USER_ENTERED 仍用於新增日期與公式；其餘文字禁止被解讀成公式。"""
    protected = []
    formula_columns = {2, 3, 4, 5, 7, 8, 9, 10}
    for row_index, row in enumerate(rows or []):
        protected_row = []
        for col_index, value in enumerate(row or []):
            formula_cell = (
                row_index % block_size == 1 and col_index in formula_columns
            )
            if (
                not formula_cell
                and isinstance(value, str)
                and value.startswith(("=", "+", "-", "@"))
            ):
                value = "'" + value
            protected_row.append(value)
        protected.append(protected_row)
    return protected


def pad_block(rows, height=6, width=12):
    """把 Sheets 省略的尾端空白補回，供精確快照與寫後核對。"""
    result = []
    for row in list(rows or [])[:height]:
        values = list(row or [])[:width]
        result.append(values + [""] * (width - len(values)))
    while len(result) < height:
        result.append([""] * width)
    return result


def get_saved_block(sheet_rows, base_row):
    """以 1-based 商品標題列取得固定 6x12 區塊。"""
    if not isinstance(base_row, int) or base_row < 1:
        raise ValueError("商品起始列不合法")
    return pad_block(list(sheet_rows or [])[base_row - 1:base_row + 5])


def normalize_name_key(value):
    """僅用於候選排序；不據此自動選定或覆蓋商品。"""
    return re.sub(r"[\W_]+", "", normalize_name(value), flags=re.UNICODE).lower()


def find_update_candidates(product, sheet_rows):
    """列出指定分頁的全部商品，符合貨號／品名者排前，但不自動選取。"""
    code = normalize_code(product.get("code", ""))
    name_key = normalize_name_key(product.get("name", ""))
    candidates = []
    for saved in extract_saved_products(sheet_rows):
        code_match = bool(code and code == saved["code"])
        name_match = bool(name_key and name_key == normalize_name_key(saved["name"]))
        rank = 0 if code_match and name_match else 1 if name_match else 2 if code_match else 3
        item = dict(saved)
        item["match_reason"] = (
            "貨號＋品名符合" if rank == 0 else
            "品名符合、貨號不同" if rank == 1 else
            "貨號符合、品名不同" if rank == 2 else
            "無直接符合"
        )
        candidates.append(item)
    return sorted(candidates, key=lambda item: (item["match_reason"] == "無直接符合", {"貨號＋品名符合": 0, "品名符合、貨號不同": 1, "貨號符合、品名不同": 2}.get(item["match_reason"], 3), item["row_index"]))


@st.cache_data(ttl=15)
def get_target_formula_block(category_name, base_row):
    """讀取既有商品的原始值／公式快照，不以計算後顯示值取代公式。"""
    try:
        creds = get_credentials()
        client = gspread.authorize(creds)
        sheet = open_spreadsheet(client).worksheet(category_name)
        rows = sheet.get(
            f"A{base_row}:L{base_row + 5}",
            value_render_option="FORMULA",
            maintain_size=True,
            pad_values=True,
        )
        return {"worksheet_id": sheet.id, "block": pad_block(rows)}
    except Exception as e:
        st.error(f"讀取原商品公式快照失敗：{e}")
        return None

def save_bulk_to_worksheet(category_name, bulk_rows, st_r, block_size=6, expected_rows=None):
    write_started = False
    try:
        creds = get_credentials()
        client = gspread.authorize(creds)
        spreadsheet = open_spreadsheet(client)
        try:
            sheet = spreadsheet.worksheet(category_name)
        except gspread.exceptions.WorksheetNotFound:
            st.error("找不到目標分頁，已停止；不會自動新建分頁。")
            return False
        fresh = sheet.get_all_values()
        expected_start = len(fresh) + 2 if fresh else 1
        if expected_rows is None or fresh != expected_rows or st_r != expected_start:
            st.error("雲表已變動或缺少讀取快照，請重新載入並校對後再存檔。")
            get_all_sheets_data.clear()
            return False
        if not bulk_rows or len(bulk_rows) % block_size:
            raise ValueError("商品區塊不完整")
        incoming = extract_saved_products(bulk_rows)
        live_sheets = {ws.title: ws.get_all_values() for ws in spreadsheet.worksheets()
                       if not ws.title.startswith("_")}
        conflicts = duplicate_messages(incoming, live_sheets)
        if conflicts:
            st.error("；".join(conflicts))
            get_all_sheets_data.clear()
            return False
        # Recheck the destination immediately before the write. Sheets has no
        # compare-and-swap; this detects changes but is not a distributed lock.
        if sheet.get_all_values() != expected_rows:
            st.error("存檔前雲表已變動，請重新載入。")
            get_all_sheets_data.clear()
            return False
        end_r = st_r + len(bulk_rows) - 1
        if end_r > sheet.row_count:
            sheet.add_rows(end_r - sheet.row_count)
        write_started = True
        sheet.update(
            values=protect_user_entered_rows(bulk_rows, block_size),
            range_name=f"A{st_r}:L{end_r}",
            value_input_option="USER_ENTERED",
        )
        actual = sheet.get(f"A{st_r}:L{end_r}", value_render_option="FORMULA")
        # API omits trailing empty cells/rows. Dates can return serial numbers
        # or formatted text; verify their value without requiring a new format.
        for row_index, expected in enumerate(bulk_rows):
            row_actual = actual[row_index] if row_index < len(actual) else []
            for col, value in enumerate(expected):
                received = row_actual[col] if col < len(row_actual) else ""
                if col == 0 and row_index % block_size == 1:
                    expected_date = datetime.datetime.strptime(value, "%Y/%m/%d").date()
                    if isinstance(received, (int, float)):
                        if received == (expected_date - datetime.date(1899, 12, 30)).days:
                            continue
                    else:
                        for date_format in ("%Y/%m/%d", "%Y-%m-%d", "%m/%d/%Y"):
                            try:
                                if datetime.datetime.strptime(str(received), date_format).date() == expected_date:
                                    received = value
                                    break
                            except ValueError:
                                pass
                if str(received) != str(value):
                    if not (isinstance(received, (int, float)) and isinstance(value, (int, float)) and received == value):
                        raise ValueError(f"寫後核對不符：第 {st_r + row_index} 列，第 {col + 1} 欄")
        num_blocks = len(bulk_rows) // block_size
        for i in range(num_blocks):
            base_r = st_r + (i * block_size)
            sheet.format(f"B{base_r}", {"backgroundColor": {"red": 1.0, "green": 0.6, "blue": 0.0}})
            sheet.format(f"C{base_r}:F{base_r}", {"backgroundColor": {"red": 1.0, "green": 0.95, "blue": 0.8}})
            sheet.format(f"G{base_r}:K{base_r}", {"backgroundColor": {"red": 0.92, "green": 0.96, "blue": 1.0}})
            note_index = (i * block_size) + 2
            if (
                note_index < len(bulk_rows)
                and len(bulk_rows[note_index]) > 8
                and bulk_rows[note_index][8] == "廣州包郵"
            ):
                sheet.format(
                    f"I{base_r + 2}",
                    {"backgroundColor": {"red": 0.8509804, "green": 0.91764706, "blue": 0.827451}},
                )
        return True
    except Exception as e:
        get_all_sheets_data.clear()
        if write_started:
            st.error(f"資料可能已寫入，但核對或格式設定未完成。請先檢查目標分頁，不可直接重試新增。詳情：{e}")
        else:
            st.error(f"尚未寫入商品，存檔檢查失敗：{e}")
        return False

def resolve_weight_inputs(carton_weight_kg, unit_weight_g, qty):
    """統一重量來源，並以較高的每件重量避免低估運費。"""
    carton_weight_kg = max(float(carton_weight_kg or 0), 0.0)
    unit_weight_g = max(float(unit_weight_g or 0), 0.0)
    qty = int(qty or 0)
    carton_unit_g = (
        carton_weight_kg * 1000 / qty
        if carton_weight_kg > 0 and qty > 0
        else 0.0
    )
    candidates = [value for value in (carton_unit_g, unit_weight_g) if value > 0]
    base_weight_g = max(candidates) if candidates else 0.0
    mismatch_ratio = 0.0
    if carton_unit_g > 0 and unit_weight_g > 0:
        mismatch_ratio = abs(carton_unit_g - unit_weight_g) / max(
            carton_unit_g,
            unit_weight_g,
        )

    if carton_unit_g > 0 and unit_weight_g > 0:
        source = "both"
    elif carton_unit_g > 0:
        source = "carton"
    elif unit_weight_g > 0:
        source = "unit"
    else:
        source = "missing"

    return {
        "source": source,
        "carton_unit_g": carton_unit_g,
        "unit_weight_g": unit_weight_g,
        "base_weight_g": base_weight_g,
        "chargeable_weight_g": base_weight_g * 1.05 if base_weight_g > 0 else 0.0,
        "mismatch_ratio": mismatch_ratio,
    }


def formula_number(value):
    """輸出 Sheets 會保留的數字常值，避免 68.0 被正規化成 68 後誤判。"""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError("公式數字不是有效有限值")
    return format(float(value), ".15g")


def build_cost_formulas(
    v_r,
    carton_weight_kg,
    unit_weight_g,
    final_qty,
    final_dom,
    intl_rate,
    ex_rate,
    vendor="",
    final_price=None,
    blocked=False,
):
    """建立成本公式；沒有有效重量時，運費、成本與報價全部留白。"""
    weight_cell = f"H{v_r}"
    domestic_cell = f"I{v_r}"
    international_cell = f"J{v_r}"
    cost_cell = f"K{v_r}"
    numeric_inputs = (carton_weight_kg, unit_weight_g, final_qty, final_dom, intl_rate, ex_rate)
    blank = dict.fromkeys(("quote_10", "quote_13", "quote_15", "quote_20", "weight", "domestic", "international", "cost"), "")
    if (not all(
            isinstance(v, (int, float))
            and not isinstance(v, bool)
            and math.isfinite(v)
            for v in numeric_inputs
        )
            or min(carton_weight_kg, unit_weight_g, final_dom, intl_rate) < 0
            or ex_rate <= 0 or final_qty <= 0 or final_qty != int(final_qty)):
        return blank
    weight_state = resolve_weight_inputs(carton_weight_kg, unit_weight_g, final_qty)
    if (blocked or final_qty <= 0 or weight_state["source"] == "missing"
            or weight_state["mismatch_ratio"] >= 0.2
            or (final_price is not None and (not math.isfinite(final_price) or final_price <= 0))):
        return blank

    carton_literal = formula_number(carton_weight_kg)
    unit_literal = formula_number(unit_weight_g)
    qty_literal = formula_number(final_qty)
    domestic_rate_literal = formula_number(final_dom)
    international_rate_literal = formula_number(intl_rate)
    exchange_rate_literal = formula_number(ex_rate)

    if weight_state["source"] == "missing":
        weight_formula = ""
    elif weight_state["source"] == "unit":
        weight_formula = f"=ROUNDUP({unit_literal}*1.05,2)"
    elif weight_state["source"] == "carton":
        weight_formula = (
            f"=ROUNDUP(({carton_literal}/{qty_literal})*1000*1.05,2)"
        )
    else:
        weight_formula = (
            f"=ROUNDUP(MAX(({carton_literal}/{qty_literal})*1000,"
            f"{unit_literal})*1.05,2)"
        )

    if is_free_shipping_vendor(vendor):
        domestic_formula = (
            f'=IF(OR({weight_cell}="",{weight_cell}<=0),"",0)'
        )
    else:
        domestic_formula = (
            f'=IF(OR({weight_cell}="",{weight_cell}<=0),"",'
            f'ROUNDUP(({weight_cell}/1000)*{domestic_rate_literal},2))'
        )

    result = {
        "quote_10": f'=IF(OR({cost_cell}="",{cost_cell}<=0),"",ROUND({cost_cell}/0.9,1))',
        "quote_13": f'=IF(OR({cost_cell}="",{cost_cell}<=0),"",ROUND({cost_cell}/0.87,1))',
        "quote_15": f'=IF(OR({cost_cell}="",{cost_cell}<=0),"",ROUND({cost_cell}/0.85,1))',
        "quote_20": f'=IF(OR({cost_cell}="",{cost_cell}<=0),"",ROUND({cost_cell}/0.8,1))',
        "weight": weight_formula,
        "domestic": domestic_formula,
        "international": (
            f'=IF(OR({weight_cell}="",{weight_cell}<=0),"",'
            f'ROUNDUP(({weight_cell}/1000)*{international_rate_literal},2))'
        ),
        "cost": (
            f'=IF(OR(NOT(ISNUMBER(G{v_r})),G{v_r}<=0,{weight_cell}="",{weight_cell}<=0,'
            f'{domestic_cell}="",{international_cell}=""),"",'
            f'ROUND((G{v_r}+{domestic_cell}+{international_cell})*{exchange_rate_literal},1))'
        ),
    }
    # A later manual deletion of G must also hide all derived numbers.
    for key, formula in result.items():
        if formula:
            result[key] = f'=IFERROR(IF(OR(NOT(ISNUMBER(G{v_r})),G{v_r}<=0),"",{formula[1:]}),"")'
    return result
# --- 3. 工具頁籤；發送管理只使用整理完成的商品資料 ---
def get_dispatch_store():
    from nas_dispatch_storage import configured_store
    return configured_store(open_spreadsheet(gspread.authorize(get_credentials())),
                            lambda: st.secrets.get("nas_images"))


def get_category_codes():
    return get_dispatch_store().category_settings()["codes"]


def persist_quote_images(category, base_row, expected_block, assets):
    if not assets:
        return True
    from quote_images import save_quote_images
    try:
        result = save_quote_images(get_dispatch_store(), category, base_row, expected_block, assets)
        if any(row["結果"] not in {"已綁定", "已存在"} for row in result):
            raise ValueError("；".join(row["原因"] for row in result if row["結果"] not in {"已綁定", "已存在"}))
        clear_library_cache()
        st.success("本款原圖已綁定到商品圖庫，發送管理會自動帶入。")
        return True
    except Exception as exc:
        st.error(f"商品已寫入，但圖片綁定未完成：{exc}")
        st.warning("不要重複新增商品；請到發送管理保存／補配圖片。")
        return False


def persist_quote_evidence(category, base_row, expected_block, raw_source, inputs, parsed, notes):
    """Capture only after a successful quote write. Failure never repeats that write."""
    from quote_evidence import PENDING_KEY, clear_evidence_caches, queue_evidence, save_evidence
    identity, pending = queue_evidence(st.session_state, category, base_row, expected_block,
                                       raw_source, inputs, parsed, notes)
    try:
        save_evidence(get_dispatch_store(), pending)
        st.session_state[PENDING_KEY].pop(identity, None)
        clear_evidence_caches(st.session_state)
        st.success("原文、擷取值與當次參數已存入原 Google 雲表的「_報價依據」，並完成讀回核對；不是只暫存在網站。")
        return True
    except Exception as exc:
        pending["last_error"] = str(exc)
        st.error(f"商品已寫入，但原始依據未完成保存：{exc}")
        st.warning("不要重複新增商品；原文尚未確認存到雲表，請勿關閉頁面。重新操作頁面後，可在上方重試保存原文與參數。")
        return False


from quote_evidence import render_pending_evidence
if st.session_state.get("quote_evidence_notice"):
    st.success(st.session_state.pop("quote_evidence_notice"))
render_pending_evidence(get_dispatch_store)

quote_tab, dispatch_tab = st.tabs(
    ["📝 報價整理", "📣 發送管理"], key="tool_page", on_change="rerun"
)
if dispatch_tab.open:
    with dispatch_tab:
        render_dispatch_manager(get_dispatch_store)
    st.stop()

# --- 側邊欄設定 ---
settings = get_settings_cached()
if settings is None:
    st.error("成本設定讀取失敗，停止解析存檔；請重試，不套用其他匯率。")
    st.stop()
st.sidebar.header("⚙️ 成本參數設定")
ex_rate = st.sidebar.number_input("匯率", value=settings["ex_rate"], step=0.05, format="%.2f")
intl_rate = st.sidebar.number_input("國際運費 (RMB/kg)", value=settings["intl_rate"], step=0.5)
dom_rate_def = st.sidebar.number_input("內陸運費 (RMB/kg)", value=settings["dom_rate"], step=0.5)

if st.sidebar.button("📌 將目前數值設為預設"):
    if save_settings({"ex_rate": ex_rate, "intl_rate": intl_rate, "dom_rate": dom_rate_def}):
        get_settings_cached.clear()
        st.sidebar.success(f"已更新預設 → 匯率 {ex_rate}")
        st.rerun()
st.sidebar.caption(f"目前雲端預設:匯率 {settings['ex_rate']} / 國際 {settings['intl_rate']} / 內陸 {settings['dom_rate']}")
# --- 4. 解析引擎 V12 ---
# 注意：zhconv 會把「只」轉成「隻」，所有單位 pattern 都需含「隻」
UNIT_PAT = r'(?:盒|pcs|PCS|只|隻|個|个|套|瓶|罐)'
EMOJI_PAT = r'[📦💰✅🔥✨🎈🍦🔫⚖️🚜🎯🛻🚗⭐️🎁🎉]'

def clean_product_name(name):
    """移除同行品名尾端已被解析成欄位的價格、箱規與重量資訊。"""
    name = name.strip().lstrip(',，、').strip()
    # 例：工程系列-mini工程隊馬卡龍是4.3元，一箱240隻，26KG
    name = re.sub(
        r'\s*[，,]?\s*(?:是\s*)?(?:RMB|rmb|¥)?\s*'
        r'[0-9]+(?:\.[0-9]+)?\s*元(?:\s*[，,、].*)?\s*$',
        '',
        name,
    ).strip()
    # 沒有價格、但把箱規接在品名後面的格式。
    name = re.sub(
        r'\s*[，,、]\s*一箱\s*[0-9]+\s*' + UNIT_PAT
        + r'(?:\s*[，,、].*)?\s*$',
        '',
        name,
    ).strip()
    return name


def labeled_dimension_info(value):
    """Classify an explicitly labelled dimension without repairing its value."""
    line = unicodedata.normalize("NFKC", str(value or "")).strip()
    match = re.fullmatch(
        r"(?P<label>[A-Za-z\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff]{0,11})"
        r"\s*[:：]\s*(?P<value>.+?)\s*",
        line,
        re.I,
    )
    if not match:
        return None
    label = match["label"]
    raw_value = match["value"].strip()
    label_is_dimension = bool(
        re.search(r"(?:尺寸|規格|長度|寬度|高度|直徑|口徑)$", label)
        or re.search(r"\d\s*[*xX×]\s*\d", raw_value)
        or label in {
            "尺寸", "產品", "彩盒", "包裝盒", "單個包裝", "亞克力",
            "外箱", "箱規", "白盒", "打開", "展開",
        }
    )
    if not label_is_dimension:
        return None
    number = r"\d+(?:\.\d+)?"
    numeric_shape = rf"{number}(?:\s*[-~～]\s*{number})?(?:\s*[*xX×]\s*{number})*"
    dimension_value = numeric_shape + r"\s*(?:cm|mm|公分|毫米)"
    valid = bool(re.fullmatch(dimension_value, raw_value, re.I))
    if not valid and label in {"彩盒尺寸", "彩盒"}:
        valid = bool(re.fullmatch(
            dimension_value + r"\s*\(\s*展示盒\s*\)", raw_value, re.I,
        ))
    missing_unit = bool(re.fullmatch(numeric_shape, raw_value, re.I))
    decimal_comma = bool(re.search(r"\d\s*[,，]\s*\d", raw_value))
    return {
        "label": label,
        "value": raw_value,
        "valid": valid,
        "missing_unit": missing_unit,
        "decimal_comma": decimal_comma,
    }


def weight_field_info(value):
    """Classify weight-like fields; unknown labels are never coerced."""
    line = unicodedata.normalize("NFKC", str(value or "")).strip()
    match = re.fullmatch(
        r"(?:"
        r"(?P<cn_label>[A-Za-z0-9\u4e00-\u9fff]{0,12}(?:重量|毛重|淨重|箱重|單重|總重))"
        r"\s*[:：]?\s*|"
        r"(?P<abbr_label>(?:G\.?\s*W\.?|N\.?\s*W\.?|GW|NW))"
        r"(?:\s*[:：]\s*|\s+)"
        r")"
        r"(?P<rest>(?:(?:大概|大約|約為|約)\s*\d.*|\d.*|不詳))",
        line,
        re.I,
    )
    if not match:
        return None
    label = match["cn_label"] or match["abbr_label"]
    strict = re.fullmatch(
        r"(?:約\s*)?"
        r"(?P<value>\d+(?:\.\d+)?(?:\s*[/／]\s*\d+(?:\.\d+)?)?)"
        r"\s*(?P<unit>kg|公斤|千克|g|公克|克)?\s*"
        r"(?:\(\s*(?P<scope>單個|每個|單件|每件)\s*\))?\s*",
        match["rest"],
        re.I,
    )
    supported = {
        "單個重量", "每個重量", "單件重量", "每件重量", "單重",
        "整箱毛重", "整箱重量", "箱重", "毛重",
        "整箱毛淨重", "箱毛淨重", "毛淨重",
    }
    scope = strict["scope"] if strict else ""
    unit = strict["unit"] if strict else ""
    return {
        "label": label,
        "supported_label": label in supported or label == "重量",
        "known": bool(
            strict
            and (label in supported or bool(label == "重量" and scope))
        ),
        "unit": unit or "",
        "scope": scope or "",
        "strict": bool(strict),
    }


def is_standalone_laser_metadata(value):
    line = unicodedata.normalize("NFKC", str(value or "")).strip()
    return bool(re.fullmatch(
        r"\(?\s*帶[鐳雷]射(?:標)?(?:\s*/\s*[^()]+)?\s*\)?",
        line,
        re.I,
    ))


def is_order_condition_line(value):
    line = unicodedata.normalize("NFKC", str(value or "")).strip()
    return bool(re.search(r"(?:起訂|起批)", line))


def is_fulfilment_condition_line(value):
    line = unicodedata.normalize("NFKC", str(value or "")).strip()
    return bool(re.fullmatch(r"\([^()]+(?:出貨|發貨)[^()]*\)", line))


def carton_qualifier_notes(value):
    """Return parenthetical carton qualifiers without changing carton units."""
    line = unicodedata.normalize("NFKC", str(value or "")).strip()
    line = re.sub(rf"^{EMOJI_PAT}+\s*", "", line).strip()
    if not re.match(r"^(?:每箱數量|箱數|裝箱量|裝箱數量|裝箱數|裝箱|一箱)\s*[:：]?", line):
        return []
    notes = [item.strip() for item in re.findall(r"\(([^()]+)\)", line) if item.strip()]
    slash_qualifier = re.search(r"/\s*([^/()]+)\s*$", line)
    if slash_qualifier and slash_qualifier[1].strip() not in {"箱"}:
        notes.append(slash_qualifier[1].strip())
    return list(dict.fromkeys(notes))


def metadata_format_issues(text):
    """Find ambiguous metadata formats that must block derived costs."""
    issues = []
    for source_line in str(text or "").splitlines():
        line = unicodedata.normalize("NFKC", source_line).strip()
        weight_info = weight_field_info(line)
        if weight_info and not re.search(r"木架|木框", line):
            if weight_info["label"] == "重量" and not weight_info["scope"]:
                issues.append("重量欄位「重量」未標明單個或整箱，須確認重量範圍")
            elif not weight_info["supported_label"]:
                issues.append(
                    f"未識別重量欄位「{weight_info['label']}」，不可猜測為整箱或單個重量"
                )
            if weight_info["strict"] and not weight_info["unit"]:
                issues.append(
                    f"重量欄位「{weight_info['label']}」缺少單位，須確認 kg 或 g"
                )
            elif weight_info["supported_label"] and not weight_info["strict"]:
                issues.append(
                    f"重量欄位「{weight_info['label']}」格式或單位不明，須核對原文"
                )
        dimension_info = labeled_dimension_info(line)
        if dimension_info:
            if dimension_info["decimal_comma"]:
                issues.append(
                    f"尺寸欄位「{dimension_info['label']}」含小數逗號，須確認原值"
                )
            elif dimension_info["missing_unit"]:
                issues.append(
                    f"尺寸欄位「{dimension_info['label']}」缺少單位，須確認 cm 或 mm"
                )
            elif not dimension_info["valid"]:
                issues.append(
                    f"尺寸欄位「{dimension_info['label']}」格式不明，須核對原文"
                )
        if re.match(r"^包裝\s*[:：]\s*包裝\s*[:：]", line, re.I):
            issues.append("包裝欄位標籤重複，須核對原文")
    return list(dict.fromkeys(issues))


def is_name_metadata_line(value):
    """Recognize narrow metadata shapes without deleting ordinary name words."""
    return bool(
        labeled_dimension_info(value)
        or weight_field_info(value)
        or is_standalone_laser_metadata(value)
        or is_order_condition_line(value)
        or is_fulfilment_condition_line(value)
        or re.fullmatch(r"展示盒\s*\d+\s*(?:個|隻|只|盒|套)", str(value or "").strip())
    )


def parse_text_legacy(text):
    common = {
        "price": 0.0,
        "qty": 0,
        "weight": 0.0,
        "unit_weight_g": 0.0,
        "prod_size": "",
        "color_box_size": "",
        "outer_box_size": "",
        "extra_tags": "",
    }
    products = []
    if not text:
        return common, products

    # 前處理
    text_n = text.replace(':', ':').replace(',', ',').replace('、', ',')
    # 清掉 [Fireworks] 等文字 emoji 標籤
    text_n = re.sub(r'\[[^\]]{1,20}\]', '', text_n)

    # ===== 價格 =====
    m_price = re.search(
        r'(?:單個價格|单个价格|單價|单价|價格|价格|價錢|价钱|售價|售价|都是|💰)\s*:?\s*(?:rmb|RMB|¥)?\s*([0-9]+(?:\.[0-9]+)?)',
        text_n)
    if not m_price:
        m_price = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*元', text_n)
    if m_price:
        common["price"] = float(m_price.group(1))

    # ===== 裝箱量 =====
    qty_keywords = ['每箱數量','每箱数量','箱數','箱数','裝箱量','裝箱數','裝箱','装箱','一箱']

    # pattern A：逐行掃描，關鍵字出現在行內（不限行首）
    for line in text_n.split('\n'):
        line_s = re.sub(EMOJI_PAT, '', line.strip()).strip()
        for kw in qty_keywords:
            if kw in line_s:
                if 'opp' in line_s.lower() or '袋' in line_s:
                    continue
                # 優先抓「一箱N隻/只/個...」
                m = re.search(r'一箱\s*([0-9]+)\s*' + UNIT_PAT, line_s)
                if not m:
                    m = re.search(r'([0-9]+)\s*' + UNIT_PAT + r'\s*[/／]\s*箱', line_s)
                if not m:
                    m = re.search(r'([0-9]+)', line_s)
                if m:
                    common["qty"] = int(m.group(1))
                    break
        if common["qty"] > 0:
            break

    # pattern B：N隻/只/盒.../箱（全文）
    if common["qty"] == 0:
        m = re.search(r'([0-9]+)\s*' + UNIT_PAT + r'\s*[/／]\s*箱', text_n)
        if m:
            common["qty"] = int(m.group(1))

    # pattern C：一箱N隻/只...（全文）
    if common["qty"] == 0:
        m = re.search(r'一箱\s*([0-9]+)\s*' + UNIT_PAT, text_n)
        if m:
            common["qty"] = int(m.group(1))

    # pattern D：兜底，純數字+單位
    if common["qty"] == 0:
        m = re.search(r'([0-9]+)\s*' + UNIT_PAT, text_n)
        if m:
            common["qty"] = int(m.group(1))

    # ===== 單個重量 =====
    unit_weight_pattern = (
        r'(?:單個重量|单个重量|每個重量|每个重量|單件重量|单件重量|'
        r'每件重量|單重|单重)\s*[：:]?\s*(?:約|约)?\s*'
        r'([0-9]+(?:\.[0-9]+)?)\s*([Kk][Gg]|公斤|千克|[Gg]|公克|克)'
    )
    m_unit_weight = re.search(unit_weight_pattern, text_n)
    if m_unit_weight:
        unit_weight_value = float(m_unit_weight.group(1))
        unit_weight_unit = m_unit_weight.group(2).lower()
        if unit_weight_unit in ("kg", "公斤", "千克"):
            unit_weight_value *= 1000
        common["unit_weight_g"] = unit_weight_value

    # 單個重量若以 kg 表示，不能再被下方無標籤的整箱 KG 規則誤抓。
    carton_weight_text = re.sub(unit_weight_pattern, "", text_n)

    # ===== 整箱毛重 =====
    m_weight_pair = re.search(
        r'(?:箱毛淨重|箱毛净重|整箱毛淨重|整箱毛净重|毛淨重|毛净重)'
        r'\s*[：:]?\s*([0-9]+(?:\.[0-9]+)?)\s*[/／]\s*'
        r'([0-9]+(?:\.[0-9]+)?)\s*[Kk][Gg]',
        carton_weight_text,
    )
    if m_weight_pair:
        common["weight"] = max(
            float(m_weight_pair.group(1)),
            float(m_weight_pair.group(2)),
        )
    else:
        m_weight = re.search(
            r'(?:整箱毛重|整箱重量|箱重|毛重|⚖️)\s*[：:]?\s*([0-9]+(?:\.[0-9]+)?)',
            carton_weight_text)
        if not m_weight:
            m_weight = re.search(
                r'([0-9]+(?:\.[0-9]+)?)\s*[Kk][Gg]',
                carton_weight_text,
            )
        if m_weight:
            common["weight"] = float(m_weight.group(1))

    # ===== 尺寸 =====
    size_pattern = r'([0-9]+(?:\.[0-9]+)?(?:[*xX×][0-9]+(?:\.[0-9]+)?)+(?:[cC][mM]|公分)?)'
    color_box_keywords = ['彩盒','亞克力','亚克力','單個包裝','单个包装','包裝盒','包装盒']
    outer_box_keywords = ['外箱規格', '外箱规格', '外箱尺寸', '外箱']
    prod_size_keywords = ['產品','产品','單個尺寸','单个尺寸','產品尺寸','产品尺寸']

    for line in text_n.split('\n'):
        line_clean = re.sub(EMOJI_PAT, '', line.strip()).strip()
        if not common["outer_box_size"]:
            for kw in outer_box_keywords:
                if line_clean.startswith(kw):
                    m = re.search(size_pattern, line_clean)
                    if m:
                        common["outer_box_size"] = m.group(1).strip()
                    break
        if not common["color_box_size"]:
            for kw in color_box_keywords:
                if line_clean.startswith(kw):
                    m = re.search(size_pattern, line_clean)
                    if m:
                        common["color_box_size"] = m.group(1).strip()
                    break
        if not common["prod_size"]:
            for kw in prod_size_keywords:
                if line_clean.startswith(kw):
                    m = re.search(size_pattern, line_clean)
                    if m:
                        common["prod_size"] = m.group(1).strip()
                    break

    # ===== 備註 =====
    extra_items = []
    for line in text_n.split('\n'):
        line_s = re.sub(EMOJI_PAT, '', line.strip()).strip()
        m_pkg = re.match(r'(?:包裝|包装)\s*:?\s*(.+)', line_s)
        if m_pkg:
            extra_items.append(f"包裝: {m_pkg.group(1).strip()}")
    if re.search(r'帶[鐳雷]射標|帶[鐳雷]射|镭射', text_n):
        extra_items.append("帶雷射標")
    if has_affirmative_license_marker(text_n):
        extra_items.append("正版授權")
    common["extra_tags"] = "\n".join(extra_items)

    # ===== 商品清單 =====
    lines = text_n.split('\n')

    # 模式 A：批量格式（多品，每行「貨號,名稱」）
    for line in lines:
        line = line.strip()
        line = re.sub(r'^' + EMOJI_PAT + r'+', '', line)
        line = re.sub(EMOJI_PAT + r'+$', '', line)
        if not line:
            continue
        m = re.match(
            r'^\s*([A-Za-z0-9]+(?:-[A-Za-z0-9]+)?)'
            r'\s*(?:[，,、:：]|\s+)\s*(.+)$',
            line,
        )
        if m:
            code = normalize_code(m.group(1))
            name = clean_product_name(m.group(2))
            if (re.search(r'[A-Za-z]', code) or '-' in code) and len(code) >= 4 and name and len(name) >= 2:
                if not re.search(r'(?:這|这|都是|價格|价格|裝箱|装箱|箱數|箱数|毛重|尺寸|彩盒|外箱|亞克力|亚克力|包裝|包装|條碼|条码)', name[:6]):
                    products.append({"code": code, "name": name})

    # 模式 B：單品
    if not products:
        single_code = ""
        m_code = re.search(
            r'(?:型號|型号|貨號|货号|產品編號|产品编号|編號|编号)\s*:?\s*([A-Za-z0-9\-/]+)',
            text_n)
        if m_code:
            single_code = m_code.group(1).strip()
        else:
            m_first = re.search(
                r'^\s*([A-Za-z]{1,6}-?[0-9]{1,6})\s*$',
                text_n,
                re.MULTILINE,
            )
            if m_first:
                single_code = normalize_code(m_first.group(1))

        exclusion_keywords = [
            '型號','型号','貨號','货号','單價','单价','單個價格','单个价格',
            '價格','价格','裝箱','装箱','箱數','箱数','每箱','一箱',
            '數量','数量','單重','单重','單個重量','单个重量',
            '每個重量','每个重量','毛重','箱重','整箱重量','整箱毛重',
            '尺寸','單個尺寸','单个尺寸','單個包裝','单个包装',
            '彩盒','外箱','產品','产品','規格','规格','亞克力','亚克力',
            '包裝','包装','運費','运费','材積','材积','條碼','条码',
            '配件','需要','帶鐳','帶雷','镭射','电池','電池'
        ]

        name_lines = []
        for line in lines:
            line_s = line.strip()
            license_stripped = strip_affirmative_license_markers(line_s)
            if license_stripped != line_s:
                line_s = license_stripped.strip()
                if not line_s:
                    continue
            line_s = re.sub(r'^[#＃【】\[\]]+', '', line_s).strip()
            line_s = re.sub(EMOJI_PAT, '', line_s).strip()
            line_s = re.sub(r'\[[^\]]{1,20}\]', '', line_s).strip()
            if not line_s:
                continue

            if is_name_metadata_line(line_s):
                if name_lines:
                    break
                continue

            # 「單套價格／單盒價格／單件價格…」都是欄位資料，不是品名。
            # parse_text() 會另外解析其數值與計價單位；此處只避免把整行併入品名。
            is_excluded = bool(re.match(
                r'^(?:單|单|每)\s*[A-Za-z0-9\u4e00-\u9fff]{1,6}\s*(?:價格|价格|價|价)',
                line_s,
            ))
            for kw in exclusion_keywords:
                if line_s.startswith(kw):
                    is_excluded = True
                    break
            if is_excluded:
                if name_lines:
                    break
                continue

            if re.match(r'^[0-9.*xX×\s\-]+(?:[cC][mM]|公分|[Kk][Gg]|元|pcs)?$', line_s):
                continue
            if re.match(r'^[0-9]+\s*(?:個|个|款|種|种)', line_s):
                continue
            if line_s in ('正版授權','正版授权','新款','熱賣','热卖'):
                continue

            # 同行格式：FF784564，名稱 6.2元，一箱128隻...
            if single_code and line_s.startswith(single_code):
                remainder = line_s[len(single_code):].lstrip('，,、 \t').strip()
                remainder = clean_product_name(remainder)
                if remainder:
                    name_lines.append(remainder)
                continue

            name_lines.append(line_s)
            if len(name_lines) >= 3:
                break

        single_name = " ".join(name_lines).strip()
        if single_code and single_code in single_name:
            single_name = single_name.replace(single_code, "").strip()

        products.append({"code": single_code, "name": single_name})

    return common, products
def canonical_unit(unit):
    unit = (unit or "").lower()
    return "個" if unit in ("pcs", "pc", "只", "隻", "个", "件") else unit


def wooden_rack_context(text):
    """分辨木架語意，並取出每行都明確標示為無木架的基礎資料。"""
    normalized = zhconv.convert(unicodedata.normalize("NFKC", text or ""), "zh-tw")
    rack_term = r"(?:木架|木框)"
    negative = (
        rf"(?:(?:無|沒有|未含|不含|不配|不加|不要|不需要|不帶|不带|免){rack_term}|"
        rf"{rack_term}\s*(?:無|沒有|未含|不含|不配|不加|不要|不需要|不帶|不带))"
    )
    optional = (
        rf"(?:(?:可加|可配|選配|选配|另加|另計|加購|加购|加裝|加装|需加|需要加|加價|加价|如需(?:加|配)?)\s*{rack_term}|"
        rf"{rack_term}\s*(?:可加|可配|選配|选配|另加|另計|加購|加购|加裝|加装|加價|加价))"
    )
    source_lines = [
        line.strip() for line in normalized.splitlines()
        if re.search(rack_term, line)
    ]
    if not source_lines:
        return {
            "state": "none",
            "source_lines": [],
            "explicit_no_rack_text": "",
            "conflicting": False,
        }

    without_negative = re.sub(negative, "", normalized, flags=re.I)
    included = bool(re.search(
        rf"(?:(?:已含|包含|含有|含|標配|标配|已配|自帶|自带|附帶|附带|帶|带|配有)\s*{rack_term}|"
        rf"{rack_term}\s*(?:已含|包含|含在內|在內|標配|标配|已配|配好|隨貨|随货))",
        without_negative,
        re.I,
    ))
    optional_match = bool(re.search(optional, normalized, re.I))
    # 多品村原文這種「獨立木架重量＋括號價格」是明確加購項。
    standalone_addon = bool(re.search(
        rf"(?m)^\s*{rack_term}\s*(?:大約|約)?\s*[:：]?\s*"
        r"\d+(?:\.\d+)?(?:\s*[-~～]\s*\d+(?:\.\d+)?)?\s*(?:kg|公斤|千克|g|公克|克)\s*"
        r"[\(（]\s*\d+(?:\.\d+)?\s*元\s*[\)）]\s*$",
        normalized,
        re.I,
    ))
    negative_match = bool(re.search(negative, normalized, re.I))
    pending_only = bool(re.search(
        rf"(?:{rack_term}[^\n，,;；]*(?:待定|待確認|另議|未定)|"
        rf"(?:待定|待確認|另議|未定)[^\n，,;；]*{rack_term})",
        normalized,
        re.I,
    ))
    relevant = bool(
        included or optional_match or standalone_addon or negative_match or pending_only
        or re.search(
            rf"(?:{rack_term}[^\n，,;；]*(?:元|kg|公斤|千克|重量|價格|單價|費用)|"
            rf"(?:元|kg|公斤|千克|重量|價格|單價|費用)[^\n，,;；]*{rack_term})",
            normalized,
            re.I,
        )
    )
    if not relevant:
        return {
            "state": "none",
            "source_lines": [],
            "explicit_no_rack_text": "",
            "conflicting": False,
        }

    explicit_no_rack = []
    for line in normalized.splitlines():
        for fragment in re.split(r"[，,;；]", line):
            if re.search(negative, fragment, re.I):
                cleaned = re.sub(negative, "", fragment, flags=re.I).strip()
                if cleaned:
                    explicit_no_rack.append(cleaned)

    conflicting = bool(included and optional_match)
    if included:
        state = "included"
    elif optional_match or standalone_addon:
        state = "optional"
    elif negative_match:
        state = "negative"
    elif pending_only:
        state = "pending"
    else:
        state = "ambiguous"
    return {
        "state": state,
        "source_lines": list(dict.fromkeys(source_lines)),
        "explicit_no_rack_text": "\n".join(explicit_no_rack),
        "conflicting": conflicting,
    }


def wooden_rack_cost_basis_text(text):
    """移除木架加購子句，保留同一行可明確切開的商品本體欄位。"""
    normalized = zhconv.convert(unicodedata.normalize("NFKC", text or ""), "zh-tw")
    context = wooden_rack_context(normalized)
    if context["state"] == "none":
        return normalized
    rack_term = r"(?:木架|木框)"
    negative = (
        rf"(?:(?:無|沒有|未含|不含|不配|不加|不要|不需要|不帶|不带|免){rack_term}|"
        rf"{rack_term}\s*(?:無|沒有|未含|不含|不配|不加|不要|不需要|不帶|不带))"
    )
    rack_detail = (
        rf"(?:(?:可加|可配|選配|选配|另加|另計|加購|加购|加裝|加装|需加|需要加|加價|加价|如需(?:加|配)?)\s*)?"
        rf"{rack_term}\s*(?:(?:費用?|單價|價格|重量|整箱重量|大約|約|可加|可配|選配|选配|另加|另計|加購|加购|加裝|加装|加價|加价|待定|待確認|另議|未定)\s*)*[:：]?\s*"
        r"(?:\d+(?:\.\d+)?(?:\s*[-~～]\s*\d+(?:\.\d+)?)?\s*(?:kg|公斤|千克|g|公克|克))?\s*"
        r"(?:[\(（]\s*\d+(?:\.\d+)?\s*元\s*[\)）])?\s*"
        r"(?:\d+(?:\.\d+)?\s*元)?"
    )
    kept = []
    for line in normalized.splitlines():
        for fragment in re.split(r"[，,;；]", line):
            fragment = fragment.strip()
            if not fragment:
                continue
            if not re.search(rack_term, fragment):
                kept.append(fragment)
                continue
            if re.search(negative, fragment, re.I):
                cleaned = re.sub(negative, "", fragment, flags=re.I).strip()
            elif context["state"] == "included":
                cleaned = ""
            else:
                cleaned = re.sub(rack_detail, "", fragment, flags=re.I).strip()
                if re.search(rack_term, cleaned):
                    cleaned = ""
            if cleaned:
                kept.append(cleaned)
    return "\n".join(kept)


def parse_text(text):
    """單一報價的保守解析；不把多則獨立報價套用同一組數字。"""
    normalized = zhconv.convert(unicodedata.normalize("NFKC", text or ""), "zh-tw")
    rack_context = wooden_rack_context(normalized)
    cost_basis_text = wooden_rack_cost_basis_text(normalized)
    price_basis_text = (
        rack_context["explicit_no_rack_text"]
        if rack_context["state"] == "included" else cost_basis_text
    )
    weight_basis_text = price_basis_text
    common, products = parse_text_legacy(normalized)
    common = {
        **common,
        "raw_text": text or "",
        "issues": [],
        "price_unit": "",
        "qty_unit": "",
        "rack_state": rack_context["state"],
        "rack_source_lines": rack_context["source_lines"],
    }
    if not normalized.strip():
        return common, []
    number = r"\d+(?:\.\d+)?"
    units = r"pcs|pc|個|隻|只|盒|套|瓶|罐|包|袋|件"
    issues = common["issues"]
    issues.extend(metadata_format_issues(normalized))
    # 未知的「單X價格」不能悄悄退回只有數字、沒有計價單位的成本。
    # 保留價格供人工核對，但用 issue 阻斷所有衍生成本與報價。
    unit_price_labels = list(re.finditer(
        rf"(?:單|每)\s*(?P<unit>[A-Za-z0-9\u4e00-\u9fff]{{1,6}})\s*(?:價格|價)"
        rf"(?=\s*[:：]?\s*(?:RMB|¥)?\s*{number})",
        price_basis_text,
        re.I,
    ))
    unit_price_labels.extend(re.finditer(
        rf"(?:單價|價格|價錢|售價)\s*[:：]?\s*(?:RMB|¥)?\s*{number}\s*元\s*[/／]\s*"
        rf"(?P<unit>[A-Za-z0-9\u4e00-\u9fff]{{1,6}})",
        price_basis_text,
        re.I,
    ))
    supported_price_units = re.compile(rf"^(?:{units})$", re.I)
    for label in unit_price_labels:
        if not supported_price_units.fullmatch(label["unit"]):
            issues.append(f"不支援的計價單位「{label['unit']}」，須先確認換算方式")
    # Require a price or carton label, never use the count inside an OPP bag.
    prices = list(re.finditer(
        rf"(?:(?:單|每)\s*(?P<unit>{units})\s*(?:價格|價)|單價|價格|價錢|售價|都是|💰)\s*:?\s*(?:RMB|¥)?\s*(?P<value>{number})",
        price_basis_text, re.I))
    if not prices:
        prices = list(re.finditer(rf"(?P<value>{number})\s*元(?:\s*/\s*(?P<unit>{units}))?", price_basis_text, re.I))
        prices = [m for m in prices if not re.search(r"運費|包裝費|打包費|加工費|貼標費|配件費|稅費|服務費|手續費", price_basis_text[price_basis_text.rfind("\n", 0, m.start()) + 1:m.start()])]
    common["price"] = float(prices[0]["value"]) if prices else 0.0
    if prices:
        common["price_unit"] = canonical_unit(prices[0]["unit"])
        tail = price_basis_text[prices[0].end():]
        suffix_unit = re.match(rf"\s*元\s*/\s*({units})", tail, re.I)
        if suffix_unit:
            common["price_unit"] = canonical_unit(suffix_unit[1])
        if re.match(r"\s*[-~～,]\s*\d", tail):
            issues.append("價格含範圍或不明數字格式，請確認單一進價")
        if re.match(r"\s*元?\s*起", tail):
            issues.append("價格僅為起價，須確認實際進價")
    if re.search(r"(?:單價|價格|進價)\s*:?\s*(?:約|大約)|待定|待確認|另議|未定|以實際", price_basis_text):
        issues.append("原文含未確認條件，請先向來源確認")
    if len(prices) > 1:
        issues.append("存在多個價格，請一次貼一則報價並確認費用範圍")
    qty_matches = list(re.finditer(
        rf"(?:每箱數量|箱數|裝箱量|裝箱數量|裝箱數|裝箱|一箱)\s*:?\s*(?P<value>\d+)\s*(?P<unit>{units})?",
        cost_basis_text, re.I))
    if not qty_matches:
        qty_matches = list(re.finditer(rf"(?P<value>\d+)\s*(?P<unit>{units})\s*/\s*箱", cost_basis_text, re.I))
    common["qty"] = int(qty_matches[0]["value"]) if qty_matches else 0
    common["qty_unit"] = canonical_unit(qty_matches[0]["unit"]) if qty_matches else ""
    if qty_matches:
        qty_tail = cost_basis_text[qty_matches[0].end():]
        has_qty_unit = bool(qty_matches[0]["unit"])
        has_range_suffix = re.match(r"\s*[-~～]\s*\d", qty_tail)
        has_decimal_suffix = (
            not has_qty_unit
            and re.match(r"\s*[.,]\s*\d", qty_tail)
        )
        if has_range_suffix or has_decimal_suffix:
            issues.append("裝箱量含範圍或小數，請確認整數裝箱量")
    if len(qty_matches) > 1:
        issues.append("存在多個裝箱量，請拆開獨立報價")
    # Explicit units and scope. Bare KG does not establish a carton weight.
    weight_unit = r"kg|公斤|千克|g|公克|克"
    prefix = rf"(?:單個重量|每個重量|單件重量|每件重量|單重)\s*:?\s*(?:約)?\s*({number})\s*({weight_unit})"
    suffix = rf"重量\s*:?\s*(?:約)?\s*({number})\s*({weight_unit})\s*\(\s*(?:單個|每個|單件|每件)\s*\)"
    unit_matches = list(re.finditer(prefix, weight_basis_text, re.I)) + list(re.finditer(suffix, weight_basis_text, re.I))
    unit_values = [float(m[1]) * (1000 if m[2].lower() in ("kg", "公斤", "千克") else 1) for m in unit_matches]
    common["unit_weight_g"] = unit_values[0] if unit_values else 0.0
    carton_text = re.sub(prefix, "", weight_basis_text, flags=re.I)
    carton_text = re.sub(suffix, "", carton_text, flags=re.I)
    carton_matches = list(re.finditer(
        rf"(?:整箱毛重|整箱重量|箱重|毛重|⚖️?)\s*:?\s*(?:約)?\s*({number})\s*({weight_unit})",
        carton_text, re.I))
    carton_values = [float(m[1]) / (1 if m[2].lower() in ("kg", "公斤", "千克") else 1000) for m in carton_matches]
    pairs = list(re.finditer(rf"(?:整箱毛淨重|箱毛淨重|毛淨重)\s*:?\s*({number})\s*/\s*({number})\s*({weight_unit})", carton_text, re.I))
    carton_values += [max(float(m[1]), float(m[2])) / (1 if m[3].lower() in ("kg", "公斤", "千克") else 1000) for m in pairs]
    # 部分廠商把箱重緊接在明確裝箱量後，例如「一箱30只，27.5KG」。
    # 只接受此窄範圍句型，不恢復把任意裸 KG 當箱重的高風險兜底。
    if not carton_values:
        inline_carton_matches = list(re.finditer(
            rf"(?:每箱數量|箱數|裝箱量|裝箱數量|裝箱數|裝箱|一箱)\s*:?\s*"
            rf"\d+\s*(?:{units})\s*[,，、;；]\s*(?:約)?\s*"
            rf"({number})\s*(kg|公斤|千克)(?=$|[\s，,。.;；）)])",
            carton_text,
            re.I,
        ))
        carton_values = [float(m[1]) for m in inline_carton_matches]
    common["weight"] = carton_values[0] if carton_values else 0.0
    if len(set(unit_values)) > 1 or len(set(carton_values)) > 1:
        issues.append("存在互相衝突的重量，請核對來源後只保留正確值")
    if rack_context["conflicting"]:
        issues.append("木架同時標示已含與另加，須先確認正確條件")
    elif rack_context["state"] == "included" and not (
        common["price"] > 0 and (common["weight"] > 0 or common["unit_weight_g"] > 0)
    ):
        issues.append("原文顯示已含木架，但缺少明確無木架進價與重量")
    elif rack_context["state"] == "pending":
        issues.append("木架條件待確認；未明確為可加選配前停止成本")
    elif rack_context["state"] == "ambiguous":
        issues.append("木架條件不明，須確認是否為選配、已含或不配")
    for issue in supplemental_uncertainty_issues(normalized):
        if issue not in issues:
            issues.append(issue)
    codes = list(re.finditer(r"(?:型號|貨號|產品編號|編號)\s*:?\s*([A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)*)", normalized))
    if len(codes) > 1 or len(products) > 1:
        issues.append("偵測到多款商品；目前請每款分別貼上解析，避免共用錯誤參數")
    if codes and len(codes) == 1:
        products = [{"code": normalize_code(codes[0][1]), "name": products[0]["name"] if products else ""}]
    size = rf"({number}(?:\s*[-~～]\s*{number})?(?:\s*[*xX×]\s*{number})*\s*(?:cm|mm|公分|毫米))"
    fields = {
        "outer_box_size": r"^(?:外箱規格|外箱尺寸|外箱)\s*:?\s*",
        "color_box_size": r"^(?:彩盒尺寸|彩盒|白盒尺寸|白盒|單個包裝尺寸|單個包裝|包裝盒尺寸|包裝盒|包裝尺寸|亞克力)\s*:?\s*",
        "prod_size": r"^(?:產品尺寸|單個尺寸|尺寸|產品)\s*:?\s*",
    }
    for field, label in fields.items():
        common[field] = ""
        for line in normalized.splitlines():
            cleaned = re.sub(EMOJI_PAT, "", line).strip()
            suffix = r"(?P<display_box>\s*\(\s*展示盒\s*\))?" if field == "color_box_size" else ""
            match = re.search(label + size + suffix + r"\s*$", cleaned, re.I)
            if match:
                display_box = bool(match.groupdict().get("display_box"))
                if display_box and not re.match(r"^彩盒(?:尺寸)?\s*[:：]?\s*(?=\d)", cleaned):
                    continue
                common[field] = match[1].strip() + ("（展示盒）" if display_box else "")
                break
    # Named metadata must not be swallowed by the product name.
    meta = rf"^(?:型號|貨號|產品編號|編號|(?:單|每)\s*[A-Za-z0-9\u4e00-\u9fff]{{1,6}}\s*(?:價格|價)|單價|價格|每箱|箱數|裝箱|一箱|單重|單個重量|每個重量|整箱|毛重|箱重|尺寸|產品尺寸|產品\s*:|彩盒|外箱|包裝|單個包裝|材質|材積|端盒|木架|木框|帶鐳|帶雷|USB|配件|電池|\d+\s*(?:個|款|種))"
    if len(products) == 1 and codes:
        name_lines = []
        for line in normalized.splitlines():
            line = re.sub(EMOJI_PAT, "", line).strip()
            license_stripped = strip_affirmative_license_markers(line)
            if license_stripped != line:
                line = license_stripped.strip()
                if not line:
                    continue
            if (
                re.match(meta, line, re.I)
                or is_name_metadata_line(line)
                or re.search(r"木架|木框", line)
                or supplemental_uncertainty_issues(line)
            ):
                continue
            if line:
                name_lines.append(line)
        products[0]["name"] = " ".join(name_lines[:3])
    # Keep supplemental text verbatim (normalized), including units/approximation.
    notes = []
    product_names = {
        str(product.get("name") or "").strip()
        for product in products
        if str(product.get("name") or "").strip()
    }
    canonical_dimension_labels = {
        "產品尺寸", "單個尺寸", "尺寸", "產品",
        "彩盒尺寸", "彩盒", "單個包裝尺寸", "單個包裝",
        "包裝盒尺寸", "包裝盒", "亞克力",
        "外箱規格", "外箱尺寸", "外箱",
    }
    for line in normalized.splitlines():
        line = line.strip()
        if line in product_names:
            continue
        if has_affirmative_license_marker(line):
            notes.append("正版授權")
            continue
        notes.extend(carton_qualifier_notes(line))
        dimension_info = labeled_dimension_info(line)
        if dimension_info and (
            not dimension_info["valid"]
            or dimension_info["label"] not in canonical_dimension_labels
            or dimension_info["label"] == "包裝尺寸"
        ):
            notes.append(line)
        weight_info = weight_field_info(line)
        if weight_info and (
            not weight_info["known"]
            or not weight_info["unit"]
            or (weight_info["label"] == "重量" and not weight_info["scope"])
        ):
            notes.append(line)
        if is_order_condition_line(line) or is_fulfilment_condition_line(line):
            notes.append(line)
        if re.fullmatch(r"展示盒\s*\d+\s*(?:個|隻|只|盒|套)", line):
            notes.append(line)
        if (
            re.search(r"帶[鐳雷]射|正版授權|材質|顏色|圖案|端盒|木架|木框|包裝|USB|充電|約.*(?:kg|公斤|克)|另加|另計|加收|另收|額外收費|附加費|運費|物流費|快遞費|郵費|配送費|打包費|包裝費|加工費|手工費|貼標費|印刷費|組裝費|開模費|版費|配件費|稅費|稅點|服務費|搬運費|裝卸費|差價|待定|待確認", line, re.I)
            or supplemental_uncertainty_issues(line)
        ):
            notes.append(line)
    if rack_context["state"] == "optional":
        notes.append("可加木架；成本按無木架的進價及重量計算")
    common["extra_tags"] = "\n".join(dict.fromkeys(notes))
    return common, products


def cost_blockers(price, qty, carton_kg, unit_g, dom, intl, ex, issues=(), price_unit="", qty_unit=""):
    reasons = list(issues)
    values = (price, qty, carton_kg, unit_g, dom, intl, ex)
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
        return reasons + ["成本參數不是有效數字"]
    if price <= 0:
        reasons.append("缺少有效進價")
    if qty <= 0 or qty != int(qty):
        reasons.append("缺少有效整數裝箱量")
    if min(carton_kg, unit_g, dom, intl) < 0 or ex <= 0:
        reasons.append("重量、運費或匯率不合法")
    weight = resolve_weight_inputs(carton_kg, unit_g, qty)
    if weight["source"] == "missing":
        reasons.append("缺少有效重量來源")
    if weight["mismatch_ratio"] >= 0.2:
        reasons.append("單個與整箱重量差異達20%，須先核對")
    if not qty_unit:
        reasons.append("裝箱單位未確認")
    if price_unit and qty_unit and price_unit != qty_unit:
        reasons.append("計價與裝箱單位不同，須先換算確認")
    return list(dict.fromkeys(reasons))


def supplemental_uncertainty_issues(text):
    """重新掃描可編輯補充欄，避免人工新增的疑慮繞過成本防呆。"""
    normalized = zhconv.convert(unicodedata.normalize("NFKC", text or ""), "zh-tw")
    # 先移除只屬於木架的子句，再掃描未確認與其他費用。
    non_rack_text = wooden_rack_cost_basis_text(normalized)
    issues = []
    if re.search(r"待定|待確認|另議|未定|以實際", non_rack_text):
        issues.append("補充欄含未確認條件，請先向來源確認")
    named_or_explicit_fee = re.search(
        r"另加|另計|加收|另收|補差|額外收費|不含運|運費另|附加費|"
        r"(?:運費|物流費|快遞費|郵費|配送費|打包費|包裝費|加工費|手工費|貼標費|印刷費|"
        r"組裝費|開模費|版費|配件費|稅費|稅點|服務費|搬運費|裝卸費|差價)",
        non_rack_text,
        re.I,
    )
    generic_fee = re.search(
        r"(?:(?!咖啡)[^\s，,;；:：]){1,8}費\s*[:：]?\s*(?:約)?\d+(?:\.\d+)?\s*元|"
        r"(?:保險|報關|倉儲|紙箱|木箱|托盤)\s*(?:費)?\s*[:：]?\s*(?:約)?\d+(?:\.\d+)?\s*元",
        non_rack_text,
        re.I,
    )
    if named_or_explicit_fee or generic_fee:
        issues.append("有非木架額外費用，須確認是否已包含重量及費用")
    return issues


def wooden_rack_note_integrity_issues(parsed_common, extra_text):
    """可選木架必須同時保留來源原文與「無木架成本」規則。"""
    if (parsed_common or {}).get("rack_state") != "optional":
        return []
    normalized_extra = zhconv.convert(
        unicodedata.normalize("NFKC", extra_text or ""),
        "zh-tw",
    )
    required = list((parsed_common or {}).get("rack_source_lines", []))
    required.append("可加木架；成本按無木架的進價及重量計算")
    normalized_required = [
        zhconv.convert(unicodedata.normalize("NFKC", item), "zh-tw")
        for item in required
    ]
    if any(item not in normalized_extra for item in normalized_required):
        return ["可選木架的原文或無木架成本規則已被刪除，必須完整保留"]
    return []


def duplicate_messages(incoming, sheets, exclude=None):
    """回報重複／衝突；原位修正時只排除已明確選定的那一個 NO。"""
    messages = []
    excluded = {
        (str(title), str(no).strip().lower())
        for title, no in (exclude or set())
    }
    existing = [
        (title, p)
        for title, rows in sheets.items()
        for p in extract_saved_products(rows)
        if (str(title), str(p["no"]).strip().lower()) not in excluded
    ]
    seen = []
    for item in incoming:
        code, name = normalize_code(item["code"]), normalize_name(item["name"])
        for title, saved in existing + [("本批次", p) for p in seen]:
            same_code = bool(code and code == saved["code"])
            same_name = bool(name and name == saved["name"])
            if same_code or same_name:
                kind = "同貨號不同品名衝突" if same_code and not same_name else "重複或同品名待核對"
                messages.append(f"{kind}：{code} {name} → {title} {saved.get('no', '')} {saved['name']}；停止寫入，不自動覆蓋或跳過")
        seen.append({"code": code, "name": name})
    return messages


def build_product_block(
    no_value,
    date_value,
    value_row,
    name,
    code,
    price,
    qty,
    qty_unit,
    carton_weight_kg,
    unit_weight_g,
    dom_rate,
    international_rate,
    exchange_rate,
    vendor,
    prod_size="",
    color_size="",
    outer_size="",
    extra="",
    blocked=False,
):
    """建立同一套 6x12 商品區塊，新增與原位修正共用。"""
    vendor = normalize_vendor(vendor)
    formulas = build_cost_formulas(
        value_row,
        carton_weight_kg,
        unit_weight_g,
        qty,
        dom_rate,
        international_rate,
        exchange_rate,
        vendor,
        final_price=price,
        blocked=blocked,
    )
    info_lines = [f"計價單位：{qty_unit}"]
    if prod_size:
        info_lines.append(f"尺寸 {prod_size}")
    if color_size:
        info_lines.append(f"彩盒尺寸 {color_size}")
    if outer_size:
        info_lines.append(f"外箱尺寸 {outer_size}")
    if extra:
        info_lines.append(str(extra).strip())
    info_display = "\n".join(info_lines)

    if carton_weight_kg > 0 and unit_weight_g > 0:
        weight_note = f"整箱毛重 {carton_weight_kg:g}KG／單個重量 {unit_weight_g:g}g"
    elif carton_weight_kg > 0:
        weight_note = f"整箱毛重 {carton_weight_kg:g}KG"
    elif unit_weight_g > 0:
        weight_note = f"單個重量 {unit_weight_g:g}g"
    else:
        weight_note = "重量 未提供"

    stored_price = price if isinstance(price, (int, float)) and math.isfinite(price) and price > 0 else ""
    return [
        [no_value, normalize_name(name), "10%報價", "13%報價", "15%報價", "20%報價", "進價rmb", f"重量g/{qty_unit}", "大陸運費rmb", "國際運費", "預估到手成本", vendor],
        [date_value, info_display, formulas["quote_10"], formulas["quote_13"], formulas["quote_15"], formulas["quote_20"], stored_price, formulas["weight"], formulas["domestic"], formulas["international"], formulas["cost"], ""],
        build_carton_note_row(qty, vendor, qty_unit),
        ["", weight_note] + [""] * 10,
        ["", f"貨號 {normalize_code(code)}"] + [""] * 10,
        [""] * 12,
    ]


UPDATE_CELL_COORDS = tuple(
    [(0, 1), (0, 7), (0, 11)]
    + [(1, col) for col in range(1, 11)]
    + [(2, 1), (2, 8), (3, 1), (4, 1)]
)
SAFETY_CLEAR_COORDS = tuple([(1, col) for col in (2, 3, 4, 5, 7, 8, 9, 10)])


def update_batch_data(base_row, new_block):
    """更新白名單；刻意不包含 A 欄、空白列、M:T、格式與圖片物件。"""
    block = pad_block(new_block)
    return [
        {"range": f"B{base_row}", "values": [[block[0][1]]]},
        {"range": f"H{base_row}", "values": [[block[0][7]]]},
        {"range": f"L{base_row}", "values": [[block[0][11]]]},
        {"range": f"B{base_row + 1}:K{base_row + 1}", "values": [block[1][1:11]]},
        {"range": f"B{base_row + 2}", "values": [[block[2][1]]]},
        {"range": f"I{base_row + 2}", "values": [[block[2][8]]]},
        {"range": f"B{base_row + 3}", "values": [[block[3][1]]]},
        {"range": f"B{base_row + 4}", "values": [[block[4][1]]]},
    ]


def safety_clear_block(old_formula_block):
    """資料仍待確認時，只清除衍生數字，保留全部原始欄位。"""
    result = pad_block(old_formula_block)
    result = [row[:] for row in result]
    for row, col in SAFETY_CLEAR_COORDS:
        result[row][col] = ""
    return result


def safety_clear_batch_data(base_row):
    return [
        {"range": f"C{base_row + 1}:F{base_row + 1}", "values": [["", "", "", ""]]},
        {"range": f"H{base_row + 1}:K{base_row + 1}", "values": [["", "", "", ""]]},
    ]


def user_entered_cell_data(value, formula_allowed=False):
    """建立明確型別的 Sheets CellData，文字永遠不交給公式解析器。"""
    if value is None or value == "":
        entered_value = {}
    elif formula_allowed:
        if not isinstance(value, str) or not value.startswith("="):
            raise ValueError("衍生欄位只能寫入公式或空白")
        entered_value = {"formulaValue": value}
    elif isinstance(value, bool):
        entered_value = {"boolValue": value}
    elif isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ValueError("不可寫入非有限數字")
        entered_value = {"numberValue": value}
    else:
        entered_value = {"stringValue": str(value)}
    return {"userEnteredValue": entered_value}


def correction_value_update_requests(
    worksheet_id,
    base_row,
    old_formula_block,
    new_block,
    active_coords,
):
    """用單一 atomic batch 的 typed updateCells，只建立真正有差異的請求。"""
    old, new = pad_block(old_formula_block), pad_block(new_block)
    requests = []
    for row, col in active_coords:
        if cells_equal(old[row][col], new[row][col]):
            continue
        requests.append({
            "updateCells": {
                "range": {
                    "sheetId": worksheet_id,
                    "startRowIndex": base_row - 1 + row,
                    "endRowIndex": base_row + row,
                    "startColumnIndex": col,
                    "endColumnIndex": col + 1,
                },
                "rows": [{
                    "values": [user_entered_cell_data(
                        new[row][col],
                        formula_allowed=(row, col) in SAFETY_CLEAR_COORDS,
                    )]
                }],
                "fields": "userEnteredValue",
            }
        })
    return requests


def cells_equal(actual, expected):
    if (
        isinstance(actual, (int, float))
        and not isinstance(actual, bool)
        and isinstance(expected, (int, float))
        and not isinstance(expected, bool)
    ):
        return actual == expected
    actual_value = "" if actual is None else actual
    expected_value = "" if expected is None else expected
    return str(actual_value) == str(expected_value)


def changed_update_cells(old_formula_block, new_block):
    old, new = pad_block(old_formula_block), pad_block(new_block)
    return [(row, col) for row, col in UPDATE_CELL_COORDS if not cells_equal(old[row][col], new[row][col])]


def update_diff_rows(old_display_block, new_block):
    """提供人可讀的修改前後差異；NO 與日期列出但永遠不寫入。"""
    old, new = pad_block(old_display_block), pad_block(new_block)
    fields = [
        ("NO（保留）", old[0][0], old[0][0]),
        ("原日期（保留）", old[1][0], old[1][0]),
        ("商品名稱", old[0][1], new[0][1]),
        ("貨號", old[4][1], new[4][1]),
        ("廠商", old[0][11], new[0][11]),
        ("進價 RMB", old[1][6], new[1][6]),
        ("商品資訊", old[1][1], new[1][1]),
        ("裝箱資訊", old[2][1], new[2][1]),
        ("重量資訊", old[3][1], new[3][1]),
        ("大陸運費備註", old[2][8], new[2][8]),
        ("報價／運費／成本", "現有值或公式", "依新版防呆公式重新計算" if any(new[1][col] for col in (2, 3, 4, 5, 7, 8, 9, 10)) else "全部留白"),
        ("圖片與格式", "保留", "不在寫入範圍"),
    ]
    return [{"欄位": field, "目前": before, "修正後": after} for field, before, after in fields]


def update_existing_product(
    category_name,
    base_row,
    expected_no,
    expected_worksheet_id,
    expected_rows,
    expected_formula_block,
    new_block,
    safety_only=False,
    identity_evidence="",
):
    """以精確 NO＋列號＋雙快照原位更新；任何漂移都停止，不重新定位。"""
    write_started = False
    try:
        if expected_rows is None or expected_formula_block is None:
            raise ValueError("缺少原商品快照")
        expected_display = get_saved_block(expected_rows, base_row)
        expected_formula = pad_block(expected_formula_block)
        planned = pad_block(new_block)
        expected_no = str(expected_no or "").strip()
        if not str(identity_evidence or "").strip():
            raise ValueError("缺少同一商品的人工作業確認")
        if not re.fullmatch(r"no\d+", expected_no, re.I):
            raise ValueError("原 NO 格式不合法")
        if str(expected_display[0][0]).strip().lower() != expected_no.lower():
            raise ValueError("所選 NO 與顯示快照不一致")
        if str(expected_formula[0][0]).strip().lower() != expected_no.lower():
            raise ValueError("所選 NO 與公式快照不一致")
        if sum(
            1 for row in expected_rows
            if row and str(row[0]).strip().lower() == expected_no.lower()
        ) != 1:
            raise ValueError("原 NO 不唯一，停止修正")
        if any(
            row and re.fullmatch(r"no\d+", str(row[0]).strip(), re.I)
            for row in expected_display[1:]
        ):
            raise ValueError("商品 6 列區塊重疊或不完整")
        if str(planned[0][0]).strip().lower() != expected_no.lower():
            raise ValueError("原位修正不得變更 NO")
        active_coords = SAFETY_CLEAR_COORDS if safety_only else UPDATE_CELL_COORDS
        if safety_only:
            for row in range(6):
                for col in range(12):
                    if (row, col) in SAFETY_CLEAR_COORDS:
                        if planned[row][col] != "":
                            raise ValueError("安全待確認模式只能清空衍生數字")
                    elif not cells_equal(planned[row][col], expected_formula[row][col]):
                        raise ValueError("安全待確認模式不得修改原始資料")
        if not any(not cells_equal(expected_formula[row][col], planned[row][col]) for row, col in active_coords):
            raise ValueError("沒有可套用的變更")

        creds = get_credentials()
        client = gspread.authorize(creds)
        spreadsheet = open_spreadsheet(client)
        try:
            sheet = spreadsheet.worksheet(category_name)
        except gspread.exceptions.WorksheetNotFound:
            st.error("找不到原分頁，停止修正；不會新建分頁。")
            return False
        if sheet.id != expected_worksheet_id:
            st.error("分頁已被刪除或重建，停止修正；請重新選擇原商品。")
            return False

        fresh_rows = sheet.get_all_values()
        fresh_formula = pad_block(sheet.get(
            f"A{base_row}:L{base_row + 5}",
            value_render_option="FORMULA",
            maintain_size=True,
            pad_values=True,
        ))
        if fresh_rows != expected_rows or fresh_formula != expected_formula:
            st.error("原商品或雲表已被修改，停止修正；請重新載入並重新核對差異。")
            get_all_sheets_data.clear()
            get_target_formula_block.clear()
            return False

        if not safety_only:
            live_sheets = {ws.title: ws.get_all_values() for ws in spreadsheet.worksheets()
                           if not ws.title.startswith("_")}
            planned_product = extract_saved_products(planned)[0]
            conflicts = duplicate_messages(
                [{"code": planned_product["code"], "name": planned_product["name"]}],
                live_sheets,
                exclude={(category_name, expected_no)},
            )
            if conflicts:
                st.error("；".join(conflicts))
                return False

        # 最後一次精確讀取；不因列移動而自動重新搜尋目標。
        # 同時再次掃描全部分頁，把跨分頁撞單的競態窗口縮到最小。
        final_live_sheets = {
            ws.title: ws.get_all_values() for ws in spreadsheet.worksheets()
            if not ws.title.startswith("_")
        }
        if final_live_sheets.get(category_name) != expected_rows or pad_block(sheet.get(
            f"A{base_row}:L{base_row + 5}",
            value_render_option="FORMULA",
            maintain_size=True,
            pad_values=True,
        )) != expected_formula:
            st.error("送出前原商品又有變動，已停止修正。")
            get_all_sheets_data.clear()
            get_target_formula_block.clear()
            return False
        if not safety_only:
            final_conflicts = duplicate_messages(
                [{
                    "code": planned_product["code"],
                    "name": planned_product["name"],
                }],
                final_live_sheets,
                exclude={(category_name, expected_no)},
            )
            if final_conflicts:
                st.error("送出前再次核對發現撞單：" + "；".join(final_conflicts))
                return False

        requests = correction_value_update_requests(
            sheet.id,
            base_row,
            expected_formula,
            planned,
            active_coords,
        )
        if not requests:
            raise ValueError("送出前已沒有可套用的變更")
        write_started = True
        spreadsheet.batch_update({"requests": requests})

        actual_formula = pad_block(sheet.get(
            f"A{base_row}:L{base_row + 5}",
            value_render_option="FORMULA",
            maintain_size=True,
            pad_values=True,
        ))
        for row, col in active_coords:
            if not cells_equal(actual_formula[row][col], planned[row][col]):
                raise ValueError(f"寫後核對不符：{category_name}!{chr(65 + col)}{base_row + row}")
        # 所有非白名單儲存格（含 NO、日期、空白列）必須完全不變。
        whitelist = set(active_coords)
        for row in range(6):
            for col in range(12):
                if (row, col) not in whitelist and not cells_equal(actual_formula[row][col], expected_formula[row][col]):
                    raise ValueError(f"保留欄位遭變更：{category_name}!{chr(65 + col)}{base_row + row}")
        return True
    except Exception as e:
        get_all_sheets_data.clear()
        get_target_formula_block.clear()
        if write_started:
            st.error(f"原位資料可能已更新，但寫後核對未完成。請先檢查原 NO，不可直接重試。詳情：{e}")
        else:
            st.error(f"尚未更新原商品：{e}")
        return False


def get_fresh_line_ad_block(category_name, base_row, expected_block):
    """Read without the list cache and reject changed/moved/duplicate records."""
    client = gspread.authorize(get_credentials())
    worksheet = open_spreadsheet(client).worksheet(category_name)
    rows = worksheet.get_all_values()
    expected = pad_block(expected_block)
    matches = [
        product for product in extract_saved_products(rows)
        if product["no"].lower() == str(expected[0][0]).lower()
    ]
    if len(matches) != 1 or matches[0]["row_index"] != base_row:
        raise ValueError("商品 NO 重複、已移動或已刪除，請重新載入並核對")
    fresh = get_saved_block(rows, base_row)
    if any(not cells_equal(fresh[r][c], expected[r][c]) for r in range(6) for c in range(12)):
        raise ValueError("雲表商品已變更，請重新載入並重新核對")
    return fresh


# --- 5. LINE 廣告文案（只讀取既有雲表，不改動採購資料） ---
with st.expander("📣 LINE 廣告文案（從雲表唯讀產生）"):
    st.caption(
        "分類代碼與發送管理共用；未設定時請先到「分類代碼設定」補齊。文案移除新品字樣；正版授權置頂；"
        "有彩盒／包裝／端盒尺寸時不顯示產品尺寸，並排除重量、外箱尺寸及所有木架資訊。"
    )
    if st.checkbox("載入雲表商品", key="load_line_ad_copy"):
        ad_sheets = get_all_sheets_data()
        if ad_sheets is None:
            st.error("無法讀取雲表，停止產生廣告文案。")
        else:
            try:
                ad_codes = get_category_codes()
            except Exception as exc:
                st.error(f"分類代碼讀取失敗，停止產生：{exc}")
                ad_codes = {}
            ad_categories = [
                category for category in ad_codes
                if category in ad_sheets
            ]
            if not ad_categories:
                st.error("找不到已設定廣告代號的分頁。")
            else:
                ad_category = st.selectbox(
                    "廣告分頁",
                    ad_categories,
                    key="line_ad_category",
                )
                ad_products = extract_saved_products(ad_sheets[ad_category])
                ad_product_map = {
                    f"{item['row_index']}|{item['no']}": item
                    for item in ad_products
                }
                ad_selected = st.selectbox(
                    "選擇商品（可搜尋 NO、貨號或名稱）",
                    [""] + list(ad_product_map),
                    format_func=lambda key: (
                        "請選擇商品"
                        if not key else
                        f"{ad_product_map[key]['no']}｜"
                        f"{ad_product_map[key]['code'] or '無貨號'}｜"
                        f"{ad_product_map[key]['name']}"
                    ),
                    key="line_ad_product",
                )
                if ad_selected:
                    ad_product = ad_product_map[ad_selected]
                    ad_block = get_saved_block(
                        ad_sheets[ad_category],
                        ad_product["row_index"],
                    )
                    ad_review_key = hashlib.sha256(repr((ad_category, ad_selected, ad_block)).encode()).hexdigest()
                    st.write(f"核對目標：{ad_category}｜{ad_product['no']}｜廠商 {ad_block[0][11]}")
                    st.dataframe(pd.DataFrame(ad_block), hide_index=True, width="stretch")
                    st.caption("程式檢查不代表原文已核對；請確認來源、單位、重量、費用與採用匯率，以及圖片屬於同一商品。")
                    ad_ready = True
                    try:
                        build_line_ad_copy_from_sheet_block(ad_category, ad_block, ad_codes)
                    except ValueError as error:
                        ad_ready = False
                        st.error(f"程式檢查未通過：{error}")
                    ad_reviewed = st.checkbox(
                        "我已逐欄對照本款完整原文及原圖，並確認廠商與成本計算依據",
                        key="ad_review_" + ad_review_key,
                        disabled=not ad_ready,
                    )
                    if st.button("檢查並產生 LINE 文案", disabled=not ad_ready or not ad_reviewed):
                        try:
                            fresh_ad_block = get_fresh_line_ad_block(ad_category, ad_product["row_index"], ad_block)
                            ad_copy = build_line_ad_copy_from_sheet_block(ad_category, fresh_ad_block, get_category_codes())
                        except Exception as error:
                            get_all_sheets_data.clear()
                            st.error(f"停止產生廣告文案：{error}")
                        else:
                            st.code(ad_copy, language=None)
                            st.caption("售價取雲表 10% 報價並無條件進位；交期固定 2-3 週。此處只產生文案，不會發送 LINE。")


# --- 6. 主畫面流程 ---
user_input = st.text_area("📝 第一步:每次貼上一款廠商完整文案（含補充費用）", height=200)
# A monotonic revision also resets A -> B -> A. A content hash alone can
# revive an old manually edited draft if the same source is pasted again.
if st.session_state.get("draft_source") != user_input:
    st.session_state["draft_source"] = user_input
    st.session_state["draft_revision"] = st.session_state.get("draft_revision", 0) + 1
draft_key = f"draft_{st.session_state['draft_revision']}_"
user_input_tw = zhconv.convert(user_input, 'zh-tw') if user_input else ""
common_data, products_data = parse_text(user_input)
with st.expander("🔧 診斷資訊 (若解析有誤可展開查看)"):
    st.write(f"原始輸入長度: {len(user_input)}")
    st.write(f"轉繁後長度: {len(user_input_tw)}")
    st.write(f"抓到商品數: {len(products_data)}")
    st.write("共用參數:", common_data)
    st.write("商品清單:", products_data)
    st.code(user_input, language=None)
st.subheader("🔍 第二步:共用參數校正")
c1, c2, c3, c4, c5 = st.columns(5)
final_price = c1.number_input("進價(RMB)", value=common_data["price"], format="%.2f", key=draft_key + "price")
final_qty = c2.number_input("裝箱量", value=common_data["qty"], step=1, key=draft_key + "qty")
final_unit_weight_g = c3.number_input(
    "單個重量(g)",
    value=common_data["unit_weight_g"],
    format="%.2f",
    key=draft_key + "unit_weight",
)
final_carton_weight_kg = c4.number_input(
    "整箱毛重(kg)",
    value=common_data["weight"],
    format="%.2f",
    key=draft_key + "carton_weight",
)
final_dom = c5.number_input("內陸運費(R/kg)", value=dom_rate_def, key=draft_key + "dom")
weight_state = resolve_weight_inputs(
    final_carton_weight_kg,
    final_unit_weight_g,
    final_qty,
)
if weight_state["source"] == "missing":
    st.warning("⚠️ 缺少單個重量或整箱毛重：重量、運費、預估到手成本及 10%～20% 報價會留白，避免誤報價。")
elif weight_state["source"] == "unit":
    st.info(
        f"ℹ️ 已辨識單個重量 {final_unit_weight_g:g}g；運費將以 "
        f"{weight_state['chargeable_weight_g']:.2f}g/pcs（含 5%）計算，不推算整箱毛重。"
    )
elif weight_state["source"] == "both" and weight_state["mismatch_ratio"] >= 0.2:
    st.warning(
        "⚠️ 單個重量與整箱毛重換算差異超過 20%；"
        "停止計算，請核對後修正重量來源；不再自動取較大值報價。"
    )
c6, c7 = st.columns(2)
final_prod_size = c6.text_input("產品尺寸 (沒抓到可手動輸入)", value=common_data["prod_size"], key=draft_key + "product_size")
final_color_size = c7.text_input("彩盒尺寸 (亞克力/單個包裝也算)", value=common_data["color_box_size"], key=draft_key + "color_size")
final_outer_size = st.text_input("外箱尺寸 (沒抓到可手動輸入)", value=common_data["outer_box_size"], key=draft_key + "outer_size")
final_extra = st.text_area("額外備註（保留顏色、材質、端盒、木架等）", value=common_data["extra_tags"], key=draft_key + "extra")
unit_options = ["", "個", "盒", "套", "瓶", "罐", "包", "袋"]
parsed_unit = common_data["qty_unit"]
final_qty_unit = st.selectbox("装箱及計價單位（必須一致；不同時先人工換算）", unit_options,
                                index=unit_options.index(parsed_unit) if parsed_unit in unit_options else 0,
                                key=draft_key + "unit")
active_issues = list(common_data["issues"])
active_issues.extend(supplemental_uncertainty_issues(final_extra))
note_integrity_issues = wooden_rack_note_integrity_issues(common_data, final_extra)
active_issues.extend(note_integrity_issues)
active_issues = list(dict.fromkeys(active_issues))
if note_integrity_issues:
    st.warning("原文是木架選配商品；木架原文與無木架成本說明不可刪除或改寫。")
if any("非木架額外費用" in issue for issue in active_issues):
    st.warning("非木架附加項目未確認前不提供成本。若費用另加，先將每銷售單位進價及整箱重量校正為含附加項目的數值；木架／木框依固定規則不列入。")
    confirmation_key = hashlib.sha256(
        repr((
            draft_key,
            user_input,
            final_price,
            final_qty,
            final_unit_weight_g,
            final_carton_weight_kg,
            final_extra,
        )).encode()
    ).hexdigest()
    extra_basis = st.text_input("供應商確認依據／費用與重量換算說明", key="basis_" + confirmation_key)
    if st.checkbox("已向來源確認：上述進價、重量已涵蓋全部附加費用，沒有未確認項目", key="extra_" + confirmation_key) and extra_basis.strip():
        active_issues = [issue for issue in active_issues if "非木架額外費用" not in issue]
        final_extra += "\n附加費用確認：" + extra_basis.strip()
block_reasons = cost_blockers(final_price, final_qty, final_carton_weight_kg, final_unit_weight_g,
                             final_dom, intl_rate, ex_rate, active_issues, common_data["price_unit"], final_qty_unit)
if block_reasons:
    st.error(
        "待確認：" + "；".join(block_reasons)
        + "。重量、運費、成本、報價全部留白；新增商品禁止存檔，"
        "修正既有商品只能清除原列的不安全衍生數字。"
    )
    st.write({key: "" for key in ("計費重量", "內陸運費", "國際運費", "到手成本", "10%報價", "13%報價", "15%報價", "20%報價")})
st.markdown("---")
st.subheader(f"📋 擷取到的商品清單 (共 {len(products_data)} 筆,可直接編輯、新增或刪除)")
df_items = pd.DataFrame(products_data)
if "code" not in df_items.columns:
    df_items["code"] = ""
if "name" not in df_items.columns:
    df_items["name"] = ""
df_items.insert(0, "寫入", True)
df_items = df_items.rename(columns={"code": "貨號", "name": "名稱"})
edited_df = st.data_editor(df_items, num_rows="dynamic", width="stretch", key=draft_key + "products")
if user_input.strip():
    st.markdown("---")
    st.subheader("📊 第三步：選擇新增或原位修正")
    operation = st.radio(
        "操作方式",
        ["新增商品", "修正既有商品"],
        horizontal=True,
        help="原位修正不新增列、不更改 NO 或原日期，也不處理圖片與格式。",
        key=draft_key + "operation",
    )
    try:
        quote_category_codes = get_category_codes()
    except Exception as exc:
        st.error(f"分類設定讀取失敗：{exc}，請重新載入後再保存。")
        st.stop()
    final_category = st.selectbox(
        "📂 分頁",
        [""] + list(dict.fromkeys((*QUOTE_CATEGORIES, *quote_category_codes))),
        index=0,
        format_func=lambda value: value or "請選擇本款分頁",
        key=draft_key + "category",
    )

    to_save_df = edited_df[
        (edited_df["寫入"] == True)
        & ((edited_df["貨號"] != "") | (edited_df["名稱"] != ""))
    ]
    hard_reasons = []
    invalid_names = any(
        not normalize_name(row["名稱"]) for _, row in to_save_df.iterrows()
    )
    if len(to_save_df) != 1 or invalid_names:
        hard_reasons.append("每次只能選擇一款有完整品名的商品")

    all_sheets_data = get_all_sheets_data()
    if all_sheets_data is None:
        st.error("雲表讀取失敗，停止所有存檔；不可把讀取失敗當成空表。")
        st.stop()
    if not final_category:
        hard_reasons.append("尚未選擇本款分頁")
    elif final_category not in all_sheets_data:
        hard_reasons.append("找不到指定分頁")
        st.error("找不到指定分頁，已停止；不會自動新建分頁。")
    if final_category and final_category not in quote_category_codes:
        st.warning("本分類尚未設定廣告代碼：報價仍可保存，但產生廣告前需到發送管理的「分類代碼設定」補齊。")

    selected_target = None
    target_display_block = None
    target_formula_block = None
    target_worksheet_id = None
    identity_verified = False
    identity_evidence = ""
    source_product = None
    if len(to_save_df) == 1:
        source_row = to_save_df.iloc[0]
        source_product = {
            "code": normalize_code(source_row["貨號"]),
            "name": normalize_name(source_row["名稱"]),
        }

    if operation == "修正既有商品" and source_product and final_category in all_sheets_data:
        candidates = find_update_candidates(source_product, all_sheets_data[final_category])
        candidate_map = {
            f"{item['row_index']}|{item['no']}": item for item in candidates
        }
        target_key = "update_target_" + hashlib.sha256(
            repr((draft_key, user_input, source_product, final_category)).encode()
        ).hexdigest()
        selected_key = st.selectbox(
            "🎯 要修正的原商品（可搜尋 NO、貨號或名稱）",
            [""] + list(candidate_map),
            index=0,
            format_func=lambda key: (
                "請明確選擇原 NO；系統不會自動選定"
                if not key else
                f"{candidate_map[key]['match_reason']}｜{candidate_map[key]['no']}｜"
                f"{candidate_map[key]['code'] or '無貨號'}｜{candidate_map[key]['name']}"
            ),
            key=target_key,
        )
        st.caption("符合解析結果者只會排在前面；即使只有一筆，也必須由你親自選定原 NO。")
        if selected_key:
            selected_target = candidate_map[selected_key]
            base_row = selected_target["row_index"]
            target_display_block = get_saved_block(
                all_sheets_data[final_category], base_row
            )
            target_snapshot = get_target_formula_block(final_category, base_row)
            if target_snapshot is None:
                hard_reasons.append("無法取得原商品公式快照")
            else:
                target_worksheet_id = target_snapshot["worksheet_id"]
                target_formula_block = target_snapshot["block"]
                no_count = sum(
                    1 for item in extract_saved_products(all_sheets_data[final_category])
                    if item["no"].lower() == selected_target["no"].lower()
                )
                if no_count != 1:
                    hard_reasons.append("原 NO 在分頁內不唯一")
                code_match = bool(
                    source_product["code"]
                    and source_product["code"] == selected_target["code"]
                )
                name_match = bool(
                    normalize_name_key(source_product["name"])
                    and normalize_name_key(source_product["name"])
                    == normalize_name_key(selected_target["name"])
                )
                if code_match and name_match:
                    identity_verified = True
                    identity_evidence = "貨號＋品名完全相符"
                    st.success(f"目標身分相符：{final_category} {selected_target['no']}")
                elif code_match or name_match:
                    st.warning(
                        "目標只有"
                        + ("貨號" if code_match else "品名")
                        + "符合；請在差異表確認這就是要修正的原商品。"
                    )
                    partial_identity_confirmed = st.checkbox(
                        f"我已核對原圖／完整原文，確認 {selected_target['no']} 是同一商品，"
                        + ("此次是在修正品名" if code_match else "此次是在修正貨號"),
                        key="partial_identity_" + target_key,
                    )
                    identity_verified = partial_identity_confirmed
                    identity_evidence = (
                        "單一識別欄符合，已人工確認另一欄修正"
                        if partial_identity_confirmed else ""
                    )
                    if not identity_verified:
                        hard_reasons.append("貨號或品名不一致，尚未人工確認")
                else:
                    st.error("所選 NO 的貨號與品名都不同，不能直接套用。")
                    identity_basis = st.text_input(
                        "請填寫用原圖／原文確認為同一商品的依據",
                        key="identity_basis_" + target_key,
                    )
                    identity_confirmed = st.checkbox(
                        f"我已用原圖或完整原文確認 {selected_target['no']} 就是同一商品",
                        key="identity_confirm_" + target_key,
                    )
                    identity_verified = bool(
                        identity_basis.strip() and identity_confirmed
                    )
                    identity_evidence = (
                        identity_basis.strip() if identity_verified else ""
                    )
                    if not identity_verified:
                        hard_reasons.append("目標商品身分尚未確認")
        else:
            hard_reasons.append("尚未選定要修正的原 NO")

    vendor_options, existing_vendor = canonical_vendor_options(
        target_display_block[0][11]
        if target_display_block else ""
    )
    vendor_default = (
        vendor_options.index(existing_vendor)
        if existing_vendor in vendor_options else 0
    )
    vendor_key = "vendor_" + hashlib.sha256(
        repr((draft_key, operation, final_category, selected_target and selected_target["no"])).encode()
    ).hexdigest()
    final_vendor = st.selectbox(
        "🏷️ 廠商",
        vendor_options,
        index=vendor_default,
        format_func=lambda value: value or "請明確選擇本款廠商（不依貨號或格式猜測）",
        key=vendor_key,
        disabled=bool(operation == "修正既有商品" and block_reasons),
    )
    final_vendor = normalize_vendor(final_vendor)
    st.caption("廠商名稱統一為小寫 v＋名稱；已確認的舊別名會自動合併。")
    if not final_vendor and not (operation == "修正既有商品" and block_reasons):
        hard_reasons.append("尚未選擇本款廠商")
    if is_free_shipping_vendor(final_vendor):
        st.success("✅ 此廠商廣州包郵")

    duplicate_warnings = []
    if source_product and operation == "新增商品":
        duplicate_warnings = duplicate_messages(
            [source_product], all_sheets_data
        )
    elif (
        source_product
        and operation == "修正既有商品"
        and selected_target
        and not block_reasons
    ):
        duplicate_warnings = duplicate_messages(
            [source_product],
            all_sheets_data,
            exclude={(final_category, selected_target["no"])},
        )
    for warning in duplicate_warnings:
        st.error(f"🚨 撞單雷達警告：{warning}")

    planned_update_block = None
    safety_only = False
    update_has_changes = False
    if (
        operation == "修正既有商品"
        and selected_target
        and target_formula_block is not None
        and target_display_block is not None
        and source_product
    ):
        base_row = selected_target["row_index"]
        if block_reasons:
            safety_only = True
            planned_update_block = safety_clear_block(target_formula_block)
            update_has_changes = any(
                not cells_equal(target_formula_block[row][col], planned_update_block[row][col])
                for row, col in SAFETY_CLEAR_COORDS
            )
            st.warning(
                "這筆資料仍有疑慮：本次只允許清空 C:F 與 H:K 的報價、重量、運費及成本；"
                "名稱、貨號、進價、備註、NO、日期、圖片與格式都不會改。"
            )
        else:
            planned_update_block = build_product_block(
                selected_target["no"],
                target_display_block[1][0],
                base_row + 1,
                source_product["name"],
                source_product["code"],
                final_price,
                final_qty,
                final_qty_unit,
                final_carton_weight_kg,
                final_unit_weight_g,
                final_dom,
                intl_rate,
                ex_rate,
                final_vendor,
                final_prod_size,
                final_color_size,
                final_outer_size,
                final_extra,
                blocked=False,
            )
            update_has_changes = bool(
                changed_update_cells(target_formula_block, planned_update_block)
            )

        st.info(
            f"修正目標：{final_category}!A{base_row}:L{base_row + 5}｜"
            f"{selected_target['no']}。A 欄 NO、原日期、第 6 列空白、M:T、格式及圖片不在寫入範圍。"
        )
        st.dataframe(
            pd.DataFrame(update_diff_rows(target_display_block, planned_update_block)),
            width="stretch",
            hide_index=True,
        )
        if not update_has_changes:
            hard_reasons.append("目前沒有可套用的變更")
            st.info("目前沒有可套用的變更；不會重複寫入。")

    quote_images = []
    if source_product and final_category and not safety_only:
        image_key = "quote_images_" + hashlib.sha256(repr((draft_key, operation, final_category, final_vendor,
                    source_product, selected_target and selected_target["no"])).encode()).hexdigest()
        quote_images, image_errors = render_quote_images(image_key)
        hard_reasons.extend(image_errors)
    if hard_reasons:
        for reason in dict.fromkeys(hard_reasons):
            st.error(f"停止存檔：{reason}")

    if not to_save_df.empty:
        cost_inputs = dict(price=final_price, qty=final_qty, unit=final_qty_unit,
                           carton_kg=final_carton_weight_kg, unit_g=final_unit_weight_g,
                           dom_rate=final_dom, intl_rate=intl_rate, ex_rate=ex_rate)
        cost_notes = ("報價操作者已勾選逐欄核對。沿用現行規則：毛利率 10%；重量加計 5%；"
                      "木架／木框不列入，其他附加費用須已涵蓋於進價及重量。\n" + final_extra)
        st.caption("按一次儲存：商品寫入後，完整廠商原文、擷取值與當次參數會另存到同一份 Google 雲表的「_報價依據」分頁並讀回確認；修改保留歷史，不改成本公式。")
        review_payload = (
            draft_key,
            user_input,
            to_save_df.to_dict(),
            final_price,
            final_qty,
            final_qty_unit,
            final_carton_weight_kg,
            final_unit_weight_g,
            final_dom,
            intl_rate,
            ex_rate,
            operation,
            final_category,
            final_vendor,
            final_prod_size,
            final_color_size,
            final_outer_size,
            final_extra,
            selected_target,
            identity_verified,
            identity_evidence,
            target_worksheet_id,
            target_formula_block,
            planned_update_block,
            safety_only,
            [a["sha256"] for a in quote_images],
        )
        review_key = hashlib.sha256(repr(review_payload).encode()).hexdigest()
        if operation == "修正既有商品" and selected_target:
            confirm_label = (
                f"我確認目標是 {final_category} {selected_target['no']}，"
                + ("並只清除待確認的衍生數字" if safety_only else "已核對完整原文及上述修改前後差異")
            )
        else:
            confirm_label = "我已逐欄對照原文、補充資訊及廠商，確認新增這 1 款商品"
        final_confirm = st.checkbox(confirm_label, key="review_" + review_key)
        if quote_images:
            st.caption("本次確認也包含：上方原圖屬於這款商品。成功保存商品後才綁定；既有不同圖庫圖片不會被自動覆蓋。")

        if operation == "新增商品":
            create_disabled = bool(
                not final_confirm
                or block_reasons
                or duplicate_warnings
                or hard_reasons
                or len(to_save_df) != 1
            )
            if st.button("💾 新增商品", type="primary", disabled=create_disabled):
                target_data = all_sheets_data[final_category]
                true_last_row = len(target_data)
                max_no = 0
                for existing_row in target_data:
                    if existing_row and existing_row[0]:
                        match = re.search(r"no(\d+)", str(existing_row[0]), re.I)
                        if match:
                            max_no = max(max_no, int(match.group(1)))
                start_row = true_last_row + 2 if true_last_row > 0 else 1
                next_no = f"no{max_no + 1}"
                today = datetime.datetime.now(ZoneInfo("Asia/Taipei"))
                today_str = f"{today.year}/{today.month}/{today.day}"
                source_row = to_save_df.iloc[0]
                new_block = build_product_block(
                    next_no,
                    today_str,
                    start_row + 1,
                    source_row["名稱"],
                    source_row["貨號"],
                    final_price,
                    final_qty,
                    final_qty_unit,
                    final_carton_weight_kg,
                    final_unit_weight_g,
                    final_dom,
                    intl_rate,
                    ex_rate,
                    final_vendor,
                    final_prod_size,
                    final_color_size,
                    final_outer_size,
                    final_extra,
                    blocked=False,
                )
                if save_bulk_to_worksheet(
                    final_category,
                    new_block,
                    start_row,
                    block_size=6,
                    expected_rows=target_data,
                ):
                    get_all_sheets_data.clear()
                    st.success(
                        f"✅ 寫入並核對成功！已將商品存入【{final_category}】，"
                        f"編號【{next_no}】，廠商【{final_vendor}】。"
                    )
                    evidence_saved = persist_quote_evidence(final_category, start_row, new_block, user_input,
                                                           cost_inputs, common_data, cost_notes)
                    persist_quote_images(final_category, start_row, new_block, quote_images)
                    if not evidence_saved:
                        st.rerun()
        else:
            update_disabled = bool(
                not final_confirm
                or hard_reasons
                or not selected_target
                or not identity_verified
                or target_formula_block is None
                or not update_has_changes
                or (duplicate_warnings and not safety_only)
            )
            button_label = (
                "🛡️ 原位清除不安全的衍生數字"
                if safety_only else
                "🛠️ 套用原位修正"
            )
            if st.button(button_label, type="primary", disabled=update_disabled):
                if update_existing_product(
                    final_category,
                    selected_target["row_index"],
                    selected_target["no"],
                    target_worksheet_id,
                    all_sheets_data[final_category],
                    target_formula_block,
                    planned_update_block,
                    safety_only=safety_only,
                    identity_evidence=identity_evidence,
                ):
                    get_all_sheets_data.clear()
                    get_target_formula_block.clear()
                    if safety_only:
                        st.success(
                            f"✅ 已原位清除【{final_category} {selected_target['no']}】"
                            "的報價、重量、運費與成本；NO、日期、原始資料、圖片及格式均保留。"
                        )
                    else:
                        st.success(
                            f"✅ 已原位修正並核對【{final_category} {selected_target['no']}】；"
                            "沒有新增列，NO、原日期、圖片及格式均保留。"
                        )
                        evidence_saved = persist_quote_evidence(final_category, selected_target["row_index"], planned_update_block,
                                                               user_input, cost_inputs, common_data, cost_notes)
                        persist_quote_images(final_category, selected_target["row_index"], planned_update_block, quote_images)
                        if not evidence_saved:
                            st.rerun()
