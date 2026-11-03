import React,{useEffect,useRef,useState} from 'react';
import AgentTrace from './AgentTrace';

export type Api=(path:string,options?:RequestInit)=>Promise<any>;
type Conversation={id:string;phase:string;messages:{role:string;content:string}[];execution_mode?:string;outcome?:any;pending_summary?:{id:string;hash:string;payload:{title:string;description:string}};ticket_id?:string};
const errors:Record<string,string>={
  model_credentials_missing:'AI 尚未配置专用凭据。你仍可直接提交故障工单。',
  model_live_calls_disabled:'AI 调用暂未开放。你仍可直接提交故障工单。',
  cloud_budget_confirmation_missing:'AI 正在等待账户负责人确认云端预算。',
  cloud_budget_confirmation_invalid:'AI 预算配置需要负责人检查。',
  model_region_or_endpoint_denied:'AI 地域配置不符合已批准设置。',
  model_budget_exhausted:'AI 费用额度不足，已停止新调用。工单功能仍可使用。',
  model_concurrency_limit:'AI 正忙，请稍后再试。',
  knowledge_index_not_ready:'知识库尚未完成真实向量化，暂时无法提供排障依据。',
  model_auth_denied:'AI 凭据验证失败，已停止调用。',
  model_permission_denied:'AI 模型权限不足，已停止调用。',
  model_rate_limited:'AI 服务限流。用量待核对，已停止新调用。',
  model_transport_or_response_unknown:'AI 响应未确认，用量待核对。请保留会话并联系 IT。',
  model_usage_missing:'AI 用量缺失，已停止新调用。',
  agent_turn_interrupted_no_blind_retry:'本轮执行中断，系统不会自动重复付费请求。请联系 IT 核对。',
  agent_turn_limit:'此会话已达到轮数上限，请新建会话。',
  sensitive_input_not_accepted:'请移除密码、API Key 或验证码后重试。',
  agent_confirmation_stale:'摘要已变化，请重新查看当前摘要。',
  agent_conversation_expired:'此会话已过期，请新建会话。',
};
const phases:Record<string,string>={ready:'待继续',processing:'处理中',awaiting_confirmation:'待确认摘要',confirming:'正在核验建单',ticket_pending:'建单结果待核对',ticket_created:'已建单',failed:'本轮未完成'};
export function aiError(code:string){return errors[code]||(code?.startsWith('budget_stopped_')?'AI 费用账本已停止调用，需要 IT 核对。':'AI 本轮未完成，请保留会话并联系 IT；工单功能仍可使用。');}

export default function AgentChat({api,onTicket,prefill,onBusy}:{api:Api;onTicket:(id:string)=>Promise<void>;prefill?:{id:number;text:string};onBusy?:(value:boolean)=>void}){
  const [conversation,setConversation]=useState<Conversation|null>(null);
  const [list,setList]=useState<{id:string;phase:string}[]>([]);
  const [status,setStatus]=useState<any>(null);const [input,setInput]=useState('');
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  const alive=useRef(true);const draft=useRef<{text:string;key:string}|null>(null);
  const currentId=useRef<string|null>(null);
  useEffect(()=>{onBusy?.(busy);},[busy]);
  useEffect(()=>{if(prefill){draft.current=null;currentId.current=null;setConversation(null);setInput(prefill.text);setError('');}},[prefill?.id]);
  useEffect(()=>{alive.current=true;refresh().catch(e=>{if(alive.current)setError(e.message);});return()=>{alive.current=false;};},[]);
  async function refresh(){const [s,l]=await Promise.all([api('/agent/status'),api('/agent/conversations')]);if(alive.current){setStatus(s);setList(l);}}
  async function run(action:()=>Promise<void>){setError('');setBusy(true);try{await action();}catch(e){if(alive.current)setError((e as Error).message);}finally{if(alive.current){try{await refresh();if(currentId.current)update(await api('/agent/conversations/'+currentId.current));}catch{/* Preserve the original failure; no model retry. */}setBusy(false);}}}
  function update(c:Conversation){if(alive.current){currentId.current=c.id;setConversation(c);}}
  const outcome=conversation?.outcome;
  const pending=conversation?.pending_summary;
  return <section className="card agent" id="agent"><div className="agent-heading"><div><span className="eyebrow">VPN / NETWORK</span><h2>AI 辅助诊断（可选）</h2></div><button className="ghost" disabled={busy} onClick={()=>run(async()=>{draft.current=null;setInput('');update(await api('/agent/conversations',{method:'POST',body:'{}'}));})}>新建会话</button></div>
    {prefill&&<p className="notice">知识入口已预填普通消息，将用于新会话。请检查或修改后点击“发送给 AI”；尚未发送。</p>}
    <p className="muted">描述 VPN 或网络故障；请勿输入密码、验证码或密钥。排障建议由资料支持，提交工单需要你确认。</p>
    {status&&<div role="status" className={status.configuration==='ready'?'muted':'agent-status'}>{status.configuration==='ready'?'AI 已配置':aiError(status.configuration)}{status.budget?<small>本阶段累计估算 ¥{(status.budget.spent_nano/1e9).toFixed(4)} · 未结算预留 ¥{(status.budget.reserved_nano/1e9).toFixed(4)} · 应用上限 ¥20</small>:<small>费用状态不可用，不能视为零费用；历史追踪仍可查看。</small>}</div>}
    {list.length>0&&<label>历史会话<select aria-label="历史会话" value={conversation?.id||''} disabled={busy} onChange={e=>{const id=e.target.value;if(id)run(async()=>{draft.current=null;setInput('');update(await api('/agent/conversations/'+id));});}}><option value="">选择你的会话</option>{list.map(c=><option key={c.id} value={c.id}>{c.id.slice(0,8)} · {phases[c.phase]||'待核对'}</option>)}</select></label>}
    {conversation?.execution_mode==='controlled_mock'&&<p className="agent-status">本会话是受控离线测试，回答来自预设测试输出；不代表真实模型质量。确认提交仍会创建真实本地 GLPI 工单。</p>}
    <div className="messages" aria-live="polite">{!conversation?.messages.length&&<p className="empty">例如：“VPN 连不上，普通网站可以打开。”</p>}{conversation?.messages.map((m,i)=><div key={i} className={'message '+m.role}><small>{m.role==='user'?'你':'ServiceOps AI'}</small><p>{m.content}</p></div>)}</div>
    {outcome?.kind==='error'&&<p role="alert" className="error">{aiError(outcome.code)}</p>}
    {outcome?.citations?.length>0&&<div className="sources"><strong>本轮资料依据</strong>{outcome.citations.map((c:any)=><p key={c.id}>{c.url?<a href={c.url} target="_blank" rel="noreferrer">{c.title}</a>:c.title}<small>{c.synthetic?'模拟企业测试规范 · synthetic':'官方公开资料'} · {c.version}</small></p>)}</div>}
    {pending&&<div className="summary"><h3>工单升级摘要</h3><strong>{pending.payload.title}</strong><p>{pending.payload.description}</p><small>摘要版本 {pending.id.slice(0,8)}</small><div className="actions"><button disabled={busy} onClick={()=>run(async()=>{update(await api('/agent/conversations/'+conversation!.id+'/confirm',{method:'POST',body:JSON.stringify({summary_id:pending.id,summary_hash:pending.hash,confirmed:true})}));})}>{conversation?.phase==='ticket_pending'?'核对同一请求':'确认提交工单'}</button>{conversation?.phase==='awaiting_confirmation'&&<button className="ghost" disabled={busy} onClick={()=>run(async()=>update(await api('/agent/conversations/'+conversation!.id+'/cancel',{method:'POST',body:JSON.stringify({summary_id:pending.id})})))}>取消摘要</button>}</div></div>}
    {(outcome?.kind==='ticket_created'||outcome?.kind==='ticket_pending'||outcome?.kind==='cancelled')&&<div role="status" className={outcome.kind==='ticket_created'?'notice':'agent-status'}><p>{outcome.message}</p>{conversation?.ticket_id&&<button className="ghost" disabled={busy} onClick={()=>run(()=>onTicket(conversation.ticket_id!))}>查看工单请求</button>}</div>}
    {conversation&&<AgentTrace key={conversation.id} api={api} conversationId={conversation.id}/>}
    {error&&<p role="alert" className="error">{error}</p>}
    <form onSubmit={e=>{e.preventDefault();if(status?.configuration!=='ready'){setError('MODEL LIVE HOLD：暂不发送，可编辑或取消草稿，或直接人工。');return;}run(async()=>{let c=conversation;if(!c){c=await api('/agent/conversations',{method:'POST',body:'{}'});update(c!);}if(!draft.current||draft.current.text!==input)draft.current={text:input,key:crypto.randomUUID()};const next=await api('/agent/conversations/'+c!.id+'/messages',{method:'POST',body:JSON.stringify({message:draft.current.text,client_request_id:draft.current.key})});if(alive.current){update(next);setInput('');draft.current=null;}});}}><label>补充故障信息<textarea aria-label="补充故障信息" value={input} onChange={e=>setInput(e.target.value)} required maxLength={900} rows={3} placeholder="错误提示、客户端、发生时间、普通网络是否正常…"/></label><button disabled={busy||status?.configuration!=='ready'||conversation?.phase==='ticket_created'}>{busy?'正在处理…':'发送给 AI'}</button><button type="button" className="ghost" onClick={()=>{setInput('');draft.current=null;}}>取消预填</button></form>
  </section>;
}
