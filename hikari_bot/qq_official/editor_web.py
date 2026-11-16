"""独立文案后台：复用密码记录，隔离登录会话，不载入旧 Bot 插件。"""
import asyncio
import json
import os
import re
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from hikari_bot.services.mycard_archive.admin_auth import AdminAuth, LoginLimited, LoginRejected, SESSION_SECONDS
from .output_templates import Store, SCHEMA, default, draft, present, validate

COOKIE = '__Host-hikari_editor'
STATIC = Path(__file__).with_name('editor_static')


class Login(BaseModel):
    model_config = ConfigDict(extra='forbid')
    password: str = Field(min_length=1, max_length=1024)


class Change(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation: str
    feature: str = Field(max_length=100)
    key: str | None = Field(default=None, max_length=160)
    spec: dict | None = None
    revision: int = Field(ge=0)


class Preview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: str = Field(max_length=160)
    spec: dict
    example: str | None = Field(default=None, max_length=80)


class QQPreview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: str = Field(max_length=160)
    revision: int = Field(ge=0)


def create_app(*, store=None, auth=None, origin='https://bot.xyk.one'):
    store = store or Store()
    if auth is None:
        root = Path.home() / '.local/share/srbs/editor'
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        auth = AdminAuth(root)
        # 路径由部署配置给出，只读复用 MyCard 密码，绝不复制或重设密码。
        auth.password_path = Path(os.environ['HIKARI_EDITOR_PASSWORD_FILE'])
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(RequestValidationError)
    async def invalid_body(request, error):
        # FastAPI 默认错误体包含 input；登录字段不能被原样反射。
        return JSONResponse({'detail': '请求字段或格式无效'}, status_code=422)

    @app.middleware('http')
    async def protect(request, call_next):
        if request.method not in {'GET', 'HEAD'} and request.headers.get('origin') != origin:
            response = JSONResponse({'detail': '请求来源无效'}, status_code=403)
        elif request.headers.get('content-length', '').isdigit() and int(request.headers['content-length']) > 65536:
            response = JSONResponse({'detail': '请求过大'}, status_code=413)
        else:
            # 包括无 Content-Length 的分块请求，先限长再交给 JSON 解析。
            size, chunks = 0, []
            async for chunk in request.stream():
                size += len(chunk)
                if size > 65536:
                    break
                chunks.append(chunk)
            if size > 65536:
                response = JSONResponse({'detail': '请求过大'}, status_code=413)
            else:
                request._body = b''.join(chunks)
                response = await call_next(request)
        response.headers.update({
            'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
            'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        })
        return response

    async def require(request):
        try:
            valid = await asyncio.to_thread(auth.valid, request.cookies.get(COOKIE))
        except (OSError, ValueError):
            raise HTTPException(503, '管理密码配置暂不可用') from None
        if not valid:
            raise HTTPException(401, '请先登录')

    def state():
        result = store.state()
        result['session'] = {}  # 不向网页暴露 QQ 预览会话；此后台只做网页预览。
        for key, value in result['states'].items():
            value['default'] = default(key)
        return result

    @app.get('/')
    async def index():
        return FileResponse(STATIC / 'index.html')

    @app.get('/assets/{name}')
    async def asset(name: str):
        if name not in {'editor.css', 'editor.js'}:
            raise HTTPException(404)
        return FileResponse(STATIC / name)

    @app.get('/api/session')
    async def session(request: Request):
        await require(request)
        return {'authenticated': True}

    @app.post('/api/login')
    async def login(body: Login, request: Request, response: Response):
        try:
            token = await asyncio.to_thread(auth.login, body.password, request.client.host)
        except LoginLimited:
            raise HTTPException(429, '尝试过多，请 15 分钟后重试', headers={'Retry-After': '900'}) from None
        except LoginRejected:
            raise HTTPException(401, '密码错误') from None
        except (OSError, ValueError, KeyError):
            raise HTTPException(503, '管理密码配置暂不可用') from None
        response.set_cookie(COOKIE, token, secure=True, httponly=True, samesite='strict', max_age=SESSION_SECONDS, path='/')
        return {'authenticated': True}

    @app.post('/api/logout')
    async def logout(request: Request, response: Response):
        await asyncio.to_thread(auth.logout, request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, secure=True, httponly=True, samesite='strict', path='/')
        return {'authenticated': False}

    @app.get('/api/templates')
    async def templates(request: Request):
        await require(request)
        return await asyncio.to_thread(state)

    @app.post('/api/change')
    async def change(body: Change, request: Request):
        await require(request)
        if body.operation not in {'save', 'publish', 'rollback'}:
            raise HTTPException(400, '操作无效')
        try:
            await asyncio.to_thread(store.mutate, body.operation, body.feature, body.key, body.spec, body.revision)
            return await asyncio.to_thread(state)
        except (ValueError, KeyError, TypeError) as error:
            # 只返回本地验证文案，不反射外部正文或内部路径。
            if isinstance(error, ValueError):
                raise HTTPException(409 if '另一个页面' in str(error) else 400, str(error)) from None
            raise HTTPException(400, '模板字段无效') from None

    @app.post('/api/preview')
    async def preview(body: Preview, request: Request):
        await require(request)
        try:
            spec = validate(body.key, body.spec)
            from .editor_examples import render_example
            return render_example(store, body.key, spec, body.example)
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400, '预览失败，请检查变量和模板格式') from None

    @app.post('/api/qq-preview')
    async def qq_preview(body: QQPreview, request: Request):
        await require(request)
        try:
            item = await asyncio.to_thread(store.preview, body.key, uuid.uuid4().hex, body.revision)
            return {key: item[key] for key in ('id', 'status', 'note')}
        except (ValueError, KeyError, TypeError):
            raise HTTPException(409, '草稿已变化或预览不可用，请重新载入') from None

    @app.get('/api/qq-preview/{request_id}')
    async def qq_preview_status(request_id: str, request: Request):
        await require(request)
        if not re.fullmatch(r'[a-f0-9]{32}', request_id):
            raise HTTPException(404)
        try:
            text = await asyncio.to_thread((store.queue / (request_id + '.json')).read_text, encoding='utf-8')
            item = json.loads(text)
            return {key: item[key] for key in ('id', 'status', 'note')}
        except (OSError, ValueError, KeyError):
            raise HTTPException(404, '没有找到这条预览请求') from None

    return app
