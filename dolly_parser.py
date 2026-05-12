import streamlit as st
import pandas as pd
import re
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import zhconv
import datetime

# --- 1. 網頁基本設定 ---
st.set_page_config(page_title="半自動 - 採購報價彙整表", layout="wide")
st.title("📱 朵麗星球 - App 專用資料庫 V47")
st.info("✅ 規格：修復【Response 200 寫入衝突】、採用最高相容 Append 模式、單行資料庫格式。")

# --- 2. Google Sheets 連線功能 ---
SHEET_NAME = "朵麗星球_App測試庫"

@st.cache_data(ttl=15)
def get_all_sheets_data():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        if st.secrets.get("gcp_service_account"):
            creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), scope)
        else:
            creds = ServiceAccountCredentials.from_json_keyfile_name("giraffe-495919-b7d55659973d.json", scope)
        client = gspread.authorize(creds)
        spreadsheet = client.open(SHEET_NAME)
        
        all_data = {}
        for ws in spreadsheet.worksheets():
            all_data[ws.title] = ws.get_all_values()
        return all_data
    except Exception as e:
        return {}

def save_to_worksheet(category_name, row_data):
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        if st.secrets.get("gcp_service_account"):
            creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), scope)
        else:
            creds = ServiceAccountCredentials.from_json_keyfile_name("giraffe-495919-b7d55659973d.json", scope)
        client = gspread.authorize(creds)
        spreadsheet = client.open(SHEET_NAME)
        
        try:
            sheet = spreadsheet.worksheet(category_name)
        except gspread.exceptions.WorksheetNotFound:
            sheet = spreadsheet.add_worksheet(title=category_name, rows="1000", cols="20")
            
        existing_data = sheet.get_all_values()
        
        # 💡 如果是全新的分頁，自動寫入 App 專用的標題列 (改用最穩定的 append_row)
        if len(existing_data) == 0:
            headers = ["編號", "日期", "分類", "貨號", "名稱", "規格與包裝", "裝箱量", "毛重KG", "進價RMB", "成本NTD", "10%報價", "商品圖片"]
            sheet.append_row(headers, value_input_option="USER_ENTERED")
            try:
                sheet.format("A1:L1", {"backgroundColor": {"red": 0.8, "green": 0.9, "blue": 1.0}, "textFormat": {"bold": True}})
            except:
                pass # 就算格式化遇到舊版本問題，也不會中斷存檔
                
        # 💡 安全寫入資料 (自動尋找最下面的一行新增，無視套件版本差異)
        sheet.append_row(row_data, value_input_option="USER_ENTERED")
        return True
    except Exception as e:
        st.error(f"寫入雲端失敗：{repr(e)}")
        return False

# --- 3. 側邊欄設定 ---
st.sidebar.header("⚙️ 成本參數設定")
ex_rate = st.sidebar.number_input("匯率", value=4.7, step=0.1)
intl_rate = st.sidebar.number_input("國際運費 (RMB/kg)", value=8.5, step=0.5)
dom_rate_def = st.sidebar.number_input("內陸運費 (RMB/kg)", value=1.5, step=0.5)

# --- 4. 解析引擎 ---
def parse_text(text):
    data = {"code": "", "name": "", "price": 0.0, "qty": 0, "weight": 0.0, "prod_size": "", "color_box_size": "", "outer_box_size": "", "extra_tags": ""}
    if not text: return data
    text_norm = text.replace('：', ':')
    
    m_code = re.search(r'(?:型號|型号|貨號|货号|產品編號|产品编号)\s*:?\s*([A-Za-z0-9-/]+)', text_norm)
    if m_code: data["code"] = m_code.group(1)
    else:
        candidates = re.findall(r'([A-Za-z0-9-/]{4,})', text_norm)
        for cand in candidates:
            if not re.match(r'^\d+(?:\.\d+)?(?:pcs|kg|g|cm|mm|rmb|m³)$', cand, re.IGNORECASE):
                data["code"] = cand
                break

    text_for_price = re.sub(r'(?:控價|控价|售价|售價|台幣|臺幣).*?(?:\n|$)', '', text_norm)
    m_price = re.search(r'(?:單價|单价|價格|价格|價錢)\s*:?\s*(?:rmb|RMB|¥)?\s*([0-9.]+)', text_for_price)
    if not m_price: m_price = re.search(r'(\d+(?:\.\d+)?)\s*元', text_for_price)
    if m_price: data["price"] = float(m_price.group(1))

    m_qty = re.search(r'(?:每箱數量|每箱數量|裝箱數|裝箱數|箱數|箱數|數量|數量|裝箱量|裝箱量)\s*:?\s*(\d+)', text_norm)
    if not m_qty: m_qty = re.search(r'(?:裝箱|一箱)\s*(\d+)', text_norm)
    if m_qty: data["qty"] = int(m_qty.group(1))

    m_total_weight = re.search(r'(?:毛重|整箱重量|箱重)\s*:?\s*([0-9.]+)', text_norm)
    if not m_total_weight: m_total_weight = re.search(r'([0-9.]+)\s*[Kk][Gg]', text_norm)
    if m_total_weight: data["weight"] = float(m_total_weight.group(1)) 

    m_color = re.search(r'彩盒尺寸\s*:?\s*([0-9.*xX×\s-]+(?:[cC][mM]|公分)?)', text_norm)
    if m_color: data["color_box_size"] = m_color.group(1).strip()
    m_outer = re.search(r'(?:外箱規格|外箱尺寸|外箱)\s*:?\s*([0-9.*xX×\s-]+(?:[cC][mM]|公分)?)', text_norm)
    if m_outer: data["outer_box_size"] = m_outer.group(1).strip()
    m_prod = re.search(r'(?<!(?:彩盒|外箱))(?:尺寸|產品|產品)\s*:?\s*([0-9.*xX×\s-]+(?:[cC][mM]|公分)?)', text_norm)
    if not m_prod: m_prod = re.search(r'帽圍\s*:?\s*([0-9.*xX×\s-]+(?:[cC][mM]|公分)?)', text_norm)
    if m_prod: data["prod_size"] = m_prod.group(1).strip()

    extra_items = []
    if re.search(r'帶[鐳雷]射標', text_norm): extra_items.append("帶雷射標")
    m_pkg = re.search(r'(?:包裝|包裝)\s*:?\s*([^\n,，]+)', text_norm)
    if m_pkg: extra_items.append(f"包裝:{m_pkg.group(1).strip()}")
    data["extra_tags"] = "\n".join(extra_items)

    segments = re.split(r'[\n,，]+', text_norm)
    name_segments = []
    exclusion_pattern = r'^(?:型號|型号|貨號|货号|產品|产品|條碼|条码|數量|数量|裝箱|装箱|箱數|箱数|一箱|價格|价格|單價|单价|重量|箱重|尺寸|彩盒|規格|规格|帽圍|帽围|包裝|包装|毛重|外箱|體積|体积|材積|材积|運費|运费|海快|控價|控价|售價|售价|台幣|臺幣)'
    
    for seg in segments:
        seg = seg.strip()
        seg = re.sub(r'[📦💰✅🔥✨🎈🍦🔫]', '', seg).strip()
        seg = re.sub(r'\[.*?\]', '', seg)
        seg = re.sub(r'是?\s*[0-9.]+\s*元', '', seg)
        if len(seg) < 2: continue 
        if re.match(exclusion_pattern, seg): continue
        if re.match(r'^[A-Za-z0-9-\s/]+$', seg) or re.match(r'^[0-9.]+\s*[Kk][Gg克]$', seg): continue
        name_segments.append(seg)
        
    if name_segments: 
        raw_name = " ".join(name_segments[:2]).strip()
        if data["code"] and data["code"] in raw_name:
            raw_name = raw_name.replace(data["code"], "").strip()
        data["name"] = raw_name
        
    return data

# --- 5. 主畫面流程 ---
user_input = st.text_area("📝 第一步：貼上廠商微信文案", height=150)
user_input_tw = zhconv.convert(user_input, 'zh-tw') if user_input else ""
p = parse_text(user_input_tw)

st.subheader("🔍 第二步：數據校正")
c1, c2, c3, c4, c5, c6 = st.columns(6)
final_code = c1.text_input("貨號", value=p["code"])
final_name = c2.text_input("名稱", value=p["name"])
final_price = c3.number_input("進價(RMB)", value=p["price"], format="%.2f")
final_qty = c4.number_input("裝箱量", value=p["qty"], step=1)
final_weight = c5.number_input("毛重(kg)", value=p["weight"], format="%.2f")
final_dom = c6.number_input("內陸運費(R/kg)", value=dom_rate_def)

if final_qty > 0:
    st.markdown("---")
    st.subheader("📊 第三步：存入 App 專用資料庫")
    final_category = st.selectbox("📂 分類：", ["正版", "玩具", "生活用品", "娃娃", "吊飾"], index=0)
    
    all_sheets_data = get_all_sheets_data()
    duplicate_no = None

    if all_sheets_data and (final_code or final_name):
        check_code = f"{final_code}".strip() if final_code and len(final_code) > 2 else None
        check_name = final_name.strip() if final_name and len(final_name) > 2 else None
        for sheet_title, sheet_rows in all_sheets_data.items():
            for i, row in enumerate(sheet_rows):
                if len(row) > 4:
                    row_code = str(row[3]).strip()
                    row_name = str(row[4]).strip()
                    if (check_code and check_code in row_code) or (check_name and check_name == row_name):
                        duplicate_no = str(row[0])
                        break
            if duplicate_no: break

    if duplicate_no:
        st.error(f"🚨 **防撞單雷達警告**：商品已經建檔過了！編號：{duplicate_no}。")

    final_confirm = st.checkbox(f"我已手動校對完成，確認寫入 App 資料庫")
    
    if st.button("執行存檔", type="primary", disabled=not final_confirm):
        target_data = all_sheets_data.get(final_category, [])
        
        max_no = 0
        for r in target_data:
            if r and len(r) > 0:
                m = re.search(r'no(\d+)', str(r[0]), re.IGNORECASE)
                if m: max_no = max(max_no, int(m.group(1)))
        next_no = f"no{max_no + 1}"
        
        f_single_weight = round((final_weight / final_qty) * 1000 * 1.03, 2)
        f_dom_cost = round((f_single_weight / 1000) * final_dom, 2)
        f_intl_cost = round((f_single_weight / 1000) * intl_rate, 2)
        f_cost = round((final_price + f_dom_cost + f_intl_cost) * ex_rate, 1)
        f10 = round(f_cost / 0.9, 1)
        
        info_lines = []
        if p["prod_size"]: info_lines.append(f"尺寸 {p['prod_size']}")
        if p["color_box_size"]: info_lines.append(f"彩盒尺寸 {p['color_box_size']}")
        if p["extra_tags"]: info_lines.append(p["extra_tags"])
        info_display = "\n".join(info_lines) if info_lines else "尺寸 (未提供)"
        today_str = datetime.datetime.now().strftime("%Y/%-m/%-d")
        
        row_data = [
            next_no,           # A: 編號
            today_str,         # B: 日期
            final_category,    # C: 分類
            final_code,        # D: 貨號
            final_name,        # E: 名稱
            info_display,      # F: 規格包裝
            final_qty,         # G: 裝箱量
            final_weight,      # H: 毛重KG
            final_price,       # I: 進價RMB
            f_cost,            # J: 成本NTD
            f10,               # K: 10%報價NTD
            ""                 # L: 留空給 AppSheet 傳圖片
        ]
        
        if save_to_worksheet(final_category, row_data):
            get_all_sheets_data.clear()
            st.success(f"✅ 已成功存入【{SHEET_NAME}】的 {final_category} 分頁！編號：{next_no}")
