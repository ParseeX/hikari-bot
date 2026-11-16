"""Presentation-only templates. No executable expressions, action edits or secrets."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
import json
import os
from pathlib import Path
import re
import string
import tempfile
import time

ROOT = Path(__file__).parent
SCHEMA = json.loads((ROOT/'output_schema.json').read_text(encoding='utf-8'))
OVERRIDE = ContextVar('srbs_output_preview',default=None)
HOME = Path.home()


def render_outputs(function):
    """只在官方调用链启用文案；共享服务的 OneBot 调用维持原文。"""
    from hikari_bot.services.wording import using_wording
    @wraps(function)
    async def wrapped(*args, **kwargs):
        with using_wording(present):
            return await function(*args, **kwargs)
    return wrapped

def escape(value):
    text = str(value or '')
    text = re.sub(r'](?=\s*[([])', ']\u200b', text)
    text = re.sub(r'(?m)^(\s*\[[^\]\n]+])(?=\s*:)',lambda m:m[1]+'\u200b',text)
    text = re.sub(r'<(?=[A-Za-z/!@])','<\u200b',text)
    text = re.sub(r'([\\`*_~])',r'\\\1',text)
    return re.sub(r'(?m)^(?=\s*(?:#{1,6}\s|>\s|[-+]\s|\d+[.)]\s))','\u200b',text)

def validate(key,spec):
    definition = SCHEMA['states'][key]
    if set(spec) != {'template','mode','visible','rows','labels'}: raise ValueError('模板字段不完整')
    if spec['mode'] not in {'text','markdown'}: raise ValueError('显示方式无效')
    if definition.get('fragment') and spec['mode'] != 'text': raise ValueError('组合字段由外层决定格式')
    template = spec['template']
    limit = definition.get('max_length', 12000)
    if not isinstance(template,str) or not 0 < len(template) <= limit: raise ValueError(f'模板长度应为 1–{limit} 字符')
    fields = definition['fields']
    for _,name,format_spec,conversion in string.Formatter().parse(template):
        if name is not None and (name not in fields or format_spec or conversion):
            raise ValueError('只能使用列出的 {变量名}，不允许表达式、属性、格式转换')
    if not isinstance(spec['visible'],list) or len(set(spec['visible'])) != len(spec['visible']) or not set(spec['visible']) <= set(fields):
        raise ValueError('显示字段无效')
    roles = definition.get('buttons',{})
    rows = spec['rows']
    if not isinstance(rows,list) or len(rows)>5 or any(not isinstance(row,list) or not 1<=len(row)<=5 for row in rows): raise ValueError('按钮最多 5 行，每行 5 个')
    flat = [role for row in rows for role in row]
    if len(flat)!=len(set(flat)) or not set(flat)<=set(roles): raise ValueError('只能排列该状态已有的按钮动作')
    if set(spec['labels']) != set(roles) or any(not isinstance(v,str) or not 1<=len(v)<=24 or '\n' in v for v in spec['labels'].values()):
        raise ValueError('按钮标签应为 1–24 字符')
    return deepcopy(spec)

def default(key):
    d=SCHEMA['states'][key]
    return {'template':d['template'],'mode':d.get('mode','text'),'visible':list(d['fields']),
            'rows':deepcopy(d.get('rows',[])),'labels':deepcopy(d.get('buttons',{}))}


def upgrade_spec(key, spec):
    """只展开旧版默认外壳；自定义文案、格式、按钮和存储文件不覆盖。"""
    definition = SCHEMA['states'][key]
    spec = deepcopy(spec)
    legacy = definition.get('legacy_template')
    if legacy and spec.get('template') == legacy:
        spec['template'] = definition['template']
        spec['visible'] = list(dict.fromkeys([*spec['visible'], *definition['fields']]))
    return spec

class Store:
    def __init__(self,home=None):
        self.home=Path(home) if home else HOME
        self.path=self.home/'.config/srbs/output-templates.json'
        self.queue=self.home/'.local/share/srbs/output-preview'

    def load(self):
        if not self.path.exists(): return {'schema':1,'revision':0,'features':{}}
        if self.path.is_symlink() or self.path.stat().st_size>2_000_000: raise ValueError('模板存储无效')
        data=json.loads(self.path.read_text(encoding='utf-8'))
        if data.get('schema')!=1: raise ValueError('模板版本无效')
        return data

    def atomic(self,path,data):
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if any(p.is_symlink() for p in [path,*path.parents]): raise ValueError('拒绝符号链接路径')
        fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.output-')
        try:
            os.chmod(tmp,0o600)
            with os.fdopen(fd,'w',encoding='utf-8') as f:
                json.dump(data,f,ensure_ascii=False,indent=2); f.flush(); os.fsync(f.fileno())
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)

    def state(self):
        data=self.load()
        result={'revision':data['revision'],'features':deepcopy(SCHEMA['features']),'states':{},'session':{}}
        for key,d in SCHEMA['states'].items():
            f=data['features'].get(d['feature'],{})
            result['states'][key]={**d,'active':upgrade_spec(key,f.get('active',{}).get(key,default(key))),
                'draft':upgrade_spec(key,f.get('draft',{}).get(key,f.get('active',{}).get(key,default(key)))),
                'has_draft':key in f.get('draft',{}),'can_rollback':bool(f.get('previous'))}
        session=self.queue/'session.json'
        if session.exists(): result['session']=json.loads(session.read_text())
        return result

    def mutate(self,operation,feature,key=None,spec=None,revision=None):
        # 网页、CLI 和其他进程共用文件锁，再用 revision 防止旧页面覆盖。
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        lock=self.path.with_suffix('.lock')
        if lock.is_symlink(): raise ValueError('存储锁无效')
        fd=os.open(lock,os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(fd,'r+b') as f:
            if os.name == 'nt':
                import msvcrt
                # Windows 可锁定 EOF 之后的字节；不能先读已被另一进程锁住的字节。
                f.seek(0)
                msvcrt.locking(f.fileno(),msvcrt.LK_LOCK,1)
            else:
                import fcntl
                fcntl.flock(f,fcntl.LOCK_EX)
            data=self.load()
            if revision!=data['revision']: raise ValueError('内容已由另一个页面更新，请重新载入')
            keys=[k for k,d in SCHEMA['states'].items() if d['feature']==feature]
            if not keys: raise ValueError('功能不存在或尚未实现')
            group=data['features'].setdefault(feature,{'active':{},'draft':{},'previous':{}})
            if operation=='save':
                if key not in keys: raise ValueError('状态不属于该功能')
                group['draft'][key]=validate(key,spec)
            elif operation=='publish':
                active={k:group['active'].get(k,default(k)) for k in keys}
                updated={**active,**group['draft']}
                for k,v in updated.items(): validate(k,v)
                group['previous']=active; group['active']=updated; group['draft']={}
            elif operation=='rollback':
                if not group['previous']: raise ValueError('该功能没有可恢复的上一版')
                group['active'],group['previous']=group['previous'],{k:group['active'].get(k,default(k)) for k in keys}
            else: raise ValueError('操作不支持')
            data['revision']+=1
            if len(json.dumps(data,ensure_ascii=False,indent=2).encode('utf-8'))>2_000_000:
                raise ValueError('模板总量超过存储上限，本次未保存')
            self.atomic(self.path,data)
        return self.state()

    def active(self,key):
        try:
            group=self.load()['features'].get(SCHEMA['states'][key]['feature'],{})
            return validate(key,upgrade_spec(key,group.get('active',{}).get(key,default(key))))
        except (OSError,ValueError,KeyError,TypeError): return default(key)

    def preview(self,key,request_id,revision):
        if not re.fullmatch(r'[a-zA-Z0-9-]{12,64}',request_id): raise ValueError('预览请求编号无效')
        state=self.state()
        if key not in state['states']: raise ValueError('预览状态无效')
        if revision!=state['revision']: raise ValueError('草稿已变化，请重新载入')
        spec=validate(key,state['states'][key]['draft'])
        path=self.queue/(request_id+'.json')
        if path.exists(): return json.loads(path.read_text())
        item={'id':request_id,'key':key,'spec':spec,'created':time.time(),'status':'awaiting_session',
              'note':'请先在现有获准 QQ 私聊发送：输出预览'}
        self.queue.mkdir(parents=True,exist_ok=True,mode=0o700)
        if len(list(self.queue.glob('*.json'))) > 500: raise ValueError('预览记录已满')
        try:
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        except FileExistsError:
            return json.loads(path.read_text(encoding='utf-8'))
        with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump(item,f,ensure_ascii=False)
        return item

class Output(str):
    def __new__(cls,value,key,mode):
        obj=super().__new__(cls,value); obj.key=key; obj.mode=mode; return obj

def present(key,values=None,*,plain=False):
    overrides=OVERRIDE.get()
    spec=overrides.get(key) if overrides and key in overrides else Store().active(key)
    values=dict(values or {})
    definition = SCHEMA['states'][key]
    for name, source in definition.get('aliases', {}).items():
        values.setdefault(name, values.get(source, ''))
    cooked={name:(str(values.get(name,'')) if name in spec['visible'] else '') for name in SCHEMA['states'][key]['fields']}
    root = definition.get('preview_root') if definition.get('fragment') else None
    root_spec = (overrides.get(root) if overrides and root in overrides else Store().active(root)) if root else spec
    if root_spec['mode']=='markdown' and not plain:
        cooked={k:(v if k in SCHEMA['states'][key].get('blocks',[]) else escape(v)) for k,v in cooked.items()}
    return Output(spec['template'].format_map(cooked),key,'text' if plain else spec['mode'])

def arrange(key,buttons):
    overrides=OVERRIDE.get()
    spec=overrides.get(key) if overrides and key in overrides else Store().active(key)
    by_role={}
    for role,button in buttons:
        b=deepcopy(button)
        b['render_data']['label']=b['render_data']['visited_label']=spec['labels'][role]
        by_role[role]=b
    return [{'buttons':[by_role[r] for r in row if r in by_role]} for row in spec['rows'] if any(r in by_role for r in row)]

@contextmanager
def draft(key,spec):
    token=OVERRIDE.set({key:validate(key,spec)})
    try: yield
    finally: OVERRIDE.reset(token)
