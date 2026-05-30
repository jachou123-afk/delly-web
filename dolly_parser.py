import streamlit as st
import pandas as pd
import re
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import zhconv
import datetime

# --- 1. 網頁基本設定 ---
st.set_page_config(page_title="半自動 - 採購報價彙整表", layout="wide")
st.title("🪐 半自動 - 採購報價彙整表 V63")
st.info("✅ 規格:【金鑰防護 V3】、【支援批量+單品兩種格式】、區塊空一行、批量建檔。")

# --- 2. Google Sheets 連線功能 ---
SHEET_NAME = "半自動 - 採購報價彙整表"

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

@st.cache_data(ttl=15)
def get_all_sheets_data():
    try:
        creds = get_credentials()
        client = gspread.authorize(creds)
        spreadsheet = client.open(SHEET_NAME)
        all_data = {}
        for ws in spreadsheet.worksheets():
            all_data[ws.title] = ws.get_all_values()
        return all_data
    except Exception as e:
        st.error(f"讀取雲端失敗:{e}")
        return {}

def save_bulk_to_worksheet(category_name, bulk_rows, st_r, block_size=6):
    try:
        creds = get_credentials()
        client = gspread.authorize(creds)
        spreadsheet = client.open(SHEET_NAME)
        try:
            sheet = spreadsheet.worksheet(category_name)
        except gspread.exceptions.WorksheetNotFound:
            sheet = spreadsheet.add_worksheet(title=category_name, rows="1000", cols="20")
        end_r = st_r + len(bulk_rows) - 1
        sheet.update(f"A{st_r}:K{end_r}", bulk_rows, value_input_option="USER_ENTERED")
        num_blocks = len(bulk_rows) // block_size
        for i in range(num_blocks):
            base_r = st_r + (i * block_size)
            sheet.format(f"B{base_r}", {"backgroundColor": {"red": 1.0, "green": 0.6, "blue": 0.0}})
            sheet.format(f"C{base_r}:F{base_r}", {"backgroundColor": {"red": 1.0, "green": 0.95, "blue": 0.8}})
            sheet.format(f"G{base_r}:K{base_r}", {"backgroundColor": {"red": 0.92, "green": 0.96, "blue": 1.0}})
        return True
    except Exception as e:
        st.error(f"寫入雲端失敗:{e}")
        return False

# --- 3. 側邊欄設定 ---
st.sidebar.header("⚙️ 成本參數設定")
ex_rate = st.sidebar.number_input("匯率", value=4.7, step=0.1)
intl_rate = st.sidebar.number_input("國際運費 (RMB/kg)", value=8.5, step=0.5)
dom_rate_def = st.sidebar.number_input("內陸運費 (RMB/kg)", value=1.5, step=0.5)

# --- 4. 解析引擎 V5 (支援批量+單品) ---
def parse_text(text):
    common = {"price": 0.0, "qty": 0, "weight": 0.0, "prod_size": "", "color_box_size": "", "extra_tags": ""}
    products = []
    if not text:
        return common, products
    
    # 把全形冒號統一成半形,方便匹配
    text_n = text.replace(':', ':').replace('，', ',').replace('、', ',')
    
    # === 共用參數抓取 ===
    
    # 抓價格:支援「單價:4 元」「都是 18.8 元」「4 元」等
    m_price = re.search(r'(?:單價|单价|價格|价格|價錢|价钱|售價|售价|都是)\s*:?\s*(?:\[[^\]]*\])?\s*(?:rmb|RMB|¥)?\s*([0-9]+(?:\.[0-9]+)?)', text_n)
    if not m_price:
        m_price = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*元', text_n)
    if m_price:
        common["price"] = float(m_price.group(1))
    
    # 抓裝箱量:支援「箱數:240pcs」「一箱 60 只」「裝箱 60」等
    m_qty = re.search(r'(?:每箱數量|裝箱數|裝箱量|箱數|箱数|裝箱|装箱|數量|数量)\s*:?\s*([0-9]+)', text_n)
    if not m_qty:
        m_qty = re.search(r'一箱\s*([0-9]+)', text_n)
    if not m_qty:
        m_qty = re.search(r'([0-9]+)\s*(?:pcs|只|個|个)\s*[//]?\s*箱?', text_n)
    if m_qty:
        common["qty"] = int(m_qty.group(1))
    
    # 抓毛重:支援「箱重:26kg」「整箱毛重 29」「29KG」等
    m_weight = re.search(r'(?:整箱毛重|箱重|毛重|整箱重量)\s*:?\s*([0-9]+(?:\.[0-9]+)?)', text_n)
    if not m_weight:
        m_weight = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*[Kk][Gg]', text_n)
    if m_weight:
        common["weight"] = float(m_weight.group(1))
    
    # 抓彩盒尺寸:支援「彩盒:10.5*6.3*13.6cm」「彩盒尺寸 61.5*4.8*25.8CM」
    m_color = re.search(r'彩盒(?:尺寸|規格|规格)?\s*:?\s*([0-9.*xX×\s\-]+(?:[cC][mM]|公分)?)', text_n)
    if m_color:
        common["color_box_size"] = m_color.group(1).strip()
    
    # 抓產品尺寸:支援「產品:11*5.7*13cm」「尺寸 X*Y*Z」(不包含彩盒、外箱)
    m_prod = re.search(r'(?:產品|产品|產品尺寸|产品尺寸)\s*:?\s*([0-9.*xX×\s\-]+(?:[cC][mM]|公分)?)', text_n)
    if m_prod:
        common["prod_size"] = m_prod.group(1).strip()
    else:
        # fallback:用外箱規格
        m_outer = re.search(r'外箱(?:規格|规格|尺寸)?\s*:?\s*([0-9.*xX×\s\-]+(?:[cC][mM]|公分)?)', text_n)
        if m_outer:
            common["prod_size"] = m_outer.group(1).strip()
    
    common["extra_tags"] = ""

    # === 商品清單抓取 ===
    
    # 模式 A:批量格式「貨號,名稱」每行一個(英文+數字開頭)
    lines = text_n.split('\n')
    for line in lines:
        line = line.strip()
        if not line:
            continue
        m = re.match(r'^([A-Za-z]+\d{3,}[A-Za-z0-9\-]*)\s*,\s*(.+)$', line)
        if m:
            code = m.group(1).strip()
            name = m.group(2).strip().lstrip(',').strip()
            if name and len(name) >= 2:
                products.append({"code": code, "name": name})
    
    # 模式 B:單品格式(若 A 沒抓到)
    if not products:
        # 抓型號/貨號:支援「型号:888-6」「貨號 FF756628」等
        single_code = ""
        m_code = re.search(r'(?:型號|型号|貨號|货号|產品編號|产品编号|編號|编号)\s*:?\s*([A-Za-z0-9\-/]+)', text_n)
        if m_code:
            single_code = m_code.group(1).strip()
        
        # 抓名稱:取第一行非參數說明的文字當名稱
        single_name = ""
        exclusion_keywords = [
            '型號', '型号', '貨號', '货号', '單價', '单价', '價格', '价格',
            '裝箱', '装箱', '箱數', '箱数', '數量', '数量', '毛重', '箱重',
            '尺寸', '彩盒', '外箱', '產品', '产品', '規格', '规格',
            '包裝', '包装', '運費', '运费', '材積', '材积'
        ]
        for line in lines:
            line_s = line.strip()
            line_s = re.sub(r'[📦💰✅🔥✨🎈🍦🔫\[\]【】]', '', line_s).strip()
            if not line_s:
                continue
            is_excluded = False
            for kw in exclusion_keywords:
                if line_s.startswith(kw):
                    is_excluded = True
                    break
            if is_excluded:
                continue
            # 跳過純數字+單位的行
            if re.match(r'^[0-9.*xX×\s\-]+(?:[cC][mM]|公分|[Kk][Gg]|元|pcs)?$', line_s):
                continue
            single_name = line_s
            break
        
        products.append({"code": single_code, "name": single_name})
        
    return common, products

# --- 5. 主畫面流程 ---
user_input = st.text_area("📝 第一步:貼上廠商微信文案 (支援批量&單品兩種格式)", height=200)
user_input_tw = zhconv.convert(user_input, 'zh-tw') if user_input else ""
common_data, products_data = parse_text(user_input_tw)

with st.expander("🔧 診斷資訊 (若解析有誤可展開查看)"):
    st.write(f"原始輸入長度: {len(user_input)}")
    st.write(f"轉繁後長度: {len(user_input_tw)}")
    st.write(f"抓到商品數: {len(products_data)}")
    st.write("共用參數:", common_data)
    st.write("商品清單:", products_data)

st.subheader("🔍 第二步:共用參數校正")
c1, c2, c3, c4 = st.columns(4)
final_price = c1.number_input("進價(RMB)", value=common_data["price"], format="%.2f")
final_qty = c2.number_input("裝箱量", value=common_data["qty"], step=1)
final_weight = c3.number_input("毛重(kg)", value=common_data["weight"], format="%.2f")
final_dom = c4.number_input("內陸運費(R/kg)", value=dom_rate_def)

st.markdown("---")
st.subheader(f"📋 擷取到的商品清單 (共 {len(products_data)} 筆,可直接編輯、新增或刪除)")
df_items = pd.DataFrame(products_data)
if "code" not in df_items.columns:
    df_items["code"] = ""
if "name" not in df_items.columns:
    df_items["name"] = ""
df_items.insert(0, "寫入", True)
df_items = df_items.rename(columns={"code": "貨號", "name": "名稱"})

edited_df = st.data_editor(df_items, num_rows="dynamic", use_container_width=True)

if final_qty > 0:
    st.markdown("---")
    st.subheader("📊 第三步:選擇分頁與批量存入")
    final_category = st.selectbox("📂 確定存入的分頁:", ["正版", "玩具", "生活用品", "娃娃", "吊飾"], index=0)
    
    to_save_df = edited_df[(edited_df["寫入"] == True) & ((edited_df["貨號"] != "") | (edited_df["名稱"] != ""))]
    
    all_sheets_data = get_all_sheets_data()
    
    duplicate_warnings = []
    if all_sheets_data and not to_save_df.empty:
        for idx, row in to_save_df.iterrows():
            check_code = f"貨號 {str(row['貨號']).strip()}" if str(row['貨號']).strip() and len(str(row['貨號']).strip()) > 2 else None
            check_name = str(row['名稱']).strip() if str(row['名稱']).strip() and len(str(row['名稱']).strip()) > 2 else None
            
            for sheet_title, sheet_rows in all_sheets_data.items():
                dup_found = False
                for i, s_row in enumerate(sheet_rows):
                    if len(s_row) > 1:
                        cell_val = str(s_row[1]).strip()
                        if (check_code and check_code in cell_val) or (check_name and check_name == cell_val):
                            for j in range(i, -1, -1):
                                if len(sheet_rows[j]) > 0 and str(sheet_rows[j][0]).lower().startswith('no'):
                                    duplicate_warnings.append(f"【{check_name or check_code}】已存在於 {sheet_title} (編號: {sheet_rows[j][0]})")
                                    dup_found = True
                                    break
                            break
                    if dup_found:
                        break

    if duplicate_warnings:
        for warn in duplicate_warnings:
            st.error(f"🚨 **撞單雷達警告**:{warn}")

    if not to_save_df.empty:
        final_confirm = st.checkbox(f"我已手動校對完成,確認寫入共 {len(to_save_df)} 款商品")
        
        if st.button("💾 執行批量存檔", type="primary", disabled=not final_confirm):
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
            if common_data["prod_size"]:
                info_lines.append(f"尺寸 {common_data['prod_size']}")
            if common_data["color_box_size"]:
                info_lines.append(f"彩盒尺寸 {common_data['color_box_size']}")
            if common_data["extra_tags"]:
                info_lines.append(common_data["extra_tags"])
            info_display = "\n".join(info_lines) if info_lines else "尺寸 (未提供)"
            today_str = datetime.datetime.now().strftime("%Y/%-m/%-d")
            
            empty_row = ["", "", "", "", "", "", "", "", "", "", ""]
            
            for idx, row in to_save_df.iterrows():
                max_no += 1
                next_no = f"no{max_no}"
                
                v_r = st_r + len(bulk_rows) + 1
                
                f10 = f"=ROUND(K{v_r}/0.9,1)"
                f13 = f"=ROUND(K{v_r}/0.87,1)"
                f15 = f"=ROUND(K{v_r}/0.85,1)"
                f20 = f"=ROUND(K{v_r}/0.8,1)"
                f_cost = f"=ROUND((G{v_r}+I{v_r}+J{v_r})*{ex_rate},1)"
                f_dom_formula = f"=ROUNDUP((H{v_r}/1000)*{final_dom}, 2)"
                f_intl_formula = f"=ROUNDUP((H{v_r}/1000)*{intl_rate}, 2)"
                f_weight_formula = f"=ROUNDUP(({final_weight}/{final_qty})*1000*1.03, 2)"
                
                block = [
                    [next_no, str(row['名稱']).strip(), "10%報價", "13%報價", "15%報價", "20%報價", "進價rmb", "重量g/pcs", "大陸運費rmb", "國際運費", "預估到手成本"],
                    [today_str, info_display, f10, f13, f15, f20, final_price, f_weight_formula, f_dom_formula, f_intl_formula, f_cost],
                    ["", f"裝箱 {final_qty}個/箱", "", "", "", "", "", "", "", "", ""],
                    ["", f"毛重 {final_weight}KG", "", "", "", "", "", "", "", "", ""],
                    ["", f"貨號 {str(row['貨號']).strip()}", "", "", "", "", "", "", "", "", ""],
                    empty_row
                ]
                bulk_rows.extend(block)
            
            if save_bulk_to_worksheet(final_category, bulk_rows, st_r, block_size=6):
                get_all_sheets_data.clear()
                st.success(f"✅ 批量儲存成功!已一口氣將 {len(to_save_df)} 款商品存入【{final_category}】!")
