"""Synthetic Sheets atomic-batch double. No network or real business data."""
from copy import deepcopy
import re

from cost_audit import block
from dispatch_fakes import FakeSpreadsheet, FakeWorksheet, product_rows
from dispatch_storage import CloudDispatchStore
from test_v74_safety import ns

TOOLS = {'parse': ns['parse_text'], 'formulas': ns['build_cost_formulas']}
RAW = '測試收納商品 1\n编号：TEST-NEW\n箱数：300pcs\n单价：10元\n产品尺寸：12*8*3cm\n重量：68g(单个)\n包装：彩盒'


class CorrectionWorksheet(FakeWorksheet):
    def __init__(self, title, rows, identity):
        super().__init__(title, rows)
        self.id = identity
        self.formula_rows = deepcopy(rows)
        for start in range(1, len(rows), 6):
            self.formula_rows[start - 1:start + 5] = FakeWorksheet.get(self, f'A{start}:L{start + 5}', 'FORMULA')

    def get(self, name, value_render_option=None):
        if value_render_option == 'FORMULA':
            self.read_ranges.append(name)
            a, b = map(int, re.fullmatch(r'A(\d+):L(\d+)', name).groups())
            return block(self.formula_rows[a - 1:b])
        return super().get(name, value_render_option)


class CorrectionSpreadsheet(FakeSpreadsheet):
    id = 'synthetic-correction-sheet'

    def __init__(self):
        super().__init__()
        self.sheets = {k: CorrectionWorksheet(k, v, n + 100) for n, (k, v) in
                       enumerate(product_rows((1, 2), vendor='v多品村').items())}
        self.requests = []
        self.fail_after_write = False
        self.plan = None

    def add_worksheet(self, title, rows, cols):
        ws = super().add_worksheet(title, rows, cols)
        ws.id = len(self.sheets) + 900
        return ws

    def batch_update(self, body):
        self.requests.append(deepcopy(body))
        staged = deepcopy(self.sheets)
        for request in body['requests']:
            data = request.get('updateCells')
            if data:
                area = data['range']
                ws = next(s for s in staged.values() if s.id == area['sheetId'])
                r, c = area['startRowIndex'], area['startColumnIndex']
                assert c != 0 and c < 12 and r % 6 != 5 and data['fields'] == 'userEnteredValue'
                value = next(iter(data['rows'][0]['values'][0]['userEnteredValue'].values()))
                while len(ws.rows[r]) <= c:
                    ws.rows[r].append('')
                ws.rows[r][c] = str(value)
                ws.formula_rows[r][c] = str(value)
            else:
                data = request['appendCells']
                ws = next(s for s in staged.values() if s.id == data['sheetId'])
                ws.rows.extend([[cell['userEnteredValue']['stringValue'] for cell in row['values']] for row in data['rows']])
        if self.plan and self.plan['impacts']:
            ws = staged[self.plan['source']['category']]
            for effect in self.plan['impacts']:
                if effect['column'] is not None:
                    ws.rows[self.plan['source']['row']][effect['column']] = effect['修正後']
        # Preserve existing worksheet handles, as gspread does.
        for title, staged_ws in staged.items():
            self.sheets[title].__dict__.update(staged_ws.__dict__)
        if self.fail_after_write:
            raise TimeoutError('synthetic uncertain server response')
        return {'replies': [{} for _ in body['requests']]}


def correction_store():
    store = CloudDispatchStore(CorrectionSpreadsheet())
    store.correction_tools = TOOLS
    return store
