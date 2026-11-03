import React,{useEffect,useRef,useState} from 'react';
import type {Api} from './AgentChat';

type Hit={id:string;title:string;version:string;content_hash:string};
type Article=Hit & {body:string;applicability:string;steps:string[];precautions:string[];not_applicable:string;verification:string;sources:{title:string;url:string;nature:string}[]};

export default function Knowledge({api,onAgent}:{api:Api;onAgent:(text:string)=>void}){
  const [query,setQuery]=useState('');const [includeProblem,setIncludeProblem]=useState(false);
  const [results,setResults]=useState<{articles:Hit[];published_count:number;has_more:boolean;match_kind?:string}|null>(null);
  const [submittedQuery,setSubmittedQuery]=useState('');const [queryChanged,setQueryChanged]=useState(false);const searchRevision=useRef(0);
  const [article,setArticle]=useState<Article|null>(null);
  const [feedback,setFeedback]=useState<boolean|null>(null);
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [saved,setSaved]=useState(false);
  const alive=useRef(true);
  useEffect(()=>()=>{alive.current=false;},[]);
  async function run(action:()=>Promise<void>){const revision=searchRevision.current;setBusy(true);setError('');try{await action();}catch(e){if(alive.current&&revision===searchRevision.current)setError((e as Error).message);}finally{if(alive.current)setBusy(false);}}
  async function open(hit:Hit){
    const revision=searchRevision.current;
    setArticle(null);setFeedback(null);setSaved(false);
    const a=await api('/knowledge/'+hit.id);
    const f=await api('/knowledge/'+hit.id+'/feedback');
    if(alive.current&&revision===searchRevision.current){setArticle(a);setFeedback(f.resolved);}
  }
  return <section className="card knowledge" id="knowledge"><span className="eyebrow">SELF SERVICE</span><h2>搜索解决方案</h2>
    <p>优先使用已审核发布的知识。紧急或不适合自助时，可随时直接提交人工工单。</p>
    <form onSubmit={e=>{e.preventDefault();const revision=++searchRevision.current;const submitted=query.trim();setSubmittedQuery(submitted);setQueryChanged(false);run(async()=>{setArticle(null);setResults(null);setSaved(false);const r=await api('/knowledge?q='+encodeURIComponent(submitted));if(alive.current&&revision===searchRevision.current)setResults(r);});}}>
      <label>IT 问题关键词<input aria-label="IT 问题关键词" value={query} onChange={e=>{searchRevision.current+=1;setQuery(e.target.value);setQueryChanged(true);setResults(null);setArticle(null);setError('');setIncludeProblem(false);}} maxLength={160} placeholder="例如：VPN 身份验证失败"/></label>
      <button disabled={busy}>{busy?'正在读取…':'搜索已发布知识'}</button>
      <small className="muted">按标题、关键词与正文查找；空关键词可浏览，最多展示20篇。请勿输入密码、验证码或密钥。</small>
    </form>
    {error&&<p role="alert" className="error">{error}</p>}
    {queryChanged&&<p role="status" className="muted">关键词已更改，请点击搜索以查看当前关键词的结果。</p>}
    {results&&<div aria-live="polite"><p className="muted">本次搜索：{submittedQuery||'全部已发布知识'}</p>{results.match_kind==='related'&&<p className="notice">以下是相关通用资料，没有找到直接对应此问题的答案。请先核对适用条件；这不代表已确定身份验证失败的原因或解决方案。</p>}{results.published_count===0?<p className="empty">暂无通过人工审核并发布的知识，暂不能提供自助解决步骤。你可以选择 AI 辅助诊断或直接提交人工工单。</p>:results.articles.length===0?<p className="empty">没有匹配的已发布知识。可调整关键词，或选择下一步。</p>:<ul className="knowledge-results">{results.articles.map(hit=><li key={hit.id}><button className="ghost" disabled={busy} onClick={()=>run(()=>open(hit))}>{hit.title}<small>{hit.version}</small></button></li>)}</ul>}{results.has_more&&<p>结果超过20篇，请缩小关键词范围。</p>}</div>}
    {article&&<article className="knowledge-detail"><h3>{article.title}</h3><small>版本：{article.version}</small><h4>适用条件</h4><p>{article.applicability}</p><p>{article.body}</p><h4>操作步骤</h4><ol>{article.steps.map((step,i)=><li key={i}>{step}</li>)}</ol><h4>注意事项</h4><ul>{article.precautions.map((item,i)=><li key={i}>{item}</li>)}</ul><h4>不适用情况</h4><p>{article.not_applicable}</p><h4>验证是否解决</h4><p>{article.verification}</p><h4>来源与性质</h4><ul>{article.sources.map((source,i)=><li key={i}><a href={source.url} target="_blank" rel="noreferrer">{source.title}</a> · {source.nature}</li>)}</ul>
      <h4>这些步骤是否解决了你的问题？</h4><div className="actions">{[true,false].map(value=><button key={String(value)} className={feedback===value?'':'ghost'} aria-pressed={feedback===value} disabled={busy} onClick={()=>run(async()=>{await api('/knowledge/'+article.id+'/feedback',{method:'PUT',body:JSON.stringify({content_hash:article.content_hash,resolved:value})});if(alive.current){setFeedback(value);setSaved(true);}})}>{value?'已解决':'未解决'}</button>)}</div>
      {saved&&<p role="status">已保存你的当前反馈。</p>}<small className="muted">仅代表你的自报结果，可修改；不等于已客观验证修复。反馈有效期30天，不记录搜索原文或自由反馈文本。</small>
    </article>}
    <label className="inline-check"><input type="checkbox" checked={includeProblem} onChange={e=>setIncludeProblem(e.target.checked)}/>我确认上方问题信息可带入AI消息草稿</label><div className="actions"><button type="button" className="ghost" disabled={busy} onClick={()=>onAgent(`${article?'我阅读的资料标题：'+article.title+'。':'未找到匹配的已发布资料。'}${includeProblem?'\n我确认的问题信息：'+query.trim():''}\n请继续协助排查；这些是普通用户提供的信息，不代表已验证结果。`)}>让 AI 继续帮我排查</button><a className="button-link" href="#direct-ticket">直接提交人工工单</a></div>
    <small className="muted">AI 接续只预填普通消息；请先检查、修改，再自行发送。</small>
  </section>;
}
