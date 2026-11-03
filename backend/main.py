import json
import logging
import time
import uuid
from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from backend.config import settings
from backend.db import SessionLocal
from backend.models import Operation, Session, Audit
from backend.auth import authenticate, login, COOKIE, digest
from backend.adapter import Upstream
from backend import service
from backend.ai_config import AIError
from backend.agent_api import router as agent_router
from backend.kb_api import router as knowledge_router
from backend.ticket_events import router as ticket_events_router
from backend.access_api import router as access_router


class SafeFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({'event': record.getMessage(), **getattr(record, 'safe', {})})


handler = logging.StreamHandler()
handler.setFormatter(SafeFormatter())
logger = logging.getLogger('serviceops')
logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(agent_router)
app.include_router(knowledge_router)
app.include_router(ticket_events_router)
app.include_router(access_router)


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Login(Strict):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class Ticket(Strict):
    title: str = Field(min_length=3, max_length=160)
    description: str = Field(min_length=5, max_length=10000)
    category: str = Field(pattern='^(vpn|software|network)$')


@app.middleware('http')
async def request_boundary(request, call_next):
    request.state.request_id = str(uuid.uuid4())
    start = time.perf_counter()
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.headers.get('Origin') not in (None, settings.employee_origin):
        response = JSONResponse({'error': {'code': 'origin_denied'}}, 403)
    elif int(request.headers.get('Content-Length', '0')) > 24000:
        response = JSONResponse({'error': {'code': 'body_too_large'}}, 413)
    else:
        from backend.access_service import REQUEST_ID
        context_token=REQUEST_ID.set(request.state.request_id)
        try:response = await call_next(request)
        finally:REQUEST_ID.reset(context_token)
    elapsed = int((time.perf_counter() - start) * 1000)
    response.headers['X-Request-ID'] = request.state.request_id
    if request.url.path.startswith(('/api/knowledge','/api/agent','/api/notifications','/internal/notifications','/api/access','/api/approvals','/internal/access')):
        response.headers['Cache-Control'] = 'no-store'
    # Template route prevents attacker-controlled path/query/body/credentials in logs.
    action = getattr(request.scope.get('route'), 'path', 'unknown_route')
    safe = {'request_id': request.state.request_id, 'method': request.method, 'route': action,
            'status': response.status_code, 'elapsed_ms': elapsed, 'user_id': getattr(request.state, 'user_id', None)}
    logger.info('request', extra={'safe': safe})
    with SessionLocal() as db:
        db.add(Audit(request_id=safe['request_id'], user_id=safe['user_id'], action=action[:60],
                     outcome=str(response.status_code), elapsed_ms=elapsed))
        db.commit()
    return response


def error(request, code, status):
    return JSONResponse({'error': {'code': code}, 'request_id': request.state.request_id}, status)


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    return error(request, str(exc.detail), exc.status_code)


@app.exception_handler(RequestValidationError)
async def invalid(request, exc):
    # Validation details can echo passwords and body values. Keep envelope safe.
    return error(request, 'invalid_input', 422)


@app.exception_handler(Upstream)
async def upstream(request, exc):
    return error(request, exc.code, exc.status)


@app.exception_handler(AIError)
async def ai_error(request,exc):
    return error(request,exc.code,exc.status)


@app.get('/api/health')
def health():
    with SessionLocal() as db:
        db.execute(text('SELECT 1'))
    return {'status': 'ready', 'stage': 'M0/M1/M2'}


@app.post('/api/login')
def sign_in(body: Login):
    user, token, csrf = login(body.username, body.password, 'employee')
    response = JSONResponse({'username': user.username, 'role': user.role, 'csrf_token': csrf})
    response.set_cookie(COOKIE, token, httponly=True, samesite='strict', secure=settings.secure_cookie, max_age=28800)
    return response


@app.get('/api/me')
def me(request: Request):
    user = authenticate(request)
    return {'username': user.username, 'role': user.role}


@app.post('/api/logout')
def logout(request: Request):
    authenticate(request, write=True)
    with SessionLocal() as db:
        session = db.get(Session, digest(request.cookies.get(COOKIE, '')))
        db.delete(session)
        db.commit()
    response = JSONResponse({'logged_out': True})
    response.delete_cookie(COOKIE)
    return response


@app.post('/api/tickets')
def create_ticket(body: Ticket, request: Request):
    user = authenticate(request, write=True)
    key = request.headers.get('Idempotency-Key', '')
    if not (8 <= len(key) <= 100) or not all(c.isalnum() or c in '-_' for c in key):
        raise HTTPException(422, 'invalid_idempotency_key')
    result, replay = service.create(user, body.model_dump(), key, request.state.request_id)
    return JSONResponse(result, status_code=200 if replay else 201)


@app.get('/api/tickets')
def tickets(request: Request):
    user = authenticate(request)
    with SessionLocal() as db:
        rows = db.scalars(select(Operation).where(Operation.user_id == user.id).order_by(Operation.created_at.desc())).all()
        result = []
        for row in rows:
            try:
                result.append(service.view(row, user))
            except Upstream as exc:
                # Only the owned local intent is acknowledged. No unverified
                # GLPI fields or persisted operation state are returned/changed.
                result.append({'id': row.id, 'request_key': row.request_key,
                               'phase': 'read_unavailable', 'title': '工单暂不可读取',
                               'error_code': 'ticket_read_unavailable'})
                db.add(Audit(request_id=request.state.request_id, operation_id=row.id,
                             user_id=user.id, action='ticket_list_item', outcome=exc.code[:60]))
        db.commit()
        return result


@app.get('/api/tickets/{ticket_id}')
def ticket(ticket_id: str, request: Request):
    user = authenticate(request)
    with SessionLocal() as db:
        return service.view(service.owned(db, user, ticket_id), user, detail=True)
