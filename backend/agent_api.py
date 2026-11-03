from typing import Literal
from fastapi import APIRouter,Request,Query
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select,func
from backend.auth import authenticate
from backend.db import SessionLocal
from backend.ai_models import Conversation,KnowledgeChunk
from backend.ai_config import config,AIError,CHAT_MODEL,EMBED_MODEL
from backend.cost import CostLedger
from backend import agent_service
import json
from pathlib import Path


def model_hold():
    # Public project decision, never a credential. Absent/invalid means HOLD.
    try:
        decision=json.loads((Path(__file__).resolve().parents[1]/'evaluation/live-execution.HOLD.json').read_text())
        return decision.get('live_execution_authorized') is not True
    except (OSError,ValueError,AttributeError):return True

router=APIRouter(prefix='/api/agent')
class Strict(BaseModel):model_config=ConfigDict(extra='forbid',strict=True)
class Empty(Strict):pass
class Message(Strict):
    message:str=Field(min_length=1,max_length=900)
    client_request_id:str=Field(pattern='^[A-Za-z0-9_-]{8,100}$')
class Confirm(Strict):
    summary_id:str=Field(min_length=36,max_length=36)
    summary_hash:str=Field(pattern='^[a-f0-9]{64}$')
    confirmed:Literal[True]
class Cancel(Strict):summary_id:str=Field(min_length=36,max_length=36)


@router.get('/status')
def status(request:Request):
    authenticate(request)
    try:
        if model_hold():raise AIError('model_live_calls_disabled')
        config.validate();configuration='ready'
    except AIError as exc:configuration=exc.code
    from backend.knowledge import corpus
    version,chunks=corpus()
    with SessionLocal() as db:
        indexed=db.scalar(select(func.count()).select_from(KnowledgeChunk).where(
            KnowledgeChunk.corpus_version==version,KnowledgeChunk.embedding_mode=='live'))
    try:budget=CostLedger().status();budget_availability='available'
    except AIError as exc:
        budget=None;budget_availability=exc.code
        if configuration=='ready':configuration=exc.code
    return {'configuration':configuration,'chat_model':CHAT_MODEL,'embedding_model':EMBED_MODEL,
        'knowledge':{'corpus_version':version,'chunks':len(chunks),'live_indexed':indexed,'ready':indexed==len(chunks)},
        'budget':budget,'budget_availability':budget_availability}


@router.post('/conversations')
def create(body:Empty,request:Request):return agent_service.create_conversation(authenticate(request,write=True))


@router.get('/conversations')
def list_conversations(request:Request):
    user=authenticate(request)
    with SessionLocal() as db:
        rows=db.scalars(select(Conversation).where(Conversation.user_id==user.id).order_by(Conversation.updated_at.desc()).limit(50)).all()
        return [{'id':row.id,'phase':row.phase,'turn_count':row.turn_count,'updated_at':row.updated_at.isoformat()} for row in rows]


@router.get('/conversations/{conversation_id}')
def get(conversation_id:str,request:Request):
    user=authenticate(request)
    with SessionLocal() as db:return agent_service.view(agent_service.owned(db,user,conversation_id))


@router.get('/conversations/{conversation_id}/trace')
def trace(conversation_id:str,request:Request,after_id:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=100)):
    from backend.agent_trace import project
    user=authenticate(request)
    with SessionLocal() as db:
        return project(db,agent_service.owned(db,user,conversation_id),after_id,limit)


@router.post('/conversations/{conversation_id}/messages')
def message(conversation_id:str,body:Message,request:Request):
    user=authenticate(request,write=True)
    with SessionLocal() as db:agent_service.owned(db,user,conversation_id)
    if model_hold():raise AIError('model_live_calls_disabled')
    return agent_service.send_message(user,conversation_id,body.message,
        body.client_request_id,request.state.request_id)


@router.post('/conversations/{conversation_id}/confirm')
def confirm(conversation_id:str,body:Confirm,request:Request):
    return agent_service.confirm(authenticate(request,write=True),conversation_id,body.summary_id,
        body.summary_hash,request.state.request_id)


@router.post('/conversations/{conversation_id}/cancel')
def cancel(conversation_id:str,body:Cancel,request:Request):
    return agent_service.cancel(authenticate(request,write=True),conversation_id,body.summary_id)
