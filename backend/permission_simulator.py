"""Independent LOCAL TEST effect. No OS/LDAP/cloud/GLPI permission operations."""
import os,json,hashlib,hmac,re,sys,uuid,asyncio
import httpx
from datetime import datetime
from fastapi import FastAPI,Request,HTTPException
from pydantic import BaseModel,ConfigDict,Field,ValidationError
from sqlalchemy import create_engine,String,JSON,text
from sqlalchemy.orm import DeclarativeBase,Mapped,mapped_column,Session
from starlette.concurrency import run_in_threadpool
KEY=os.environ.get('PERMISSION_SERVICE_KEY','')
engine=create_engine(os.environ['PERMISSION_DATABASE_URL'],pool_pre_ping=True,hide_parameters=True)
class Base(DeclarativeBase):pass
class Grant(Base):
    __tablename__='simulated_grants'
    id:Mapped[str]=mapped_column(String(36),primary_key=True)
    request_id:Mapped[str]=mapped_column(String(36),unique=True)
    payload_hash:Mapped[str]=mapped_column(String(64))
    payload:Mapped[dict]=mapped_column(JSON)
class Payload(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    grant_id:str=Field(pattern='^[a-f0-9-]{36}$')
    request_id:str=Field(pattern='^[a-f0-9-]{36}$')
    version:int=Field(gt=0)
    scope_hash:str=Field(pattern='^[a-f0-9]{64}$')
    subject_id:int=Field(gt=0)
    resource:str=Field(pattern='^TEST-office-share$')
    permission:str=Field(pattern='^(read|contribute)$')
def auth(r):
    if len(KEY)<32 or len(r.headers.getlist('x-permission-key'))!=1 or not hmac.compare_digest(r.headers.get('X-Permission-Key',''),KEY):raise HTTPException(401,'permission_service_denied')
def view(row):return {'grant_id':row.id,'payload_hash':row.payload_hash,'status':'SIMULATED_GRANTED'}
app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
@app.get('/internal/grants/{gid}')
def get(gid:str,request:Request):
    auth(request)
    with Session(engine) as db:
        row=db.get(Grant,gid)
        if not row:raise HTTPException(404,'grant_not_found')
        return view(row)
def store(p):
    payload=p.model_dump();sha=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    with Session(engine) as db:
        db.execute(text('select pg_advisory_xact_lock(:key)'),{'key':int.from_bytes(hashlib.sha256(p.request_id.encode()).digest()[:8],'big',signed=True)})
        prior=db.get(Grant,p.grant_id)
        if prior:
            if prior.payload_hash!=sha:raise HTTPException(409,'grant_payload_conflict')
            return view(prior)
        try:
            with httpx.Client(timeout=2,trust_env=False,follow_redirects=False) as c:r=c.get('http://api:8080/internal/access/'+p.request_id+'/authorization',headers={'X-Permission-Key':KEY})
            verified=r.json()
            if r.status_code!=200 or not isinstance(verified,dict) or set(verified)!={'grant','not_after'} or verified['grant']!=payload or datetime.fromisoformat(verified['not_after']).tzinfo is None:raise ValueError()
        except (httpx.HTTPError,ValueError,TypeError):raise HTTPException(403,'permission_approval_denied') from None
        # The effect statement itself checks the trusted deadline, including delay
        # after the source HTTP check. No cross-service clock guess grants access.
        inserted=db.execute(text('INSERT INTO simulated_grants(id,request_id,payload_hash,payload) SELECT :id,:request,:hash,CAST(:payload AS json) WHERE clock_timestamp()<CAST(:deadline AS timestamptz)'),
            {'id':p.grant_id,'request':p.request_id,'hash':sha,'payload':json.dumps(payload),'deadline':verified['not_after']})
        if inserted.rowcount!=1:raise HTTPException(403,'permission_approval_expired')
        db.commit();return view(db.get(Grant,p.grant_id))
@app.post('/internal/grants')
async def grant(request:Request):
    auth(request)
    if request.headers.get('content-type','').split(';')[0]!='application/json':raise HTTPException(415,'content_type_denied')
    raw=b''
    async for part in request.stream():
        raw+=part
        if len(raw)>2048:raise HTTPException(413,'body_too_large')
    try:p=Payload.model_validate(json.loads(raw));assert str(uuid.UUID(p.grant_id))==p.grant_id and str(uuid.UUID(p.request_id))==p.request_id
    except (ValueError,ValidationError,AssertionError):raise HTTPException(422,'grant_schema_denied') from None
    result=await run_in_threadpool(store,p)
    if os.environ.get('PERMISSION_TEST_LOSE_RESPONSE')=='true':await asyncio.sleep(5)
    return result
if __name__=='__main__' and sys.argv[1]=='init':
    with engine.connect() as c:
        assert c.scalar(text('select current_database()'))=='serviceops_permissions'
        assert c.scalar(text('select system_identifier::text from pg_control_system()'))!='7696146962828046370'
    Base.metadata.create_all(engine);print('Independent SIMULATED permission schema ready; no enterprise operations.')
