"""Normalize inclusive-sea quote layout without inventing units or weight."""
import re
import unicodedata


def _unbullet(line):
    line = re.sub(r'^\[(?:愛心|爱心)\]\s*', '', line.strip())
    while line and (unicodedata.category(line[0]) in {'So', 'Sk'} or line[0] in '✔✓👉→·•⚡!！\ufe0f'):
        line = line[1:].lstrip()
    return re.sub(r'^-\s*(?=(?:數量|編號|重量|包裝|電池|插腳)\s*[:：])', '', line)


def prepare_inclusive_sea_layout(text):
    """Recognize the explicit shipping phrase, never infer a supplier from it."""
    lines = [_unbullet(line) for line in text.splitlines()]
    if not any(re.match(r'^包郵\s*海快(?:價)?\s*[:：]', line) for line in lines):
        return text, None
    boilerplate = re.compile(r'^(?:現貨[!！\s]*)+$|^下單可(?:馬上發貨|馬上安排發貨|立馬安排發貨)[!！\s]*$|^(?:日本商超熱賣款|爆品上新|禮品渠道爆款)[!！💥\s]*$')
    packaging = re.compile(r'^(?:#.*盒|【[^】]*盒[^】]*】|(?:巨無霸|方形|長方形|長方|長盒)鐵?盒[|｜].*系列)')
    metadata = re.compile(r'^(?:產品貨號|貨號|編號|型號|數量|一箱數量|箱裝|箱規|重量|鐵盒重量|一盒重量|產品重量|單盒|包裝|彩盒|鐵盒尺寸|電池|插腳|備註|台灣控價|臺灣控價|包郵)\s*[:：]?')
    name = ''
    output = []
    notes = []
    for line in lines:
        if not line or boilerplate.fullmatch(line):
            continue
        if packaging.match(line):
            notes.append(line)
            output.append('包裝描述:' + line)
            continue
        if not name and not metadata.match(line):
            name = line.strip('—– ').strip()
            # NFKC turns the decorative circled M into plain M.
            if name.startswith('M') and name.endswith('M') and re.search(r'[\u4e00-\u9fff]', name[1:-1]):
                name = name[1:-1].strip()
        else:
            notes.append(line)
        quantity = re.fullmatch(r'(?:箱規|數量|一箱數量|箱裝)\s*[:：]\s*(\d+)\s*(個|盒|把|套|雙)?\s*/\s*箱', line)
        if quantity:
            # A known count is useful; absent individual units remain absent.
            line = '裝箱:' + quantity[1] + (quantity[2] or '')
        if re.match(r'^鐵盒尺寸\s*[:：]', line):
            line = re.sub(r'^鐵盒尺寸', '包裝尺寸', line)
        # A trailing separator before an explicit unit is layout punctuation.
        # Keep the original line in notes; never infer a missing fourth axis.
        line = re.sub(
            r'^(包裝(?:尺寸)?\s*[:：]\s*\d+(?:\.\d+)?(?:\s*[*xX×]\s*\d+(?:\.\d+)?){2})\s*\*\s*(cm|mm|公分|毫米)$',
            r'\1\2', line, flags=re.I,
        )
        output.append(line)
    return '\n'.join(output), {'name': name, 'notes': notes}
