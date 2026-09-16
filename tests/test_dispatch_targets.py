from copy import deepcopy

import pytest

from dispatch_targets import ADVERTISING_TARGET, canonical_target, same_target
from dispatch_manager import (
    DispatchError, approve_batch, batch_digest, catalog, new_batch,
    prior_activity, record_observation,
)
from dispatch_fakes import product_rows, ready_batch


@pytest.mark.parametrize('old', ['【自動排廣告群組】', '自動排廣告群組',
                                  '【利潤10%】自動排廣告群組'])
def test_explicit_rename_preserves_duplicate_detection_without_rewriting_history(old):
    history = ready_batch(numbers=(1,))
    history['target'] = old
    history = approve_batch(history, catalog(product_rows((1,))), [], '測試')
    history = record_observation(history, item_id='G正版:no1', part='text',
                                 evidence='既有文字紀錄', actor='測試', target=old)
    snapshot = deepcopy(history)
    draft = ready_batch(numbers=(1,))
    draft['target'] = ADVERTISING_TARGET
    assert prior_activity(draft, draft['items'][0], [history])
    with pytest.raises(DispatchError, match='同聊天室已有'):
        approve_batch(draft, catalog(product_rows((1,))), [history], '測試')
    assert history == snapshot
    assert history['approved_digest'] == batch_digest(history)
    assert history['observations'][0]['target'] == old


def test_other_rooms_are_not_merged():
    for other in ['周俊安', '【利潤15%】自動排廣告群組', '自動排廣告群組2', '']:
        assert not same_target(other, ADVERTISING_TARGET)
        assert canonical_target(other) == other
    assert same_target(ADVERTISING_TARGET, ' 【自動排廣告群組】 ')


def test_new_batches_use_current_name_even_for_known_old_label():
    products = catalog(product_rows((1,)))
    assert new_batch('測試', '【自動排廣告群組】', products, '測試')['target'] == ADVERTISING_TARGET
