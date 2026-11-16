"""Draft delivery bound exclusively to an explicit authorized C2C preview event."""
import asyncio
import json
import time
from datetime import datetime,timezone
from nonebot.adapters.qq import Message,MessageSegment
from nonebot.adapters.qq.event import C2CMessageCreateEvent
from .output_templates import Store,OVERRIDE,draft,present
from .editor_examples import render_example
from .card_actions import card_message,image_navigation
from .preview import build_preview,CARD_ID,report

class PreviewSession:
    def __init__(self,config,pager,catalog,*,image_loader=None,store=None,clock=time.time):
        self.config,self.pager,self.catalog=config,pager,catalog
        self.loader=image_loader;self.store=store or Store();self.clock=clock
        self.bound=None;self.task=None;self.lock=asyncio.Lock();self.seen={}

    def available(self):
        return bool(self.bound and self.pager.capacity(self.bound[1],'c2c'))

    def public(self):
        remaining=self.pager.capacity(self.bound[1],'c2c') if self.bound else 0
        self.store.atomic(self.store.queue/'session.json',{'available':remaining>0,'remaining':remaining,
            'expires':self.bound[2] if self.bound else 0})

    async def reset(self):
        self.bound=None
        await self.tick()  # Mark interrupted requests; never send without a new session.

    async def open(self,bot,event,*,start_worker=True):
        if not isinstance(event,C2CMessageCreateEvent) or len(self.config.users)!=1 or not self.config.allows('c2c',event.get_user_id()): return False
        async with self.lock:
            self.seen={k:v for k,v in self.seen.items() if v>self.clock()}
            if event.id in self.seen or not self.pager.capacity(event,'c2c'): return False
            self.seen[event.id]=self.clock()+3600
            try:
                await bot.send(event,present('preview.editor_preview_bff7dcc4', {}))
            except Exception as exc:
                report('editor_session_failed',exc);return False
            stamp=event.timestamp
            if isinstance(stamp,str):stamp=datetime.fromisoformat(stamp.replace('Z','+00:00'))
            if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
            self.bound=(bot,event,self.clock()+max(0,3600-(self.pager.now()-stamp).total_seconds()))
            self.public()
            if start_worker and (self.task is None or self.task.done()): self.task=asyncio.create_task(self.run())
            return True

    async def render(self,key,user):
        if key in {'effect.card','art.page','preview.card'}:
            info=None if self.catalog is None else await asyncio.wait_for(asyncio.to_thread(self.catalog.search,CARD_ID),timeout=8)
            if not info: raise ValueError('Preview card unavailable')
            if key=='effect.card': message=card_message(info,user,images=self.loader is not None,prices=True)[0]
            elif key=='preview.card': message=build_preview(info,user)[0]
            else:
                ids=await asyncio.wait_for(asyncio.to_thread(self.catalog.image_ids,CARD_ID),timeout=8)
                if not ids or not self.loader: raise ValueError('Preview art unavailable')
                # First actual art, followed by the exact same caption/button builder.
                data=await asyncio.wait_for(self.loader(ids[0]),timeout=30)
                if not data or len(data)>8*1024*1024: raise ValueError('Preview art unavailable')
                message=image_navigation(info,user,0,len(ids),prices=True)
                return [Message(MessageSegment.file_image(data)),self.labeled(message,'草稿预览 · 真实卡库/卡图；未发布')]
            return [self.labeled(message,'草稿预览 · 真实卡库；未发布')]
        spec=(OVERRIDE.get() or {}).get(key) or self.store.state()['states'][key]['draft']
        example=render_example(self.store,key,spec)
        content=example['context']
        if len(content)>1400: raise ValueError('Preview too long')
        segment=MessageSegment.markdown(content) if example['context_mode']=='markdown' else MessageSegment.text(content)
        return [self.labeled(Message(segment),'草稿预览 · 示例数据，非实时报价；未发布')]

    @staticmethod
    def labeled(message,label):
        for seg in message:
            if seg.type=='markdown':
                # QQ SDK stores its Markdown model under markdown.
                seg.data['markdown'].content=label+'\n\n'+seg.data['markdown'].content
                return message
            if seg.type=='text': seg.data['text']=label+'\n\n'+seg.data['text'];return message
        return Message(label+'\n')+message

    async def tick(self):
        async with self.lock:
            self.public()
            for path in sorted(self.store.queue.glob('*.json')):
                if path.name=='session.json':continue
                try: item=json.loads(path.read_text(encoding='utf-8'))
                except (ValueError,OSError):continue
                status=item.get('status')
                if status=='sending':
                    item.update(status='unknown',note='上次发送被中断，结果不确定；不会自动重发。');self.store.atomic(path,item);continue
                if status!='awaiting_session': continue
                if self.clock()-item['created']>900:
                    item.update(status='expired',note='预览请求超过 15 分钟，请重新发起。');self.store.atomic(path,item);continue
                if not self.available():return
                needed=2 if item['key']=='art.page' else 1
                bot,event,_=self.bound
                if self.pager.capacity(event,'c2c')<needed:
                    item['note']='回复额度不足，请在 QQ 重新发送“输出预览”。';self.store.atomic(path,item);return
                item.update(status='sending',note='正在使用主动打开的 QQ 回复会话发送。');self.store.atomic(path,item)
                try:
                    with draft(item['key'],item['spec']): messages=await self.render(item['key'],event.get_user_id())
                    if any(len(str(seg.data.get('markdown','')))>1800 for m in messages for seg in m if seg.type=='markdown'): raise ValueError('Preview too long')
                    for message in messages:
                        if not self.pager.capacity(event,'c2c'):raise ValueError('Preview expired')
                        await asyncio.wait_for(bot.send(event,message),timeout=35)
                    item.update(status='accepted',note='QQ API 已接受；请在 QQ 查看实际渲染。草稿未发布。')
                except Exception as exc:
                    report('editor_preview_failed',exc)
                    item.update(status='failed',note='预览发送失败或未确认，不会自动重发；草稿未发布。')
                self.store.atomic(path,item);self.public();return

    async def run(self):
        try:
            while self.bound and self.clock()<self.bound[2]:
                try: await self.tick()
                except Exception as exc: report('editor_queue_failed',exc)
                await asyncio.sleep(1)
        finally:
            self.bound=None;self.public()
