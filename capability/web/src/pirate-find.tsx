import {useEffect,useRef,useState} from 'react';
import {api,bytes} from './client';
import {HubFiles} from './hub-find';
import {Callout,Details,Help,Icon,Mono,Monogram,initials} from './ui';

export interface PirateResult {repository:string;name:string;owner:string;size_bytes:number;seeds:number;infohash:string;revision:string|null}
interface PirateFile {path:string;size_bytes:number;weight:boolean}
interface PirateReview {listed:boolean;repository:string;revision?:string|null;on_hub?:boolean;seeders?:number;infohash?:string;files?:PirateFile[];variants?:string[];default_files?:string[]}

const fileName=(path:string)=>path.split('/').at(-1)??path;

/** Pirate Face as a Find source: opt-in search, then Hugging Face's exact files, or the swarm for rescued models. */
export function PirateFind({query,enabled,swarm,busy,hidden=[],explainError,onEnabled,onSwarm,onPlan,onFound}:{query:string;enabled:boolean;swarm:boolean;busy:boolean;hidden?:string[];explainError:(code:string)=>string;onEnabled:()=>Promise<void>;onSwarm:()=>Promise<void>;onPlan:(body:Record<string,unknown>)=>Promise<void>;onFound?:(rows:PirateResult[])=>void}) {
  const [results,setResults]=useState<PirateResult[]|null>(null),[error,setError]=useState(''),[searching,setSearching]=useState(false),[open,setOpen]=useState<string|null>(null);
  const searches=useRef(0),found=useRef(onFound);found.current=onFound;
  const words=query.trim();
  useEffect(()=>{if(!enabled||!words){setResults(null);setError('');found.current?.([]);return;}const sequence=++searches.current;setSearching(true);const timer=setTimeout(()=>{void api<{results:PirateResult[]}>('desktop-pirate-face-search',{query:words}).then(value=>{if(sequence===searches.current){setResults(value.results);setError('');found.current?.(value.results);}}).catch(err=>{if(sequence===searches.current)setError(err instanceof Error?err.message:'pirate_face_unavailable');}).finally(()=>{if(sequence===searches.current)setSearching(false);});},450);return()=>{clearTimeout(timer);searches.current++;};},[enabled,words]);
  if(!enabled)return <section className="sk-card sk-hub" aria-label="Pirate Face">
    <div className="sk-model-head"><Monogram text="PF"/><div className="sk-model-title"><h2>Also search Pirate Face?</h2><p className="sk-meta">Community torrents of Hugging Face models, including models Hugging Face has removed.</p></div></div>
    <p className="sk-hint">Your search words go to pirateface.co. Sovereign checks every file against its fingerprint before keeping it.</p>
    <div className="sk-actions"><button className="sk-btn sk-solid" disabled={busy} onClick={()=>void onEnabled()}><Icon name="search"/>Search Pirate Face too</button></div>
  </section>;
  if(!words)return null;
  // A model also found on Hugging Face shows once, there, with its seed count.
  const shown=results?.filter(item=>!hidden.includes(item.repository));
  return <section className="sk-hub" aria-label="Results from Pirate Face">
    <h2 className="sk-section-title">From Pirate Face {searching&&<span className="sk-meta">· searching…</span>}<Help label="About Pirate Face">Pirate Face lists community torrents of Hugging Face models. Peers share the files, so a torrent can keep working after Hugging Face removes a model. Seed counts are Pirate Face’s own observations.</Help></h2>
    {error&&<Callout tone="bad" role="alert">{explainError(error)}</Callout>}
    {results&&shown?.length===0&&!searching&&<p className="sk-hint sk-pad">{results.length?'Every Pirate Face match is shown with Hugging Face above.':`No Pirate Face torrents match “${words}”.`}</p>}
    <div className="sk-cards">{shown?.map(item=><article className="sk-card sk-result" key={item.repository} aria-label={item.repository}>
      <div className="sk-model-head"><Monogram text={initials(item.name)}/><div className="sk-model-title"><h3>{item.name}</h3><p className="sk-meta">{item.owner} · {bytes(item.size_bytes)}</p></div><span className="sk-tag">{item.seeds} {item.seeds===1?'seed':'seeds'} on Pirate Face</span></div>
      {open===item.repository?<PirateFiles result={item} swarm={swarm} busy={busy} explainError={explainError} onSwarm={onSwarm} onClose={()=>setOpen(null)} onPlan={onPlan}/>:<div className="sk-actions"><button className="sk-btn sk-outline" disabled={busy} onClick={()=>setOpen(item.repository)}>Choose files</button></div>}
    </article>)}</div>
  </section>;
}

function PirateFiles({result,swarm,busy,explainError,onSwarm,onClose,onPlan}:{result:PirateResult;swarm:boolean;busy:boolean;explainError:(code:string)=>string;onSwarm:()=>Promise<void>;onClose:()=>void;onPlan:(body:Record<string,unknown>)=>Promise<void>}) {
  const [review,setReview]=useState<PirateReview|null>(null),[selected,setSelected]=useState<Set<string>>(new Set()),[variant,setVariant]=useState<string|null>(null),[error,setError]=useState('');
  useEffect(()=>{let live=true;void api<PirateReview>('desktop-pirate-face-review',{repository:result.repository,...(variant?{variant}:{})}).then(value=>{if(!live)return;setReview(value);setSelected(new Set(value.default_files??[]));setError('');}).catch(err=>{if(live)setError(err instanceof Error?err.message:'pirate_face_unavailable');});return()=>{live=false;};},[result.repository,variant]);
  if(error)return <><Callout tone="bad" role="alert">{explainError(error)}</Callout><div className="sk-actions"><button className="sk-btn sk-quiet" onClick={onClose}>Close</button></div></>;
  if(!review)return <p className="sk-hint" role="status">Reading this model’s torrent…</p>;
  if(!review.listed)return <><p className="sk-hint">Pirate Face no longer lists a torrent for this model.</p><div className="sk-actions"><button className="sk-btn sk-quiet" onClick={onClose}>Close</button></div></>;
  // Still on Hugging Face: the same exact, file-by-file review as any Hub model.
  if(review.on_hub)return <HubFiles repository={result.repository} revision={review.revision??result.revision} busy={busy} pirateFace explainError={explainError} onClose={onClose} onPlan={onPlan}/>;
  const files=review.files??[];
  const total=files.filter(file=>selected.has(file.path)).reduce((sum,file)=>sum+file.size_bytes,0);
  const toggle=(path:string)=>setSelected(current=>{const next=new Set(current);if(next.has(path))next.delete(path);else next.add(path);return next;});
  return <div className="sk-hub-files">
    <Callout tone="warn">Rescued: Hugging Face no longer has this model. It can be kept from the Pirate Face swarm, where peers still share it. Nothing independent can confirm where it came from, so you’ll be asked to acknowledge that before keeping it.</Callout>
    <p className="sk-meta">{review.seeders??0} {review.seeders===1?'seed':'seeds'} · version <span className="sk-num">{(review.revision??'').slice(0,10)}</span></p>
    {(review.variants?.length??0)>1&&<label className="sk-field"><span>Version</span><select className="sk-input" value={variant??review.variants?.find(name=>review.default_files?.includes(name))??''} disabled={busy} onChange={event=>setVariant(event.target.value)}>{review.variants?.map(name=><option key={name} value={name}>{fileName(name).replace(/\.gguf$/i,'')}</option>)}</select></label>}
    <Details summary={`Files · ${selected.size} of ${files.length} selected`}><ul className="sk-list">{files.map(file=><li className="sk-list-row" key={file.path}><label className="sk-check"><input type="checkbox" checked={selected.has(file.path)} disabled={busy} onChange={()=>toggle(file.path)}/> <span className="sk-wrap">{file.path}</span></label><span className="sk-meta">{bytes(file.size_bytes)}</span></li>)}</ul><p className="sk-hint">Only files Pirate Face recorded a Hugging Face fingerprint for are listed, so each one can be checked.</p></Details>
    {swarm?<div className="sk-actions"><button className="sk-btn sk-solid" disabled={busy||selected.size===0} onClick={()=>void onPlan({kind:'receive',origin:'pirateface',repository:result.repository,files:[...selected].sort()})}>Keep a copy · {bytes(total)}</button><button className="sk-btn sk-quiet" onClick={onClose}>Cancel</button></div>
      :<div className="sk-card"><h4>Turn on swarm downloads?</h4><p>Downloading from a public swarm shows this computer’s address to the other peers in it. Sovereign only downloads: it doesn’t share these files with the swarm, and nothing keeps running after the save. You choose this once.</p><div className="sk-actions"><button className="sk-btn sk-solid" disabled={busy} onClick={()=>void onSwarm()}>Turn on swarm downloads</button><button className="sk-btn sk-quiet" onClick={onClose}>Cancel</button></div></div>}
    <Details summary="Verification details"><p>Files come from Pirate Face peers and are each checked against the SHA-256 Pirate Face recorded from Hugging Face before they’re kept. The copy records that its origin couldn’t be confirmed.</p>{review.infohash&&<Mono value={review.infohash} label="torrent"/>}{review.revision&&<Mono value={review.revision} label="exact version"/>}</Details>
  </div>;
}
