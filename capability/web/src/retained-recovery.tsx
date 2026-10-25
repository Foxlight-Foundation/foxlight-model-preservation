import {useEffect, useRef, useState} from 'react';
import {api, bytes, errorText, type PublicationMember} from './client';
import {Callout, Consent, Details, Icon, Mono, Pill, Progress, Row, Sheet, Step, ago} from './ui';

export interface RetainedModel {
  manifest_id:string; state:'pending'|'retained'|'removed'; pinned:boolean; blocked:boolean;
  error_code:string|null; name?:string|null; member_count?:number|null; size_bytes?:number|null;
}
export interface RetainedJob {
  job_id:string; owner_id:string; kind:string; state:string; manifest_id:string|null;
  bytes_hashed:number; bytes_copied:number; error_code:string|null; kit_removed?:boolean;
  created_at?:number; updated_at?:number;
  kit?:{state:string; manifest_id:string; size_bytes:number; sha256:string}|null;
}
interface RetainedReview {
  manifest_id:string; name:string; size_bytes:number; workloads:string[];
  runtime_requirements:string[]; members:PublicationMember[];
  capture_key_ids:string[]; capture_threshold:number;
}
interface Catalog {
  configured:boolean; available:boolean; policy_id:string|null;
  models:RetainedModel[]; next_offset:number|null; controller_job_id?:string|null;
}
interface Selection {
  manifest_id:string; configured:boolean; available:boolean; policy_id:string|null;
  model:RetainedModel|null;
}
const active=(job:RetainedJob)=>['queued','running','cancelling'].includes(job.state);
const terminal=(job:RetainedJob)=>['succeeded','failed','cancelled','interrupted'].includes(job.state);
const filename=(job:RetainedJob)=>new URL(`api/mirror-kit/${encodeURIComponent(job.job_id)}`,location.href).href;
export const retainedName=(model:RetainedModel)=>model.name||`Retained model ${model.manifest_id.slice(0,12)}`;

/** Recover from custody even when the original import/copy receipt is gone. */
export function RetainedLibrary({blocked,onOverlayChange,onSharing}:{blocked:boolean;onOverlayChange:(open:boolean)=>void;onSharing:()=>void}) {
  const [supported,setSupported]=useState(false),[pendingSupported,setPendingSupported]=useState(false),[catalog,setCatalog]=useState<Catalog|null>(null);
  const [offset,setOffset]=useState(0),[error,setError]=useState('');
  const [selection,setSelection]=useState<{model:RetainedModel;policy:string}|null>(null);
  const overlay=useRef(onOverlayChange);overlay.current=onOverlayChange;
  useEffect(()=>{if(blocked)setSelection(null);},[blocked]);
  useEffect(()=>{overlay.current(Boolean(selection));return()=>overlay.current(false);},[Boolean(selection)]);
  useEffect(()=>{let current=true;void api<Record<string,boolean>>('core-features',{}).then(value=>{if(current){setSupported(value['Retained recovery']===true);setPendingSupported(value['Pending recovery']===true);}}).catch(()=>{});return()=>{current=false;};},[]);
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
        {model.state==='pending'&&(pendingSupported?<button className="sk-btn sk-outline" disabled={!catalog.available||!catalog.policy_id||Boolean(error)} onClick={()=>{if(catalog.policy_id)setSelection({model,policy:catalog.policy_id});}}><Icon name="check"/>Check local bytes</button>:<p className="sk-hint">Update the preservation host to check pending copies offline. Existing retained-copy and sharing controls remain available.</p>)}
      </article>)}</div>
      <div className="sk-actions">{offset>0&&<button className="sk-btn sk-quiet" onClick={()=>setOffset(0)}>First retained copies</button>}{catalog?.next_offset!=null&&<button className="sk-btn sk-quiet" onClick={()=>setOffset(catalog.next_offset!)}>Next retained copies</button>}</div>
    </section>
    {selection&&(selection.model.state==='pending'?<PendingRecovery model={selection.model} policyId={selection.policy} onClose={()=>setSelection(null)}/>:<RetainedRecovery model={selection.model} policyId={selection.policy} onClose={()=>setSelection(null)}/>)}
  </>;
}

/** Observe and explicitly check a selected pending closure without receiving bytes. */
export function PendingRecovery({model,policyId,onClose}:{model:RetainedModel;policyId:string;onClose:()=>void}) {
  const [review,setReview]=useState<RetainedReview|null>(null),[job,setJob]=useState<RetainedJob|null>(null);
  const [selection,setSelection]=useState<Selection|null>(null),[catalog,setCatalog]=useState<Catalog|null>(null);
  const [loaded,setLoaded]=useState(false),[revision,setRevision]=useState(0),[readError,setReadError]=useState(''),[error,setError]=useState('');
  const [busy,setBusy]=useState(false),[kit,setKit]=useState(false);
  const mounted=useRef(true),pending=useRef<{previous_job_id:string|null}|null>(null);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  useEffect(()=>{
    let current=true;setLoaded(false);setReadError('');
    void Promise.all([
      api<RetainedReview>('mirror-retained-review',{manifest_id:model.manifest_id,...(model.member_count?{member_count:model.member_count}:{})}),
      api<{manifest_id:string;job:RetainedJob|null}>('mirror-recovery-latest',{manifest_id:model.manifest_id}),
      api<Selection>('mirror-selection',{manifest_id:model.manifest_id}),
      api<Catalog>('mirror-read',{offset:0}),
    ]).then(([metadata,latest,selected,page])=>{if(current){setReview(metadata);setJob(latest.job);setSelection(selected);setCatalog(page);if(latest.job)pending.current=null;setLoaded(true);}}).catch(reason=>{if(current)setReadError(reason instanceof Error?reason.message:'host_unavailable');});
    return()=>{current=false;};
  },[model.manifest_id,model.member_count,revision]);
  useEffect(()=>{
    if(!job||!active(job))return;
    let current=true,timer:number|undefined;
    async function read(){
      try{
        const value=await api<RetainedJob>('mirror-observe',{job_id:job!.job_id});
        if(value.kind!=='recover'||value.manifest_id!==model.manifest_id)throw new Error('invalid_job_identity');
        if(current){setJob(value);setReadError('');if(active(value))timer=window.setTimeout(()=>void read(),2000);else setRevision(value=>value+1);}
      }catch(reason){if(current){setReadError(reason instanceof Error?reason.message:'host_unavailable');timer=window.setTimeout(()=>void read(),2000);}}
    }
    timer=window.setTimeout(()=>void read(),1000);return()=>{current=false;window.clearTimeout(timer);};
  },[job?.job_id,job?.state,model.manifest_id]);
  useEffect(()=>{
    if(!catalog?.controller_job_id)return;
    const timer=window.setTimeout(()=>setRevision(value=>value+1),2000);
    return()=>window.clearTimeout(timer);
  },[catalog?.controller_job_id,revision]);
  async function recover(){
    if(!pending.current)pending.current={previous_job_id:job?.job_id??null};
    const decision=pending.current;setBusy(true);setError('');
    try{
      const value=await api<RetainedJob>('mirror-recover',{manifest_id:model.manifest_id,policy_id:policyId,...(decision.previous_job_id?{previous_job_id:decision.previous_job_id}:{})});
      pending.current=null;
      if(mounted.current){setJob(value);if(!active(value))setRevision(value=>value+1);}
    }catch(reason){if(mounted.current)setError(reason instanceof Error?reason.message:'host_unavailable');}
    finally{if(mounted.current)setBusy(false);}
  }
  async function stop(jobId:string){
    setBusy(true);setError('');
    try{await api<RetainedJob>('mirror-stop',{job_id:jobId});if(mounted.current)setRevision(value=>value+1);}
    catch(reason){if(mounted.current)setError(reason instanceof Error?reason.message:'host_unavailable');}
    finally{if(mounted.current)setBusy(false);}
  }
  const available=loaded&&!readError&&selection?.configured&&selection.available&&catalog?.available;
  const currentPolicy=selection?.policy_id===policyId&&catalog?.policy_id===policyId;
  const ready=Boolean(available&&currentPolicy&&selection?.model?.state==='retained'&&(!job||terminal(job)));
  const canCheck=Boolean(available&&currentPolicy&&!busy&&!catalog?.controller_job_id&&selection?.model?.state==='pending'&&(!job||terminal(job)));
  return <><div inert={kit}><Sheet kicker="Pending mirror copy" title={review?.name??retainedName(model)} back="Close pending recovery" onClose={onClose} footer={<><button className="sk-btn sk-quiet" onClick={onClose}>Close</button>{ready?<button className="sk-btn sk-solid" disabled={busy} onClick={()=>setKit(true)}><Icon name="download"/>Save recovery kit</button>:<button className="sk-btn sk-solid" disabled={!canCheck} onClick={()=>void recover()}>{busy?'Working…':job&&active(job)?'Checking local bytes…':pending.current?'Retry the same check':job?'Check local bytes again':'Check local bytes'}</button>}</>}>
    {!loaded&&!readError&&<p role="status">Authenticating the original selection and finding earlier checks…</p>}
    {readError&&<Callout tone="bad" role="alert">{errorText(readError)}</Callout>}
    {loaded&&!selection?.available&&<Callout tone="warn">Mirror storage is unavailable or busy. Reconnect it and read again; this does not mean your bytes are gone.</Callout>}
    {loaded&&!currentPolicy&&<Callout tone="warn">Mirror settings changed. Close this review and reopen the copy to use its current policy.</Callout>}
    {loaded&&(!selection?.model||selection.model.state==='removed')&&<Callout tone="warn">This selection is no longer retained. Checking cannot return a deliberately removed model.</Callout>}
    {review&&<>
      <RetainedReviewDetails review={review} pending/>
      <Step n={2} title="Check the bytes already here">
        <p>This offline check uses the original accepted signatures and verifies every required file. It downloads nothing, starts no sharing and keeps pin, block and deletion choices. It clears permission to restart sharing.</p>
        {catalog?.controller_job_id&&<Callout tone="warn">Stop mirror sharing before checking. <button className="sk-btn sk-outline" disabled={busy} onClick={()=>void stop(catalog.controller_job_id!)}><Icon name="stop"/>Stop mirror sharing</button></Callout>}
        {job&&<article className="sk-card" aria-label="Pending copy check"><Row k="Local-byte check"><Pill tone={job.state==='succeeded'?'ok':job.state==='failed'?'bad':active(job)?'live':'muted'}>{job.state}</Pill></Row>{active(job)&&<Progress label="Checking retained local bytes"/>}<p className="sk-hint">Checked {bytes(job.bytes_hashed)} · copied {bytes(job.bytes_copied)}</p>{job.error_code&&<Callout tone="warn">{errorText(job.error_code)} Incomplete or corrupt copies stay pending; no replacement download is started.</Callout>}{(active(job)||job.state==='failed')&&<button className="sk-btn sk-outline" disabled={busy} onClick={()=>void stop(job.job_id)}><Icon name="stop"/>Stop this check</button>}<Details summary="Check receipt"><Mono value={job.job_id} label="pending recovery job identity"/></Details></article>}
        {ready?<Callout tone="ok">The complete signed copy is retained here. Save a recovery kit next to keep the model, companions, cards and provenance outside this host.</Callout>:<p>The check continues when this page closes. Return here to observe its outcome. After interruption, choose a new check explicitly; opening this page never starts one.</p>}
      </Step>
    </>}
    <button className="sk-btn sk-quiet" disabled={busy} onClick={()=>setRevision(value=>value+1)}>Read again</button>
    {error&&<Callout tone="bad" role="alert">{errorText(error)}</Callout>}
  </Sheet></div>{kit&&selection?.model&&<RetainedRecovery model={selection.model} policyId={policyId} onClose={()=>{setKit(false);setRevision(value=>value+1);}}/>}</>;
}

/** Metadata reads never start work. Each preparation is an explicit durable decision. */
export function RetainedRecovery({model,policyId,onClose}:{model:RetainedModel;policyId:string;onClose:()=>void}) {
  const [review,setReview]=useState<RetainedReview|null>(null),[job,setJob]=useState<RetainedJob|null>(null);
  const [loaded,setLoaded]=useState(false),[revision,setRevision]=useState(0),[readError,setReadError]=useState(''),[error,setError]=useState('');
  const [busy,setBusy]=useState(false),[remove,setRemove]=useState(false),[confirmed,setConfirmed]=useState(false);
  const [earlier,setEarlier]=useState(false);
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
  return <><div inert={earlier}><Sheet kicker="Retained mirror copy" title={review?.name??retainedName(model)} back="Close retained recovery" onClose={onClose} footer={<><button className="sk-btn sk-quiet" onClick={onClose}>Close</button>{ready&&!readError?<a className="sk-btn sk-solid" href={filename(job!)} download><Icon name="download"/>Download recovery kit</a>:<button className="sk-btn sk-solid" disabled={!canPrepare} onClick={()=>void prepare()}>{busy?'Working…':pending.current?'Retry the same preparation':job?'Prepare another kit':'Prepare recovery kit'}</button>}</>}>
    {!loaded&&!readError&&<p role="status">Authenticating retained metadata and finding earlier preparations…</p>}
    {readError&&<Callout tone="bad" role="alert">{errorText(readError)} <button className="sk-btn sk-quiet" disabled={busy} onClick={()=>setRevision(value=>value+1)}>Read again</button></Callout>}
    {review&&<>
      <RetainedReviewDetails review={review}/>
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
    <button className="sk-btn sk-quiet" disabled={busy} onClick={()=>setEarlier(true)}>Earlier recovery kits</button>
    <p className="sk-hint">Preparing another kit keeps earlier files. Open an earlier preparation to download or explicitly remove only its kit.</p>
    {error&&<Callout tone="bad" role="alert">{errorText(error)}</Callout>}
  </Sheet></div>{earlier&&<EarlierKits manifestId={model.manifest_id} onClose={()=>{setEarlier(false);setRevision(value=>value+1);}}/>}</>;
}

/** Page permanent receipts without treating an empty filtered page as absence. */
function EarlierKits({manifestId,onClose}:{manifestId:string;onClose:()=>void}) {
  const [offset,setOffset]=useState(0),[revision,setRevision]=useState(0);
  const [page,setPage]=useState<{jobs:RetainedJob[];next_offset:number|null}|null>(null),[error,setError]=useState('');
  const [selected,setSelected]=useState<string|null>(null);
  useEffect(()=>{
    let current=true;setPage(null);setError('');
    void api<{jobs:RetainedJob[];next_offset:number|null}>('mirror-history',{offset}).then(value=>{if(current)setPage(value);}).catch(reason=>{if(current)setError(reason instanceof Error?reason.message:'host_unavailable');});
    return()=>{current=false;};
  },[offset,revision]);
  const jobs=page?.jobs.filter(job=>job.kind==='prepare_kit'&&job.manifest_id===manifestId)??[];
  return <><div inert={Boolean(selected)}><Sheet kicker="Retained mirror copy" title="Earlier recovery kits" back="Back to retained recovery" onClose={onClose}>
    <p>Each preparation owns separate files. A newer or failed preparation does not replace an earlier kit. Removing a kit keeps the retained model and recipient files.</p>
    {!page&&!error&&<p role="status">Reading permanent preparation receipts…</p>}
    {error&&<Callout tone="bad" role="alert">{errorText(error)} <button className="sk-btn sk-quiet" onClick={()=>setRevision(value=>value+1)}>Read again</button></Callout>}
    {page&&jobs.length===0&&<p>No preparations for this model on this page. {page.next_offset!==null?'Older decisions remain available below.':'You have reached the end of this owner’s decisions.'}</p>}
    {jobs.map(job=><article className="sk-card" key={job.job_id} aria-label={`Recovery kit receipt ${job.job_id}`}><Row k="Preparation"><Pill tone={job.kit_removed?'muted':job.state==='succeeded'?'ok':active(job)?'live':'muted'}>{job.kit_removed?'kit removed':job.state}</Pill></Row>{job.created_at&&<p className="sk-meta">Prepared {ago(job.created_at)}</p>}{job.kit&&<Row k="Kit size">{bytes(job.kit.size_bytes)}</Row>}<Mono value={job.job_id} label="earlier preparation job identity"/><button className="sk-btn sk-outline" onClick={()=>setSelected(job.job_id)}>Recovery kit actions</button></article>)}
    <div className="sk-actions">{offset>0&&<button className="sk-btn sk-quiet" onClick={()=>setOffset(0)}>Latest decisions</button>}{page?.next_offset!=null&&<button className="sk-btn sk-quiet" onClick={()=>setOffset(page.next_offset!)}>Older decisions</button>}</div>
  </Sheet></div>{selected&&<MirrorKitReceipt jobId={selected} onClose={()=>{setSelected(null);setRevision(value=>value+1);}}/>}</>;
}

/** Keep each exact own-job kit manageable even after model/copy metadata loss. */
export function MirrorKitReceipt({jobId,onClose}:{jobId:string;onClose:()=>void}) {
  const [job,setJob]=useState<RetainedJob|null>(null),[readError,setReadError]=useState(''),[error,setError]=useState('');
  const [revision,setRevision]=useState(0),[busy,setBusy]=useState(false),[confirmed,setConfirmed]=useState(false);
  const mounted=useRef(true);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  useEffect(()=>{
    let current=true,timer:number|undefined;setReadError('');
    async function read(){
      try{const value=await api<RetainedJob>('mirror-observe',{job_id:jobId});if(value.kind!=='prepare_kit')throw new Error('invalid_job_identity');if(current){setJob(value);setReadError('');if(active(value))timer=window.setTimeout(()=>void read(),2000);}}
      catch(reason){if(current)setReadError(reason instanceof Error?reason.message:'host_unavailable');}
    }
    void read();return()=>{current=false;window.clearTimeout(timer);};
  },[jobId,revision]);
  async function control(action:'mirror-stop'|'mirror-kit-remove'){
    if(!job)return;setBusy(true);setError('');
    try{const value=await api<RetainedJob>(action,{job_id:jobId,...(action==='mirror-kit-remove'?{confirm_job_id:jobId}:{})});if(mounted.current){setJob(value);setConfirmed(false);setRevision(value=>value+1);}}
    catch(reason){if(mounted.current)setError(reason instanceof Error?reason.message:'host_unavailable');}
    finally{if(mounted.current)setBusy(false);}
  }
  const ready=job?.state==='succeeded'&&job.kit?.state==='prepared'&&job.kit_removed===false;
  return <Sheet kicker="Permanent preparation receipt" title="Recovery kit actions" back="Close recovery kit actions" onClose={onClose} footer={<><button className="sk-btn sk-quiet" onClick={onClose}>Close</button>{ready&&!readError&&<a className="sk-btn sk-solid" href={filename(job!)} download><Icon name="download"/>Download this recovery kit</a>}</>}>
    <p>This receipt refers only to this preparation. Newer preparations and missing import or model records do not remove its files.</p>
    {!job&&!readError&&<p role="status">Reading this preparation…</p>}
    {readError&&<Callout tone="bad" role="alert">{errorText(readError)} <button className="sk-btn sk-quiet" disabled={busy} onClick={()=>setRevision(value=>value+1)}>Read again</button></Callout>}
    {job&&<><Row k="Preparation state"><Pill tone={ready?'ok':active(job)?'live':'muted'}>{job.kit_removed?'kit removed':job.state}</Pill></Row>{job.created_at!=null&&<Row k="Prepared">{ago(job.created_at)}</Row>}{active(job)&&<Progress label="Owned recovery kit preparation"/>}{job.kit&&<Row k="Prepared file">{bytes(job.kit.size_bytes)}</Row>}<Details summary="Exact kit identity"><Mono value={jobId} label="selected preparation job identity"/>{job.manifest_id&&<Mono value={job.manifest_id} label="prepared model manifest identity"/>}{job.kit&&<Mono value={job.kit.sha256} label="selected kit SHA-256"/>}</Details>{job.error_code&&<Callout tone="warn">{errorText(job.error_code)}</Callout>}
      {(active(job)||job.state==='failed')&&<button className="sk-btn sk-outline" disabled={busy} onClick={()=>void control('mirror-stop')}>Stop this preparation</button>}
      {terminal(job)&&!job.kit_removed&&<><Consent checked={confirmed} disabled={busy||Boolean(readError)} onChange={setConfirmed}>Delete only this preparation’s kit files on this host. Keep the retained model and recipient files.</Consent><button className="sk-btn sk-outline" disabled={!confirmed||busy||Boolean(readError)} onClick={()=>void control('mirror-kit-remove')}>Remove only this kit</button></>}
    </>}
    <Callout>Kits are not encrypted. Verify the expected signed identity, fingerprints and threshold independently when restoring. A prepared-file digest does not grant model or registry trust.</Callout>
    {error&&<Callout tone="bad" role="alert">{errorText(error)}</Callout>}
  </Sheet>;
}

/** Display the full authenticated selection while keeping capture and runtime trust distinct. */
function RetainedReviewDetails({review,pending=false}:{review:RetainedReview;pending?:boolean}) {
  return <Step n={1} title="Complete retained selection"><Row k="Model files">{bytes(review.size_bytes)} · {review.members.length} members</Row><Mono value={review.manifest_id} label="retained recovery manifest identity"/><p>{pending?'This is the original signed selection, including every required companion and notice. Reading it does not check the local file bytes.':'Preparation rechecks all required bytes and preserves the original signed envelope, cards, extensions and companions. It creates a separate portable file on this host.'}</p>
        <Details summary="Members and declared terms">{review.members.map(member=><article className="sk-card sk-mirror-member" key={member.member_id}><h3>{member.member_id} · {member.role}</h3><Row k="Declared terms">{member.license_id}</Row><Row k="Terms reference">{member.terms_reference}</Row><p className="sk-meta">{member.repository} · {bytes(member.size_bytes)} · {member.file_count} files</p>{member.notice_paths.length>0&&<p>Required notices: {member.notice_paths.join(', ')}</p>}</article>)}</Details>
        <Details summary="Capture signatures and trust"><p>Capture signature threshold: {review.capture_threshold}. Included keys are verification material. Confirm the expected identity, fingerprints and threshold independently before restoring. A capture signature does not grant registry or runtime trust.</p>{review.capture_key_ids.map(key=><Mono key={key} value={key} label="capture key fingerprint"/>)}</Details>
      </Step>;
}
