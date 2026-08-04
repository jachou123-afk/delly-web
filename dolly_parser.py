import streamlit as st
import pandas as pd
import re
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import zhconv
import datetime
# --- 1. 網頁基本設定 ---
st.set_page_config(page_title="半自動 - 採購報價彙整表", layout="wide")
st.title("🪐 半自動 - 採購報價彙整表 V70")
st.info("✅ 規格:【金鑰防護 V3】、【單個包裝=彩盒】、【名稱多行合併】、區塊空一行。【V70 新增:成本參數可存為雲端預設(_設定分頁)】")
# --- 2. Google Sheets 連線功能 ---
SHEET_NAME = "半自動 - 採購報價彙整表"
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

# --- 2.5 成本參數預設值(存於 Google Sheets 的 _設定 分頁)---
def load_settings():
    defaults = {"ex_rate": 4.7, "intl_rate": 8.5, "dom_rate": 1.5}
    try:
        creds = get_credentials()
        client = gspread.authorize(creds)
        spreadsheet = client.open(SHEET_NAME)
        try:
            ws = spreadsheet.worksheet(SETTINGS_WS)
        except gspread.exceptions.WorksheetNotFound:
            return defaults
        for row in ws.get_all_values():
            if len(row) >= 2 and row[0] in defaults:
                try:
                    defaults[row[0]] = float(row[1])
                except ValueError:
                    pass
    except Exception:
        pass
    return defaults

def save_settings(s):
    try:
        creds = get_credentials()
        client = gspread.authorize(creds)
        spreadsheet = client.open(SHEET_NAME)
        try:
            ws = spreadsheet.worksheet(SETTINGS_WS)
        except gspread.exceptions.WorksheetNotFound:
            ws = spreadsheet.add_worksheet(title=SETTINGS_WS, rows="10", cols="2")
        rows = [[k, v] for k, v in s.items()]
        ws.update("A1:B" + str(len(rows)), rows, value_input_option="USER_ENTERED")
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
        sheet.update(f"A{st_r}:L{end_r}", bulk_rows, value_input_option="USER_ENTERED")
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
settings = get_settings_cached()
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
# --- 4. 解析引擎 V11 ---
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

def parse_text(text):
    common = {
        "price": 0.0,
        "qty": 0,
        "weight": 0.0,
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

    # ===== 毛重 =====
    m_weight_pair = re.search(
        r'(?:箱毛淨重|箱毛净重|整箱毛淨重|整箱毛净重|毛淨重|毛净重)'
        r'\s*[：:]?\s*([0-9]+(?:\.[0-9]+)?)\s*[/／]\s*'
        r'([0-9]+(?:\.[0-9]+)?)\s*[Kk][Gg]',
        text_n,
    )
    if m_weight_pair:
        common["weight"] = max(
            float(m_weight_pair.group(1)),
            float(m_weight_pair.group(2)),
        )
    else:
        m_weight = re.search(
            r'(?:整箱毛重|整箱重量|箱重|毛重|⚖️)\s*[：:]?\s*([0-9]+(?:\.[0-9]+)?)',
            text_n)
        if not m_weight:
            m_weight = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*[Kk][Gg]', text_n)
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
            '數量','数量','毛重','箱重','整箱重量','整箱毛重',
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
# --- 5. 主畫面流程 ---
user_input = st.text_area("📝 第一步:貼上廠商微信文案 (支援批量&單品&emoji 文案)", height=200)
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
c5, c6 = st.columns(2)
final_prod_size = c5.text_input("產品尺寸 (沒抓到可手動輸入)", value=common_data["prod_size"])
final_color_size = c6.text_input("彩盒尺寸 (亞克力/單個包裝也算)", value=common_data["color_box_size"])
final_outer_size = st.text_input("外箱尺寸 (沒抓到可手動輸入)", value=common_data["outer_box_size"])
final_extra = st.text_input("額外備註 (包裝資訊、雷射標等,可手動編輯)", value=common_data["extra_tags"])
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
    category_col, vendor_col = st.columns([1, 1])
    with category_col:
        final_category = st.selectbox(
            "📂 確定存入的分頁:",
            ["正版", "玩具", "生活用品", "娃娃", "吊飾"],
            index=0,
        )
    with vendor_col:
        final_vendor = st.selectbox(
            "🏷️ 廠商:",
            ["v菲凡", "v多品村", "v優娜卡樂星"],
            index=0,
        )
    to_save_df = edited_df[(edited_df["寫入"] == True) & ((edited_df["貨號"] != "") | (edited_df["名稱"] != ""))]
    all_sheets_data = get_all_sheets_data()
    duplicate_warnings = []
    seen_batch = {}
    if not to_save_df.empty:
        for idx, row in to_save_df.iterrows():
            check_code = normalize_code(row["貨號"])
            check_name = normalize_name(row["名稱"])
            if len(check_code) <= 2:
                check_code = ""
            if len(check_name) <= 2:
                check_name = ""

            # 同一批資料：有貨號時以貨號識別，沒有貨號才退回使用名稱。
            batch_key = ("code", check_code) if check_code else ("name", check_name)
            if batch_key in seen_batch:
                duplicate_warnings.append(
                    f"【{check_code or check_name}】本批次與第 {seen_batch[batch_key] + 1} 筆重複"
                )
            else:
                seen_batch[batch_key] = idx

            # 雲端既有資料：有貨號時只比貨號；沒有貨號時才比名稱。
            if all_sheets_data and (check_code or check_name):
                for sheet_title, sheet_rows in all_sheets_data.items():
                    for existing in extract_saved_products(sheet_rows):
                        if check_code:
                            dup_found = existing["code"] == check_code
                        else:
                            dup_found = existing["name"] == check_name

                        if dup_found:
                            duplicate_warnings.append(
                                f"【{check_code or check_name}】已存在於 "
                                f"{sheet_title} (編號: {existing['no']})"
                            )
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
            if final_prod_size:
                info_lines.append(f"尺寸 {final_prod_size}")
            if final_color_size:
                info_lines.append(f"彩盒尺寸 {final_color_size}")
            if final_outer_size:
                info_lines.append(f"外箱尺寸 {final_outer_size}")
            if final_extra:
                info_lines.append(final_extra)
            info_display = "\n".join(info_lines) if info_lines else "尺寸 (未提供)"
            today_str = datetime.datetime.now().strftime("%Y/%-m/%-d")
            empty_row = [""] * 12
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
                    [next_no, str(row['名稱']).strip(), "10%報價", "13%報價", "15%報價", "20%報價", "進價rmb", "重量g/pcs", "大陸運費rmb", "國際運費", "預估到手成本", final_vendor],
                    [today_str, info_display, f10, f13, f15, f20, final_price, f_weight_formula, f_dom_formula, f_intl_formula, f_cost, ""],
                    ["", f"裝箱 {final_qty}個/箱"] + [""] * 10,
                    ["", f"毛重 {final_weight}KG"] + [""] * 10,
                    ["", f"貨號 {normalize_code(row['貨號'])}"] + [""] * 10,
                    empty_row
                ]
                bulk_rows.extend(block)
            if save_bulk_to_worksheet(final_category, bulk_rows, st_r, block_size=6):
                get_all_sheets_data.clear()
                st.success(
                    f"✅ 批量儲存成功!已一口氣將 {len(to_save_df)} 款商品存入【{final_category}】，"
                    f"廠商【{final_vendor}】!"
                )
