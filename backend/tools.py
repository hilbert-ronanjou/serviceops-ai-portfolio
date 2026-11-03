"""Model-proposed tool intents. No identity fields, arbitrary URLs, commands or ITSM APIs."""
import re
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,ValidationError
from backend.ai_config import AIError


class Strict(BaseModel):model_config=ConfigDict(extra='forbid',strict=True)
class Clarify(Strict):question:str=Field(min_length=5,max_length=250)
class Search(Strict):query:str=Field(min_length=3,max_length=800)
class Propose(Strict):
    title:str=Field(min_length=3,max_length=160)
    description:str=Field(min_length=5,max_length=2000)
class OutOfScope(Strict):message:str=Field(min_length=5,max_length=300)
class Step(Strict):
    instruction:str=Field(min_length=5,max_length=500)
    chunk_id:str=Field(min_length=5,max_length=100)
    evidence_quote:str=Field(min_length=8,max_length=220)
class Guidance(Strict):
    summary:str=Field(min_length=5,max_length=300)
    steps:list[Step]=Field(min_length=1,max_length=4)

REGISTRY={'ask_clarification':Clarify,'search_vpn_knowledge':Search,'propose_ticket':Propose,
    'out_of_scope':OutOfScope,'provide_guidance':Guidance}
DESCRIPTIONS={'ask_clarification':'向员工追问本次 VPN/网络故障的必要信息。',
    'search_vpn_knowledge':'仅检索已版本化的 VPN/网络知识，不读取用户工单或任意文件。',
    'propose_ticket':'提出故障摘要，等员工在界面确认；现在不会创建工单。',
    'out_of_scope':'说明该问题超出本阶段 VPN/网络排障范围。',
    'provide_guidance':'对实际检索的资料给出有短证据与 chunk_id 的安全建议。'}


def schemas(names):
    return [{'type':'function','function':{'name':name,'description':DESCRIPTIONS[name],
        'parameters':REGISTRY[name].model_json_schema()}} for name in names]


def safe_text(text):
    if not isinstance(text,str):raise AIError('invalid_text',422)
    if re.search(r'(?:sk-[A-Za-z0-9_-]{12,}|-----BEGIN [A-Z ]*PRIVATE KEY|(?:password|api[_ -]?key|密码|验证码|恢复码)\s*[:=：]\s*\S{3,})',text,re.I):
        raise AIError('sensitive_input_not_accepted',422)
    return text.replace('\x00','').strip()


def validate(call,allowed):
    name=call.get('name')
    if name not in allowed or name not in REGISTRY:raise AIError('tool_not_allowed',422)
    try:arguments=REGISTRY[name].model_validate(call['args']).model_dump()
    except (ValidationError,KeyError,TypeError):raise AIError('tool_arguments_invalid',422) from None
    def check(value):
        if isinstance(value,str):safe_text(value)
        elif isinstance(value,dict):
            for item in value.values():check(item)
        elif isinstance(value,list):
            for item in value:check(item)
    check(arguments)
    return name,arguments


def prevent_fabricated_execution(text):
    # Observing an already authorized network does not assert an approval/action.
    # Keep all other approval/execution patterns, including a second claim nearby.
    claims=text.replace('已授权的另一网络','授权网络')
    if re.search(r'(?:已(?:经)?(?:为你)?(?:创建|提交)工单|工单编号(?:为|是)|(?:工单|ticket|GLPI)\s*[#：:]?\s*\d+|已批准|已授权|已执行权限|ticket\s+(?:has been\s+)?created)',claims,re.I):
        raise AIError('model_fabricated_execution_denied',422)

def verify_safe_claim(text,evidence):
    safe_text(text);prevent_fabricated_execution(text)
    tokens=re.findall(r'https?://\S+|\b(?:\d{1,3}\.){3}\d{1,3}\b|\b[a-zA-Z0-9-]+\.(?:com|net|org|cn|internal|invalid)\b',text)
    if any(token not in evidence for token in tokens):raise AIError('unsupported_enterprise_configuration',422)
    # Only complete standalone prohibitions are exempt. Do not strip a whole
    # clause or all negated text: a second unsafe instruction must still fail.
    actions=re.sub(r'(?:^|(?<=[。；！\n]))\s*(?:不要|不得|请勿)关闭(?:防火墙|证书验证|MFA)[。；！]',
                   '',text,flags=re.I)
    if re.search(r'关闭(?:防火墙|证书验证|MFA)|禁用(?:防火墙|证书验证)|(?:ignore|disable)\s+(?:certificate|firewall)|Invoke-Expression|rm\s+-rf',actions,re.I):
        raise AIError('unsafe_guidance_denied',422)


def verify_guidance(arguments,chunks):
    allowed={chunk['id']:chunk for chunk in chunks}
    verify_safe_claim(arguments['summary'],'\n'.join(chunk['text'] for chunk in chunks))
    citations=[]
    for step in arguments['steps']:
        safe_text(step['instruction']);safe_text(step['evidence_quote'])
        prevent_fabricated_execution(step['instruction'])
        chunk=allowed.get(step['chunk_id'])
        if not chunk or step['evidence_quote'] not in chunk['text']:raise AIError('unsupported_citation_or_claim',422)
        # Never let generated links or server addresses bypass the evidence.
        verify_safe_claim(step['instruction'],chunk['text'])
        if chunk['id'] not in [c['id'] for c in citations]:
            citations.append({k:chunk[k] for k in ('id','document_id','title','url','synthetic','version')})
    prevent_fabricated_execution(arguments['summary'])
    return citations
