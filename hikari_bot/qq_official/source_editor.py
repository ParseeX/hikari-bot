"""源码草稿与发布请求。检查只编译语法，不导入、不运行用户代码。"""
import ast
from contextlib import contextmanager
import difflib
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from .source_catalog import files, checked_path
from .output_templates import Store


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def check(name, text):
    if not isinstance(text, str) or not text.strip() or len(text.encode('utf-8')) > 1_000_000:
        raise ValueError('文件不能为空，最多 1 MB')
    try:
        if name.endswith('.py'):
            compile(text.encode('utf-8'), name, 'exec', dont_inherit=True)
        else:
            value = json.loads(text)
            if not isinstance(value, dict) or not isinstance(value.get('features'), dict) or not isinstance(value.get('states'), dict):
                raise ValueError('文案定义必须包含 features 和 states')
            # 不执行渲染器；仅验证完整目录仍可被现有页面读取。
            for key, item in value['states'].items():
                if not isinstance(item, dict) or not all(k in item for k in ('feature','template','fields','samples','label')):
                    raise ValueError('文案定义缺少必要字段')
                if item['feature'] not in value['features']:
                    raise ValueError('文案所属功能不存在')
    except SyntaxError as exc:
        return {'ok': False, 'message': f'第 {exc.lineno} 行，第 {exc.offset or 1} 列：Python 语法错误', 'line': exc.lineno}
    except json.JSONDecodeError as exc:
        return {'ok': False, 'message': f'第 {exc.lineno} 行，第 {exc.colno} 列：JSON 格式错误', 'line': exc.lineno}
    return {'ok': True, 'message': '语法检查通过；尚未执行代码，不代表业务正确。'}


class SourceStore:
    def __init__(self, home=None, root=None):
        self.home = Path(home) if home else Path.home()
        self.root = Path(root) if root else Path(__file__).resolve().parents[2]
        self.directory = self.home/'.local/share/srbs/source-editor'
        self.path = self.directory/'state.json'
        self.writer = Store(self.home)

    @contextmanager
    def locked(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = self.directory/'state.lock'
        if lock.is_symlink():
            raise ValueError('源码锁路径无效')
        fd = os.open(lock, os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0), 0o600)
        with os.fdopen(fd, 'r+b') as f:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX)
            yield

    def load(self):
        if not self.path.exists():
            return {'revision':0, 'drafts':{}, 'job':None, 'last_success':None}
        if self.path.is_symlink() or self.path.stat().st_size > 16_000_000:
            raise ValueError('源码草稿存储无效')
        return json.loads(self.path.read_text(encoding='utf-8'))

    def write(self, state):
        if len(json.dumps(state).encode()) > 16_000_000:
            raise ValueError('源码草稿总量超过上限')
        self.writer.atomic(self.path, state)

    def current(self, name):
        return checked_path(self.root, name).read_text(encoding='utf-8')

    def listing(self, feature):
        state = self.load()
        return {'revision':state['revision'], 'files':[{'path':p, 'has_draft':p in state['drafts']} for p in files(feature)],
                'job':self.public_job(state.get('job')), 'can_rollback':bool(state.get('last_success'))}

    @staticmethod
    def public_job(job):
        if not job:
            return None
        return {k:job[k] for k in ('id','status','note','commit','pushed','operation') if k in job}

    def read(self, feature, name):
        if name not in files(feature):
            raise ValueError('该源码不属于当前功能')
        state = self.load()
        current = self.current(name)
        saved = state['drafts'].get(name)
        draft = saved['text'] if saved else current
        from .source_catalog import SOURCES, SCHEMA_PATH
        affected = list(SOURCES) if name == SCHEMA_PATH else [f for f, names in SOURCES.items() if name in names]
        anchors = []
        if name.endswith('.py'):
            try:
                tree = ast.parse(draft.encode('utf-8'))
                anchors = [{'name':n.name,'line':n.lineno} for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
            except SyntaxError:
                pass
        else:
            try:
                definitions = json.loads(draft)['states']
                lines = draft.splitlines()
                for key, definition in definitions.items():
                    if definition.get('feature') == feature:
                        token = json.dumps(key,ensure_ascii=False)+':'
                        line = next((i+1 for i,text in enumerate(lines) if text.strip().startswith(token)),1)
                        anchors.append({'name':definition['label'],'line':line})
            except (ValueError,KeyError,TypeError,AttributeError):
                pass
        return {'path':name, 'revision':state['revision'], 'base':saved['base'] if saved else digest(current),
                'current':current, 'text':draft, 'has_draft':bool(saved), 'conflict':bool(saved and saved['base']!=digest(current)),
                'affected':affected, 'anchors':anchors,
                'diff':''.join(difflib.unified_diff(current.splitlines(True),draft.splitlines(True),fromfile='当前代码',tofile='草稿'))}

    def save(self, feature, name, text, base, revision):
        if name not in files(feature):
            raise ValueError('该源码不属于当前功能')
        # 可以保存有语法错误的草稿，发布前必须修好。
        if not isinstance(text,str) or len(text.encode('utf-8')) > 1_000_000:
            raise ValueError('文件最多 1 MB')
        with self.locked():
            state = self.load()
            self.ensure_idle(state, revision)
            if digest(self.current(name)) != base:
                raise ValueError('线上源码已变化，请重新载入后合并修改')
            state['drafts'][name] = {'base':base,'text':text}
            state['revision'] += 1
            self.write(state)
        return self.read(feature, name)

    @staticmethod
    def ensure_idle(state, revision):
        if state['revision'] != revision:
            raise ValueError('草稿或发布状态已变化，请重新载入')
        if state.get('job') and state['job']['status'] in {'pending','running','recovery_required'}:
            raise ValueError('已有发布任务进行中或待恢复，请先查看发布状态')

    def enqueue(self, feature, revision, operation='publish'):
        with self.locked():
            state = self.load()
            self.ensure_idle(state, revision)
            selected = {p:d for p,d in state['drafts'].items() if p in files(feature)}
            if operation == 'publish':
                selected = {p:d for p,d in selected.items() if d['text'] != self.current(p)}
                if not selected:
                    raise ValueError('本功能没有待发布的源码改动')
                for name, draft in selected.items():
                    if digest(self.current(name)) != draft['base']:
                        raise ValueError('线上源码已变化，请先合并草稿')
                    result = check(name, draft['text'])
                    if not result['ok']:
                        raise ValueError(name+'：'+result['message'])
            elif operation != 'rollback' or not state.get('last_success'):
                raise ValueError('没有可回退的源码发布')
            state['job'] = {'id':uuid.uuid4().hex, 'operation':operation, 'feature':feature, 'status':'pending',
                            'note':'等待发布器处理；保存草稿不会影响线上。', 'created':time.time(),
                            'release':str(self.root.resolve()), 'files':selected if operation=='publish' else {},
                            'previous':state.get('last_success') if operation=='rollback' else None}
            state['revision'] += 1
            self.write(state)
        return self.listing(feature)
