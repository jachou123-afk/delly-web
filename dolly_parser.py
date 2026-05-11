import streamlit as st
import pandas as pd
import re
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- 1. 網頁基本設定 ---
st.set_page_config(page_title="朵麗星球 - 採購雲端同步系統", layout="wide")
st.title("🪐 朵麗星球 - 採購報價彙整系統 V16")
st.info("✅ 規格：雙引擎解析(支援條列式與段落式文案)、全繁體優化、活公式。")

# --- 2. Google Sheets 連線功能 ---
SHEET_NAME = "朵麗星球 - 採購報價彙整表"

def get_sheet():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        
        if st.secrets.get("gcp_service_account"):
            creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), scope)
        else:
            creds = ServiceAccountCredentials.from_json_keyfile_name("giraffe-495919-b7d55659973d.json", scope)
            
        client = gspread.authorize(creds)
        return client.open(SHEET_NAME).sheet1
    except Exception as e:
        st.error(f"連線錯誤: {e}")
        return None

# --- 3. 側邊欄設定 ---
st.sidebar.header("⚙️ 成本參數設定")
ex_rate = st.sidebar.number_input("匯率", value=4.7, step=0.1)
intl_rate = st.sidebar.number_input("國際運費 (RMB/kg)", value=8.5, step=0.5)
dom_rate_def = st.sidebar.number_input("內陸運費 (RMB/kg)", value=1.5, step=0.5)

# --- 4. 解析引擎 (V16 雙引擎版) ---
def parse_text(text):
    data = {"code": "", "name": "", "price": 0.0, "qty": 0, "weight": 0.0, "size": ""}
    if not text: return data
    
    # 統一將全形冒號轉為半形，方便程式辨識
    text_norm = text.replace('：', ':')
    lines = [line.strip() for line in text_norm.split('\n') if line.strip()]
    first_line = lines[0] if lines else ""

    # 1. 提取貨號與名稱 (支援放開頭的新格式)
    m_code_start = re.match(r'^([A-Za-z0-9]{4,})', first_line)
    if m_code_start:
        data["code"] = m_code_start.group(1)
        data["name"] = first_line[m_code_start.end():].strip()
    else:
        # 舊版搜尋邏輯
        m_code = re.search(r'([A-Za-z0-9]{4,})', text_norm)
        if m_code: data["code"] = m_code.group(1)
        
        name_raw = text_norm
        if data["code"]: name_raw = name_raw.replace(data["code"], "", 1)
        m_name = re.search(r'^[，\s,]*([^，,\n\r是\[價]+)', name_raw)
        if m_name:
            nc = m_name.group(1).strip()
            for w in ["尺寸", "裝箱", "KG", "kg", "重量", "價格", "單價", "毛重"]:
                if w in nc: nc = nc.split(w)[0].strip()
            data["name"] = nc

    # 2. 提取進價 (支援「單價：」標籤)
    m_price = re.search(r'(?:單價|價格|價錢)\s*:\s*([0-9.]+)', text_norm)
    if not m_price: m_price = re.search(r'(\d+(?:\.\d+)?)\s*元', text_norm)
    if not m_price: m_price = re.search(r'是\s*(?:\[.*?\])?\s*(\d+(?:\.\d+)?)', text_norm)
    if m_price: data["price"] = float(m_price.group(1))

    # 3. 提取裝箱量 (支援「裝箱量：」標籤)
    m_qty = re.search(r'裝箱(?:量)?\s*:\s*(\d+)', text_norm)
    if not m_qty: m_qty = re.search(r'(?:裝箱|一箱)\s*(\d+)', text_norm)
    if m_qty: data["qty"] = int(m_qty.group(1))

    # 4. 提取毛重 (支援「毛重：」標籤)
    m_weight = re.search(r'毛重\s*:\s*([0-9.]+)', text_norm)
    if not m_weight: m_weight = re.search(r'([0-9.]+)\s*[Kk][Gg]', text_norm)
    if m_weight: data["weight"] = float(m_weight.group(1))

    # 5. 提取尺寸 (支援乘號 × 和 x)
    m_size = re.search(r'尺寸\s*:?\s*([0-9.*xX×\s]+(?:[cC][mM]|公分)?)', text_norm)
    if m_size: data["size"] = m_size.group(1).strip()

    return data

# --- 5. 主畫面流程 ---
default_text = "L919A 庫洛米泡中泡電動泡泡槍🫧\n超萌庫洛米聯名泡泡槍顏值拉滿✨一鍵自動出泡 泡中泡\n💰單價：13.5元\n📦裝箱量：36pcs\n彩盒尺寸：16×8×16cm\n外箱規格：80×30×91cm\n外箱體積 / 材積：0.218cbm /7.71cuft\n毛重：15kg"
user_input = st.text_area("📝 第一步：貼上廠商微信文案", value=default_text, height=150)
p = parse_text(user_input)

st.subheader("🔍 第二步：確認數據")
c1, c2, c3, c4, c5, c6 = st.columns(6)
code = c1.text_input("貨號", value=p["code"])
name = c2.text_input("名稱", value=p["name"])
price = c3.number_input("進價(RMB)", value=p["price"], format="%.2f")
qty = c4.number_input("裝箱量", value=p["qty"], step=1)
weight = c5.number_input("毛重(kg)", value=p["weight"], format="%.2f")
dom_rate = c6.number_input("內陸運費(R/kg)", value=dom_rate_def)

if qty > 0:
    st.subheader("📊 第三步：儲存預覽")
    if st.button("💾 儲存並產出進位公式到雲端", type="primary"):
        sheet = get_sheet()
        if sheet:
            try:
                all_data = sheet.get_all_values()
                true_last_row = len(all_data)
                
                max_no = 0
                for r in all_data:
                    if r and r[0]:
                        m = re.search(r'no(\d+)', str(r[0]), re.IGNORECASE)
                        if m: max_no = max(max_no, int(m.group(1)))
                next_no = f"no{max_no + 1}"
                
                st_r = true_last_row + 2 if true_last_row > 0 else 1
                v_r = st_r + 1
                
                f10, f13, f15, f20 = f"=ROUND(K{v_r}/0.9,1)", f"=ROUND(K{v_r}/0.87,1)", f"=ROUND(K{v_r}/0.85,1)", f"=ROUND(K{v_r}/0.8,1)"
                f_dom = f"=ROUND((H{v_r}/1000)*{dom_rate},1)"
                f_intl = f"=ROUND((H{v_r}/1000)*{intl_rate},1)"
                f_cost = f"=ROUND((G{v_r}+I{v_r}+J{v_r})*{ex_rate},1)"
                
                rows = [
                    [next_no, name, "10%報價", "13%報價", "15%報價", "20%報價", "進價rmb", "重量g/pcs", "大陸運費rmb", "國際運費", "預估到手成本"],
                    ["", f"尺寸 {p['size']}", f10, f13, f15, f20, price, round((weight/qty)*1000, 0), f_dom, f_intl, f_cost],
                    ["", f"裝箱 {qty}個/箱", "", "", "", "", "", "", "", "", ""],
                    ["", f"毛重 {weight}KG", "", "", "", "", "", "", "", "", ""],
                    ["", f"貨號 {code}", "", "", "", "", "", "", "", "", ""]
                ]
                
                sheet.update(f"A{st_r}:K{st_r+4}", rows, value_input_option="USER_ENTERED")
                sheet.format(f"C{st_r}:F{st_r}", {"backgroundColor": {"red": 1.0, "green": 0.95, "blue": 0.8}})
                sheet.format(f"G{st_r}:K{st_r}", {"backgroundColor": {"red": 0.92, "green": 0.96, "blue": 1.0}})

                st.success(f"✅ 儲存成功！編號【{next_no}】。")
            except Exception as e:
                st.error(f"儲存失敗：{e}")
