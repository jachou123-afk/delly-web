import streamlit as st
import pandas as pd
import re
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import zhconv

# --- 1. 網頁基本設定 ---
# 💡 網頁標籤和標題也一起幫您改成新名字了！
st.set_page_config(page_title="半自動 - 採購報價彙整表", layout="wide")
st.title("🪐 半自動 - 採購報價彙整表 V23")
st.info("✅ 規格：已同步最新雲端表格名稱、防呆過濾陷阱、自動簡轉繁。")

# --- 2. Google Sheets 連線功能 ---
# 💡 這裡是系統找檔案的關鍵！已經幫您換成新名字了
SHEET_NAME = "半自動 - 採購報價彙整表"

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

# --- 4. 解析引擎 (V23) ---
def parse_text(text):
    data = {"code": "", "name": "", "price": 0.0, "qty": 0, "weight": 0.0, "size": ""}
    if not text: return data
    
    text_norm = text.replace('：', ':')
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    
    m_code = re.search(r'(?:型號|型号|貨號|货号|產品編號|产品编号)\s*:?\s*([A-Za-z0-9-]+)', text_norm)
    if m_code:
        data["code"] = m_code.group(1)
    else:
        m_code_start = re.match(r'^([A-Za-z0-9]{4,})', lines[0] if lines else "")
        if m_code_start: data["code"] = m_code_start.group(1)
        else:
            m_code_fallback = re.search(r'([A-Za-z0-9]{4,})', text_norm)
            if m_code_fallback: data["code"] = m_code_fallback.group(1)

    text_for_price = re.sub(r'(?:控價|控价|售价|售價|台幣|臺幣).*?(?:\n|$)', '', text_norm)
    
    m_price = re.search(r'(?:單價|单价|價格|价格|價錢)\s*:?\s*(?:rmb|RMB|¥)?\s*([0-9.]+)', text_for_price)
    if not m_price: m_price = re.search(r'(\d+(?:\.\d+)?)\s*元', text_for_price)
    if m_price: data["price"] = float(m_price.group(1))

    m_qty = re.search(r'(?:每箱數量|每箱数量|裝箱數|装箱数|數量|数量|裝箱量|装箱量)\s*:?\s*(\d+)', text_norm)
    if not m_qty: m_qty = re.search(r'(?:裝箱|一箱)\s*(\d+)', text_norm)
    if m_qty: data["qty"] = int(m_qty.group(1))

    m_total_weight = re.search(r'(?:毛重|整箱重量)\s*:?\s*([0-9.]+)', text_norm)
    if not m_total_weight: m_total_weight = re.search(r'([0-9.]+)\s*[Kk][Gg]', text_norm)
    
    m_single_weight = re.search(r'(?:單個重量|单个重量|克重)\s*:?\s*([0-9.]+)\s*[Gg克]', text_norm)
    
    if m_total_weight and float(m_total_weight.group(1)) > 0:
        data["weight"] = float(m_total_weight.group(1)) 
    elif m_single_weight and data["qty"] > 0:
        single_g = float(m_single_weight.group(1))
        data["weight"] = (single_g * data["qty"]) / 1000.0 

    m_size = re.search(r'(?:尺寸|帽圍|帽围)\s*:?\s*([0-9.*xX×\s-]+(?:[cC][mM]|公分)?)', text_norm)
    if not m_size: m_size = re.search(r'尺寸\s*:?\s*([0-9.*xX×\s]+(?:[cC][mM]|公分)?)', text_norm)
    if m_size: data["size"] = m_size.group(1).strip()

    name_lines = []
    for line in lines:
        if re.search(r'(?:型號|型号|貨號|货号|產品|产品|條碼|条码|數量|数量|裝箱|装箱|價格|价格|單價|单价|重量|尺寸|帽圍|帽围|包裝|包装|毛重|體積|体积|運費|运费|海快|控價|控价|售價|售价|台幣|臺幣)\s*:?', line.replace('：', ':')):
            continue
        if re.match(r'^[A-Za-z0-9-]+\s*$', line):
            continue
        name_lines.append(line)
        
    if name_lines:
        raw_name = " ".join(name_lines[:2]).strip()
        if data["code"] and data["code"] in raw_name:
            raw_name = raw_name.replace(data["code"], "").strip()
        data["name"] = raw_name
        
    return data

# --- 5. 主畫面流程 ---
default_text = "產品編號YZ018\n獨家定制款，手提行李箱，\n14寸手提式條紋包角化妝箱\n皮克敏卡通手提行李箱\n化妝裝，手袋便攜箱包\n超萌卡通圖案➕配色\n尺寸:31x22x15CM\n單個重量：650g\n整箱重量：17kg\n裝箱數：20pcs\n價格24.8\n\n海快運費7.7\n\n控價：不得低於臺幣190元銷售"
user_input = st.text_area("📝 第一步：貼上廠商微信文案", value=default_text, height=250)

user_input_tw = zhconv.convert(user_input, 'zh-tw') if user_input else ""
p = parse_text(user_input_tw)

st.subheader("🔍 第二步：確認數據")
c1, c2, c3, c4, c5, c6 = st.columns(6)
code = c1.text_input("貨號", value=p["code"])
name = c2.text_input("名稱", value=p["name"])
price = c3.number_input("進價(RMB)", value=p["price"], format="%.2f")
qty = c4.number_input("裝箱量", value=p["qty"], step=1)
weight = c5.number_input("毛重(kg)", value=p["weight"], format="%.2f")
dom_rate = c6.number_input("內陸運費(R/kg)", value=dom_rate_def)

if qty > 0:
    single_weight_g = (weight / qty) * 1000 if qty > 0 else 0
    dom_fee_rmb = (single_weight_g / 1000) * dom_rate
    intl_fee_rmb = (single_weight_g / 1000) * intl_rate
    cost_ntd = (price + dom_fee_rmb + intl_fee_rmb) * ex_rate
    
    q10 = round(cost_ntd / 0.9, 1)
    q13 = round(cost_ntd / 0.87, 1)
    q15 = round(cost_ntd / 0.85, 1)
    q20 = round(cost_ntd / 0.8, 1)

    st.markdown("---")
    st.subheader("📝 第三步：一鍵生成客戶文案")
    
    margin_choice = st.radio(
        "請選擇要報給客人的利潤單價：",
        options=[f"10% (單價: {q10}元)", f"13% (單
