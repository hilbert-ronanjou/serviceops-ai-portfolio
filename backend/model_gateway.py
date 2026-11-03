"""Only outbound model transport in the application; ALL calls reserve cost first."""
import json
import time
import httpx
from backend.ai_config import (AIError,config,CHAT_MODEL,EMBED_MODEL,DIMENSION,MAX_INPUT_TOKENS,MAX_OUTPUT_TOKENS)
from backend.cost import CostLedger
from backend.model_diagnostics import tool_rejection,transport_diagnostic
from backend.billable_bounds import prove

# Qwen's documented API does not guarantee/support required; the backend still
# rejects every response that is not exactly one allowed, valid function call.
TOOL_CHOICE='auto'


def bounded_input(payload):
    # Application byte estimate plus protocol allowance, not a vendor-tokenizer
    # proof. Paid authorization must separately substantiate its billable bound.
    # No images/audio/web search/cache creation are requested.
    bound=len(json.dumps(payload,ensure_ascii=False).encode())+2048
    if bound>MAX_INPUT_TOKENS:raise AIError('model_input_limit',422)
    return bound


def parse_usage(data,embedding=False):
    raw=data.get('usage')
    if not isinstance(raw,dict):raise AIError('model_usage_missing')
    input_tokens=raw.get('prompt_tokens',raw.get('input_tokens'))
    output_tokens=0 if embedding else raw.get('completion_tokens',raw.get('output_tokens'))
    if type(input_tokens) is not int or input_tokens<=0 or type(output_tokens) is not int or output_tokens<0:
        raise AIError('model_usage_invalid')
    total=raw.get('total_tokens')
    if total is not None and (type(total) is not int or total!=input_tokens+output_tokens):raise AIError('model_usage_invalid')
    prompt_details=raw.get('prompt_tokens_details') or {}
    output_details=raw.get('completion_tokens_details') or {}
    if not isinstance(prompt_details,dict) or not isinstance(output_details,dict):raise AIError('model_usage_invalid')
    # completion_tokens includes reasoning tokens. Never charge only the visible answer.
    reasoning=output_details.get('reasoning_tokens',0)
    cached=prompt_details.get('cached_tokens',0)
    if type(reasoning) is not int or not 0<=reasoning<=output_tokens or type(cached) is not int or not 0<=cached<=input_tokens:
        raise AIError('model_usage_invalid')
    if any(prompt_details.get(k,0) or output_details.get(k,0) for k in ('audio_tokens','image_tokens','video_tokens')):
        raise AIError('unsupported_billable_modality')
    safe={'input_tokens':input_tokens,'output_tokens':output_tokens,'total_tokens':input_tokens+output_tokens,
        'reasoning_tokens_in_output':reasoning,'cached_input_tokens':cached,
        'pricing':'all input at full standard rate; no explicit cache requested'}
    return safe,input_tokens,output_tokens


class ModelGateway:
    def __init__(self,model_config=config,ledger=None,transport=None):
        if transport is not None and type(transport) is not httpx.MockTransport:
            raise AIError('model_test_transport_denied')
        self.config=model_config;self.ledger=ledger or CostLedger()
        self.mode='controlled_mock' if transport is not None else 'live'
        self.transport=transport

    def _send(self,path,payload,key,operation,purpose,embedding=False):
        if (self.transport is not None and type(self.transport) is not httpx.MockTransport) or self.mode!=('live' if self.transport is None else 'controlled_mock'):
            raise AIError('model_test_transport_denied')
        self.config.validate()
        if self.mode=='live':
            if not isinstance(self.ledger,CostLedger):raise AIError('model_original_authority_required')
            CostLedger.require_authoritative(self.ledger)
        # Own a JSON snapshot: caller-owned message lists cannot drift while a
        # concurrent caller edits its next turn between proof and HTTP encoding.
        payload=json.loads(json.dumps(payload,ensure_ascii=False))
        bounded_input(payload) # Size/abuse limit ONLY, never a billing guarantee.
        if (path!=('embeddings' if embedding else 'chat/completions') or
            embedding!=(payload.get('model')==EMBED_MODEL)):
            raise AIError('billable_path_denied')
        proof=prove(payload,self.config.base_url)
        call,replayed=self.ledger.reserve(key,operation,purpose,payload['model'],payload,
            proof['input_bound'],proof['output_bound'],self.mode,proof=proof)
        if replayed:return call.result,call.id
        if prove(payload,self.config.base_url)!=proof:raise AIError('billable_proof_drift')
        self.ledger.sent(call.id,payload=payload,proof=proof);start=time.perf_counter()
        response=None
        def unknown(code,error_class=None,network=None):
            details={'error_class':error_class}
            if network:details['network']=network
            if response is not None:
                details.update(http_status=response.status_code,
                    provider_request_id=response.headers.get('x-request-id',response.headers.get('x-dashscope-request-id','')))
            self.ledger.unknown(call.id,code,int((time.perf_counter()-start)*1000),details=details)
        try:
            with httpx.Client(base_url=self.config.base_url.rstrip('/')+'/',timeout=25,
                    trust_env=False,follow_redirects=False,transport=self.transport) as client:
                response=client.post(path,json=payload,headers={'Authorization':'Bearer '+self.config.api_key})
            if response.status_code!=200:
                code={401:'model_auth_denied',403:'model_permission_denied',429:'model_rate_limited'}.get(response.status_code,'model_upstream_failure')
                raise AIError(code)
            data=response.json()
            if not isinstance(data,dict):raise AIError('model_response_invalid')
            observed=data.get('model')
            if observed is not None and (not isinstance(observed,str) or len(observed)>100 or
                not (observed==payload['model'] or observed.startswith(payload['model']+'-'))):
                raise AIError('model_response_model_mismatch')
            usage,input_tokens,output_tokens=parse_usage(data,embedding)
            if input_tokens>proof['input_bound'] or output_tokens>proof['output_bound']:
                raise AIError('model_usage_exceeds_proven_maximum')
        except AIError as exc:
            unknown(exc.code);raise
        except httpx.HTTPError as exc:
            classified={httpx.ConnectTimeout:'connect_timeout',httpx.ReadTimeout:'read_timeout',
                httpx.WriteTimeout:'write_timeout',httpx.PoolTimeout:'pool_timeout',
                httpx.ConnectError:'connect_error',httpx.RemoteProtocolError:'protocol_error'}
            code='model_'+next((name for cls,name in classified.items() if isinstance(exc,cls)),'transport')+'_unknown'
            unknown(code,type(exc).__name__ if type(exc) in classified else 'HTTPError',transport_diagnostic(exc))
            raise AIError(code) from None
        except ValueError:
            unknown('model_json_response_invalid','JSONDecodeError')
            raise AIError('model_json_response_invalid') from None
        # Store only bounded response fields, never private reasoning, request bodies or secrets.
        result={'invalid_response':True};function=None;parse_phase='envelope'
        try:
            observed=data.get('model') # Missing server version is unavailable, never invented from request.
            if embedding:
                import math
                items=data['data']
                if not isinstance(items,list) or len(items)!=len(payload['input']):raise ValueError()
                safe_items=[]
                for item in items:
                    vector=item['embedding']
                    if not isinstance(vector,list) or len(vector)!=DIMENSION or any(type(x) not in (int,float) or not math.isfinite(x) for x in vector) or not any(vector):
                        raise AIError('embedding_dimension_or_value_invalid')
                    if type(item.get('index')) is not int:raise ValueError()
                    safe_items.append({'index':item['index'],'embedding':vector})
                result={'data':safe_items,'model_observed':observed}
            else:
                from backend.tools import validate
                parse_phase='choices'
                choices=data['choices']
                if not isinstance(choices,list) or len(choices)!=1:raise ValueError()
                choice=choices[0];calls=choice['message']['tool_calls']
                if not isinstance(calls,list) or len(calls)!=1 or choice.get('finish_reason') not in (None,'stop','tool_calls','function_call'):raise ValueError()
                parse_phase='function'
                function=calls[0]['function'];parse_phase='arguments_json'
                args=json.loads(function['arguments']);parse_phase='name_and_arguments'
                _,validated=validate({'name':function['name'],'args':args},[t['function']['name'] for t in payload['tools']])
                result={'tool_calls':[{'function':{'name':function['name'],'arguments':json.dumps(validated,ensure_ascii=False)}}],
                    'finish_reason':choice.get('finish_reason'),'model_observed':observed}
        except (AIError,KeyError,TypeError,ValueError) as exc:
            result={'invalid_response':True,'error_code':getattr(exc,'code','model_tool_response_invalid'),
                'model_observed':observed}
            if not embedding:
                result['tool_validation']=tool_rejection(function,payload['tools'],purpose,parse_phase)
        self.ledger.settle(call.id,usage,input_tokens,output_tokens,result,int((time.perf_counter()-start)*1000))
        if result.get('invalid_response'):raise AIError(result.get('error_code','model_response_invalid'),422)
        return result,call.id

    def chat(self,messages,tools,key,operation,purpose='agent_plan'):
        payload={'model':CHAT_MODEL,'messages':messages,'tools':tools,'tool_choice':TOOL_CHOICE,
            'parallel_tool_calls':False,'temperature':0,'max_completion_tokens':MAX_OUTPUT_TOKENS,
            'enable_thinking':False,'preserve_thinking':False,'enable_search':False,'n':1,'stream':False}
        result,call_id=self._send('chat/completions',payload,key,operation,purpose)
        calls=result.get('tool_calls')
        if result.get('finish_reason')=='length' or not isinstance(calls,list) or len(calls)!=1:
            raise AIError('model_tool_response_invalid',422)
        try:
            function=calls[0]['function'];name=function['name'];args=json.loads(function['arguments'])
        except (KeyError,TypeError,ValueError):raise AIError('model_tool_response_invalid',422) from None
        if not isinstance(name,str) or not isinstance(args,dict):raise AIError('model_tool_response_invalid',422)
        return {'name':name,'args':args,'call_id':call_id}

    def embed(self,texts,key,operation,purpose='knowledge_embedding'):
        if not texts or len(texts)>8 or any(not isinstance(t,str) or not 0<len(t)<=1800 for t in texts):
            raise AIError('embedding_input_limit',422)
        payload={'model':EMBED_MODEL,'input':texts,'dimensions':DIMENSION,'encoding_format':'float'}
        result,call_id=self._send('embeddings',payload,key,operation,purpose,embedding=True)
        data=result.get('data')
        if not isinstance(data,list) or len(data)!=len(texts):raise AIError('embedding_response_invalid')
        if any(not isinstance(item,dict) or type(item.get('index')) is not int for item in data):raise AIError('embedding_response_invalid')
        ordered=sorted(data,key=lambda x:x.get('index',-1))
        import math
        if [item.get('index') for item in ordered]!=list(range(len(texts))):raise AIError('embedding_response_invalid')
        vectors=[]
        for item in ordered:
            vector=item.get('embedding')
            if not isinstance(vector,list) or len(vector)!=DIMENSION or any(type(x) not in (int,float) or not math.isfinite(x) for x in vector) or not any(vector):
                raise AIError('embedding_dimension_or_value_invalid')
            vectors.append(vector)
        return vectors,call_id
