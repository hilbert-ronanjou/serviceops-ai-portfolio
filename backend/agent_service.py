"""Owned conversations, durable turn intent and explicit version-bound confirmation."""
import hashlib
import json
import uuid
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import select
from backend.db import SessionLocal
from backend.models import User,Operation,utcnow
from backend.ai_models import Conversation,AgentTurn
from backend.ai_config import AIError
from backend.model_gateway import ModelGateway
from backend.agent_graph import build_graph,event
from backend.checkpoints import saver
from backend.tools import safe_text
from backend import service

MAX_TURNS=20
MAX_STATE_BYTES=64000


def owned(db,user,conversation_id):
    conversation=db.scalar(select(Conversation).where(Conversation.id==conversation_id,Conversation.user_id==user.id))
    if not conversation:raise HTTPException(404,'agent_conversation_not_found')
    if conversation.expires_at<=utcnow():raise HTTPException(410,'agent_conversation_expired')
    return conversation

def scenario_a(row):
    if row.state.get('workflow')=='access-approval':raise HTTPException(409,'conversation_workflow_mismatch')


def create_conversation(user):
    with SessionLocal() as db:
        row=Conversation(id=str(uuid.uuid4()),user_id=user.id,state={'messages':[]},
            expires_at=utcnow()+timedelta(days=7));db.add(row);db.commit()
        return view(row)


def view(row):
    return {'id':row.id,'phase':row.phase,'turn_count':row.turn_count,'messages':row.state.get('messages',[]),
        'outcome':row.state.get('outcome'),'pending_summary':row.pending_summary,'ticket_id':row.ticket_id,
        'execution_mode':row.state.get('execution_mode'),
        'updated_at':row.updated_at.isoformat(),'expires_at':row.expires_at.isoformat()}


def safe_history(messages):
    bounded=[{'role':m['role'],'content':safe_text(m['content'])[:1400]} for m in messages[-10:]]
    if len(json.dumps(bounded,ensure_ascii=False).encode())>18000:
        bounded=bounded[-6:]
    return bounded


def finalize(db,row,turn,state):
    outcome=dict(state['outcome'])
    row.pending_summary=None
    if outcome['kind']=='ticket_proposal':
        payload={**outcome.pop('proposal'),'category':'vpn'}
        summary={'id':str(uuid.uuid4()),'payload':payload,
            'hash':hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()}
        row.pending_summary=summary;row.phase='awaiting_confirmation'
    else:row.phase='ready'
    response_text=outcome['message']+'\n'+'\n'.join(s['instruction'] for s in outcome.get('steps',[]))
    row.state={'messages':safe_history([*state['messages'],{'role':'assistant','content':response_text}]),
        'outcome':outcome,'corpus_version':state.get('evidence',{}).get('corpus_version'),
        'execution_mode':state.get('execution_mode')}
    row.updated_at=utcnow();turn.status='complete'
    result=view(row);turn.result=result
    if len(json.dumps(row.state,ensure_ascii=False).encode())>MAX_STATE_BYTES:raise AIError('agent_state_limit')
    db.commit();return result


def send_message(user,conversation_id,text,client_key,request_id,gateway=None,checkpointer=None,retriever=None):
    text=safe_text(text)
    if not text or len(text)>900:raise AIError('agent_message_limit',422)
    digest=hashlib.sha256(text.encode()).hexdigest()
    gateway=gateway or ModelGateway()
    with service.locked(user.id,'conversation-'+conversation_id) as db:
        row=owned(db,user,conversation_id)
        scenario_a(row)
        turn=db.scalar(select(AgentTurn).where(AgentTurn.conversation_id==row.id,AgentTurn.client_key==client_key))
        if turn:
            if turn.input_hash!=digest:raise HTTPException(409,'agent_turn_key_conflict')
            if turn.status=='complete':return turn.result
            if turn.status=='failed':raise AIError(turn.error_code or 'agent_turn_failed')
            # Recover a completed final checkpoint without another paid call.
            from contextlib import nullcontext
            with (nullcontext(checkpointer) if checkpointer else saver()) as cp:
                graph=build_graph(gateway,cp,**({'retriever':retriever} if retriever else {}))
                saved=graph.get_state({'configurable':{'thread_id':row.id}})
                if saved and not saved.next and saved.values.get('turn_id')==turn.id and saved.values.get('outcome'):
                    return finalize(db,row,turn,saved.values)
            raise AIError('agent_turn_interrupted_no_blind_retry',409)
        if row.turn_count>=MAX_TURNS:raise AIError('agent_turn_limit',429)
        if row.phase in ('processing','confirming'):raise AIError('agent_conversation_busy',409)
        if row.phase=='ticket_created':raise AIError('agent_ticket_already_created',409)
        turn=AgentTurn(id=str(uuid.uuid4()),conversation_id=row.id,client_key=client_key,input_hash=digest,status='processing')
        messages=safe_history([*row.state.get('messages',[]),{'role':'user','content':text}])
        row.phase='processing';row.pending_summary=None;row.turn_count+=1;db.add(turn);db.commit()
        state={'conversation_id':row.id,'turn_id':turn.id,'request_id':request_id,'messages':messages,
            'intent':{},'evidence':{},'outcome':{},'tool_count':0,'execution_mode':gateway.mode}
        from contextlib import nullcontext
        try:
            with (nullcontext(checkpointer) if checkpointer else saver()) as cp:
                graph=build_graph(gateway,cp,**({'retriever':retriever} if retriever else {}))
                result=graph.invoke(state,{'configurable':{'thread_id':row.id},'recursion_limit':6})
            return finalize(db,row,turn,result)
        except Exception as exc:
            code=exc.code if isinstance(exc,AIError) else 'agent_execution_interrupted'
            row.phase='failed'
            row.state={'messages':messages,'outcome':{'kind':'error','code':code},'execution_mode':gateway.mode}
            turn.status='failed';turn.error_code=code;row.updated_at=utcnow();db.commit()
            event(state,'failure',code)
            raise AIError(code,getattr(exc,'status',503)) from None


def confirm(user,conversation_id,summary_id,summary_hash,request_id):
    with service.locked(user.id,'conversation-'+conversation_id) as db:
        row=owned(db,user,conversation_id);summary=row.pending_summary
        scenario_a(row)
        if not summary or summary['id']!=summary_id or summary['hash']!=summary_hash:
            raise HTTPException(409,'agent_confirmation_stale')
        if row.phase not in ('awaiting_confirmation','confirming','ticket_pending','ticket_created'):
            raise HTTPException(409,'agent_confirmation_not_pending')
        row.phase='confirming';db.commit()
        key='agent-'+row.id+'-'+summary['id']
        state={'conversation_id':row.id,'turn_id':summary['id'],'request_id':request_id}
        try:
            result,replayed=service.create(user,summary['payload'],key,request_id)
            row.ticket_id=result['id'];row.phase='ticket_created';row.updated_at=utcnow()
            row.state={**row.state,'outcome':{'kind':'ticket_created','message':f"工单 #{result['native_id']} 已创建并核验归属。",
                'ticket':result,'replayed':replayed,'citations':[]}}
            db.commit();event(state,'confirmed_ticket','verified');return view(row)
        except (service.Upstream,HTTPException) as exc:
            operation=db.scalar(select(Operation).where(Operation.user_id==user.id,Operation.request_key==key))
            row.ticket_id=operation.id if operation else None;row.phase='ticket_pending';row.updated_at=utcnow()
            code=getattr(exc,'code',str(getattr(exc,'detail','ticket_result_unknown')))
            row.state={**row.state,'outcome':{'kind':'ticket_pending','message':'工单结果尚未确认，不能宣称成功。保留同一摘要和请求，不要重复建单。',
                'code':code,'operation_id':row.ticket_id,'citations':[]}}
            db.commit();event(state,'confirmed_ticket',code);return view(row)


def cancel(user,conversation_id,summary_id):
    with service.locked(user.id,'conversation-'+conversation_id) as db:
        row=owned(db,user,conversation_id)
        scenario_a(row)
        if row.phase!='awaiting_confirmation' or not row.pending_summary or row.pending_summary['id']!=summary_id:
            raise HTTPException(409,'agent_confirmation_stale')
        row.pending_summary=None;row.phase='ready';row.updated_at=utcnow()
        row.state={**row.state,'outcome':{'kind':'cancelled','message':'待提交摘要已取消，未创建工单。','citations':[]}}
        db.commit();return view(row)
