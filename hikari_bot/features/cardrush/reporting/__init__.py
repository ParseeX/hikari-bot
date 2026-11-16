"""按需加载报表组件，纯文本查询不初始化旧 Bot 图片服务。"""
from importlib import import_module

_EXPORTS = {
    'draw_price_chart': '.chart', 'compress_for_qq': '.compression',
    'render_daily_report_html': '.html', 'DailyReportRenderer': '.renderer',
    'format_daily_report': '.text', 'parse_date_arg': '.text', 'DailyReportWorkflow': '.workflow',
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    return getattr(import_module(_EXPORTS[name], __name__), name)
