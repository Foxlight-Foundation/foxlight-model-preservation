import {useEffect,useRef,useState} from 'react';
import {api,bytes} from './client';
import {Callout,Details,Help,Icon,Mono,Monogram,Pill,initials} from './ui';

export interface HubResult {repository:string;name:string;owner:string;downloads:number;likes:number;task:string|null;library:string|null;gated:boolean}
interface HubFile {path:string;size_bytes:number;weight:boolean}
interface HubReview {repository:string;revision:string;licence:string|null;gated:boolean;task:string|null;library:string|null;variants:string[];files:HubFile[];default_files:string[]}

const count=(value:number)=>value>=1e6?`${(value/1e6).toFixed(1)}M`:value>=1e3?`${Math.round(value/1e3)}k`:String(value);
const fileName=(path:string)=>path.split('/').at(-1)??path;

/** Hugging Face as a Find source: opt-in search, then an exact file review. */
export function HubFind({query,enabled,busy,kept={},explainError,onEnabled,onPlan}:{query:string;enabled:boolean;busy:boolean;kept?:Record<string,number>;explainError:(code:string)=>string;onEnabled:()=>Promise<void>;onPlan:(body:Record<string,unknown>)=>Promise<void>}) {
  const [results,setResults]=useState<HubResult[]|null>(null),[error,setError]=useState(''),[searching,setSearching]=useState(false),[open,setOpen]=useState<string|null>(null);
  const searches=useRef(0);
  const words=query.trim();
  useEffect(()=>{if(!enabled||!words){setResults(null);setError('');return;}const sequence=++searches.current;setSearching(true);const timer=setTimeout(()=>{void api<{results:HubResult[]}>('desktop-hub-search',{query:words}).then(value=>{if(sequence===searches.current){setResults(value.results);setError('');}}).catch(err=>{if(sequence===searches.current)setError(err instanceof Error?err.message:'hub_unavailable');}).finally(()=>{if(sequence===searches.current)setSearching(false);});},450);return()=>{clearTimeout(timer);searches.current++;};},[enabled,words]);
  if(!enabled)return <section className="sk-card sk-hub" aria-label="Hugging Face">
    <div className="sk-model-head"><Monogram text="HF"/><div className="sk-model-title"><h2>Also search Hugging Face?</h2><p className="sk-meta">Find any public model there, not only the listings saved on this device.</p></div></div>
    <p className="sk-hint">Your search words go to huggingface.co. Sovereign still pins each model to one exact version and checks every file before keeping it.</p>
    <div className="sk-actions"><button className="sk-btn sk-solid" disabled={busy} onClick={()=>void onEnabled()}><Icon name="search"/>Search Hugging Face too</button></div>
  </section>;
  if(!words)return null;
  return <section className="sk-hub" aria-label="Results from Hugging Face">
    <h2 className="sk-section-title">From Hugging Face {searching&&<span className="sk-meta">· searching…</span>}</h2>
    {error&&<Callout tone="bad" role="alert">{explainError(error)}</Callout>}
    {results?.length===0&&!searching&&<p className="sk-hint sk-pad">No public Hugging Face models match “{words}”.</p>}
    <div className="sk-cards">{results?.map(item=><article className="sk-card sk-result" key={item.repository} aria-label={item.repository}>
      <div className="sk-model-head"><Monogram text={initials(item.name)}/><div className="sk-model-title"><h3>{item.name}</h3><p className="sk-meta">{item.owner}{item.task&&` · ${item.task.replaceAll('-',' ')}`} · {count(item.downloads)} downloads</p></div><span className="sk-tag">Hugging Face</span>{kept[item.repository]?<span className="sk-tag">Also kept by {kept[item.repository]} {kept[item.repository]===1?'mirror':'mirrors'}</span>:null}</div>
      {item.gated&&<p className="sk-hint sk-warn">Hugging Face asks for an account and permission before downloading this model. If you’ve been given access, add your Hugging Face token under Source and privacy.</p>}
      {open===item.repository?<HubFiles repository={item.repository} busy={busy} explainError={explainError} onClose={()=>setOpen(null)} onPlan={onPlan}/>:<div className="sk-actions"><button className="sk-btn sk-outline" disabled={busy} onClick={()=>setOpen(item.repository)}>Choose files</button></div>}
    </article>)}</div>
  </section>;
}

function HubFiles({repository,busy,explainError,onClose,onPlan}:{repository:string;busy:boolean;explainError:(code:string)=>string;onClose:()=>void;onPlan:(body:Record<string,unknown>)=>Promise<void>}) {
  const [review,setReview]=useState<HubReview|null>(null),[selected,setSelected]=useState<Set<string>>(new Set()),[variant,setVariant]=useState<string|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(true);
  const reads=useRef(0);
  useEffect(()=>{const sequence=++reads.current;setLoading(true);void api<HubReview>('desktop-hub-review',{repository,...(review?{revision:review.revision}:{}),...(variant?{variant}:{})}).then(value=>{if(sequence!==reads.current)return;setReview(value);setSelected(new Set(value.default_files));setError('');}).catch(err=>{if(sequence===reads.current)setError(err instanceof Error?err.message:'hub_unavailable');}).finally(()=>{if(sequence===reads.current)setLoading(false);});return()=>{reads.current++;};
    // The pinned revision is reused once known; a variant change re-derives the default files.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[repository,variant]);
  if(error)return <><Callout tone="bad" role="alert">{explainError(error)}</Callout><div className="sk-actions"><button className="sk-btn sk-quiet" onClick={onClose}>Close</button></div></>;
  if(!review)return <p className="sk-hint" role="status">Reading this model’s files…</p>;
  const total=review.files.filter(file=>selected.has(file.path)).reduce((sum,file)=>sum+file.size_bytes,0);
  const toggle=(path:string)=>setSelected(current=>{const next=new Set(current);if(next.has(path))next.delete(path);else next.add(path);return next;});
  return <div className="sk-hub-files">
    <p className="sk-meta">Licence: {review.licence??'not stated'} · version <span className="sk-num">{review.revision.slice(0,10)}</span></p>
    {review.variants.length>1&&<label className="sk-field"><span>Version <Help label="About model versions">These are the same model compressed by different amounts. Q4_K_M is a good balance of size and quality; larger numbers keep more quality and take more space.</Help></span><select className="sk-input" value={variant??review.variants.find(name=>review.default_files.includes(name))??''} disabled={loading||busy} onChange={event=>setVariant(event.target.value)}>{review.variants.map(name=>{const size=review.files.filter(file=>file.path===name||file.path.startsWith(name.replace(/-00001-of-\d{5}\.gguf$/i,'')+'-')).reduce((sum,file)=>sum+file.size_bytes,0);return <option key={name} value={name}>{fileName(name).replace(/\.gguf$/i,'')} · {bytes(size)}</option>;})}</select></label>}
    <Details summary={`Files · ${selected.size} of ${review.files.length} selected`}><ul className="sk-list">{review.files.map(file=><li className="sk-list-row" key={file.path}><label className="sk-check"><input type="checkbox" checked={selected.has(file.path)} disabled={loading||busy} onChange={()=>toggle(file.path)}/> <span className="sk-wrap">{file.path}</span></label><span className="sk-meta">{bytes(file.size_bytes)}</span></li>)}</ul><p className="sk-hint">Sovereign picks the model’s files and skips extra copies of the same weights in other formats. Change the selection if you need them.</p></Details>
    <div className="sk-actions"><button className="sk-btn sk-solid" disabled={busy||loading||selected.size===0} onClick={()=>void onPlan({kind:'receive',origin:'hub',repository:review.repository,revision:review.revision,files:[...selected].sort()})}>Keep a copy · {bytes(total)}</button><button className="sk-btn sk-quiet" onClick={onClose}>Cancel</button></div>
    <Details summary="Verification details"><p>This copy comes straight from Hugging Face, not from a reviewed listing. Sovereign pins it to this exact version, checks every file against its fingerprint, and can fetch the same files from community mirrors if Hugging Face can’t provide them.</p><Mono value={review.revision} label="exact version"/></Details>
    {review.gated&&<Pill tone="bad">Access required on Hugging Face</Pill>}
  </div>;
}

/** A Hugging Face token: checked with Hugging Face, kept in the system keychain, never shown again. */
export function HubToken({user,busy,explainError,onChange}:{user:string|null;busy:boolean;explainError:(code:string)=>string;onChange:()=>Promise<void>}) {
  const [token,setToken]=useState(''),[working,setWorking]=useState(false),[error,setError]=useState('');
  async function run(action:string,body:Record<string,unknown>){setWorking(true);setError('');try{await api(action,body);setToken('');await onChange();}catch(err){setError(err instanceof Error?err.message:'hub_unavailable');}finally{setWorking(false);}}
  if(user)return <div className="sk-hub-token"><p>Connected to Hugging Face as <strong>{user}</strong>. Downloads use your account’s higher limits and can include models you’ve been given access to.</p><button className="sk-btn sk-quiet" disabled={busy||working} onClick={()=>void run('desktop-hub-token-remove',{})}>Remove Hugging Face token</button>{error&&<Callout tone="bad" role="alert">{explainError(error)}</Callout>}</div>;
  return <div className="sk-hub-token"><p>Optional: add a Hugging Face access token for faster downloads and models you’ve been given access to. Create a <strong>read</strong> token at huggingface.co under Settings → Access Tokens. Sovereign checks it with Hugging Face, keeps it in this computer’s keychain, and sends it only to huggingface.co.</p>
    <label className="sk-field" htmlFor="hub-token"><span>Hugging Face token</span><input id="hub-token" className="sk-input" type="password" autoComplete="off" spellCheck={false} placeholder="hf_…" maxLength={210} value={token} onChange={event=>setToken(event.target.value.trim())}/></label>
    <div className="sk-actions"><button className="sk-btn sk-outline" disabled={busy||working||!/^hf_[A-Za-z0-9]{20,200}$/.test(token)} onClick={()=>void run('desktop-hub-token-set',{token})}>{working?'Checking…':'Save token'}</button></div>
    {error&&<Callout tone="bad" role="alert">{explainError(error)}</Callout>}</div>;
}
