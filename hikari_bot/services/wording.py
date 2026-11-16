"""可注入的展示文案；默认调用保持原文字，不依赖任何平台。"""
from contextlib import contextmanager
from contextvars import ContextVar

_renderer = ContextVar('hikari_wording_renderer', default=None)


class NoticeText(str):
    """共享通知保持原文字，官方投递分支可用原变量重新渲染。"""
    def __new__(cls, key, template, values):
        value = super().__new__(cls, template.format_map(values))
        value.key, value.values = key, values
        return value


def wording(key, template, values=None):
    values = values or {}
    render = _renderer.get()
    return render(key, values) if render else template.format_map(values)


@contextmanager
def using_wording(render):
    token = _renderer.set(render)
    try:
        yield
    finally:
        _renderer.reset(token)
