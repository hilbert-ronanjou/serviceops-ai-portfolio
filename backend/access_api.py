"""F01/F06 trusted human desk. Disabled until a local deployment provisions roles."""
import hmac
from typing import Literal
from fastapi import APIRouter,Request,HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select
from backend.auth import authenticate,login,digest
from backend.config import settings
from backend.db import SessionLocal
from backend.models import Session
from backend.access_models import AccessRequest,ApproverScope
from backend import access_service as authority

router=APIRouter();COOKIE='serviceops_approval'
class Strict(BaseModel):model_config=ConfigDict(extra='forbid',strict=True)
class Credentials(Strict):
    username:str=Field(min_length=1,max_length=100)
    password:str=Field(min_length=1,max_length=200)
class Scope(Strict):
    resource:Literal['TEST-office-share']
    permission:Literal['read','contribute']
    reason:str=Field(min_length=5,max_length=400)
class Create(Scope):operation_id:str|None=Field(None,pattern='^[a-f0-9-]{36}$')
class Binding(Strict):
    version:int=Field(gt=0,le=1000)
    scope_hash:str=Field(pattern='^[a-f0-9]{64}$')
class Edit(Scope,Binding):pass
class Decision(Binding):action:Literal['approve','reject','revoke']
class Empty(Strict):pass
def person(request,write=False):return authenticate(request,cookie=COOKIE,required_role='approver',write=write)

@router.post('/api/approvals/login')
def sign_in(body:Credentials):
    authority.enabled();u,token,csrf=login(body.username,body.password,'approver')
    result=JSONResponse({'username':u.username,'role':u.role,'csrf_token':csrf})
    result.set_cookie(COOKIE,token,httponly=True,samesite='strict',secure=settings.secure_cookie,max_age=28800);return result
@router.post('/api/approvals/logout')
def sign_out(request:Request,body:Empty):
    person(request,True)
    with SessionLocal() as db:
        row=db.get(Session,digest(request.cookies[COOKIE]));db.delete(row);db.commit()
    result=JSONResponse({'logged_out':True});result.delete_cookie(COOKIE);return result
@router.get('/api/approvals')
def list_approvals(request:Request):
    authority.enabled();actor=person(request)
    with SessionLocal() as db:
        resources=list(db.scalars(select(ApproverScope.resource).where(ApproverScope.user_id==actor.id)))
        rows=db.scalars(select(AccessRequest).where(AccessRequest.resource.in_(resources),AccessRequest.user_id!=actor.id).order_by(AccessRequest.created_at.desc()).limit(50))
        return [authority.view(row) for row in rows]
@router.post('/api/approvals/{rid}/decision')
def decision(rid:str,body:Decision,request:Request):return authority.decide(person(request,True),rid,body.version,body.scope_hash,body.action)
@router.post('/api/access')
def create(body:Create,request:Request):
    actor=authenticate(request,write=True);key=request.headers.get('Idempotency-Key','')
    if not (8<=len(key)<=100 and all(c.isalnum() or c in '-_' for c in key)):raise HTTPException(422,'invalid_idempotency_key')
    return authority.create(actor,body.model_dump(),key)
@router.get('/api/access')
def list_owned(request:Request):
    authority.enabled();actor=authenticate(request)
    with SessionLocal() as db:return [authority.view(r) for r in db.scalars(select(AccessRequest).where(AccessRequest.user_id==actor.id).order_by(AccessRequest.created_at.desc()).limit(50))]
@router.get('/api/access/{rid}')
def get(rid:str,request:Request):authority.enabled();return authority.get(authenticate(request),rid)
@router.get('/api/access/{rid}/checkpoint')
def checkpoint(rid:str,request:Request):
    authority.enabled();row=authority.get(authenticate(request),rid)
    from backend.checkpoints import saver
    from backend.access_graph import build
    with saver() as cp:
        state=build(cp).get_state({'configurable':{'thread_id':row['conversation_id']}})
        return {'next':list(state.next),'interrupted':any(task.interrupts for task in state.tasks),'authority':'Approval is reloaded from trusted PG; resume is only a wake-up.'}
@router.patch('/api/access/{rid}')
def edit(rid:str,body:Edit,request:Request):return authority.change(authenticate(request,write=True),rid,body.version,body.scope_hash,body.model_dump(exclude={'version','scope_hash'}))
@router.post('/api/access/{rid}/resume')
def resume(rid:str,body:Empty,request:Request):
    authority.enabled();actor=authenticate(request,write=True);authority.get(actor,rid)
    authority.run(rid);return authority.get(actor,rid)
@router.get('/internal/access/{rid}/authorization')
def authorization(rid:str,request:Request):
    authority.enabled()
    if len(request.headers.getlist('x-permission-key'))!=1 or not hmac.compare_digest(request.headers.get('X-Permission-Key',''),authority.KEY):raise HTTPException(401,'permission_service_denied')
    with SessionLocal() as db:
        row=db.get(AccessRequest,rid)
        if not row or row.phase not in ('executing','execution_unknown') or not authority.valid_approval(db,row):raise HTTPException(403,'permission_approval_denied')
        return {'grant':authority.grant_payload(row),'not_after':row.expires_at.isoformat()}
