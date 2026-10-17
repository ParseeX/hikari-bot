"""MyCard 管理后台：独立密码登录和主卡组分类映射。"""
from pathlib import Path
import sqlite3
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, StrictInt

from hikari_bot.core.config import settings
from hikari_bot.services.card_catalog import CardCatalog
from hikari_bot.services.mycard_archive.admin_auth import (
    AdminAuth, LoginLimited, LoginRejected, SESSION_SECONDS,
)
from hikari_bot.services.mycard_archive.rule_admin import RuleAdmin, StaleRules, representative_cards
from hikari_bot.services.mycard_archive.art import card_art


router = APIRouter()
COOKIE = '__Host-mc_admin'
ORIGIN = 'https://ygo.xyk.one'
STATIC = Path(__file__).parents[1] / 'static'
HEADERS = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
           'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
           'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; "
                                      "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}


def auth():
    return AdminAuth(settings.mycard_archive_dir)


def same_origin(request: Request):
    if request.headers.get('origin') != ORIGIN:
        raise HTTPException(403, '请求来源不允许')


def admin(request: Request, response: Response):
    response.headers.update(HEADERS)
    if request.method != 'GET':
        same_origin(request)
    try:
        valid = auth().valid(request.cookies.get(COOKIE))
    except (OSError, ValueError, sqlite3.Error):
        raise HTTPException(503, '管理登录暂不可用') from None
    if not valid:
        raise HTTPException(401, '请先登录')


def rules():
    return RuleAdmin(settings.mycard_archive_dir / 'deck-rules.json')


def catalog():
    return CardCatalog(settings.card_catalog_path)


async def refresh_series(request: Request):
    cache = getattr(request.app.state, 'mc_series_cache', None)
    if cache is not None:
        cache.request_refresh()


@router.get('/mc/admin', include_in_schema=False)
def page():
    return FileResponse(STATIC / 'mycard-admin.html', headers=HEADERS)


@router.get('/mc/admin/app.js', include_in_schema=False)
def javascript():
    return FileResponse(STATIC / 'mycard-admin.js', media_type='text/javascript', headers=HEADERS)


@router.get('/mc/admin/style.css', include_in_schema=False)
def stylesheet():
    return FileResponse(STATIC / 'mycard-admin.css', media_type='text/css', headers=HEADERS)


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


@router.post('/mc/admin/api/login', dependencies=[Depends(same_origin)])
def login(body: Login, request: Request, response: Response):
    response.headers.update(HEADERS)
    try:
        token = auth().login(body.password, request.client.host if request.client else 'unknown')
    except LoginLimited as exc:
        raise HTTPException(429, str(exc), headers={'Retry-After': '900'}) from None
    except LoginRejected as exc:
        raise HTTPException(401, str(exc)) from None
    except FileNotFoundError:
        raise HTTPException(503, '管理员密码尚未设置') from None
    except (OSError, ValueError, sqlite3.Error):
        raise HTTPException(503, '管理登录暂不可用') from None
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, secure=True,
                        httponly=True, samesite='strict', path='/')
    return {'ok': True}


@router.post('/mc/admin/api/logout', dependencies=[Depends(admin)])
def logout(request: Request, response: Response):
    auth().logout(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, secure=True, httponly=True, samesite='strict', path='/')
    return {'ok': True}


def card_info(card):
    return {'id': card['id'], 'name': card.get('cn_name') or card.get('sc_name') or
            card.get('jp_name') or str(card['id'])}


def main_deck_card(card):
    card_type = (card.get('data') or {}).get('type') or 0
    # 融合、同调、超量、连接及衍生物均不能作为主卡组分类条件。
    excluded = 0x40 | 0x2000 | 0x800000 | 0x4000000 | 0x4000
    return bool(card_type & 0x7) and not card_type & excluded


@router.get('/mc/admin/api/cards', dependencies=[Depends(admin)])
def search_card(q: str = Query(min_length=1, max_length=100), purpose: Literal['rule', 'representative'] = 'rule'):
    try:
        cards = [card_info(card) for card in catalog().search_many(q.strip())
                 if purpose == 'representative' or main_deck_card(card)]
    except (OSError, sqlite3.Error):
        raise HTTPException(503, '卡片资料暂不可用') from None
    # 保留首项字段，让部署前已打开的旧页面仍能查卡；新页面使用完整列表。
    return {'cards': cards, 'card': cards[0] if cards else None}


@router.get('/mc/admin/api/rules', dependencies=[Depends(admin)])
def list_rules():
    try:
        data, revision = rules().read()
        effective = representative_cards(data, catalog())
        names = {}
        for rule in data['rules']:
            for code in rule['cards']:
                if code not in names:
                    card = catalog().by_id(code)
                    names[code] = card_info(card)['name'] if card else str(code)
        for code in effective.values():
            card = catalog().by_id(code)
            names[code] = card_info(card)['name'] if card else str(code)
    except (OSError, ValueError, sqlite3.Error):
        raise HTTPException(503, '规则或卡片资料读取失败') from None
    return {'revision': revision, 'rules': data['rules'], 'names': names,
            'representatives': data.get('representatives', {}), 'effective_representatives': effective}


class RepresentativeEdit(BaseModel):
    revision: str = Field(min_length=64, max_length=64)
    label: str = Field(min_length=1, max_length=60)
    card_id: StrictInt | None = Field(default=None, gt=0, lt=2**27)


@router.post('/mc/admin/api/representatives', dependencies=[Depends(admin)])
def save_representative(body: RepresentativeEdit, request: Request, background_tasks: BackgroundTasks):
    try:
        if body.card_id is not None and catalog().by_id(body.card_id) is None:
            raise ValueError('请选择有效卡片')
        rules().representative(body.revision, body.label, body.card_id)
    except StaleRules as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except (OSError, sqlite3.Error):
        raise HTTPException(503, '保存失败，请稍后重试') from None
    background_tasks.add_task(refresh_series, request)
    return {'ok': True}


@router.get('/mc/admin/api/art/{code}', dependencies=[Depends(admin)])
async def preview_art(code: int):
    if not 0 < code < 2**27:
        raise HTTPException(404, '卡片不存在')
    data = await card_art(code)
    if not data:
        raise HTTPException(404, '图片暂不可用')
    return Response(data, media_type='image/jpeg', headers=HEADERS)


class RuleEdit(BaseModel):
    revision: str = Field(min_length=64, max_length=64)
    index: int | None = Field(default=None, ge=0)
    label: str = Field(min_length=1, max_length=60)
    cards: list[StrictInt] = Field(min_length=1)
    match: Literal['any'] = 'any'


@router.post('/mc/admin/api/rules', dependencies=[Depends(admin)])
def save_rule(body: RuleEdit, request: Request, background_tasks: BackgroundTasks):
    label = body.label.strip()
    if not label or any(ord(c) < 32 for c in label):
        raise HTTPException(400, '分类名称无效')
    try:
        for code in body.cards:
            if not 0 < code < 2**27 or catalog().by_id(code) is None:
                raise ValueError('请选择有效卡片')
        rules().change(body.revision, index=body.index,
                       rule={'label': label, 'cards': list(dict.fromkeys(body.cards)), 'match': body.match})
    except StaleRules as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except (OSError, sqlite3.Error):
        raise HTTPException(503, '保存失败，请稍后重试') from None
    background_tasks.add_task(refresh_series, request)
    return {'ok': True}


class RuleDelete(BaseModel):
    revision: str = Field(min_length=64, max_length=64)
    index: int = Field(ge=0)


@router.post('/mc/admin/api/rules/delete', dependencies=[Depends(admin)])
def delete_rule(body: RuleDelete, request: Request, background_tasks: BackgroundTasks):
    try:
        rules().change(body.revision, index=body.index)
    except StaleRules as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except OSError:
        raise HTTPException(503, '保存失败，请稍后重试') from None
    background_tasks.add_task(refresh_series, request)
    return {'ok': True}
