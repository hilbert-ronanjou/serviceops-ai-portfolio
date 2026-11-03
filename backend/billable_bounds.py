"""Supplier-contract maxima, NOT byte/token forecasts or supplier invoices.

2026-10-20 primary contracts are retained in the task evidence. No compatible
tokenizer + complete Chat template proof exists here, so use the full context
ceiling. An explicit price/spec change requires a new reviewed contract version.
"""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
from backend import ai_config as cfg

VERSION='beijing-text-hard-max-v1-20261020'
CHAT_INPUT=1_000_000
CHAT_MAX_COMPLETION=131_072
OUTPUT_TOLERANCE=10
EMBED_PER_TEXT=8192
EMBED_PROJECT_MAX_ITEMS=8 # Supplier accepts 10; deliberately retain project limit.
CONTRACT={
    'version':VERSION,'region':'cn-beijing','price_version':'aliyun-cn-beijing-2026-10-14',
    'chat_model':'qwen3.8-flash','embedding_model':'text-embedding-v4',
    'chat_input':CHAT_INPUT,'chat_max_completion':CHAT_MAX_COMPLETION,
    'completion_tolerance':OUTPUT_TOLERANCE,'embedding_tokens_per_text':EMBED_PER_TEXT,
    'supplier_embedding_max_items':10,'project_embedding_max_items':EMBED_PROJECT_MAX_ITEMS,
    'input_rate_nano':800,'output_rate_nano':2700,'embedding_rate_nano':500,
    'sources':['https://help.aliyun.com/zh/model-studio/qwen3-8-flash',
        'https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions/',
        'https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api'],
}

def digest(obj):
    # Same representation as the historical ModelCall.input_hash.
    return hashlib.sha256(json.dumps(obj,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

CONTRACT_SHA256=digest(CONTRACT)

def implementation_digest():
    root=Path(__file__).parent
    return digest({n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in
        ('billable_bounds.py','model_gateway.py','cost.py','cost_fence.py','ai_config.py','ai_models.py',
         'owner_unknown_acceptance.py')})

def chat_maximum_nano(completion_limit=1024):
    price_contract()
    if type(completion_limit) is not int or not 1<=completion_limit<=CHAT_MAX_COMPLETION:
        denied('billable_output_limit_denied')
    return CHAT_INPUT*800+(completion_limit+OUTPUT_TOLERANCE)*2700

def embedding_maximum_nano(items=1):
    price_contract()
    if type(items) is not int or not 1<=items<=EMBED_PROJECT_MAX_ITEMS:
        denied('billable_embedding_mode_denied')
    return items*EMBED_PER_TEXT*500

def denied(code='maximum_billable_cost_unproven'):
    raise cfg.AIError(code,422)

def price_contract():
    if (cfg.CHAT_MODEL,cfg.EMBED_MODEL,cfg.REGION,cfg.PRICE_VERSION,
        cfg.INPUT_RATE_NANO,cfg.OUTPUT_RATE_NANO,cfg.EMBED_RATE_NANO)!=(
        'qwen3.8-flash','text-embedding-v4','cn-beijing','aliyun-cn-beijing-2026-10-14',800,2700,500):
        denied('billable_price_contract_drift')

def endpoint(base_url):
    # Reuse the endpoint rules without reading credentials or attestations.
    import re
    try:p=urlsplit(base_url);port=p.port
    except (ValueError,TypeError):denied('billable_endpoint_denied')
    if (p.scheme!='https' or p.username or p.password or p.query or p.fragment or
        p.path.rstrip('/')!='/compatible-mode/v1' or port not in (None,443) or
        not (p.hostname=='dashscope.aliyuncs.com' or
            re.fullmatch(r'[a-zA-Z0-9-]+\.cn-beijing\.maas\.aliyuncs\.com',p.hostname or ''))):
        denied('billable_endpoint_denied')
    return base_url.rstrip('/')

def prove(payload,base_url='https://dashscope.aliyuncs.com/compatible-mode/v1'):
    price_contract();base=endpoint(base_url)
    if not isinstance(payload,dict):denied()
    model=payload.get('model');count=1;limit=0
    if model==cfg.CHAT_MODEL:
        keys={'model','messages','tools','tool_choice','parallel_tool_calls','temperature',
            'max_completion_tokens','enable_thinking','preserve_thinking','enable_search','stream','n'}
        if set(payload)!=keys:denied('billable_chat_mode_denied')
        if (payload['enable_thinking'] is not False or payload['preserve_thinking'] is not False or
            payload['enable_search'] is not False or payload['stream'] is not False or
            payload['parallel_tool_calls'] is not False or type(payload['n']) is not int or payload['n']!=1 or
            payload['tool_choice']!='auto' or payload['temperature']!=0):denied('billable_chat_mode_denied')
        limit=payload['max_completion_tokens']
        if type(limit) is not int or not 1<=limit<=CHAT_MAX_COMPLETION:denied('billable_output_limit_denied')
        messages=payload['messages'];tools=payload['tools']
        if (not isinstance(messages,list) or not messages or any(not isinstance(m,dict) or
            set(m)!={'role','content'} or m['role'] not in ('system','user','assistant','tool') or
            not isinstance(m['content'],str) for m in messages)):denied('billable_text_only_required')
        if (not isinstance(tools,list) or not tools or any(not isinstance(t,dict) or set(t)!={'type','function'} or
            t['type']!='function' or not isinstance(t['function'],dict) or
            not {'name','parameters'}<=set(t['function'])<= {'name','description','parameters','strict'}
            for t in tools)):denied('billable_tools_denied')
        inp=CHAT_INPUT;out=limit+OUTPUT_TOLERANCE
        amount=inp*800+out*2700
    elif model==cfg.EMBED_MODEL:
        texts=payload.get('input')
        if (set(payload)!={'model','input','dimensions','encoding_format'} or
            payload['dimensions']!=1024 or payload['encoding_format']!='float' or
            not isinstance(texts,list) or not 1<=len(texts)<=EMBED_PROJECT_MAX_ITEMS or
            any(not isinstance(t,str) or not 0<len(t)<=1800 for t in texts)):
            denied('billable_embedding_mode_denied')
        count=len(texts);inp=count*EMBED_PER_TEXT;out=0;amount=inp*500
    else:denied('billable_model_denied')
    proof=dict(version=VERSION,contract_sha256=CONTRACT_SHA256,implementation_sha256=implementation_digest(),price_version=cfg.PRICE_VERSION,
        region=cfg.REGION,model=model,endpoint=base,payload_sha256=digest(payload),
        input_items=count,input_bound=inp,output_bound=out,completion_limit=limit,
        input_rate_nano=800 if model==cfg.CHAT_MODEL else 500,
        output_rate_nano=2700 if model==cfg.CHAT_MODEL else 0,max_cost_nano=amount)
    proof['proof_sha256']=digest(proof)
    return proof

def verify(proof,payload,model,input_bound,output_bound):
    if not isinstance(proof,dict):denied('billable_proof_required')
    expected=prove(payload,proof.get('endpoint'))
    if (proof!=expected or model!=expected['model'] or type(input_bound) is not int or
        type(output_bound) is not int or (input_bound,output_bound)!=(expected['input_bound'],expected['output_bound'])):
        denied('billable_proof_drift')
    return expected
