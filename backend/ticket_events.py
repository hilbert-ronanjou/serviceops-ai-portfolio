"""GLPI 11.0.11 HMAC inbox, authoritative recheck and bounded idempotent delivery.

Native payload is a signed wake-up, never a requester/state authority. Outbox reads
are rechecked at dispatch; sink receipts distinguish delivery from HTTP acceptance.
"""
import hashlib,hmac,json,os,re,time,uuid
from datetime import datetime,timezone
from typing import Literal
import httpx
from fastapi import APIRouter,Request,HTTPException
from pydantic import BaseModel,ConfigDict,Field,ValidationError,field_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from backend.db import SessionLocal
from backend.models import Operation,User,utcnow
from backend.event_models import TicketEvent,TicketNotice,TicketEventCursor
from backend.auth import authenticate
from backend.adapter import Upstream
from backend.config import settings
from backend import service

router=APIRouter()
STATES={1:'new',2:'assigned',3:'planned',4:'pending',5:'resolved',6:'closed',10:'approval_pending'}
SOURCE=os.environ.get('GLPI_EVENT_SOURCE','')
SECRET=os.environ.get('GLPI_WEBHOOK_SECRET','')
N8N_URL=os.environ.get('EVENT_N8N_URL','')
N8N_KEY=os.environ.get('EVENT_N8N_KEY','')
SINK=os.environ.get('NOTIFICATION_URL','')
SINK_KEY=os.environ.get('NOTIFICATION_KEY','')
MAX_BODY=4096
WINDOW=600

class NativeEvent(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    event:Literal['new','update']
    itemtype:Literal['Ticket']
    ticket_id:int=Field(gt=0,le=2147483647)
    entity_id:int=Field(ge=0,le=2147483647)
    status:Literal[1,2,3,4,5,6,10]
    @field_validator('status',mode='before')
    @classmethod
    def integer_status(cls,value):
        if type(value) is not int:raise ValueError('integer_required')
        return value

def enabled():
    if (os.environ.get('TICKET_EVENTS_ENABLED')!='true' or not re.fullmatch('[a-z0-9-]{1,60}',SOURCE)
        or min(len(SECRET),len(N8N_KEY),len(SINK_KEY))<32
        or N8N_URL!='http://n8n:5678/webhook/serviceops-ticket-state' or SINK!='http://notification:8090'):
        raise HTTPException(503,'ticket_events_disabled')

def canonical(data):return json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
def digest(data):return hashlib.sha256(data).hexdigest()
def revision(value):
    try:
        dt=datetime.fromisoformat(value.replace('Z','+00:00'))
        if dt.tzinfo is None:raise ValueError()
        return dt.astimezone(timezone.utc).isoformat(timespec='seconds')
    except (ValueError,AttributeError,TypeError):raise Upstream('event_source_revision_unavailable') from None

def transition(row,state,reason=None):
    row.state=state;row.reason=reason
    row.trail=[*(row.trail or [])[-39:],{'at':utcnow().isoformat(),'state':state,'reason':reason}]

def verify_signature(raw,timestamp,signature,now=None):
    if not re.fullmatch('[0-9]{10}',timestamp or '') or not re.fullmatch('[a-f0-9]{64}',signature or ''):
        raise HTTPException(401,'webhook_signature_denied')
    delta=(time.time() if now is None else now)-int(timestamp)
    if delta>WINDOW or delta< -30:raise HTTPException(401,'webhook_timestamp_denied')
    if not hmac.compare_digest(hmac.new(SECRET.encode(),raw+timestamp.encode(),hashlib.sha256).hexdigest(),signature):
        raise HTTPException(401,'webhook_signature_denied')

def parse(raw):
    def pairs(items):
        data={}
        for k,v in items:
            if k in data:raise ValueError()
            data[k]=v
        return data
    try:return NativeEvent.model_validate(json.loads(raw,object_pairs_hook=pairs))
    except (ValueError,ValidationError,TypeError):raise HTTPException(422,'webhook_schema_denied') from None

def source_snapshot(db,tid):
    operations=list(db.scalars(select(Operation).where(Operation.native_id==tid)))
    if len(operations)!=1:raise Upstream('event_operation_unverified')
    op=operations[0]
    if op.phase!='complete':raise Upstream('event_operation_pending')
    user=db.get(User,op.user_id)
    if not user or user.role!='employee':raise Upstream('event_owner_unverified')
    native=service.adapter.ticket(tid);members=service.adapter.requesters(tid)
    if (native.get('entity',{}).get('id')!=settings.entity_id or len(members)!=1
        or members[0].get('type')!='User' or members[0].get('id')!=user.glpi_user_id):
        raise Upstream('event_ownership_mismatch',403)
    status=native.get('status',{}).get('id')
    if status not in STATES:raise Upstream('event_status_unverified')
    return op,user,revision(native.get('date_mod')),status

def notice_payload(notice):
    return {'notice_id':notice.id,'source_id':notice.source,'ticket_id':notice.ticket_id,
        'entity_id':settings.entity_id,'recipient_id':notice.user_id,'status':notice.status,
        'revision':notice.revision,'message':f'Ticket #{notice.ticket_id}: {STATES[notice.status]}. Local TEST notification.'}

def validate_received(event_id):
    with SessionLocal() as lookup:
        item=lookup.get(TicketEvent,event_id)
        tid=item.ticket_id
    with service.locked(-4,'ticket-event-'+str(tid)) as db:
        event=db.get(TicketEvent,event_id)
        if event.source!=SOURCE:raise HTTPException(403,'event_source_denied')
        if event.notice_id or event.state in ('coalesced','stale'):return event.state
        try:op,user,version,status=source_snapshot(db,tid)
        except Upstream as exc:
            transition(event,'awaiting_source',exc.code);db.commit();return event.state
        event.operation_id=op.id;event.payload_status_mismatch=event.payload['status']!=status
        transition(event,'validated')
        cursor=db.get(TicketEventCursor,(SOURCE,tid))
        if cursor and version<cursor.revision:
            transition(event,'stale','source_revision_older');db.commit();return event.state
        if cursor and version==cursor.revision:
            if status!=cursor.status:
                transition(event,'awaiting_source','same_second_revision_ambiguous')
            else:event.notice_id=cursor.notice_id;transition(event,'coalesced','same_trusted_snapshot')
            db.commit();return event.state
        notice=TicketNotice(id=str(uuid.uuid4()),event_id=event.id,source=SOURCE,ticket_id=tid,user_id=user.id,
            revision=version,status=status,payload_hash='',state='queued',trail=[])
        notice.payload_hash=digest(canonical(notice_payload(notice)))
        transition(notice,'queued');db.add(notice)
        if not cursor:db.add(TicketEventCursor(source=SOURCE,ticket_id=tid,revision=version,status=status,notice_id=notice.id))
        else:cursor.revision=version;cursor.status=status;cursor.notice_id=notice.id
        event.notice_id=notice.id;transition(event,'queued');db.commit();return event.state

@router.post('/internal/glpi/ticket-events',status_code=202)
async def receive(request:Request):
    enabled()
    if request.headers.get('content-type','').split(';')[0].strip().lower()!='application/json':raise HTTPException(415,'webhook_content_type_denied')
    if len(request.headers.getlist('x-glpi-timestamp'))!=1 or len(request.headers.getlist('x-glpi-signature'))!=1:raise HTTPException(401,'webhook_signature_denied')
    raw=b''
    async for chunk in request.stream():
        raw+=chunk
        if len(raw)>MAX_BODY:raise HTTPException(413,'webhook_body_too_large')
    ts=request.headers['x-glpi-timestamp'];verify_signature(raw,ts,request.headers['x-glpi-signature'])
    body=parse(raw)
    if body.entity_id!=settings.entity_id:raise HTTPException(403,'webhook_entity_denied')
    fingerprint=digest(SOURCE.encode()+b'\0'+ts.encode()+b'\0'+raw)
    with SessionLocal() as db:
        row={'id':str(uuid.uuid4()),'source':SOURCE,'fingerprint':fingerprint,'body_sha256':digest(raw),
            'signed_at':int(ts),'payload':body.model_dump(),'ticket_id':body.ticket_id,'state':'received',
            'trail':[{'state':'received','at':utcnow().isoformat(),'reason':None}]}
        db.execute(insert(TicketEvent).values(**row).on_conflict_do_nothing(index_elements=['fingerprint']))
        db.commit();event=db.scalar(select(TicketEvent).where(TicketEvent.fingerprint==fingerprint));event_id=event.id
    state=validate_received(event_id)
    # Received durably is not the same as queued or delivered. Native GLPI may retry 503.
    if state=='awaiting_source':raise HTTPException(503,'webhook_source_pending')
    return {'event_id':event_id,'state':state,'delivery':'NOT_CONFIRMED'}

@router.get('/api/notifications')
def notifications(request:Request):
    enabled()
    user=authenticate(request)
    with SessionLocal() as db:
        rows=db.scalars(select(TicketNotice).where(TicketNotice.user_id==user.id).order_by(TicketNotice.created_at.desc()).limit(50)).all()
        return [{'id':n.id,'ticket_id':n.ticket_id,'status':STATES[n.status],'revision':n.revision,
            'delivery_state':n.state,'delivery_outcome':(n.receipt or {}).get('outcome'),'message':notice_payload(n)['message']} for n in rows]

@router.get('/internal/notifications/{notice_id}/eligibility')
def eligibility(notice_id:str,request:Request):
    enabled()
    if len(request.headers.getlist('x-notification-key'))!=1 or not hmac.compare_digest(request.headers.get('x-notification-key',''),SINK_KEY):
        raise HTTPException(401,'notification_auth_denied')
    with SessionLocal() as db:
        row=db.get(TicketNotice,notice_id)
        if not row or row.source!=SOURCE:raise HTTPException(404,'notice_not_found')
        try:_,user,version,status=source_snapshot(db,row.ticket_id)
        except Upstream:raise HTTPException(503,'notice_source_pending') from None
        return {'eligible':(user.id,version,status)==(row.user_id,row.revision,row.status),'payload_hash':row.payload_hash}

def get_receipt(client,notice):
    response=client.get(SINK+'/internal/receipts/'+notice.id,headers={'X-Notification-Key':SINK_KEY})
    if response.status_code==404:return None
    if response.status_code!=200:raise Upstream('receipt_unavailable')
    receipt=response.json()
    if (not isinstance(receipt,dict) or set(receipt)!={'notice_id','payload_hash','outcome','execution_id'}
        or receipt.get('notice_id')!=notice.id or receipt.get('payload_hash')!=notice.payload_hash
        or receipt.get('outcome') not in ('delivered','suppressed_stale','held_ambiguous')
        or not isinstance(receipt.get('execution_id'),str) or not re.fullmatch('[0-9]{1,20}',receipt['execution_id'])):
        raise Upstream('receipt_mismatch')
    return receipt

def dispatch_one(notice_id,client=None):
    enabled()
    with SessionLocal() as lookup:
        row=lookup.get(TicketNotice,notice_id)
        if not row:return
        tid=row.ticket_id
    with service.locked(-4,'ticket-event-'+str(tid)) as db:
        row=db.get(TicketNotice,notice_id)
        if row.source!=SOURCE or row.state in ('acknowledged','superseded'):return
        managed=client is None;client=client or httpx.Client(timeout=3,trust_env=False,follow_redirects=False)
        try:
            # Always reconcile first. Retry only because sink guarantees durable ID/hash dedup.
            receipt=get_receipt(client,row)
            if receipt:row.receipt=receipt;transition(row,'acknowledged');db.commit();return
            _,user,version,status=source_snapshot(db,tid)
            if (user.id,version,status)!=(row.user_id,row.revision,row.status):
                transition(row,'superseded','source_changed_before_dispatch');db.commit();return
            if row.attempts>=5:transition(row,'failed','attempt_limit_review_required');db.commit();return
            row.attempts+=1;transition(row,'dispatched');db.commit()
            response=client.post(N8N_URL,json=notice_payload(row),headers={'X-ServiceOps-Event-Key':N8N_KEY})
            # HTTP acceptance alone is never delivery. Independently read durable sink receipt.
            receipt=get_receipt(client,row)
            if receipt:row.receipt=receipt;transition(row,'acknowledged')
            else:transition(row,'delivery_unknown','n8n_accepted_no_receipt' if response.status_code<300 else 'n8n_not_confirmed')
            db.commit()
        except Upstream as exc:
            transition(row,'delivery_unknown',exc.code);db.commit()
        except ValueError:
            transition(row,'delivery_unknown','receipt_response_invalid');db.commit()
        except httpx.HTTPError:
            transition(row,'delivery_unknown','notification_transport_unknown');db.commit()
        finally:
            if managed:client.close()

def dispatch_pending():
    enabled()
    with SessionLocal() as db:
        pending=list(db.scalars(select(TicketEvent.id).where(TicketEvent.source==SOURCE,TicketEvent.state.in_(['received','awaiting_source'])).order_by(TicketEvent.created_at).limit(20)))
    for event_id in pending:validate_received(event_id)
    with SessionLocal() as db:
        pending=list(db.scalars(select(TicketNotice.id).where(TicketNotice.source==SOURCE,TicketNotice.state.in_(['queued','dispatched','delivery_unknown'])).order_by(TicketNotice.created_at).limit(20)))
    for notice_id in pending:dispatch_one(notice_id)
    return len(pending)

if __name__=='__main__':
    import sys
    if sys.argv[1]=='dispatch':print('Processed notices:',dispatch_pending())
    elif sys.argv[1]=='worker':
        while True:
            try:dispatch_pending()
            except Exception:print('ticket_event_worker: dependency_unavailable',flush=True)
            time.sleep(3)
    else:raise SystemExit('Expected dispatch or worker')
