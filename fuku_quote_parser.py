"""Normalize inclusive-sea quote layout without inventing units or weight."""
import re
import unicodedata


def _unbullet(line):
    line = line.strip()
    while line and (unicodedata.category(line[0]) in {'So', 'Sk'} or line[0] in '✔✓👉→·•⚡\ufe0f'):
        line = line[1:].lstrip()
    return re.sub(r'^-\s*(?=(?:數量|編號|重量|包裝|電池|插腳)\s*[:：])', '', line)


def prepare_inclusive_sea_layout(text):
    """Recognize the explicit shipping phrase, never infer a supplier from it."""
    lines = [_unbullet(line) for line in text.splitlines()]
    if not any(re.match(r'^包郵\s*海快(?:價)?\s*[:：]', line) for line in lines):
        return text, None
    boilerplate = re.compile(r'^(?:現貨[!！\s]*)+$|^下單可馬上發貨[!！\s]*$')
    packaging = re.compile(r'^(?:#.*盒|【[^】]*盒[^】]*】|(?:巨無霸|方形|長方形|長方|長盒)鐵?盒[|｜].*系列)')
    metadata = re.compile(r'^(?:產品貨號|貨號|編號|型號|數量|箱規|重量|鐵盒重量|單盒|包裝|彩盒|鐵盒尺寸|電池|插腳|備註|台灣控價|臺灣控價|包郵)\s*[:：]?')
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
        else:
            notes.append(line)
        quantity = re.fullmatch(r'(?:箱規|數量)\s*[:：]\s*(\d+)\s*/\s*箱', line)
        if quantity:
            # A known count is useful; absent individual units remain absent.
            line = '裝箱:' + quantity[1]
        if re.match(r'^鐵盒尺寸\s*[:：]', line):
            line = re.sub(r'^鐵盒尺寸', '包裝尺寸', line)
        output.append(line)
    return '\n'.join(output), {'name': name, 'notes': notes}
