import copy
import json

import pytest

from hikari_bot.services.qq_forward_receipt import confirmed_in_history, forward_resource
from nonebot.adapters.onebot.v11.exception import ActionFailed


def message():
    return {'sender': {'user_id': 333}, 'real_seq': '100', 'time': 1000,
            'message': [{'type': 'json', 'data': {'data': json.dumps({
                'app': 'com.tencent.multimsg', 'meta': {'detail': {'resid': 'resource'}}})}}]}


@pytest.mark.parametrize('change', [
    {'sender': {'user_id': 999}}, {'real_seq': '0'}, {'real_seq': 'invalid'},
    {'time': 900}, {'message': [{'type': 'text', 'data': {'text': 'resource'}}]},
    {'message': [{'type': 'json', 'data': {'data': '{invalid json'}}]},
])
def test_history_requires_same_sender_recent_server_sequence_and_resource(change):
    row = message()
    assert confirmed_in_history([row], '333', 'resource', 1000)
    row.update(change)
    assert not confirmed_in_history([row], '333', 'resource', 1000)


def test_other_resource_and_malformed_rows_do_not_confirm_delivery():
    row = message()
    before = copy.deepcopy(row)
    assert not confirmed_in_history([None, {'sender': None}, row], '333', 'other-resource', 1000)
    assert row == before


def test_error_resource_is_only_taken_from_known_napcat_failure():
    for field in ('message', 'wording'):
        error = ActionFailed(retcode=1200, **{field: '发送转发消息（res_id：ABC+def/ghi= 失败'})
        assert forward_resource(error) == 'ABC+def/ghi='
    assert forward_resource(ActionFailed(retcode=1400, message='invalid payload')) is None
    assert forward_resource(TimeoutError()) is None
