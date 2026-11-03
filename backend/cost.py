"""Integer-nanoyuan, row-locked reservations shared across debug/index/evaluation.

Unknown usage is a persistent circuit breaker; no time-based reset/release exists.
"""
import hashlib
import json
import uuid
from sqlalchemy import select, func, inspect
from backend.db import SessionLocal
from backend.models import utcnow
from datetime import timedelta
from backend.ai_models import BudgetAccount, ModelCall, UnknownDisposition
from backend.ai_config import (AIError,BUDGET_ID,REGION,PRICE_VERSION,INTERNAL_LIMIT_NANO,
    MAX_CONCURRENT,CHAT_MODEL,EMBED_MODEL,INPUT_RATE_NANO,OUTPUT_RATE_NANO,EMBED_RATE_NANO)


def estimate(model,input_tokens,output_tokens=0):
    if model==CHAT_MODEL:return input_tokens*INPUT_RATE_NANO+output_tokens*OUTPUT_RATE_NANO
    if model==EMBED_MODEL and output_tokens==0:return input_tokens*EMBED_RATE_NANO
    raise AIError('model_not_approved',422)


class CostLedger:
    def __init__(self,budget_id=BUDGET_ID,session_factory=None,cumulative_ceiling_nano=None,risk_scope=None):
        self.budget_id=budget_id
        self.session_factory=session_factory or SessionLocal
        if cumulative_ceiling_nano is not None and (type(cumulative_ceiling_nano) is not int or not 0<cumulative_ceiling_nano<=INTERNAL_LIMIT_NANO):
            raise AIError('model_scoped_budget_invalid')
        self.cumulative_ceiling_nano=cumulative_ceiling_nano
        self.risk_scope=risk_scope

    def require_authoritative(self):
        """A TEST DB with the same account name is never a paid authority."""
        from sqlalchemy import text
        if self.budget_id!=BUDGET_ID:raise AIError('shared_budget_required')
        with self.session_factory() as db:
            row=db.execute(text('SELECT current_database(),system_identifier::text FROM pg_catalog.pg_control_system()')).one()
            if tuple(row)!=('serviceops','7696146962828046370'):
                raise AIError('model_original_authority_required')
            from backend.cost_fence import require
            require(db.connection())

    def _dispositions(self,db):
        # Older offline fixtures may intentionally contain only the two original tables.
        if not inspect(db.connection()).has_table('ai_unknown_dispositions'):return []
        return list(db.scalars(select(UnknownDisposition).where(UnknownDisposition.budget_id==self.budget_id)))

    def _usable(self,db,account):
        if account.status=='open':
            return self.risk_scope is None and not db.scalar(select(ModelCall.id).where(
                ModelCall.budget_id==account.id,ModelCall.status=='usage_unknown').limit(1))
        if account.status=='usage_unknown' and self.risk_scope is None:
            from backend.owner_unknown_acceptance import classification
            return classification(db,account)['all_current_unknowns_owner_accepted']
        if account.status!='usage_unknown' or not self.risk_scope:return False
        from backend.unknown_disposition import valid_scoped_record
        records=self._dispositions(db)
        unknown=list(db.scalars(select(ModelCall).where(ModelCall.budget_id==account.id,ModelCall.status=='usage_unknown')))
        return (len(records)==len(unknown)==1 and records[0].batch_phase=='running' and
                valid_scoped_record(records[0],unknown[0],self.risk_scope))

    def _account(self,db):
        if 'max_cost_proof' not in {c['name'] for c in inspect(db.connection()).get_columns('ai_model_calls')}:
            raise AIError('billable_database_fence_missing')
        account=db.scalar(select(BudgetAccount).where(BudgetAccount.id==self.budget_id).with_for_update())
        if not account:raise AIError('budget_ledger_missing')
        actual_spent=db.scalar(select(func.coalesce(func.sum(ModelCall.cost_nano),0)).where(ModelCall.budget_id==account.id))
        actual_reserved=db.scalar(select(func.coalesce(func.sum(ModelCall.reservation_nano),0)).where(
            ModelCall.budget_id==account.id,ModelCall.status.in_(['reserved','sending','usage_unknown'])))
        actual_reserved+=sum(r.additional_nano for r in self._dispositions(db))
        if account.spent_nano!=actual_spent or account.reserved_nano!=actual_reserved:
            account.status='ledger_corrupt';db.commit();raise AIError('budget_ledger_corrupt')
        stale=db.scalar(select(ModelCall).where(ModelCall.budget_id==account.id,
            ModelCall.status.in_(['reserved','sending']),ModelCall.created_at<utcnow()-timedelta(seconds=90)))
        if stale:
            account.status='unsettled_call_stale';db.commit();raise AIError('budget_stopped_unsettled_call_stale')
        return account

    def reserve(self,key,operation,purpose,model,payload,input_bound,output_bound,mode='live',*,proof=None):
        if mode not in ('live','controlled_mock'):raise AIError('model_call_mode_invalid')
        if mode=='live' and self.budget_id!=BUDGET_ID:raise AIError('shared_budget_required')
        if mode=='live' or proof is not None:
            from backend.billable_bounds import verify
            verify(proof,payload,model,input_bound,output_bound)
        if type(input_bound) is not int or type(output_bound) is not int or input_bound<0 or output_bound<0:
            raise AIError('model_bound_invalid')
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        amount=estimate(model,input_bound,output_bound)
        with self.session_factory() as db:
            if mode=='live':
                from backend.cost_fence import require
                require(db.connection())
            account=self._account(db)
            prior=db.scalar(select(ModelCall).where(ModelCall.request_key==key))
            if prior:
                if prior.input_hash!=digest or prior.budget_id!=account.id or prior.max_cost_proof!=proof:
                    raise AIError('model_call_key_conflict',409)
                if prior.status=='succeeded':return prior,True
                raise AIError('model_call_result_uncertain',409)
            def deny(code,status=503):
                db.add(ModelCall(id=str(uuid.uuid4()),budget_id=account.id,request_key=key,operation_id=operation,
                    purpose=purpose,model=model,region=REGION,price_version=PRICE_VERSION,mode=mode,input_hash=digest,
                    input_bound=input_bound,output_bound=output_bound,reservation_nano=0,cost_nano=0,
                    status='budget_rejected',error_code=code))
                db.commit();raise AIError(code,status)
            if not self._usable(db,account):deny('budget_stopped_'+account.status)
            if self.risk_scope:
                from backend.unknown_disposition import reserve_batch
                input_bound,amount=reserve_batch(db,self,account,key,operation,model,payload,input_bound,output_bound,mode)
            if account.spent_nano+account.reserved_nano+amount>INTERNAL_LIMIT_NANO or account.spent_nano>=INTERNAL_LIMIT_NANO:
                deny('model_budget_exhausted',429)
            if self.cumulative_ceiling_nano is not None and account.spent_nano+account.reserved_nano+amount>self.cumulative_ceiling_nano:
                deny('model_batch_budget_exhausted',429)
            active=db.scalar(select(func.count()).select_from(ModelCall).where(ModelCall.budget_id==account.id,
                ModelCall.status.in_(['reserved','sending'])))
            if active>=MAX_CONCURRENT:deny('model_concurrency_limit',429)
            call=ModelCall(id=str(uuid.uuid4()),budget_id=account.id,request_key=key,operation_id=operation,
                purpose=purpose,model=model,region=REGION,price_version=PRICE_VERSION,mode=mode,input_hash=digest,
                input_bound=input_bound,output_bound=output_bound,reservation_nano=amount,status='reserved',max_cost_proof=proof)
            account.reserved_nano+=amount;db.add(call);db.commit();return call,False

    def sent(self,call_id,*,payload=None,proof=None):
        with self.session_factory() as db:
            account=self._account(db)
            call=db.get(ModelCall,call_id)
            if not call or call.budget_id!=self.budget_id:raise AIError('invalid_model_call_phase')
            if not self._usable(db,account):raise AIError('budget_stopped_'+account.status)
            if call.mode=='live' or call.max_cost_proof is not None:
                from backend.billable_bounds import verify
                verify(proof,payload,call.model,call.input_bound,call.output_bound)
                if proof!=call.max_cost_proof or call.price_version!=PRICE_VERSION:
                    raise AIError('billable_proof_drift')
                if call.mode=='live':
                    from backend.cost_fence import require
                    require(db.connection())
            if call.status!='reserved':raise AIError('invalid_model_call_phase')
            call.status='sending';db.commit()

    def unknown(self,call_id,code,elapsed_ms=0,details=None):
        with self.session_factory() as db:
            account=self._account(db);call=db.get(ModelCall,call_id)
            if call.status not in ('reserved','sending'):raise AIError('invalid_model_call_phase')
            call.status='usage_unknown';call.error_code=code;call.elapsed_ms=elapsed_ms
            if details:
                # Caller supplies classified metadata, never exception messages or headers.
                safe={}
                classes={'ConnectTimeout','ReadTimeout','WriteTimeout','PoolTimeout','ConnectError',
                    'RemoteProtocolError','HTTPError','JSONDecodeError'}
                if details.get('error_class') in classes:safe['error_class']=details['error_class']
                if type(details.get('http_status')) is int and 100<=details['http_status']<=599:
                    safe['http_status']=details['http_status']
                try:safe['provider_request_id']=str(uuid.UUID(details.get('provider_request_id','')))
                except (ValueError,TypeError,AttributeError):pass
                if isinstance(details.get('network'),dict):
                    from backend.model_diagnostics import sanitize_network
                    safe['network']=sanitize_network(details['network'])
                call.result={'failure_evidence':safe}
            account.status='usage_unknown';db.commit()

    def settle(self,call_id,usage,input_tokens,output_tokens,result,elapsed_ms):
        with self.session_factory() as db:
            account=self._account(db);call=db.get(ModelCall,call_id)
            if call.status=='succeeded':return call
            if call.status not in ('reserved','sending'):raise AIError('invalid_model_call_phase')
            if call.max_cost_proof is not None:
                if (call.status!='sending' or type(input_tokens) is not int or input_tokens<=0 or
                    type(output_tokens) is not int or output_tokens<0 or
                    input_tokens>call.input_bound or output_tokens>call.output_bound):
                    call.status='usage_unknown';call.error_code='model_usage_exceeds_proven_maximum'
                    account.status='usage_unknown';db.commit()
                    raise AIError('model_usage_exceeds_proven_maximum')
            cost=estimate(call.model,input_tokens,output_tokens)
            if input_tokens>call.input_bound or output_tokens>call.output_bound or cost>call.reservation_nano:
                account.status='usage_exceeded_reservation';call.error_code='usage_exceeded_reservation'
            account.reserved_nano-=call.reservation_nano;account.spent_nano+=cost
            call.cost_nano=cost;call.input_tokens=input_tokens;call.output_tokens=output_tokens
            call.usage=usage;call.result=result;call.elapsed_ms=elapsed_ms;call.status='succeeded';call.settled_at=utcnow()
            db.commit()
            if not self._usable(db,account):raise AIError('budget_stopped_'+account.status)
            return call

    def check_batch(self,estimated_nano):
        if type(estimated_nano) is not int or estimated_nano<0:raise AIError('model_bound_invalid')
        with self.session_factory() as db:
            account=self._account(db)
            if (not self._usable(db,account) or account.spent_nano+account.reserved_nano>=INTERNAL_LIMIT_NANO or
                account.spent_nano+account.reserved_nano+estimated_nano>INTERNAL_LIMIT_NANO):
                raise AIError('evaluation_batch_budget_denied',429)
            return {'spent_nano':account.spent_nano,'reserved_nano':account.reserved_nano,
                    'remaining_nano':INTERNAL_LIMIT_NANO-account.spent_nano-account.reserved_nano}

    def status(self):
        with self.session_factory() as db:
            account=self._account(db)
            from backend.owner_unknown_acceptance import classification
            return {'budget_id':account.id,'status':account.status,'spent_nano':account.spent_nano,
                'reserved_nano':account.reserved_nano,'limit_nano':INTERNAL_LIMIT_NANO,'price_version':PRICE_VERSION,
                **classification(db,account)}
