import streamlit as st
import pandas as pd
import re
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import zhconv
import datetime

# --- 1. 網頁基本設定 ---
st.set_page_config(page_title="半自動 - 採購報價彙整表", layout="wide")
st.title("🪐 半自動 - 採購報價彙整表 V38")
st.info("✅ 規格：新增【分類手動更正】、全欄位二次編輯、分頁自動分流、團隊防撞提醒。")

# --- 2. Google Sheets 連線功能 ---
SHEET_NAME = "半自動 - 採購報價彙整表"

def get_worksheet(category_name):
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        if st.secrets.get("gcp_service_account"):
            creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), scope)
        else:
            creds = ServiceAccountCredentials.from_json_keyfile_name("giraffe-495919-b7d55659973d.json", scope)
        client = gspread.authorize(creds)
        spreadsheet = client.open(SHEET_NAME)
        try:
            return spreadsheet.worksheet(category_name)
        except gspread.exceptions.WorksheetNotFound:
            return spreadsheet.add_worksheet(title=category_name, rows="1000", cols="20")
    except Exception as e:
        st.error(f"連線錯誤: {e}")
        return None

# --- 3. 側邊欄設定 ---
st.sidebar.header("⚙️ 成本參數設定")
ex_rate = st.sidebar.number_input("匯率", value=4.7, step=0.1)
intl_rate = st.sidebar.number_input("國際運費 (RMB/kg)", value=8.5, step=0.5)
dom_rate_def = st.sidebar.number_input("內陸運費 (RMB/kg)", value=1.5, step=0.5)

# --- 4. 解析引擎 ---
def parse_text(text):
    data = {"code": "", "name": "", "price": 0.0, "qty": 0, "weight": 0.0, "prod_size": "", "box_size": "", "extra_tags": ""}
    if not text: return data
    text_norm = text.replace('：', ':')
    
    m_code = re.search(r'(?:型號|型号|貨號|货号|產品編號|产品编号)\s*:?\s*([A-Za-z0-9-]+)', text_norm)
    if m_code: data["code"] = m_code.group(1)
    else:
        candidates = re.findall(r'([A-Za-z0-9-]{4,})', text_norm)
        for cand in candidates:
            if not re.match(r'^\d+(?:\.\d+)?(?:pcs|kg|g|cm|mm|rmb)$', cand, re.IGNORECASE):
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

    m_box = re.search(r'(?:彩盒尺寸|外箱尺寸|外箱)\s*:?\s*([0-9.*xX×\s-]+(?:[cC][mM]|公分)?)', text_norm)
    if m_box: data["box_size"] = m_box.group(1).strip()
    m_prod = re.search(r'(?<!(?:彩盒|外箱))(?:尺寸|產品|產品)\s*:?\s*([0-9.*xX×\s-]+(?:[cC][mM]|公分)?)', text_norm)
    if m_prod: data["prod_size"] = m_prod.group(1).strip()

    extra_items = []
    if re.search(r'帶[鐳雷]射標', text_norm): extra_items.append("帶雷射標")
    m_pkg = re.search(r'(?:包裝|包裝)\s*:?\s*([^\n,，]+)', text_norm)
    if m_pkg: extra_items.append(f"包裝:{m_pkg.group(1).strip()}")
    data["extra_tags"] = "\n".join(extra_items)

    segments = re.split(r'[\n,，]+', text_norm)
    name_segments = []
    for seg in segments:
        seg = seg.strip()
        seg = re.sub(r'\[.*?\]', '', seg)
        seg = re.sub(r'是?\s*[0-9.]+\s*元', '', seg)
        if len(seg) < 2: continue 
        if re.search(r'(?:型號|貨號|產品|數量|裝箱|箱數|價格|單價|重量|尺寸|包裝|外箱|運費|控價|台幣|帶[鐳雷]射標)\s*:?', seg): continue
        name_segments.append(seg)
    if name_segments: data["name"] = " ".join(name_segments[:2]).strip()
    return data

# --- 5. 主畫面流程 ---
user_input = st.text_area("📝 第一步：貼上廠商微信文案", height=150)
user_input_tw = zhconv.convert(user_input, 'zh-tw') if user_input else ""
p = parse_text(user_input_tw)

st.subheader("🔍 第二步：數據校正 (若解析有誤可直接修改)")
c1, c2, c3, c4, c5, c6 = st.columns(6)
# 💡 這裡開放讓您手動改，改完後存檔會以這裡為準
final_code = c1.text_input("貨號", value=p["code"])
final_name = c2.text_input("名稱", value=p["name"])
final_price = c3.number_input("進價(RMB)", value=p["price"], format="%.2f")
final_qty = c4.number_input("裝箱量", value=p["qty"], step=1)
final_weight = c5.number_input("毛重(kg)", value=p["weight"], format="%.2f")
final_dom = c6.number_input("內陸運費(R/kg)", value=dom_rate_def)

if final_qty > 0:
    st.markdown("---")
    st.subheader("📊 第三步：選擇分頁與最終確認")
    
    # 💡 分類選擇直接放在這，方便更正「正版/玩具/生活用品」
    # 系統會預設在「正版」，若不是則手動切換
    final_category = st.selectbox("📂 確定存入的分頁：", ["正版", "玩具", "生活用品"], index=0)
    
    st.warning(f"即將存入【{final_category}】分頁。請確認以上貨號、名稱、價格皆正確無誤。")
    final_confirm = st.checkbox(f"我已手動校對完成，確認資料正確")
    
    if st.button("💾 執行存檔", type="primary", disabled=not final_confirm):
        sheet = get_worksheet(final_category)
        if sheet:
            try:
                fresh_data = sheet.get_all_values()
                true_last_row = len(fresh_data)
                max_no = 0
                for r in fresh_data:
                    if r and r[0]:
                        m = re.search(r'no(\d+)', str(r[0]), re.IGNORECASE)
                        if m: max_no = max(max_no, int(m.group(1)))
                next_no = f"no{max_no + 1}"
                
                st_r = true_last_row + 2 if true_last_row > 0 else 1
                v_r = st_r + 1
                
                f10, f13, f15, f20 = f"=ROUND(K{v_r}/0.9,1)", f"=ROUND(K{v_r}/0.87,1)", f"=ROUND(K{v_r}/0.85,1)", f"=ROUND(K{v_r}/0.8,1)"
                f_cost = f"=ROUND((G{v_r}+I{v_r}+J{v_r})*{ex_rate},1)"
                f_dom_formula = f"=ROUNDUP((H{v_r}/1000)*{final_dom}, 2)"
                f_intl_formula = f"=ROUNDUP((H{v_r}/1000)*{intl_rate}, 2)"
                f_weight_formula = f"=ROUNDUP(({final_weight}/{final_qty})*1000*1.03, 2)"
                
                info_display = f"尺寸 {p['prod_size']}\n外箱尺寸 {p['box_size']}\n{p['extra_tags']}".strip()
                today_str = datetime.datetime.now().strftime("%Y/%-m/%-d")
                
                rows = [
                    [next_no, final_name, "10%報價", "13%報價", "15%報價", "20%報價", "進價rmb", "重量g/pcs", "大陸運費rmb", "國際運費", "預估到手成本"],
                    [today_str, info_display, f10, f13, f15, f20, final_price, f_weight_formula, f_dom_formula, f_intl_formula, f_cost],
                    ["", f"裝箱 {final_qty}個/箱", "", "", "", "", "", "", "", "", ""],
                    ["", f"毛重 {final_weight}KG", "", "", "", "", "", "", "", "", ""],
                    ["", f"貨號 {final_code}", "", "", "", "", "", "", "", "", ""]
                ]
                
                sheet.update(f"A{st_r}:K{st_r+4}", rows, value_input_option="USER_ENTERED")
                sheet.format(f"B{st_r}", {"backgroundColor": {"red": 1.0, "green": 0.6, "blue": 0.0}})
                st.success(f"✅ 儲存成功！已存入【{final_category}】。編號：{next_no}")
            except Exception as e:
                st.error(f"儲存失敗：{e}")
