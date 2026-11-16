"""Presentation-only templates. No executable expressions, action edits or secrets."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
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
    if not isinstance(template,str) or not 0 < len(template) <= 12000: raise ValueError('模板长度应为 1–12000 字符')
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
            result['states'][key]={**d,'active':f.get('active',{}).get(key,default(key)),
                'draft':f.get('draft',{}).get(key,f.get('active',{}).get(key,default(key))),
                'has_draft':key in f.get('draft',{}),'can_rollback':bool(f.get('previous'))}
        session=self.queue/'session.json'
        if session.exists(): result['session']=json.loads(session.read_text())
        return result

    def mutate(self,operation,feature,key=None,spec=None,revision=None):
        # CLI calls are serialized by a short advisory lock, protecting separate tabs.
        import fcntl
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        lock=self.path.with_suffix('.lock')
        if lock.is_symlink(): raise ValueError('存储锁无效')
        fd=os.open(lock,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as f:
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
            self.atomic(self.path,data)
        return self.state()

    def active(self,key):
        try:
            group=self.load()['features'].get(SCHEMA['states'][key]['feature'],{})
            return validate(key,group.get('active',{}).get(key,default(key)))
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
    values=values or {}
    cooked={name:(str(values.get(name,'')) if name in spec['visible'] else '') for name in SCHEMA['states'][key]['fields']}
    if spec['mode']=='markdown' and not plain:
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
