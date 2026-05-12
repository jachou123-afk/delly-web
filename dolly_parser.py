import streamlit as st
import pandas as pd
import re
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import zhconv
import datetime

# --- 1. 網頁基本設定 ---
st.set_page_config(page_title="半自動 - 採購報價彙整表", layout="wide")
st.title("📱 朵麗星球 - 透明資料庫 V48")
st.info("✅ 規格：寫入【透明 Google 公式】、詳細欄位拆解、單行資料庫格式、全局防撞。")

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
        all_data = {ws.title: ws.get_all_values() for ws in spreadsheet.worksheets()}
        return all_data
    except:
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
        # 💡 V48 標題欄位擴充：拆解運費以便顯示公式
        if len(existing_data) == 0:
            headers = ["編號", "日期", "分類", "貨號", "名稱", "規格包裝", "裝箱量(G)", "毛重KG(H)", "進價RMB(I)", "單個重量g(J)", "大陸運費(K)", "國際運費(L)", "預估成本(M)", "10%報價(N)", "商品圖片"]
            sheet.append_row(headers, value_input_option="USER_ENTERED")
            try:
                sheet.format("A1:O1", {"backgroundColor": {"red": 0.9, "green": 0.9, "blue": 1.0}, "textFormat": {"bold": True}})
            except: pass
                
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

# --- 4. 解析引擎 (V48) ---
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

    m_price = re.search(r'(?:單價|单价|價格|价格|價錢)\s*:?\s*(?:rmb|RMB|¥)?\s*([0-9.]+)', text_norm)
    if not m_price: m_price = re.search(r'(\d+(?:\.\d+)?)\s*元', text_norm)
    if m_price: data["price"] = float(m_price.group(1))

    m_qty = re.search(r'(?:裝箱|每箱數量|裝箱數|箱數|數量|裝箱量)\s*:?\s*(\d+)', text_norm)
    if m_qty: data["qty"] = int(m_qty.group(1))

    m_weight = re.search(r'(?:毛重|整箱重量|箱重)\s*:?\s*([0-9.]+)', text_norm)
    if not m_weight: m_weight = re.search(r'([0-9.]+)\s*[Kk][Gg]', text_norm)
    if m_weight: data["weight"] = float(m_weight.group(1)) 

    # 尺寸解析
    m_color = re.search(r'彩盒尺寸\s*:?\s*([0-9.*xX×\s-]+(?:[cC][mM]|公分)?)', text_norm)
    data["color_box_size"] = m_color.group(1).strip() if m_color else ""
    m_prod = re.search(r'(?<!(?:彩盒|外箱))(?:尺寸|產品|產品)\s*:?\s*([0-9.*xX×\s-]+(?:[cC][mM]|公分)?)', text_norm)
    data["prod_size"] = m_prod.group(1).strip() if m_prod else ""
    
    extra_items = []
    if re.search(r'帶[鐳雷]射標', text_norm): extra_items.append("帶雷射標")
    m_pkg = re.search(r'包裝\s*:?\s*([^\n,，]+)', text_norm)
    if m_pkg: extra_items.append(f"包裝:{m_pkg.group(1).strip()}")
    data["extra_tags"] = "\n".join(extra_items)

    segments = re.split(r'[\n,，]+', text_norm)
    name_segments = []
    exclusion = r'^(?:型號|貨號|產品|條碼|數量|裝箱|箱數|價格|單價|重量|尺寸|彩盒|規格|包裝|毛重|外箱|體積|材積|運費|控價|台幣)'
    for seg in segments:
        seg = re.sub(r'[📦💰✅🔥✨🎈🍦🔫]', '', seg).strip()
        if len(seg) < 2 or re.match(exclusion, seg): continue
        name_segments.append(seg)
        
    if name_segments: data["name"] = " ".join(name_segments[:2]).strip()
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
        for s_title, s_rows in all_sheets_data.items():
            for row in s_rows:
                if len(row) > 4 and ((final_code and final_code in row[3]) or (final_name and final_name == row[4])):
                    duplicate_no = f"{row[0]} (位於 {s_title})"
                    break

    if duplicate_no: st.error(f"🚨 **防撞單雷達警告**：商品已建過！編號：{duplicate_no}")

    final_confirm = st.checkbox(f"我已確認【{final_name}】資料正確無誤")
    
    if st.button("執行存檔", type="primary", disabled=not final_confirm):
        target_ws_data = all_sheets_data.get(final_category, [])
        max_no = 0
        for r in target_ws_data:
            if r and len(r) > 0:
                m = re.search(r'no(\d+)', str(r[0]), re.IGNORECASE)
                if m: max_no = max(max_no, int(m.group(1)))
        next_no = f"no{max_no + 1}"
        
        # 💡 V48 重點：產出 Google Sheets 公式字串
        # 假設資料會寫入到 next_row (資料筆數+1)
        r_idx = len(target_ws_data) + 1 if len(target_ws_data) > 0 else 2
        
        # 單個重量g (J欄) = (H欄毛重 / G欄裝箱量) * 1000 * 1.03
        f_weight = f"=ROUNDUP((H{r_idx}/G{r_idx})*1000*1.03, 2)"
        # 大陸運費 (K欄) = (J欄重量 / 1000) * 參數
        f_dom_cost = f"=ROUNDUP((J{r_idx}/1000)*{final_dom}, 2)"
        # 國際運費 (L欄) = (J欄重量 / 1000) * 參數
        f_intl_cost = f"=ROUNDUP((J{r_idx}/1000)*{intl_rate}, 2)"
        # 預估成本 (M欄) = (I進價 + K大陸運費 + L國際運費) * 匯率
        f_total_cost = f"=ROUND((I{r_idx}+K{r_idx}+L{r_idx})*{ex_rate}, 1)"
        # 10%報價 (N欄) = M成本 / 0.9
        f_quote = f"=ROUND(M{r_idx}/0.9, 1)"
        
        info_lines = []
        if p["prod_size"]: info_lines.append(f"尺寸 {p['prod_size']}")
        if p["color_box_size"]: info_lines.append(f"彩盒尺寸 {p['color_box_size']}")
        if p["extra_tags"]: info_lines.append(p["extra_tags"])
        info_display = "\n".join(info_lines) if info_lines else "尺寸 (未提供)"
        
        row_data = [
            next_no,                       # A 編號
            datetime.datetime.now().strftime("%Y/%-m/%-d"), # B 日期
            final_category,                # C 分類
            final_code,                    # D 貨號
            final_name,                    # E 名稱
            info_display,                  # F 規格包裝
            final_qty,                     # G 裝箱量
            final_weight,                  # H 毛重KG
            final_price,                   # I 進價RMB
            f_weight,                      # J 公式:單個重量g
            f_dom_cost,                    # K 公式:大陸運費
            f_intl_cost,                   # L 公式:國際運費
            f_total_cost,                  # M 公式:預估成本
            f_quote,                       # N 公式:10%報價
            ""                             # O 圖片(留空)
        ]
        
        if save_to_worksheet(final_category, row_data):
            get_all_sheets_data.clear()
            st.success(f"✅ 已存入【{final_category}】。您可以去表格檢查公式了！")
