"""前排二十名的公开卡组链接；群聊和私聊使用相同模板。"""
from .output_templates import present, escape


def label(value, markdown):
    value = ' '.join(str(value).splitlines())
    return escape(value).replace('[', r'\[').replace(']', r'\]') if markdown else value


def render_front_decks(rows):
    markdown = present('front_decks.availability_1ddd1afd').mode == 'markdown'
    lines = []
    for row in rows:
        name = row.deck_name
        if name is None:
            name = str(present('front_decks.missing'))
        elif not name:
            name = str(present('front_decks.unclassified'))
        lines.append(str(present('front_decks.row', {'rank': row.rank,
            'player': label(row.player, markdown), 'deck_name': label(name, markdown), 'url': row.url})))
    return present('front_decks.availability_1ddd1afd', {'rows': '\n'.join(lines), 'footer': present('front_decks.footer')})
