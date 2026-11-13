import {useEffect,useRef,useState} from 'react';
import {api,bytes} from './client';
import {Callout,Details,Help,Icon,Mono,Monogram,initials} from './ui';

export interface HeldModel {manifest:string;name:string;origin_repository:string|null;origin_revision:string|null;format:string;size_bytes:number;file_count:number;licence:string|null;workloads:string[];mirrors:string[];mirror_count:number}

let discovered=false;

/** Copies community mirrors keep, found from their signed holdings and searched on this device. */
export function HeldFind({query,busy,explainError,onPlan,onFound}:{query:string;busy:boolean;explainError:(code:string)=>string;onPlan:(body:Record<string,unknown>)=>Promise<void>;onFound?:(rows:HeldModel[])=>void}) {
  const [results,setResults]=useState<HeldModel[]|null>(null),[error,setError]=useState(''),[refreshing,setRefreshing]=useState(!discovered);
  const searches=useRef(0),found=useRef(onFound);found.current=onFound;
  const words=query.trim();
  // Discover community mirrors once per session; searching then stays on this device.
  useEffect(()=>{if(discovered)return;let live=true;void api('desktop-held-refresh',{}).catch(()=>undefined).finally(()=>{discovered=true;if(live)setRefreshing(false);});return()=>{live=false;};},[]);
  useEffect(()=>{if(refreshing)return;const sequence=++searches.current;const timer=setTimeout(()=>{void api<{results:HeldModel[]}>('desktop-held-search',{query:words}).then(value=>{if(sequence===searches.current){setResults(value.results);setError('');found.current?.(value.results);}}).catch(err=>{if(sequence===searches.current)setError(err instanceof Error?err.message:'held_copy_unavailable');});},250);return()=>{clearTimeout(timer);searches.current++;};},[words,refreshing]);
  if(refreshing)return <p className="sk-hint sk-pad" role="status">Looking for models that community mirrors keep…</p>;
  if(error)return <Callout tone="bad" role="alert">{explainError(error)}</Callout>;
  if(!results?.length)return words?<p className="sk-hint sk-pad">No community mirror lists a model matching “{words}”.</p>:null;
  return <section className="sk-held" aria-label="Kept by community mirrors">
    <h2 className="sk-section-title">Kept by community mirrors <Help label="About copies community mirrors keep">People running Sovereign mirrors publish a signed list of the models they keep, so a model stays findable after its original host removes it. Searching these lists happens on this device.</Help></h2>
    <div className="sk-cards">{results.map(model=><article className="sk-card sk-result" key={model.manifest} aria-label={model.name}>
      <div className="sk-model-head"><Monogram text={initials(model.name)}/><div className="sk-model-title"><h3>{model.name}</h3><p className="sk-meta">{model.origin_repository??'Origin not stated'} · {model.format.toUpperCase()} · {bytes(model.size_bytes)}{model.licence&&` · ${model.licence}`}</p></div><span className="sk-tag">Kept by {model.mirror_count} {model.mirror_count===1?'mirror':'mirrors'}</span></div>
      <div className="sk-actions"><button className="sk-btn sk-solid" disabled={busy} onClick={()=>void onPlan({kind:'receive',origin:'mirror',manifest:model.manifest})}><Icon name="download"/>Keep a copy</button></div>
      <Details summary="Verification details"><p>Names and origins are what the mirrors say. Before you keep it, Sovereign checks the origin against Hugging Face when it can, and every file is checked against its fingerprint while it downloads.</p><Mono value={model.manifest} label="copy identity"/>{model.origin_revision&&<Mono value={model.origin_revision} label="claimed version"/>}</Details>
    </article>)}</div>
  </section>;
}
