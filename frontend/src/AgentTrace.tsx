import React,{useEffect,useRef,useState} from 'react';
import type {Api} from './AgentChat';
const stages:Record<string,string>={plan:'规划 · 工具意图',retrieve:'检索 · 后端读取',respond:'回答 · 工具意图',guidance_gate:'引用与安全校验',failure:'执行失败',confirmed_ticket:'确认后的工单执行',approval_wait:'等待可信人工审批',approval_gate:'后端审批决定',approval_resume:'审批流恢复',permission_execute:'独立模拟权限执行与核验'};
const origins:Record<string,string>={live:'真实供应商调用记录',controlled_mock:'固定模型 / Mock',mock:'Mock',test:'合成测试',unrecorded:'来源未记录'};
const money=(n:number|null)=>n===null?'未知':`¥${(n/1e9).toFixed(7)}`;
export default function AgentTrace({api,conversationId}:{api:Api;conversationId:string}){
  const [trace,setTrace]=useState<any>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[opened,setOpened]=useState(false);
  const generation=useRef(0);
  useEffect(()=>{generation.current++;setTrace(null);setError('');setOpened(false);setBusy(false);return()=>{generation.current++;};},[conversationId]);
  async function load(more=false){
    const current=++generation.current;setBusy(true);setError('');
    try{
      const suffix=more&&trace?.next_after_id?`?after_id=${trace.next_after_id}`:'';
      const result=await api(`/agent/conversations/${conversationId}/trace${suffix}`);
      if(current===generation.current){setTrace((previous:any)=>more?{...result,events:[...previous.events,...result.events]}:result);setOpened(true);}
    }catch{if(current===generation.current)setError('追踪暂不可读，可能已过期或没有访问权限。未重新执行 Agent。');}
    finally{if(current===generation.current)setBusy(false);}
  }
  return <section className="agent-trace" aria-label="Agent 运行追踪">
    <div className="trace-heading"><div><span className="eyebrow">RUN TRACE · F13</span><h3>Agent 运行追踪</h3></div><button type="button" className="ghost" disabled={busy} onClick={()=>opened?setOpened(false):load()}>{busy?'读取中…':opened?'收起追踪':'查看运行追踪'}</button></div>
    <small className="muted">只读查看本人的记录，不启动模型，不执行工单操作。参数仅显示字段及长度。</small>
    {error&&<p role="alert" className="error">{error}</p>}
    {opened&&trace&&<div className="trace-body">
      {trace.origin!=='database_record'&&<p className="notice">{trace.origin==='historical_copy'?'历史真实记录的隔离副本 · 非本轮模型执行':'明确标记的合成 TEST 夹具 · 非真实模型结果'}</p>}
      <p className="trace-id">会话 / checkpoint thread：{trace.conversation_id}</p>
      <div className="actions"><button type="button" className="ghost" disabled={busy} onClick={()=>load()}>刷新追踪</button></div>
      <h4>轮次与资料引用</h4>
      {!trace.turns.length&&<p className="empty">尚无轮次记录，不能据此推断模型请求数量。</p>}
      {trace.turns.map((t:any)=><article className="trace-turn" key={t.id}><strong>{t.status} · {origins[t.mode]||'来源未记录'}</strong><small className="trace-id">Turn {t.id} · {trace.origin==='historical_copy'?'副本登记时间':'记录时间'} {t.created_at}</small>{t.error&&<p className="error">拒绝 / 失败：{t.error}</p>}{t.citations.length?<ul>{t.citations.map((s:any)=><li key={s.id}>{s.title} · {s.version}<small className="trace-id">{s.id} · {s.synthetic?'合成企业资料':'官方公开资料'}</small></li>)}</ul>:<p className="muted">未记录已验证引用。引用通过不等于业务语义已审核。</p>}</article>)}
      <h4>执行时间线</h4>
      {!trace.events.length&&<p className="empty">没有可用事件；不补造节点或审批记录。</p>}
      <ol className="trace-timeline">{trace.events.map((e:any)=><li key={e.id}><div className="trace-stage"><strong>{stages[e.node]||e.node}</strong><span className="badge">{e.outcome}</span></div><small>{e.created_at||'历史时间未保存'} · {e.elapsed_ms===null?'耗时未记录':`${e.elapsed_ms} ms（0可能表示未测量）`}</small><p className="trace-id">Turn {e.turn_id||'未记录'}<br/>Request {e.request_id||'未记录'}</p>{e.call_id&&<p className="trace-id">模型关联 {e.call_id} · {e.model_record==='available'?'本库费用记录可用':'费用记录不可用或为缓存引用'}</p>}
        {e.details?<><dl className="trace-parameters">{Object.entries(e.details.parameters||{}).map(([k,v]:[string,any])=><React.Fragment key={k}><dt>{k}</dt><dd>{v.items!==undefined?`${v.items} 项（内容已隐藏）`:`文本已隐藏 · ${v.characters??'未知'} 字符`}</dd></React.Fragment>)}</dl>{e.details.sources&&<div><p>实际检索来源{e.details.cached_embedding?' · 复用查询向量缓存':''}</p>{e.details.sources.length?e.details.sources.map((s:any)=><p className="trace-id" key={s.id}>{s.title} · {s.version}<br/>{s.id}</p>):<small>来源未匹配当前受控语料；不替换为其他版本。</small>}</div>}</>:<small className="muted">历史事件无扩展元数据，参数与检索列表不补造。</small>}
      </li>)}</ol>
      {trace.next_after_id&&<button type="button" className="ghost" disabled={busy} onClick={()=>load(true)}>加载后续事件</button>}
      <h4>模型请求与费用</h4><p className="muted">本库持久记录不代表 HTTP 尝试次数的独立审计。缓存及外置权威账本可能缺席。Usage、账本估算与最终账单分别看待。</p>
      {!trace.model_calls.length&&<p className="empty">本库无关联费用记录；不代表零调用或零费用。</p>}
      {trace.model_calls.map((c:any)=><article className="trace-call" key={c.id}><strong>{c.model} · {c.status}</strong><small>{origins[c.mode]||'来源未记录'}</small><p className="trace-id">{c.id}<br/>{c.created_at}</p><p>Usage 输入 {c.input_tokens??'未知'} / 输出 {c.output_tokens??'未知'}<br/>原预留 {money(c.reserved_nano)} · Usage 费用估算 {money(c.estimated_cost_nano)}<br/>供应商最终结算：未核验</p>{c.error&&<p className="error">{c.error}</p>}</article>)}
      <h4>业务副作用</h4>{trace.business_effect.status==='simulated_permission'?<p>独立模拟权限结果见下方。此过程不创建 GLPI 工单。</p>:trace.business_effect.status==='linked_operation'?<p className="trace-id">关联 Operation {trace.business_effect.operation_id}<br/>本地状态 {trace.business_effect.phase} · GLPI #{trace.business_effect.native_id??'未知'}<br/>仅为本地操作记录；此页面未请求 GLPI 核验当前状态。</p>:<p>没有关联的 Operation 记录。工具意图不是工单执行；不能单独证明全系统无副作用。</p>}
      {trace.business_effect.status==='simulated_permission'&&<p className="notice">仅独立模拟权限 · {trace.business_effect.phase} · 版本 {trace.business_effect.version}<br/>申请 {trace.business_effect.request_id}<br/>已核验回执的授权 ID：{trace.business_effect.grant_id||'尚无'}。此记录不代表企业真实权限，也不将测试审批当作真人签署。</p>}
      <p className="notice">{trace.workflow==='access-approval'?'审批流从可信PG重读决定；恢复信号本身没有批准权。此流程无需模型调用。':'审批暂停 / 恢复不属于当前场景 A Graph，不补造。'}私人推理、完整 Prompt、用户输入和认证信息均不展示。</p>
    </div>}
  </section>;
}
