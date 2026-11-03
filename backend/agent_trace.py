"""F13 read projections. Never read checkpoints, raw prompts/results or upstream APIs.

Missing cost rows (e.g. a separate authoritative ledger) are explicitly unavailable.
This projection is not a cost ledger or a live GLPI ownership recheck.
"""
import uuid
from sqlalchemy import select
from backend.ai_models import AgentEvent,AgentTurn,ModelCall
from backend.models import Operation
from backend.tools import REGISTRY

NODES={'plan','retrieve','respond','guidance_gate','failure','confirmed_ticket'}
NODES.update({'approval_wait','approval_gate','approval_resume','permission_execute'})
CODES=set(REGISTRY)|{'hit','no_reliable_hit','knowledge_insufficient','verified',
    'tool_not_allowed','tool_arguments_invalid','unsupported_citation_or_claim',
    'unsupported_enterprise_configuration','unsafe_guidance_denied','sensitive_input_not_accepted',
    'model_fabricated_execution_denied','model_transport_or_response_unknown','model_usage_missing',
    'agent_execution_interrupted','agent_tool_limit','model_live_calls_disabled',
    'model_auth_denied','model_permission_denied','model_rate_limited',
    'create_uncertain_reconciliation_required','requester_pending','upstream_unavailable',
    'upstream_result_unknown','create_not_verified','ownership_mismatch'}
MODES={'live','controlled_mock','mock','test'}
STATUSES={'reserved','sending','succeeded','budget_rejected','usage_unknown','failed','complete','processing','cancelled'}
CODES.update({'model_connect_error_unknown','model_read_timeout_unknown','model_connect_timeout_unknown',
    'model_write_timeout_unknown','model_pool_timeout_unknown','model_protocol_error_unknown','model_transport_unknown',
    'model_json_response_invalid','model_response_invalid','tool_response_invalid'})
CODES.update({'approval_pending','approval_approved','approval_rejected','approval_revoked','approval_expired',
    'permission_denied','permission_intent','permission_verified','permission_unknown','approval_resume_requested','approval_resumed'})

def code(value):return value if isinstance(value,str) and value in CODES else 'unrecorded_or_other'

def identifier(value):
    try:return str(uuid.UUID(value))
    except (ValueError,TypeError,AttributeError):return None

def number(value):return value if type(value) is int and value>=0 else None

def parameters(name,args):
    """Names/counts/lengths only. Free text, protected fields and quotes never leave storage."""
    if name not in REGISTRY or not isinstance(args,dict):return {}
    result={}
    for key in REGISTRY[name].model_fields:
        value=args.get(key)
        if isinstance(value,str):result[key]={'redacted':True,'characters':min(len(value),10000)}
        elif key=='steps' and isinstance(value,list):result[key]={'items':min(len(value),100)}
    return result

def metadata(name=None,args=None,evidence=None):
    data={}
    if name in REGISTRY:data['parameters']=parameters(name,args)
    if isinstance(evidence,dict):
        # IDs are resolved against the versioned corpus again at read time.
        data['source_refs']=[{'id':c['id'],'version':c.get('version',c.get('source_version'))}
            for c in evidence.get('chunks',[])[:4] if isinstance(c,dict) and isinstance(c.get('id'),str)]
        data['cached_embedding']=evidence.get('cached_embedding') is True
    return data

def source_catalog():
    from backend.knowledge import corpus
    _,chunks=corpus()
    return {c['id']:{'id':c['id'],'document_id':c['document_id'],'title':c['title'],
        'version':c['source_version'],'synthetic':c['synthetic']} for c in chunks}

def safe_metadata(data,catalog):
    if not isinstance(data,dict):return None
    result={}
    if number(data.get('access_version')) is not None:result['access_version']=data['access_version']
    if number(data.get('execution_attempt')) is not None:result['execution_attempt']=data['execution_attempt']
    if isinstance(data.get('parameters'),dict):
        # Already masked at capture, still validate storage at the read boundary.
        result['parameters']={k:{f:number(v[f]) for f in ('characters','items') if f in v}
            for k,v in data['parameters'].items() if k in {'query','question','title','description','message','summary','steps'}
            and isinstance(v,dict)}
    if isinstance(data.get('source_refs'),list):
        result['sources']=[catalog[c['id']] for c in data['source_refs'][:4] if isinstance(c,dict)
            and isinstance(c.get('id'),str) and c['id'] in catalog and c.get('version')==catalog[c['id']]['version']]
        result['cached_embedding']=data.get('cached_embedding') is True
    return result

def model_view(call):
    return {'id':call.id,'model':call.model if call.model in {'qwen3.8-flash','text-embedding-v4'} else 'other',
        'mode':call.mode if call.mode in MODES else 'unrecorded',
        'status':call.status if call.status in STATUSES else 'other',
        'input_tokens':number(call.input_tokens),'output_tokens':number(call.output_tokens),
        'reserved_nano':number(call.reservation_nano),'estimated_cost_nano':number(call.cost_nano),
        'error':code(call.error_code) if call.error_code else None,'elapsed_ms':number(call.elapsed_ms),
        'created_at':call.created_at.isoformat(),'supplier_settlement':'NOT_VERIFIED'}

def project(db,row,after_id=0,limit=100):
    catalog=source_catalog()
    turns=db.scalars(select(AgentTurn).where(AgentTurn.conversation_id==row.id).order_by(AgentTurn.created_at,AgentTurn.id)).all()
    turn_ids={t.id for t in turns}
    events=db.scalars(select(AgentEvent).where(AgentEvent.conversation_id==row.id,AgentEvent.id>after_id)
        .order_by(AgentEvent.id).limit(limit+1)).all()
    more=len(events)>limit;events=events[:limit]
    # Exact owned turn linkage; don't expose another user's cached model request.
    calls=db.scalars(select(ModelCall).where(ModelCall.operation_id.in_(turn_ids))
        .order_by(ModelCall.created_at,ModelCall.id)).all() if turn_ids else []
    by_id={c.id:c for c in calls}
    entries=[]
    for e in events:
        entries.append({'id':e.id,'turn_id':identifier(e.turn_id),'request_id':identifier(e.request_id),
            'node':e.node if e.node in NODES else 'other','outcome':code(e.outcome),
            'call_id':identifier(e.call_id),'elapsed_ms':number(e.elapsed_ms),
            'created_at':None if isinstance(e.trace_data,dict) and e.trace_data.get('historical_timestamp_unavailable') is True else e.created_at.isoformat(),
            'details':safe_metadata(e.trace_data,catalog),
            'model_record': 'available' if e.call_id in by_id else 'unavailable_or_cached' if e.call_id else 'not_linked'})
    summaries=[]
    for t in turns:
        result=t.result if isinstance(t.result,dict) else {}
        outcome=result.get('outcome') or {}
        cited=outcome.get('citations',[]) if isinstance(outcome,dict) else []
        if not isinstance(cited,list):cited=[]
        mode=result.get('execution_mode')
        summaries.append({'id':t.id,'status':t.status if t.status in STATUSES else 'other',
            'error':code(t.error_code) if t.error_code else None,'created_at':t.created_at.isoformat(),
            'mode':mode if isinstance(mode,str) and mode in MODES else 'unrecorded',
            'citations':[catalog[c['id']] for c in cited[:4] if isinstance(c,dict) and isinstance(c.get('id'),str)
                and c['id'] in catalog and c.get('version')==catalog[c['id']]['version']]})
    operation=db.scalar(select(Operation).where(Operation.id==row.ticket_id,Operation.user_id==row.user_id)) if row.ticket_id else None
    effect={'status':'no_linked_operation','meaning':'No linked operation is not proof of global absence of side effects.'}
    if operation:
        effect={'status':'linked_operation','operation_id':operation.id,'native_id':number(operation.native_id),
            'phase':operation.phase if operation.phase in {'complete','creating','uncertain','repair_pending'} else 'other',
            'authority':'LOCAL_OPERATION_RECORD_ONLY; GLPI not queried by trace view'}
    is_access=isinstance(row.state,dict) and row.state.get('workflow')=='access-approval'
    if is_access:
        from backend.access_models import AccessRequest
        access=db.scalar(select(AccessRequest).where(AccessRequest.conversation_id==row.id,AccessRequest.user_id==row.user_id))
        if access:effect={'status':'simulated_permission','request_id':access.id,'phase':access.phase,'version':access.version,
            'grant_id':access.grant_id if access.receipt_hash else None,'authority':'LOCAL APPROVAL AND VERIFIED INDEPENDENT SIMULATOR RECEIPT; NEVER ENTERPRISE IAM'}
    origin=row.state.get('trace_origin') if isinstance(row.state,dict) else None
    return {'conversation_id':row.id,'checkpoint_thread_id':row.id,'workflow':'access-approval' if is_access else 'scenario-a',
        'origin':origin if origin in {'historical_copy','synthetic_fixture'} else 'database_record',
        'events':entries,'turns':summaries,
        'model_calls':[model_view(c) for c in calls],'business_effect':effect,
        'next_after_id':events[-1].id if more else None,
        'limitations':['No prompt, private reasoning, free-text parameters or raw checkpoints exposed.',
            'Missing events/Usage are not zero calls/cost; separate ledger records are not loaded.',
            'Citations show recorded validation, not a technical correctness or live GLPI recheck.',
            'Controlled TEST approval decisions are not human signoff.' if is_access else 'Approval pause/resume is not implemented by this scenario-A graph.']}
