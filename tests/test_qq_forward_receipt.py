from hikari_bot.services.qq_forward_receipt import forward_resource
from nonebot.adapters.onebot.v11.exception import ActionFailed


def test_error_resource_is_only_taken_from_known_napcat_failure():
    for field in ('message', 'wording'):
        error = ActionFailed(retcode=1200, **{field: '发送转发消息（res_id：ABC+def/ghi= 失败'})
        assert forward_resource(error) == 'ABC+def/ghi='
    assert forward_resource(ActionFailed(retcode=1400, message='invalid payload')) is None
    assert forward_resource(TimeoutError()) is None
