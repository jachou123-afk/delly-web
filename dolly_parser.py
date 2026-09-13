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
# --- 1. 網頁基本設定 ---
st.set_page_config(page_title="半自動 - 採購報價彙整表", layout="wide")
st.title("🪐 半自動 - 採購報價彙整表 V74")
st.info("V74：逐款解析、保留補充資訊、成本防呆、衝突阻擋及寫後核對。重量加成5%、多品村廣州包郵與既有報價公式不變。")
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
    normalized = re.sub(r"\s+", "", str(vendor or "")).lower()
    return normalized in ("多品村", "v多品村")

def build_carton_note_row(final_qty, vendor, qty_unit="個"):
    """建立裝箱備註列，並把多品村包郵註記放在大陸運費欄下方。"""
    row = [""] * 12
    row[1] = f"裝箱 {final_qty}{qty_unit}/箱"
    if is_free_shipping_vendor(vendor):
        row[8] = "廣州包郵"
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
        })
    return products

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
        live_sheets = {ws.title: ws.get_all_values() for ws in spreadsheet.worksheets()}
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
            values=bulk_rows,
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
    if (not all(isinstance(v, (int, float)) and math.isfinite(v) for v in numeric_inputs)
            or min(carton_weight_kg, unit_weight_g, final_dom, intl_rate) < 0
            or ex_rate <= 0 or final_qty <= 0 or final_qty != int(final_qty)):
        return blank
    weight_state = resolve_weight_inputs(carton_weight_kg, unit_weight_g, final_qty)
    if (blocked or final_qty <= 0 or weight_state["source"] == "missing"
            or weight_state["mismatch_ratio"] >= 0.2
            or (final_price is not None and (not math.isfinite(final_price) or final_price <= 0))):
        return blank

    if weight_state["source"] == "missing":
        weight_formula = ""
    elif weight_state["source"] == "unit":
        weight_formula = f"=ROUNDUP({unit_weight_g}*1.05,2)"
    elif weight_state["source"] == "carton":
        weight_formula = (
            f"=ROUNDUP(({carton_weight_kg}/{final_qty})*1000*1.05,2)"
        )
    else:
        weight_formula = (
            f"=ROUNDUP(MAX(({carton_weight_kg}/{final_qty})*1000,"
            f"{unit_weight_g})*1.05,2)"
        )

    if is_free_shipping_vendor(vendor):
        domestic_formula = (
            f'=IF(OR({weight_cell}="",{weight_cell}<=0),"",0)'
        )
    else:
        domestic_formula = (
            f'=IF(OR({weight_cell}="",{weight_cell}<=0),"",'
            f'ROUNDUP(({weight_cell}/1000)*{final_dom},2))'
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
            f'ROUNDUP(({weight_cell}/1000)*{intl_rate},2))'
        ),
        "cost": (
            f'=IF(OR(NOT(ISNUMBER(G{v_r})),G{v_r}<=0,{weight_cell}="",{weight_cell}<=0,'
            f'{domestic_cell}="",{international_cell}=""),"",'
            f'ROUND((G{v_r}+{domestic_cell}+{international_cell})*{ex_rate},1))'
        ),
    }
    # A later manual deletion of G must also hide all derived numbers.
    for key, formula in result.items():
        if formula:
            result[key] = f'=IFERROR(IF(OR(NOT(ISNUMBER(G{v_r})),G{v_r}<=0),"",{formula[1:]}),"")'
    return result
# --- 3. 側邊欄設定 ---
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
    if re.search(r'正版授權|正版授权', text_n):
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
            '數量','数量','重量','單重','单重','單個重量','单个重量',
            '每個重量','每个重量','毛重','箱重','整箱重量','整箱毛重',
            '尺寸','單個尺寸','单个尺寸','單個包裝','单个包装',
            '彩盒','外箱','產品','产品','規格','规格','亞克力','亚克力',
            '包裝','包装','運費','运费','材積','材积','條碼','条码',
            '配件','需要','帶鐳','帶雷','镭射','电池','電池'
        ]

        name_lines = []
        for line in lines:
            line_s = line.strip()
            line_s = re.sub(r'^[#＃【】\[\]]+', '', line_s).strip()
            line_s = re.sub(EMOJI_PAT, '', line_s).strip()
            line_s = re.sub(r'\[[^\]]{1,20}\]', '', line_s).strip()
            if not line_s:
                continue

            is_excluded = False
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


def parse_text(text):
    """單一報價的保守解析；不把多則獨立報價套用同一組數字。"""
    normalized = zhconv.convert(unicodedata.normalize("NFKC", text or ""), "zh-tw")
    common, products = parse_text_legacy(normalized)
    common = {**common, "raw_text": text or "", "issues": [], "price_unit": "", "qty_unit": ""}
    if not normalized.strip():
        return common, []
    number = r"\d+(?:\.\d+)?"
    units = r"pcs|個|隻|只|盒|套|瓶|罐|包|袋|件"
    issues = common["issues"]
    # Require a price or carton label, never use the count inside an OPP bag.
    prices = list(re.finditer(
        rf"(?:單(?P<unit>{units})價格|單價|價格|價錢|售價|都是|💰)\s*:?\s*(?:RMB|¥)?\s*(?P<value>{number})",
        normalized, re.I))
    if not prices:
        prices = list(re.finditer(rf"(?P<value>{number})\s*元(?:\s*/\s*(?P<unit>{units}))?", normalized, re.I))
        prices = [m for m in prices if not re.search(r"運費|木架|木框|包裝費|打包費|加工費", normalized[normalized.rfind("\n", 0, m.start()) + 1:m.start()])]
    common["price"] = float(prices[0]["value"]) if prices else 0.0
    if prices:
        common["price_unit"] = canonical_unit(prices[0]["unit"])
        tail = normalized[prices[0].end():]
        suffix_unit = re.match(rf"\s*元\s*/\s*({units})", tail, re.I)
        if suffix_unit:
            common["price_unit"] = canonical_unit(suffix_unit[1])
        if re.match(r"\s*[-~～,]\s*\d", tail):
            issues.append("價格含範圍或不明數字格式，請確認單一進價")
        if re.match(r"\s*元?\s*起", tail):
            issues.append("價格僅為起價，須確認實際進價")
    if re.search(r"(?:單價|價格|進價)\s*:?\s*(?:約|大約)|待定|待確認|另議|未定|以實際", normalized):
        issues.append("原文含未確認條件，請先向來源確認")
    if len(prices) > 1:
        issues.append("存在多個價格，請一次貼一則報價並確認費用範圍")
    qty_matches = list(re.finditer(
        rf"(?:每箱數量|箱數|裝箱量|裝箱數|裝箱|一箱)\s*:?\s*(?P<value>\d+)\s*(?P<unit>{units})?",
        normalized, re.I))
    if not qty_matches:
        qty_matches = list(re.finditer(rf"(?P<value>\d+)\s*(?P<unit>{units})\s*/\s*箱", normalized, re.I))
    common["qty"] = int(qty_matches[0]["value"]) if qty_matches else 0
    common["qty_unit"] = canonical_unit(qty_matches[0]["unit"]) if qty_matches else ""
    if qty_matches and re.match(r"\s*[-~～.,]\s*\d", normalized[qty_matches[0].end():]):
        issues.append("裝箱量含範圍或小數，請確認整數裝箱量")
    if len(qty_matches) > 1:
        issues.append("存在多個裝箱量，請拆開獨立報價")
    # Explicit units and scope. Bare KG does not establish a carton weight.
    weight_unit = r"kg|公斤|千克|g|公克|克"
    prefix = rf"(?:單個重量|每個重量|單件重量|每件重量|單重)\s*:?\s*(?:約)?\s*({number})\s*({weight_unit})"
    suffix = rf"重量\s*:?\s*(?:約)?\s*({number})\s*({weight_unit})\s*\(\s*(?:單個|每個|單件|每件)\s*\)"
    unit_matches = list(re.finditer(prefix, normalized, re.I)) + list(re.finditer(suffix, normalized, re.I))
    unit_values = [float(m[1]) * (1000 if m[2].lower() in ("kg", "公斤", "千克") else 1) for m in unit_matches]
    common["unit_weight_g"] = unit_values[0] if unit_values else 0.0
    carton_text = re.sub(prefix, "", normalized, flags=re.I)
    carton_text = re.sub(suffix, "", carton_text, flags=re.I)
    carton_matches = list(re.finditer(
        rf"(?:整箱毛重|整箱重量|箱重|毛重|⚖️?)\s*:?\s*(?:約)?\s*({number})\s*({weight_unit})",
        carton_text, re.I))
    carton_values = [float(m[1]) / (1 if m[2].lower() in ("kg", "公斤", "千克") else 1000) for m in carton_matches]
    pairs = list(re.finditer(rf"(?:整箱毛淨重|箱毛淨重|毛淨重)\s*:?\s*({number})\s*/\s*({number})\s*({weight_unit})", carton_text, re.I))
    carton_values += [max(float(m[1]), float(m[2])) / (1 if m[3].lower() in ("kg", "公斤", "千克") else 1000) for m in pairs]
    common["weight"] = carton_values[0] if carton_values else 0.0
    if len(set(unit_values)) > 1 or len(set(carton_values)) > 1:
        issues.append("存在互相衝突的重量，請核對來源後只保留正確值")
    if re.search(r"木架|木框|另加|另計|不含運|運費另|附加費|(?:運費|打包費|包裝費|加工費)\s*:?\s*\d", normalized):
        issues.append("有木架或額外費用，須確認是否已包含重量及費用")
    codes = list(re.finditer(r"(?:型號|貨號|產品編號|編號)\s*:?\s*([A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)*)", normalized))
    if len(codes) > 1 or len(products) > 1:
        issues.append("偵測到多款商品；目前請每款分別貼上解析，避免共用錯誤參數")
    if codes and len(codes) == 1:
        products = [{"code": normalize_code(codes[0][1]), "name": products[0]["name"] if products else ""}]
    size = rf"({number}(?:\s*[-~～]\s*{number})?(?:\s*[*xX×]\s*{number})*\s*(?:cm|mm|公分|毫米)?)"
    fields = {
        "outer_box_size": r"^(?:外箱規格|外箱尺寸|外箱)\s*:?\s*",
        "color_box_size": r"^(?:彩盒尺寸|彩盒|單個包裝尺寸|單個包裝|包裝盒尺寸|包裝盒|包裝尺寸|亞克力)\s*:?\s*",
        "prod_size": r"^(?:產品尺寸|單個尺寸|尺寸|產品)\s*:?\s*",
    }
    for field, label in fields.items():
        common[field] = ""
        for line in normalized.splitlines():
            cleaned = re.sub(EMOJI_PAT, "", line).strip()
            match = re.search(label + size + r"\s*$", cleaned, re.I)
            if match:
                common[field] = match[1].strip()
                break
    # Named metadata must not be swallowed by the product name.
    meta = r"^(?:型號|貨號|產品編號|編號|單價|單個價格|單盒價格|價格|每箱|箱數|裝箱|一箱|重量|單重|單個重量|每個重量|整箱|毛重|箱重|尺寸|產品尺寸|產品\s*:|彩盒|外箱|包裝|單個包裝|材質|材積|端盒|木架|木框|帶鐳|帶雷|USB|配件|電池|\d+\s*(?:個|款|種))"
    if len(products) == 1 and codes:
        name_lines = []
        for line in normalized.splitlines():
            line = re.sub(EMOJI_PAT, "", line).strip()
            if re.match(meta, line, re.I):
                continue
            line = re.sub(r"^(?:新品\s*#?\s*)?(?:正版授權)\s*$", "", line)
            if line:
                name_lines.append(line)
        products[0]["name"] = " ".join(name_lines[:3])
    # Keep supplemental text verbatim (normalized), including units/approximation.
    notes = []
    for line in normalized.splitlines():
        line = line.strip()
        if re.search(r"帶[鐳雷]射|正版授權|材質|顏色|圖案|端盒|木架|木框|包裝|USB|充電|約.*(?:kg|公斤|克)|另加|另計|運費|打包費|加工費|待定|待確認", line, re.I):
            notes.append(line)
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


def duplicate_messages(incoming, sheets):
    messages = []
    existing = [(title, p) for title, rows in sheets.items() for p in extract_saved_products(rows)]
    seen = []
    for item in incoming:
        code, name = normalize_code(item["code"]), normalize_name(item["name"])
        for title, saved in existing + [("本批次", p) for p in seen]:
            same_code = bool(code and code == saved["code"])
            same_name = bool(name and name == saved["name"])
            if same_code or same_name:
                kind = "同貨號不同品名衝突" if same_code and not same_name else "重複或同品名待核對"
                messages.append(f"{kind}：{code} {name} → {title} {saved.get('no', '')} {saved['name']}；停止新增，不自動覆蓋或跳過")
        seen.append({"code": code, "name": name})
    return messages


# --- 5. 主畫面流程 ---
user_input = st.text_area("📝 第一步:每次貼上一款廠商完整文案（含補充費用）", height=200)
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
final_price = c1.number_input("進價(RMB)", value=common_data["price"], format="%.2f")
final_qty = c2.number_input("裝箱量", value=common_data["qty"], step=1)
final_unit_weight_g = c3.number_input(
    "單個重量(g)",
    value=common_data["unit_weight_g"],
    format="%.2f",
)
final_carton_weight_kg = c4.number_input(
    "整箱毛重(kg)",
    value=common_data["weight"],
    format="%.2f",
)
final_dom = c5.number_input("內陸運費(R/kg)", value=dom_rate_def)
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
final_prod_size = c6.text_input("產品尺寸 (沒抓到可手動輸入)", value=common_data["prod_size"])
final_color_size = c7.text_input("彩盒尺寸 (亞克力/單個包裝也算)", value=common_data["color_box_size"])
final_outer_size = st.text_input("外箱尺寸 (沒抓到可手動輸入)", value=common_data["outer_box_size"])
final_extra = st.text_area("額外備註（保留顏色、材質、端盒、木架等）", value=common_data["extra_tags"])
unit_options = ["", "個", "盒", "套", "瓶", "罐", "包", "袋"]
parsed_unit = common_data["qty_unit"]
final_qty_unit = st.selectbox("装箱及計價單位（必須一致；不同時先人工換算）", unit_options,
                                index=unit_options.index(parsed_unit) if parsed_unit in unit_options else 0)
active_issues = list(common_data["issues"])
if any("木架或額外費用" in issue for issue in active_issues):
    st.warning("木架等附加項目未確認前不提供成本。若費用另加，先將每銷售單位進價及整箱重量校正為含附加項目的數值。")
    confirmation_key = hashlib.sha256(repr((user_input, final_price, final_qty, final_unit_weight_g, final_carton_weight_kg)).encode()).hexdigest()
    extra_basis = st.text_input("供應商確認依據／費用與重量換算說明", key="basis_" + confirmation_key)
    if st.checkbox("已向來源確認：上述進價、重量已涵蓋全部附加費用，沒有未確認項目", key="extra_" + confirmation_key) and extra_basis.strip():
        active_issues = [issue for issue in active_issues if "木架或額外費用" not in issue]
        final_extra += "\n附加費用確認：" + extra_basis.strip()
block_reasons = cost_blockers(final_price, final_qty, final_carton_weight_kg, final_unit_weight_g,
                             final_dom, intl_rate, ex_rate, active_issues, common_data["price_unit"], final_qty_unit)
if block_reasons:
    st.error("待確認：" + "；".join(block_reasons) + "。重量、運費、成本、報價全部留白；本次禁止存檔。")
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
edited_df = st.data_editor(df_items, num_rows="dynamic", width="stretch")
if final_qty > 0:
    st.markdown("---")
    st.subheader("📊 第三步:選擇分頁與逐款存入")
    category_col, vendor_col = st.columns([1, 1])
    with category_col:
        final_category = st.selectbox(
            "📂 確定存入的分頁:",
            ["G正版", "W玩具", "S生活用品", "W娃娃", "D吊飾"],
            index=0,
        )
    with vendor_col:
        final_vendor = st.selectbox(
            "🏷️ 廠商:",
            ["v菲凡", "v多品村", "v優娜卡樂星"],
            index=0,
        )
    if is_free_shipping_vendor(final_vendor):
        st.success("✅ 此廠商廣州包郵")
    to_save_df = edited_df[(edited_df["寫入"] == True) & ((edited_df["貨號"] != "") | (edited_df["名稱"] != ""))]
    all_sheets_data = get_all_sheets_data()
    if all_sheets_data is None:
        st.error("雲表讀取失敗，停止存檔；不可把讀取失敗當成空表。")
        st.stop()
    duplicate_warnings = duplicate_messages(
        [{"code": row["貨號"], "name": row["名稱"]} for _, row in to_save_df.iterrows()], all_sheets_data)
    if final_category not in all_sheets_data:
        block_reasons.append("找不到指定分頁，禁止自動新建分頁")
        st.error("找不到指定分頁，已停止存檔；請核對分頁名稱。")
    if duplicate_warnings:
        for warn in duplicate_warnings:
            st.error(f"🚨 **撞單雷達警告**:{warn}")
    if not to_save_df.empty:
        review_key = hashlib.sha256(repr((user_input, to_save_df.to_dict(), final_price, final_qty,
            final_qty_unit, final_carton_weight_kg, final_unit_weight_g, final_dom, intl_rate, ex_rate,
            final_category, final_vendor, final_prod_size, final_color_size, final_outer_size, final_extra)).encode()).hexdigest()
        final_confirm = st.checkbox(f"我已逐欄對照原文、補充資訊及廠商，確認寫入共 {len(to_save_df)} 款商品", key="review_" + review_key)
        invalid_names = any(not normalize_name(row["名稱"]) for _, row in to_save_df.iterrows())
        if invalid_names or len(to_save_df) != 1:
            st.error("請每次選擇一款有完整品名的商品存檔。")
        if st.button("💾 執行存檔", type="primary", disabled=bool(not final_confirm or block_reasons or duplicate_warnings or invalid_names or len(to_save_df) != 1)):
            target_data = all_sheets_data.get(final_category, [])
            true_last_row = len(target_data)
            max_no = 0
            for r in target_data:
                if r and r[0]:
                    m = re.search(r'no(\d+)', str(r[0]), re.IGNORECASE)
                    if m:
                        max_no = max(max_no, int(m.group(1)))
            st_r = true_last_row + 2 if true_last_row > 0 else 1
            bulk_rows = []
            info_lines = []
            info_lines.append(f"計價單位：{final_qty_unit}")
            if final_prod_size:
                info_lines.append(f"尺寸 {final_prod_size}")
            if final_color_size:
                info_lines.append(f"彩盒尺寸 {final_color_size}")
            if final_outer_size:
                info_lines.append(f"外箱尺寸 {final_outer_size}")
            if final_extra:
                info_lines.append(final_extra)
            info_display = "\n".join(info_lines) if info_lines else "尺寸 (未提供)"
            today = datetime.datetime.now(ZoneInfo("Asia/Taipei"))
            today_str = f"{today.year}/{today.month}/{today.day}"
            empty_row = [""] * 12
            for idx, row in to_save_df.iterrows():
                max_no += 1
                next_no = f"no{max_no}"
                v_r = st_r + len(bulk_rows) + 1
                formulas = build_cost_formulas(
                    v_r,
                    final_carton_weight_kg,
                    final_unit_weight_g,
                    final_qty,
                    final_dom,
                    intl_rate,
                    ex_rate,
                    final_vendor,
                    final_price=final_price,
                    blocked=bool(block_reasons),
                )
                if final_carton_weight_kg > 0 and final_unit_weight_g > 0:
                    weight_note = (
                        f"整箱毛重 {final_carton_weight_kg:g}KG／"
                        f"單個重量 {final_unit_weight_g:g}g"
                    )
                elif final_carton_weight_kg > 0:
                    weight_note = f"整箱毛重 {final_carton_weight_kg:g}KG"
                elif final_unit_weight_g > 0:
                    weight_note = f"單個重量 {final_unit_weight_g:g}g"
                else:
                    weight_note = "重量 未提供"
                block = [
                    [next_no, str(row['名稱']).strip(), "10%報價", "13%報價", "15%報價", "20%報價", "進價rmb", f"重量g/{final_qty_unit}", "大陸運費rmb", "國際運費", "預估到手成本", final_vendor],
                    [
                        today_str,
                        info_display,
                        formulas["quote_10"],
                        formulas["quote_13"],
                        formulas["quote_15"],
                        formulas["quote_20"],
                        final_price,
                        formulas["weight"],
                        formulas["domestic"],
                        formulas["international"],
                        formulas["cost"],
                        "",
                    ],
                    build_carton_note_row(final_qty, final_vendor, final_qty_unit),
                    ["", weight_note] + [""] * 10,
                    ["", f"貨號 {normalize_code(row['貨號'])}"] + [""] * 10,
                    empty_row
                ]
                bulk_rows.extend(block)
            if save_bulk_to_worksheet(final_category, bulk_rows, st_r, block_size=6, expected_rows=target_data):
                get_all_sheets_data.clear()
                st.success(
                    f"✅ 寫入並核對成功！已將 {len(to_save_df)} 款商品存入【{final_category}】，"
                    f"廠商【{final_vendor}】!"
                )
