import {useEffect, useRef, useState} from 'react';
import {api, bytes, errorText, type PublicationMember} from './client';
import {Callout, Consent, Details, Icon, Mono, Pill, Progress, Row, Sheet, Step} from './ui';

export interface RetainedModel {
  manifest_id:string; state:'pending'|'retained'|'removed'; pinned:boolean; blocked:boolean;
  error_code:string|null; name?:string|null; member_count?:number|null; size_bytes?:number|null;
}
export interface RetainedJob {
  job_id:string; owner_id:string; kind:string; state:string; manifest_id:string|null;
  bytes_hashed:number; bytes_copied:number; error_code:string|null; kit_removed?:boolean;
  kit?:{state:string; manifest_id:string; size_bytes:number; sha256:string}|null;
}
interface RetainedReview {
  manifest_id:string; name:string; size_bytes:number; workloads:string[];
  runtime_requirements:string[]; members:PublicationMember[];
  capture_key_ids:string[]; capture_threshold:number;
}
interface Catalog {
  configured:boolean; available:boolean; policy_id:string|null;
  models:RetainedModel[]; next_offset:number|null;
}
const active=(job:RetainedJob)=>['queued','running','cancelling'].includes(job.state);
const terminal=(job:RetainedJob)=>['succeeded','failed','cancelled','interrupted'].includes(job.state);
const filename=(job:RetainedJob)=>new URL(`api/mirror-kit/${encodeURIComponent(job.job_id)}`,location.href).href;
export const retainedName=(model:RetainedModel)=>model.name||`Retained model ${model.manifest_id.slice(0,12)}`;

/** Recover from custody even when the original import/copy receipt is gone. */
export function RetainedLibrary({blocked,onOverlayChange,onSharing}:{blocked:boolean;onOverlayChange:(open:boolean)=>void;onSharing:()=>void}) {
  const [supported,setSupported]=useState(false),[catalog,setCatalog]=useState<Catalog|null>(null);
  const [offset,setOffset]=useState(0),[error,setError]=useState('');
  const [selection,setSelection]=useState<{model:RetainedModel;policy:string}|null>(null);
  const overlay=useRef(onOverlayChange);overlay.current=onOverlayChange;
  useEffect(()=>{if(blocked)setSelection(null);},[blocked]);
  useEffect(()=>{overlay.current(Boolean(selection));return()=>overlay.current(false);},[Boolean(selection)]);
  useEffect(()=>{let current=true;void api<Record<string,boolean>>('core-features',{}).then(value=>{if(current)setSupported(value['Retained recovery']===true);}).catch(()=>{});return()=>{current=false;};},[]);
  useEffect(()=>{
    if(!supported)return;
    let current=true,timer:number|undefined;
    async function read(){
      try{const value=await api<Catalog>('mirror-read',{offset});if(current){setCatalog(value);setError('');}}
      catch(reason){if(current)setError(reason instanceof Error?reason.message:'host_unavailable');}
      finally{if(current)timer=window.setTimeout(()=>void read(),2000);}
    }
    void read();return()=>{current=false;window.clearTimeout(timer);};
  },[supported,offset]);
  if(!supported||(!catalog?.configured&&!error))return null;
  return <>
    <section aria-labelledby="retained-library-title" inert={blocked||Boolean(selection)}>
      <div className="sk-page-head"><div><h2 id="retained-library-title">Retained mirror copies</h2><p>Recover complete signed copies kept on this host, including companions and provenance.</p></div><button className="sk-btn sk-quiet" onClick={onSharing}>Manage mirror</button></div>
      {error&&<Callout tone="bad" role="alert">{errorText(error)}</Callout>}
      {catalog&&!catalog.available&&<Callout tone="warn">Mirror storage is unavailable or busy. Reconnect it to inspect and recover retained copies. This does not mean your mirror is empty.</Callout>}
      {catalog?.available&&catalog.models.length===0&&<p className="sk-hint">No selections on this page. Manage your mirror to retain public models.</p>}
      <div className="sk-cards">{catalog?.models.map(model=><article className="sk-card" key={model.manifest_id} aria-label={`Retained copy ${retainedName(model)}`}>
        <div className="sk-model-head"><div className="sk-model-title"><h3>{retainedName(model)}</h3><p className="sk-meta">{model.state}{model.size_bytes!=null?` · ${bytes(model.size_bytes)}`:''}{model.member_count?` · ${model.member_count} members`:''}</p></div><Pill tone={model.state==='retained'?'ok':'muted'}>{model.state}</Pill></div>
        {model.error_code&&<Callout tone="warn">{errorText(model.error_code)}</Callout>}
        {model.state==='retained'&&<button className="sk-btn sk-outline" disabled={!catalog.available||!catalog.policy_id||Boolean(error)} onClick={()=>{if(catalog.policy_id)setSelection({model,policy:catalog.policy_id});}}><Icon name="download"/>Save recovery kit</button>}
      </article>)}</div>
      <div className="sk-actions">{offset>0&&<button className="sk-btn sk-quiet" onClick={()=>setOffset(0)}>First retained copies</button>}{catalog?.next_offset!=null&&<button className="sk-btn sk-quiet" onClick={()=>setOffset(catalog.next_offset!)}>Next retained copies</button>}</div>
    </section>
    {selection&&<RetainedRecovery model={selection.model} policyId={selection.policy} onClose={()=>setSelection(null)}/>}
  </>;
}

/** Metadata reads never start work. Each preparation is an explicit durable decision. */
export function RetainedRecovery({model,policyId,onClose}:{model:RetainedModel;policyId:string;onClose:()=>void}) {
  const [review,setReview]=useState<RetainedReview|null>(null),[job,setJob]=useState<RetainedJob|null>(null);
  const [loaded,setLoaded]=useState(false),[revision,setRevision]=useState(0),[readError,setReadError]=useState(''),[error,setError]=useState('');
  const [busy,setBusy]=useState(false),[remove,setRemove]=useState(false),[confirmed,setConfirmed]=useState(false);
  const mounted=useRef(true),pending=useRef<{previous_job_id:string|null}|null>(null);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  useEffect(()=>{
    let current=true;setLoaded(false);setReadError('');
    void Promise.all([
      api<RetainedReview>('mirror-retained-review',{manifest_id:model.manifest_id,...(model.member_count?{member_count:model.member_count}:{})}),
      api<{manifest_id:string;job:RetainedJob|null}>('mirror-kit-latest',{manifest_id:model.manifest_id}),
    ]).then(([metadata,latest])=>{if(current){setReview(metadata);setJob(latest.job);if(latest.job)pending.current=null;setLoaded(true);}}).catch(reason=>{if(current)setReadError(reason instanceof Error?reason.message:'host_unavailable');});
    return()=>{current=false;};
  },[model.manifest_id,model.member_count,revision]);
  useEffect(()=>{
    if(!job||!active(job))return;
    let current=true,timer:number|undefined;
    async function read(){
      try{const value=await api<RetainedJob>('mirror-observe',{job_id:job!.job_id});if(current){setJob(value);setReadError('');if(active(value))timer=window.setTimeout(()=>void read(),2000);}}
      catch(reason){if(current){setReadError(reason instanceof Error?reason.message:'host_unavailable');timer=window.setTimeout(()=>void read(),2000);}}
    }
    timer=window.setTimeout(()=>void read(),1000);return()=>{current=false;window.clearTimeout(timer);};
  },[job?.job_id,job?.state]);
  useEffect(()=>{setRemove(false);setConfirmed(false);},[job?.job_id]);
  async function prepare(){
    if(!pending.current)pending.current={previous_job_id:job?.job_id??null};
    const decision=pending.current;
    setBusy(true);setError('');
    try{
      const value=await api<RetainedJob>('mirror-prepare',{manifest_id:model.manifest_id,policy_id:policyId,...(decision.previous_job_id?{previous_job_id:decision.previous_job_id}:{})});
      pending.current=null;
      if(mounted.current)setJob(value);
    }catch(reason){if(mounted.current)setError(reason instanceof Error?reason.message:'host_unavailable');}
    finally{if(mounted.current)setBusy(false);}
  }
  async function control(action:'mirror-stop'|'mirror-kit-remove'){
    if(!job)return;
    setBusy(true);setError('');
    try{
      const value=await api<RetainedJob>(action,{job_id:job.job_id,...(action==='mirror-kit-remove'?{confirm_job_id:job.job_id}:{})});
      if(mounted.current){setJob(value);setRemove(false);setConfirmed(false);}
    }catch(reason){if(mounted.current)setError(reason instanceof Error?reason.message:'host_unavailable');}
    finally{if(mounted.current)setBusy(false);}
  }
  const ready=job?.state==='succeeded'&&job.kit?.state==='prepared'&&job.kit_removed===false;
  const canPrepare=loaded&&!readError&&!busy&&(!job||terminal(job));
  return <Sheet kicker="Retained mirror copy" title={review?.name??retainedName(model)} back="Close retained recovery" onClose={onClose} footer={<><button className="sk-btn sk-quiet" onClick={onClose}>Close</button>{ready&&!readError?<a className="sk-btn sk-solid" href={filename(job!)} download><Icon name="download"/>Download recovery kit</a>:<button className="sk-btn sk-solid" disabled={!canPrepare} onClick={()=>void prepare()}>{busy?'Working…':pending.current?'Retry the same preparation':job?'Prepare another kit':'Prepare recovery kit'}</button>}</>}>
    {!loaded&&!readError&&<p role="status">Authenticating retained metadata and finding earlier preparations…</p>}
    {readError&&<Callout tone="bad" role="alert">{errorText(readError)} <button className="sk-btn sk-quiet" disabled={busy} onClick={()=>setRevision(value=>value+1)}>Read again</button></Callout>}
    {review&&<>
      <Step n={1} title="Complete retained selection"><Row k="Model files">{bytes(review.size_bytes)} · {review.members.length} members</Row><Mono value={review.manifest_id} label="retained recovery manifest identity"/><p>Preparation rechecks all required bytes and preserves the original signed envelope, cards, extensions and companions. It creates a separate portable file on this host.</p>
        <Details summary="Members and declared terms">{review.members.map(member=><article className="sk-card sk-mirror-member" key={member.member_id}><h3>{member.member_id} · {member.role}</h3><Row k="Declared terms">{member.license_id}</Row><Row k="Terms reference">{member.terms_reference}</Row><p className="sk-meta">{member.repository} · {bytes(member.size_bytes)} · {member.file_count} files</p>{member.notice_paths.length>0&&<p>Required notices: {member.notice_paths.join(', ')}</p>}</article>)}</Details>
        <Details summary="Capture signatures and trust"><p>Capture signature threshold: {review.capture_threshold}. Included keys are verification material. Confirm the expected identity, fingerprints and threshold independently before restoring. A capture signature does not grant registry or runtime trust.</p>{review.capture_key_ids.map(key=><Mono key={key} value={key} label="capture key fingerprint"/>)}</Details>
      </Step>
      <Step n={2} title="Save a portable recovery kit">
        <p>The job continues when this page closes. Return here to find its result. Saving a kit starts no sharing and does not install a model.</p>
        <Callout>Kits contain model files and public verification material. They are not encrypted. Save the file somewhere you control; then use Restore here or the standalone model-preserve tools, even without the original Core or registry.</Callout>
        {job&&<article className="sk-card" aria-label="Retained recovery preparation"><Row k="Preparation"><Pill tone={ready?'ok':job.state==='failed'?'bad':active(job)?'live':'muted'}>{job.kit_removed?'kit removed':job.state}</Pill></Row>{active(job)&&<Progress label="Preparing retained recovery kit"/>}<p className="sk-hint">Checked {bytes(job.bytes_hashed)} · copied {bytes(job.bytes_copied)}</p>{job.error_code&&<Callout tone="warn">{errorText(job.error_code)}</Callout>}{ready&&<><Row k="Prepared file">{bytes(job.kit!.size_bytes)}</Row><Details summary="Prepared file integrity"><Mono value={job.kit!.sha256} label="prepared recovery kit SHA-256"/><p>This digest checks the kit file. Recipient trust is checked separately during restore.</p></Details></>}
          {(active(job)||job.state==='failed')&&<button className="sk-btn sk-outline" disabled={busy} onClick={()=>void control('mirror-stop')}><Icon name="stop"/>Stop this preparation</button>}
          <Details summary="Preparation receipt"><Mono value={job.job_id} label="retained preparation job identity"/></Details>
        </article>}
        {ready&&<button className="sk-btn sk-quiet" disabled={!canPrepare} onClick={()=>void prepare()}>Prepare another kit</button>}
        {job&&terminal(job)&&!job.kit_removed&&<div className="sk-actions"><button className="sk-btn sk-quiet" disabled={busy||Boolean(readError)} onClick={()=>{setRemove(value=>!value);setConfirmed(false);}}>Remove this prepared kit…</button></div>}
        {remove&&job&&<><Consent checked={confirmed} disabled={busy} onChange={setConfirmed}>Delete only this preparation’s kit files on this host. Keep the retained model and copies already saved elsewhere.</Consent><button className="sk-btn sk-outline" disabled={!confirmed||busy||Boolean(readError)} onClick={()=>void control('mirror-kit-remove')}>Remove only this kit</button></>}
      </Step>
    </>}
    {error&&<Callout tone="bad" role="alert">{errorText(error)}</Callout>}
  </Sheet>;
}
