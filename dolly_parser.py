import streamlit as st
import pandas as pd
import re
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import zhconv

# --- 1. 網頁基本設定 ---
st.set_page_config(page_title="朵麗星球 - 採購雲端同步系統", layout="wide")
st.title("🪐 朵麗星球 - 採購報價彙整系統 V22")
st.info("✅ 規格：新增防呆過濾(無視控價/台幣陷阱)、支援產品編號與無冒號標籤、自動簡轉繁。")

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

# --- 4. 解析引擎 (V22 終極防呆版) ---
def parse_text(text):
    data = {"code": "", "name": "", "price": 0.0, "qty": 0, "weight": 0.0, "size": ""}
    if not text: return data
    
    text_norm = text.replace('：', ':')
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    
    # 💡 1. 貨號 (新增「產品編號」，並支援無冒號格式)
    m_code = re.search(r'(?:型號|型号|貨號|货号|產品編號|产品编号)\s*:?\s*([A-Za-z0-9-]+)', text_norm)
    if m_code:
        data["code"] = m_code.group(1)
    else:
        m_code_start = re.match(r'^([A-Za-z0-9]{4,})', lines[0] if lines else "")
        if m_code_start: data["code"] = m_code_start.group(1)
        else:
            m_code_fallback = re.search(r'([A-Za-z0-9]{4,})', text_norm)
            if m_code_fallback: data["code"] = m_code_fallback.group(1)

    # 💡 2. 進價 (防呆機制：主動屏蔽含有控價、售價、台幣的句子，防止誤抓)
    text_for_price = re.sub(r'(?:控價|控价|售价|售價|台幣|臺幣).*?(?:\n|$)', '', text_norm)
    
    # 支援無冒號的價格標籤
    m_price = re.search(r'(?:單價|单价|價格|价格|價錢)\s*:?\s*(?:rmb|RMB|¥)?\s*([0-9.]+)', text_for_price)
    if not m_price: m_price = re.search(r'(\d+(?:\.\d+)?)\s*元', text_for_price)
    if m_price: data["price"] = float(m_price.group(1))

    # 💡 3. 裝箱量 (新增「裝箱數」，支援無冒號格式)
    m_qty = re.search(r'(?:每箱數量|每箱数量|裝箱數|装箱数|數量|数量|裝箱量|装箱量)\s*:?\s*(\d+)', text_norm)
    if not m_qty: m_qty = re.search(r'(?:裝箱|一箱)\s*(\d+)', text_norm)
    if m_qty: data["qty"] = int(m_qty.group(1))

    # 💡 4. 毛重 (新增「整箱重量」，並優先採用)
    m_total_weight = re.search(r'(?:毛重|整箱重量)\s*:?\s*([0-9.]+)', text_norm)
    if not m_total_weight: m_total_weight = re.search(r'([0-9.]+)\s*[Kk][Gg]', text_norm)
    
    m_single_weight = re.search(r'(?:單個重量|单个重量|克重)\s*:?\s*([0-9.]+)\s*[Gg克]', text_norm)
    
    if m_total_weight and float(m_total_weight.group(1)) > 0:
        data["weight"] = float(m_total_weight.group(1)) # 優先直接填入整箱重量
    elif m_single_weight and data["qty"] > 0:
        single_g = float(m_single_weight.group(1))
        data["weight"] = (single_g * data["qty"]) / 1000.0 # 沒整箱重量才用單個換算

    # 5. 尺寸
    m_size = re.search(r'(?:尺寸|帽圍|帽围)\s*:?\s*([0-9.*xX×\s-]+(?:[cC][mM]|公分)?)', text_norm)
    if not m_size: m_size = re.search(r'尺寸\s*:?\s*([0-9.*xX×\s]+(?:[cC][mM]|公分)?)', text_norm)
    if m_size: data["size"] = m_size.group(1).strip()

    # 6. 名稱 (新增過濾標籤：產品編號、運費、海快、控價...)
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
        options=[f"10% (單價: {q10}元)", f"13% (單價: {q13}元)", f"15% (單價: {q15}元)", f"20% (單價: {q20}元)"],
        horizontal=True
    )
    
    if "10%" in margin_choice: final_p = q10
    elif "13%" in margin_choice: final_p = q13
    elif "15%" in margin_choice: final_p = q15
    else: final_p = q20

    size_text = f"尺寸 {p['size']}" if p['size'] else ""
    copy_text = f"{name}\n{size_text}\n裝箱 {qty}個/箱\n單價 {final_p}元"
    
    st.code(copy_text, language="text")

    st.markdown("---")
    st.subheader("📊 第四步：儲存至雲端表格")
    if st.button("💾 儲存並產出進位公式", type="primary"):
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
                f_cost = f"=ROUND((G{v_r}+I{v_r}+J{v_r})*{ex_rate},1)"
                f_dom = f"=(H{v_r}/1000)*{dom_rate}"
                f_intl = f"=(H{v_r}/1000)*{intl_rate}"
                single_weight_raw = (weight/qty)*1000
                
                rows = [
                    [next_no, name, "10%報價", "13%報價", "15%報價", "20%報價", "進價rmb", "重量g/pcs", "大陸運費rmb", "國際運費", "預估到手成本"],
                    ["", f"尺寸 {p['size']}", f10, f13, f15, f20, price, single_weight_raw, f_dom, f_intl, f_cost],
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
