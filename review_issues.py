"""Source-scoped issue register, independent of math and dispatch approvals."""
from copy import deepcopy
import json

from dispatch_manager import DispatchError, digest, now

ISSUE_SHEET = '_核對問題'


def source_key(source):
    # Do not merge similar names or silently transfer issues between suppliers.
    return digest([source['identity'], source.get('vendor', ''), source.get('date', '')])


def issue_reason(issue):
    return f"既有問題：{issue['field']}｜{issue['detail']}"


def attach_issues(batch, registry):
    result = deepcopy(batch)
    for item in result['items']:
        item['known_issues'] = [deepcopy(issue) for issue in registry['issues'].values()
                                if issue['source_key'] == source_key(item['source']) and issue['status'] == 'open']
    return result


def parse_import(text, batch, actor):
    if not actor.strip():
        raise DispatchError('請填問題登記人')
    try:
        records = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise DispatchError('請貼上合法 JSON 問題清單') from exc
    if not isinstance(records, list) or not 1 <= len(records) <= 150:
        raise DispatchError('每次匯入 1–150 筆問題')
    result = {}
    for record in records:
        if not isinstance(record, dict) or set(record) != {'code', 'field', 'detail', 'current', 'expected', 'evidence', 'action'}:
            raise DispatchError('問題需含 code、field、detail、current、expected、evidence、action，不能匯入通過狀態')
        if any(not isinstance(v, str) or not v.strip() or len(v) > 4000 for v in record.values()):
            raise DispatchError('問題各欄需為非空文字且不超過 4000 字')
        matches = [i for i in batch['items'] if i['source'].get('code') == record['code'].strip()]
        if len(matches) != 1:
            raise DispatchError(f"{record['code']}：本批找不到唯一品號，未匯入")
        source = matches[0]['source']
        clean = {k: v.strip() for k, v in record.items()}
        key = source_key(source)
        identity = digest([key, clean])
        result[identity] = dict(clean, id=identity, source_key=key, source_hash=source['source_hash'],
                                status='open', actor=actor.strip(), at=now())
    return list(result.values())


class ReviewIssueStoreMixin:
    def review_issues(self):
        from dispatch_storage import decode_records
        ws = self._sheet(ISSUE_SHEET)
        result = dict(schema=1, id='registry', created_at=now(), issues={}, _revision='')
        if ws is not None:
            for record in decode_records(ws.get_all_values()[1:]):
                value = record['value']
                if record['entity'] != 'registry' or record['parent'] != result['_revision']:
                    raise DispatchError('核對問題紀錄有版本衝突，停止確認；請先核實雲端紀錄')
                if value.get('schema') != 1 or value.get('id') != 'registry' or not isinstance(value.get('issues'), dict):
                    raise DispatchError('核對問題紀錄格式錯誤，停止確認')
                for identity, issue in value['issues'].items():
                    if issue.get('id') != identity or issue.get('status') not in {'open', 'resolved'}:
                        raise DispatchError('核對問題識別碼或狀態錯誤，停止確認')
                result = dict(value, _revision=record['id'])
        return result

    def _save_review_issues(self, registry, expected_revision):
        from dispatch_storage import encode_record
        if self.review_issues()['_revision'] != expected_revision:
            raise DispatchError('問題清單已在其他視窗更新，請重新載入')
        clean = {k: deepcopy(v) for k, v in registry.items() if not k.startswith('_')}
        rows = encode_record('registry', clean, expected_revision)
        try:
            self._sheet(ISSUE_SHEET, create=True).append_rows(rows, value_input_option='RAW', table_range='A:H')
            saved = self.review_issues()
            if saved['_revision'] != rows[0][0]:
                raise DispatchError('寫後核對版本不符')
        except Exception as exc:
            raise DispatchError('問題保存結果待確認，請重新載入；不要重複新增或當作已解決') from exc
        return saved

    def import_review_issues(self, text, batch, actor, expected_revision):
        records = parse_import(text, batch, actor)
        registry = self.review_issues()
        if registry['_revision'] != expected_revision:
            raise DispatchError('問題清單已更新，請重新載入')
        added = [r for r in records if r['id'] not in registry['issues']]
        if not added:
            return registry
        registry['issues'].update({r['id']: r for r in added})
        return self._save_review_issues(registry, expected_revision)

    def resolve_review_issue(self, identity, source, actor, evidence, expected_revision):
        if not actor.strip() or not evidence.strip():
            raise DispatchError('解除問題必須填核對人與實際修正／核對證據')
        registry = self.review_issues()
        if registry['_revision'] != expected_revision:
            raise DispatchError('問題清單已更新，請重新載入')
        issue = registry['issues'].get(identity)
        if not issue or issue['source_key'] != source_key(source):
            raise DispatchError('問題不屬於本款來源，不能解除')
        self.read_cost_source(source)  # Reject stale source; this does not certify the issue was repaired.
        if issue['status'] == 'resolved':
            return registry
        issue.update(status='resolved', resolution=dict(actor=actor.strip(), evidence=evidence.strip(),
                                                        at=now(), source_hash=source['source_hash']))
        return self._save_review_issues(registry, expected_revision)

    def verify_known_issues(self, batch):
        current = attach_issues(batch, self.review_issues())
        problems = [f"{i['source']['code']}：{issue_reason(issue)}" for i in current['items']
                    if not i['excluded'] for issue in i['known_issues']]
        if problems:
            raise DispatchError('\n'.join(problems))
