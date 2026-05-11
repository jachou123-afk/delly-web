import streamlit as st
import pandas as pd
import re
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- 1. 網頁基本設定 ---
st.set_page_config(page_title="朵麗星球 - 採購雲端同步系統", layout="wide")
st.title("🪐 朵麗星球 - 採購報價彙整系統 V14")
st.info("✅ 規格：自動編號、活公式(進位至.1)、貨號最底、精準偵測末端空一行。")

# --- 2. Google Sheets 連線功能 ---
SHEET_NAME = "朵麗星球 - 採購報價彙整表"

def get_sheet():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        
        # 自動偵測：優先使用 Streamlit Cloud Secrets，若無則抓取本機 JSON
        if st.secrets.get("gcp_service_account"):
            creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), scope)
        else:
            # 這裡請確認檔案名稱是否正確
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
dom_rate_def = st.sidebar.number_input("內陸運費 (RMB/kg)", value=1.0, step=0.5)

# --- 4. 解析引擎 ---
def parse_text(text):
    data = {"code": "", "name": "", "price": 0.0, "qty": 0, "weight": 0.0, "size": ""}
    if not text: return data
    m_code = re.search(r'([A-Z0-9]{4,})', text.strip())
    name_raw = text
    if m_code:
        data["code"] = m_code.group(1)
        name_raw = text[m_code.end():] 
    m_name = re.search(r'^[，\s,]*([^，,\n\r是\[价]+)', name_raw)
    if m_name:
        nc = m_name.group(1).strip()
        for w in ["尺寸", "装箱", "裝箱", "KG", "kg", "重量", "价格"]:
            if w in nc: nc = nc.split(w)[0].strip()
        data["name"] = nc
    m_price = re.search(r'(\d+(?:\.\d+)?)\s*元', text)
    if not m_price: m_price = re.search(r'是\s*(?:\[.*?\])?\s*(\d+(?:\.\d+)?)', text)
    if m_price: data["price"] = float(m_price.group(1))
    m_qty = re.search(r'(?:装箱|裝箱|一箱)\s*(\d+)', text)
    if m_qty: data["qty"] = int(m_qty.group(1))
    m_weight = re.search(r'(\d+(?:\.\d+)?)\s*[Kk][Gg]', text)
    if m_weight: data["weight"] = float(m_weight.group(1))
    m_size = re.search(r'尺寸\s*([0-9.*xX\s]+(?:CM|cm|公分)?)', text, re.IGNORECASE)
    if m_size: data["size"] = m_size.group(1).strip()
    return data

# --- 5. 主畫面流程 ---
user_input = st.text_area("📝 第一步：貼上廠商微信文案", height=100)
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
                # 取得整張表所有資料以精準定位「最底部」
                all_data = sheet.get_all_values()
                true_last_row = len(all_data)
                
                # 計算 no 編號
                max_no = 0
                for r in all_data:
                    if r and r[0]:
                        m = re.search(r'no(\d+)', str(r[0]), re.IGNORECASE)
                        if m: max_no = max(max_no, int(m.group(1)))
                next_no = f"no{max_no + 1}"
                
                # 計算寫入起始行：真正最後一行 + 2 (空一格)
                st_r = true_last_row + 2 if true_last_row > 0 else 1
                v_r = st_r + 1 # 公式所在行
                
                # 建立公式 (ROUND 到小數第一位)
                f10, f13, f15, f20 = f"=ROUND(K{v_r}/0.9,1)", f"=ROUND(K{v_r}/0.87,1)", f"=ROUND(K{v_r}/0.85,1)", f"=ROUND(K{v_r}/0.8,1)"
                f_dom = f"=ROUND((H{v_r}/1000)*{dom_rate},1)"
                f_intl = f"=ROUND((H{v_r}/1000)*{intl_rate},1)"
                f_cost = f"=ROUND((G{v_r}+I{v_r}+J{v_r})*{ex_rate},1)"
                
                # 組裝 5 行格式
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