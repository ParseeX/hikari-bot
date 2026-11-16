"""Formatting only. Matching, currencies, sessions and price collection remain upstream."""
from .output_templates import present

def rarity_prompt(name_jp,groups):
    lines=[str(present('price.rarity_header',{'name':name_jp}))]
    for index,(rarity,versions) in enumerate(groups.items(),1):
        numbers='、'.join(dict.fromkeys(v.number or str(present('price.number_unknown')) for v in versions))
        lines.append(str(present('price.rarity_row',{'index':index,'rarity':rarity,'versions':numbers})))
    hint = str(present('price.rarity_hint'))
    return present('price.prompt', {'body': '\n'.join(lines)+'\n'+hint,
        'heading': lines[0], 'options': '\n'.join(lines[1:]), 'hint': hint})

def format_comparison(name_jp,rarity,rows,*,japanese=False):
    header=str(present('price.heading',{'name':name_jp,'rarity':rarity}))
    pages=[];text=header;blocks=[]
    def page():
        return present('price.result', {'body': text.rstrip(), 'heading': header, 'versions': ''.join(blocks).rstrip()})
    for row in rows:
        lines=[str(present('price.version',{'version':row.version.number or str(present('price.number_unknown'))}))]
        if row.jhs is None or row.jhs.error:
            lines.append(str(present('price.japanese_unavailable' if japanese else 'price.jhs_unavailable')))
        elif japanese:
            value=f'{row.jhs.minimum:.2f} 元' if row.jhs.minimum is not None else '未找到符合备注的在售出品'
            lines.append(str(present('price.japanese',{'value':value})))
        else:
            def yuan(value): return f'{value:.2f} 元' if value is not None else '暂无数据'
            lines.append(str(present('price.jhs',{'minimum':yuan(row.jhs.minimum),'market':yuan(row.jhs.market)})))
        if row.cardrush_error: lines.append(str(present('price.cardrush_unavailable')))
        elif not row.cardrush: lines.append(str(present('price.cardrush_missing')))
        else:
            lines.extend(str(present('price.cardrush',{'value':f'{record.price:,} 円'})) for record in row.cardrush)
        block=present('price.version_block', {'version': lines[0], 'domestic': lines[1], 'buyback': '\n'.join(lines[2:])})
        if len(text)+len(block)>2800 and text!=header:
            pages.append(page());text=header;blocks=[]
        text+=block
        blocks.append(str(block))
    if text!=header: pages.append(page())
    return pages
