import React,{useState,useRef} from 'react';
import type {Api} from './AgentChat';
import AgentTrace from './AgentTrace';
export default function AccessRequests({api}:{api:Api}){
 const [items,setItems]=useState<any[]>([]),[reason,setReason]=useState(''),[permission,setPermission]=useState('read'),[error,setError]=useState(''),[busy,setBusy]=useState(false),[checkpoint,setCheckpoint]=useState<any>({});
 const requestKey=useRef(crypto.randomUUID());
 async function run(work:()=>Promise<void>){setBusy(true);setError('');try{await work();}catch{setError('审批服务不可用、版本已变化或当前操作不被允许。请刷新核查；不会自动授权。');}finally{setBusy(false)}}
 async function load(){setItems(await api('/access'))}
 return <section className="card" id="access"><h2>模拟权限申请</h2><p>仅用于 TEST 办公资料共享区，不连接企业账号系统。申请后等待授权人员明确审批，再由本人恢复流程。</p><p className="muted">请勿填写密码或敏感信息。审批入口：<a href="/approval">授权审批工作台</a>。自动化测试决定不代表真人审批验收。</p>
 {error&&<p role="alert" className="error">{error}</p>}
 <form onSubmit={e=>{e.preventDefault();run(async()=>{await api('/access',{method:'POST',headers:{'Idempotency-Key':requestKey.current},body:JSON.stringify({resource:'TEST-office-share',permission,reason})});requestKey.current=crypto.randomUUID();setReason('');await load()})}}>
 <label>目标资源<input value="TEST-office-share（独立模拟系统）" readOnly/></label><label>申请权限<select value={permission} onChange={e=>setPermission(e.target.value)}><option value="read">只读</option><option value="contribute">协作编辑</option></select></label><label>申请理由<textarea value={reason} onChange={e=>setReason(e.target.value)} minLength={5} maxLength={400} required/></label><button disabled={busy}>确认提交权限申请</button></form>
 <button className="ghost" disabled={busy} onClick={()=>run(load)}>刷新我的申请</button>
 {items.map(r=><article className="detail" key={r.id}><h3>{r.resource} · {r.permission}</h3><p>{r.reason}</p><p>状态：<strong>{r.phase}</strong> · 版本 {r.version}</p><small className="trace-id">申请 {r.id}<br/>范围校验 {r.scope_hash}<br/>到期 {r.expires_at}</small><div className="actions"><button className="ghost" disabled={busy} onClick={()=>run(async()=>{await api('/access/'+r.id+'/resume',{method:'POST',body:'{}'});await load()})}>核验审批并恢复</button><button className="ghost" disabled={busy} onClick={()=>run(async()=>setCheckpoint({...checkpoint,[r.id]:await api('/access/'+r.id+'/checkpoint')}))}>查看暂停状态</button></div>
 {checkpoint[r.id]&&<p>持久中断：{checkpoint[r.id].interrupted?'是':'否'} · 下一节点 {checkpoint[r.id].next.join(', ')||'无'}</p>}<AgentTrace api={api} conversationId={r.conversation_id}/></article>)}
 </section>
}

export function ApprovalDesk(){
 const [user,setUser]=useState(''),[csrf,setCsrf]=useState(''),[items,setItems]=useState<any[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 async function api(path:string,options:RequestInit={}){const r=await fetch('/api/approvals'+path,{...options,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}});const d=await r.json();if(!r.ok)throw Error('审批身份、范围、版本或状态校验未通过。请重新登录或刷新核对。');return d}
 async function run(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 return <div className="shell"><header><strong>ServiceOps · 授权审批工作台</strong><a href="/">员工入口</a></header><main><section className="card"><h1>可信人工审批</h1><p>仅处理本人获授权的 TEST 资源。请核对主体、范围和理由；点击决定由当前登录责任人承担。测试脚本的角色模拟不是真人人工验收。</p>{error&&<p role="alert" className="error">{error}</p>}
 {!user?<form onSubmit={e=>{e.preventDefault();const body=JSON.stringify(Object.fromEntries(new FormData(e.currentTarget)));run(async()=>{const d=await api('/login',{method:'POST',body});setCsrf(d.csrf_token);setUser(d.username);setItems(await api(''))})}}><label>审批账号<input name="username" required autoComplete="username"/></label><label>审批密码<input name="password" type="password" required autoComplete="current-password"/></label><button disabled={busy}>登录审批工作台</button></form>:<><p>责任账号：{user}</p><div className="actions"><button disabled={busy} onClick={()=>run(async()=>setItems(await api('')))}>刷新待审记录</button><button className="ghost" disabled={busy} onClick={()=>run(async()=>{await api('/logout',{method:'POST',body:'{}'});setUser('');setCsrf('');setItems([])})}>退出审批</button></div>{items.map(r=><article className="detail" key={r.id}><h2>{r.resource} · {r.permission}</h2><p>申请员工 ID：{r.user_id} · 状态 {r.phase} · 版本 {r.version}</p><p>{r.reason}</p><small className="trace-id">申请 {r.id}<br/>精确范围 {r.scope_hash}<br/>到期 {r.expires_at}</small><div className="actions">{(r.phase==='pending'?['approve','reject']:r.phase==='approved'?['revoke']:[]).map(action=><button disabled={busy} key={action} onClick={()=>run(async()=>{await api('/'+r.id+'/decision',{method:'POST',body:JSON.stringify({version:r.version,scope_hash:r.scope_hash,action})});setItems(await api(''))})}>{({approve:'明确批准此版本',reject:'拒绝此申请',revoke:'撤销尚未执行的批准'} as any)[action]}</button>)}</div></article>)}</>}
 </section></main></div>
}
