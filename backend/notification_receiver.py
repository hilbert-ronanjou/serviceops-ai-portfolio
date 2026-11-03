"""Isolated local notification effect: durable receipt and ID/hash idempotency.

This is a TEST notification endpoint, never an enterprise email/chat integration.
"""
import asyncio,hashlib,hmac,json,os,re,sys,uuid
import httpx
from datetime import datetime,timezone
from typing import Literal
from fastapi import FastAPI,Request,HTTPException
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel,ConfigDict,Field,ValidationError,field_validator
from sqlalchemy import create_engine,String,Integer,JSON,text
from sqlalchemy.orm import DeclarativeBase,Mapped,mapped_column,Session

KEY=os.environ.get('NOTIFICATION_KEY','');SOURCE=os.environ.get('GLPI_EVENT_SOURCE','')
ENTITY=int(os.environ.get('GLPI_ENTITY_ID','0'))
engine=create_engine(os.environ.get('NOTIFICATION_DATABASE_URL',''),pool_pre_ping=True,hide_parameters=True)
class Base(DeclarativeBase):pass
class Receipt(Base):
    __tablename__='notification_receipts'
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    payload_hash:Mapped[str]=mapped_column(String(64))
    payload:Mapped[dict]=mapped_column(JSON)
    outcome:Mapped[str]=mapped_column(String(30))
    execution_id:Mapped[str]=mapped_column(String(20))
    delivered_at:Mapped[str]=mapped_column(String(40))
class Cursor(Base):
    __tablename__='notification_cursors'
    source:Mapped[str]=mapped_column(String(60),primary_key=True)
    ticket_id:Mapped[int]=mapped_column(Integer,primary_key=True)
    revision:Mapped[str]=mapped_column(String(40))
    status:Mapped[int]=mapped_column(Integer)

class Notice(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    notice_id:str=Field(pattern='^[a-f0-9-]{36}$')
    source_id:str=Field(max_length=60)
    ticket_id:int=Field(gt=0,le=2147483647)
    entity_id:int=Field(ge=0)
    recipient_id:int=Field(gt=0)
    status:Literal[1,2,3,4,5,6,10]
    revision:str=Field(max_length=40)
    message:str=Field(max_length=160)
    @field_validator('status',mode='before')
    @classmethod
    def integer_status(cls,value):
        if type(value) is not int:raise ValueError('integer_required')
        return value

STATES={1:'new',2:'assigned',3:'planned',4:'pending',5:'resolved',6:'closed',10:'approval_pending'}
def authorize(request):
    if len(KEY)<32 or len(request.headers.getlist('x-notification-key'))!=1 or not hmac.compare_digest(request.headers.get('x-notification-key',''),KEY):
        raise HTTPException(401,'notification_auth_denied')
def view(row):return {'notice_id':row.id,'payload_hash':row.payload_hash,'outcome':row.outcome,'execution_id':row.execution_id}
app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)

@app.get('/internal/receipts/{notice_id}')
def receipt(notice_id:str,request:Request):
    authorize(request)
    try:notice_id=str(uuid.UUID(notice_id))
    except ValueError:raise HTTPException(422,'invalid_notice_id') from None
    with Session(engine) as db:
        row=db.get(Receipt,notice_id)
        if not row:raise HTTPException(404,'receipt_not_found')
        return view(row)

@app.post('/internal/deliver')
async def deliver(request:Request):
    authorize(request)
    if request.headers.get('content-type','').split(';')[0]!='application/json':raise HTTPException(415,'content_type_denied')
    raw=b''
    async for part in request.stream():
        raw+=part
        if len(raw)>4096:raise HTTPException(413,'body_too_large')
    try:
        body=Notice.model_validate(json.loads(raw));assert str(uuid.UUID(body.notice_id))==body.notice_id
        rev=datetime.fromisoformat(body.revision)
        assert rev.tzinfo is not None and rev.astimezone(timezone.utc).isoformat(timespec='seconds')==body.revision
    except (ValueError,ValidationError,AssertionError):raise HTTPException(422,'notice_schema_denied') from None
    execution=request.headers.get('x-n8n-execution-id','')
    if not re.fullmatch('[0-9]{1,20}',execution):raise HTTPException(422,'n8n_execution_id_required')
    if body.source_id!=SOURCE or body.entity_id!=ENTITY:raise HTTPException(403,'notice_source_denied')
    if body.message!=f'Ticket #{body.ticket_id}: {STATES[body.status]}. Local TEST notification.':raise HTTPException(422,'notice_message_denied')
    result=await run_in_threadpool(store,body,execution)
    # Explicit TEST-only response-loss injection, after the real durable effect.
    delay=int(os.environ.get('NOTIFICATION_TEST_DELAY_MS','0')) if os.environ.get('NOTIFICATION_TEST_MODE')=='true' else 0
    if delay:await asyncio.sleep(min(max(delay,0),10000)/1000)
    return result

def store(body,execution):
    # Synchronous SQL locks/network work must not block the ASGI event loop:
    # another duplicate request may otherwise block the lock holder's callback.
    payload=body.model_dump();sha=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    with Session(engine) as db:
        lock=int.from_bytes(hashlib.sha256((SOURCE+':'+str(body.ticket_id)).encode()).digest()[:8],'big',signed=True)
        db.execute(text('select pg_advisory_xact_lock(:key)'),{'key':lock})
        prior=db.get(Receipt,body.notice_id)
        if prior:
            if prior.payload_hash!=sha:raise HTTPException(409,'notice_id_payload_conflict')
            return view(prior)
        cursor=db.get(Cursor,(SOURCE,body.ticket_id));outcome='delivered'
        # Recheck source at the effect boundary, without giving the sink GLPI credentials.
        try:
            with httpx.Client(timeout=2,trust_env=False,follow_redirects=False) as client:
                checked=client.get('http://api:8080/internal/notifications/'+body.notice_id+'/eligibility',headers={'X-Notification-Key':KEY})
            if checked.status_code!=200 or set(checked.json())!={'eligible','payload_hash'} or type(checked.json()['eligible']) is not bool or not re.fullmatch('[a-f0-9]{64}',checked.json()['payload_hash']):
                raise ValueError()
        except (httpx.HTTPError,ValueError,TypeError):raise HTTPException(503,'source_eligibility_unavailable') from None
        if checked.json()['payload_hash']!=sha:raise HTTPException(403,'notice_payload_denied')
        if not checked.json()['eligible']:outcome='suppressed_stale'
        elif cursor and body.revision<cursor.revision:outcome='suppressed_stale'
        elif cursor and body.revision==cursor.revision:
            outcome='suppressed_stale' if body.status==cursor.status else 'held_ambiguous'
        else:
            if not cursor:db.add(Cursor(source=SOURCE,ticket_id=body.ticket_id,revision=body.revision,status=body.status))
            else:cursor.revision=body.revision;cursor.status=body.status
        row=Receipt(id=body.notice_id,payload_hash=sha,payload=payload,outcome=outcome,execution_id=execution,delivered_at=datetime.now(timezone.utc).isoformat())
        db.add(row);db.commit();result=view(row)
    return result

if __name__=='__main__' and sys.argv[1]=='init':
    with engine.connect() as c:
        assert c.scalar(text('select current_database()'))=='serviceops_notifications'
        assert c.scalar(text('select system_identifier::text from pg_control_system()'))!='7696146962828046370'
    Base.metadata.create_all(engine)
    print('Independent local TEST notification receipt schema ready.')
