"""Trusted F06 authority; models do not approve or receive execution credentials."""
import os,uuid,hashlib,json,hmac
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import select
import httpx
from contextvars import ContextVar
from backend.models import User,Operation,utcnow
from backend.ai_models import Conversation,AgentEvent
from backend.access_models import AccessRequest,AccessDecision,ApproverScope
from backend.db import SessionLocal
from backend import service

RESOURCE='TEST-office-share';PERMISSIONS={'read','contribute'}
KEY=os.environ.get('PERMISSION_SERVICE_KEY','')
URL='http://permission:8091'
REQUEST_ID=ContextVar('access_http_request_id',default=None)
def enabled():
    if os.environ.get('ACCESS_APPROVAL_ENABLED')!='true' or len(KEY)<32:raise HTTPException(503,'access_workflow_disabled')
def sha(data):return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def scope(row):return sha({'subject_id':row.user_id,'resource':row.resource,'permission':row.permission,'reason':row.reason,'version':row.version,'operation_id':row.operation_id})
def event(db,row,node,outcome):
    # Replayed graph nodes do not manufacture repeated approvals.
    key={'access_version':row.version}
    filters=[AgentEvent.conversation_id==row.conversation_id,AgentEvent.node==node,AgentEvent.outcome==outcome,AgentEvent.trace_data['access_version'].as_integer()==row.version]
    if node=='permission_execute':
        key['execution_attempt']=row.attempts or 0;filters.append(AgentEvent.trace_data['execution_attempt'].as_integer()==key['execution_attempt'])
    exists=db.scalar(select(AgentEvent.id).where(*filters))
    if not exists:db.add(AgentEvent(conversation_id=row.conversation_id,turn_id=row.id,request_id=REQUEST_ID.get() or str(uuid.uuid4()),node=node,outcome=outcome,trace_data=key))
def view(row):
    return {k:getattr(row,k) for k in ('id','conversation_id','user_id','resource','permission','reason','operation_id','version','scope_hash','phase','decision_id','grant_id','error','attempts')}|{'expires_at':row.expires_at.isoformat(),'mode':'INDEPENDENT SIMULATED PERMISSION ONLY'}
def owned(db,user,rid):
    row=db.get(AccessRequest,rid)
    if not row or row.user_id!=user.id:raise HTTPException(404,'access_request_not_found')
    return row
def approver(db,actor,row):
    live=db.get(User,actor.id)
    if not live or live.role!='approver' or live.id==row.user_id or not db.get(ApproverScope,(live.id,row.resource)):
        raise HTTPException(403,'approval_scope_denied')
def valid_approval(db,row):
    if row.resource!=RESOURCE or row.permission not in PERMISSIONS:return False
    d=db.get(AccessDecision,row.decision_id) if row.decision_id else None
    if not d or (d.request_id,d.version,d.scope_hash,d.action)!=(row.id,row.version,row.scope_hash,'approve'):return False
    actor=db.get(User,d.actor_id)
    try:approver(db,actor,row)
    except (HTTPException,AttributeError):return False
    return row.scope_hash==scope(row) and row.expires_at>utcnow()
def matching(row,version,digest):
    if row.version!=version or not hmac.compare_digest(row.scope_hash,digest):raise HTTPException(409,'approval_version_changed')
def create(user,payload,key):
    enabled();identity=sha(payload)
    with service.locked(user.id,'access-create-'+key) as db:
        prior=db.scalar(select(AccessRequest).where(AccessRequest.user_id==user.id,AccessRequest.request_key==key))
        if prior:
            if prior.creation_hash!=identity:raise HTTPException(409,'access_idempotency_conflict')
            return view(prior)
        if payload.get('operation_id'):
            op=db.get(Operation,payload['operation_id'])
            if not op or op.user_id!=user.id or op.phase!='complete':raise HTTPException(404,'access_operation_not_found')
        cid=str(uuid.uuid4());db.add(Conversation(id=cid,user_id=user.id,state={'workflow':'access-approval','messages':[]},phase='approval_wait',expires_at=utcnow()+timedelta(days=7)));db.flush()
        row=AccessRequest(id=str(uuid.uuid4()),conversation_id=cid,user_id=user.id,request_key=key,creation_hash=identity,
            **payload,version=1,scope_hash='',grant_id=str(uuid.uuid4()),expires_at=utcnow()+timedelta(hours=24))
        row.scope_hash=scope(row);db.add(row);db.flush();event(db,row,'approval_wait','approval_pending');db.commit()
    run(row.id);return get(user,row.id)
def get(user,rid):
    with SessionLocal() as db:return view(owned(db,user,rid))
def change(user,rid,version,digest,payload):
    enabled()
    with service.locked(-5,'access-'+rid) as db:
        row=owned(db,user,rid);matching(row,version,digest)
        if row.phase in ('executing','execution_unknown','executed'):raise HTTPException(409,'execution_locked')
        if row.expires_at<=utcnow():raise HTTPException(409,'approval_expired')
        for k,v in payload.items():setattr(row,k,v)
        row.version+=1;row.scope_hash=scope(row);row.phase='pending';row.decision_id=None;row.error=None
        event(db,row,'approval_wait','approval_pending');db.commit();return view(row)
def decide(actor,rid,version,digest,action):
    enabled()
    with service.locked(-5,'access-'+rid) as db:
        row=db.get(AccessRequest,rid)
        if not row:raise HTTPException(404,'access_request_not_found')
        approver(db,actor,row);matching(row,version,digest)
        prior=db.scalar(select(AccessDecision).where(AccessDecision.request_id==rid,AccessDecision.version==version,AccessDecision.action==action))
        if prior:return view(row)
        if row.expires_at<=utcnow():raise HTTPException(409,'approval_expired')
        if row.phase!=('approved' if action=='revoke' else 'pending'):raise HTTPException(409,'approval_not_pending')
        d=AccessDecision(id=str(uuid.uuid4()),request_id=rid,version=row.version,scope_hash=row.scope_hash,actor_id=actor.id,action=action)
        db.add(d);row.decision_id=d.id;row.phase={'approve':'approved','reject':'rejected','revoke':'revoked'}[action]
        event(db,row,'approval_gate','approval_'+row.phase);db.commit();return view(row)
def grant_payload(row):
    return {'grant_id':row.grant_id,'request_id':row.id,'version':row.version,'scope_hash':row.scope_hash,
        'subject_id':row.user_id,'resource':row.resource,'permission':row.permission}
def execute(rid,client=None):
    enabled()
    with service.locked(-5,'access-'+rid) as db:
        row=db.get(AccessRequest,rid)
        if not row:raise HTTPException(404,'access_request_not_found')
        if row.phase=='executed':return view(row)
        if row.phase not in ('approved','executing','execution_unknown'):return view(row)
        managed=client is None;client=client or httpx.Client(timeout=3,trust_env=False,follow_redirects=False)
        payload=grant_payload(row);digest=sha(payload)
        def receipt(response):
            if response.status_code==404:return None
            data=response.json()
            if response.status_code!=200 or data!={'grant_id':row.grant_id,'payload_hash':digest,'status':'SIMULATED_GRANTED'}:raise ValueError('receipt_unverified')
            return data
        try:
            # Recovery checks the effect before expiry/authority; it never grants again.
            found=receipt(client.get(URL+'/internal/grants/'+row.grant_id,headers={'X-Permission-Key':KEY}))
            if not found:
                if not valid_approval(db,row):
                    row.phase='expired' if row.expires_at<=utcnow() else 'denied';event(db,row,'permission_execute','permission_denied');db.commit();return view(row)
                if row.attempts>=3:raise ValueError('attempt_limit')
                row.attempts+=1;row.phase='executing';event(db,row,'permission_execute','permission_intent');db.commit()
                response=client.post(URL+'/internal/grants',json=payload,headers={'X-Permission-Key':KEY})
                found=receipt(client.get(URL+'/internal/grants/'+row.grant_id,headers={'X-Permission-Key':KEY}))
                if not found:raise ValueError('receipt_missing')
            row.phase='executed';row.receipt_hash=digest;row.error=None;event(db,row,'permission_execute','permission_verified');db.commit()
        except (httpx.HTTPError,ValueError,TypeError):
            row.phase='execution_unknown';row.error='permission_reconciliation_required';event(db,row,'permission_execute','permission_unknown');db.commit()
        finally:
            if managed:client.close()
        return view(row)
def run(rid):
    from backend.access_graph import build
    from backend.checkpoints import saver
    from langgraph.types import Command
    with service.locked(-6,'access-graph-'+rid) as db:
        row=db.get(AccessRequest,rid)
        if not row:raise HTTPException(404,'access_request_not_found')
        with saver() as cp:
            graph=build(cp);cfg={'configurable':{'thread_id':row.conversation_id},'recursion_limit':6}
            state=graph.get_state(cfg)
            if state.next and row.phase=='pending':return # no human decision, no resume
            if state.next:event(db,row,'approval_resume','approval_resume_requested');db.commit()
            graph.invoke(Command(resume={'wake_only':True}) if state.next else {'request_id':rid},cfg)
            if state.next:event(db,row,'approval_resume','approval_resumed');db.commit()
