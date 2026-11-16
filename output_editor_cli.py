"""Fixed SSH entry point for non-secret presentation settings only."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from hikari_bot.qq_official.output_templates import Store

def dispatch(request):
    store=Store();operation=request.get('operation')
    if operation=='state': return store.state()
    if operation in {'save','publish','rollback'}:
        return store.mutate(operation,request.get('feature'),request.get('key'),request.get('spec'),request.get('revision'))
    if operation=='preview': return store.preview(request.get('key'),request.get('id'),request.get('revision'))
    if operation=='status':
        state=store.state();items=[]
        for path in sorted(store.queue.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True):
            if path.name=='session.json': continue
            try:
                item=json.loads(path.read_text(encoding='utf-8'))
                items.append({k:item[k] for k in ('id','key','status','note','created') if k in item})
            except (ValueError,OSError): continue
            if len(items)==20:break
        return {'session':state['session'],'requests':items}
    raise ValueError('操作不支持')

if __name__=='__main__':
    try:
        raw=sys.stdin.buffer.read(65537)
        if len(raw)>65536: raise ValueError('请求过长')
        result={'ok':True,'data':dispatch(json.loads(raw))}
    except (ValueError,KeyError,TypeError): result={'ok':False,'error':'输入无效、版本冲突或没有可恢复版本，请刷新核对。'}
    except Exception: result={'ok':False,'error':'私有输出存储暂不可用；未返回内部信息。'}
    print(json.dumps(result,ensure_ascii=True))
