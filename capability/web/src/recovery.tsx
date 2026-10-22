import {useEffect,useRef,useState} from 'react';
import {api,bytes,errorText,uploadChunk,type Plan,type Upload} from './client';

/** Explicit file selection; Core's flushed ingress survives all UI lifetimes. */
export function UploadKit({uploads,onReview,onBack,onChange,publicImport=false}:{publicImport?:boolean;uploads:Upload[];onReview:(plan:Plan)=>void;onBack:()=>void;onChange:()=>Promise<void>}) {
  const [busy,setBusy]=useState(false),[progress,setProgress]=useState<Upload|null>(null),[error,setError]=useState('');
  const stop=useRef(false);
  useEffect(()=>()=>{stop.current=true;},[]);
  async function choose(file:File) {
    stop.current=false;setBusy(true);setError('');
    try {
      let current:Upload|undefined;
      // Match a flushed prefix before skipping ahead. Full signed hashing,
      // rather than this resume aid, remains the integrity boundary.
      for(const candidate of uploads.filter(item=>item.size_bytes===file.size).sort((a,b)=>b.offset-a.offset)) {
        try {current=await api<Upload>('upload-status',{upload_id:candidate.upload_id});if(current.offset>0)current=await uploadChunk(current.upload_id,0,file.slice(0,Math.min(current.offset,4*1024**2)));break;}catch{current=undefined;}
      }
      if(!current)current=await api<Upload>('upload-create',{size_bytes:file.size});
      setProgress(current);
      while(current.offset<file.size&&!stop.current){current=await uploadChunk(current.upload_id,current.offset,file.slice(current.offset,Math.min(file.size,current.offset+4*1024**2)));setProgress(current);}
      await onChange();if(stop.current)return;
      const reviewed=await api<Plan>(publicImport?'sharing-plan':'plan',publicImport?{kind:'mirror_import',location_id:'public-kit',upload_id:current.upload_id}:{kind:'recover',location_id:'kit',upload_id:current.upload_id,historical:true});
      if(!stop.current)onReview(reviewed);
    }catch(err){setError(err instanceof Error?err.message:'kit_transfer_refused');}finally{setBusy(false);}
  }
  async function remove(item:Upload){try{await api('upload-remove',{upload_id:item.upload_id});if(progress?.upload_id===item.upload_id)setProgress(null);await onChange();}catch(err){setError(err instanceof Error?err.message:'kit_transfer_refused');}}
  return <section className="mp-flow" aria-label={publicImport?'Import a public bundle':'Restore from recovery kit'}>
    <button className="mp-button mp-quiet" onClick={()=>{stop.current=true;onBack();}}>← Back to Library</button><span className="mp-kicker">{publicImport?'Keep a community copy':'Recover your models'}</span><h2>{publicImport?'Import a public bundle':'Restore from a recovery kit'}</h2>
    <p className="mp-muted">{publicImport?'Choose a signed public .foxkit. Every member must already declare a public audience. Importing preserves the original signatures and starts no sharing.':'Choose a kit from your backup drive, NAS or downloaded files. The original Core and registry do not need to be available.'}</p>
    <div className="mp-summary"><h3>One file. The complete model.</h3><p>{publicImport?'Core verifies every captured byte into a separate public copy. Cards remain unchanged, including metadata this version cannot load. This does not install or trust the model for inference.':'Skulk checks the signed model, required companions and retained provenance before making them available. Review the contents and permissions next.'}</p><label className="mp-button mp-primary mp-file"><span>{busy?'Transferring…':publicImport?'Choose public bundle':'Choose recovery kit'}</span><input aria-label={publicImport?'Public bundle file':'Recovery kit file'} type="file" accept=".foxkit,application/octet-stream" disabled={busy} onChange={event=>{const file=event.target.files?.[0];if(file)void choose(file);event.target.value='';}}/></label></div>
    {progress&&<div role="status"><p>{bytes(progress.offset)} of {bytes(progress.size_bytes)} transferred{!busy&&!progress.complete?' · Paused. Choose the same file to continue.':''}</p><progress max={progress.size_bytes} value={progress.offset} aria-label="Kit transfer progress"/>{busy&&<button className="mp-button" onClick={()=>{stop.current=true;}}>Pause transfer</button>}</div>}
    {error&&<p role="alert" className="mp-notice mp-error">{errorText(error)} Completed transfer bytes are kept.</p>}
    {uploads.length>0&&<details><summary>Retained kit transfers · {uploads.length}</summary><p className="mp-muted mp-small">Select the same file to resume. Remove unused transfers after recovery to free storage.</p>{uploads.map(item=><div className="mp-line" key={item.upload_id}><span>{bytes(item.offset)} / {bytes(item.size_bytes)} · {item.complete?'Transferred':'Incomplete'}</span><button className="mp-button mp-quiet" disabled={busy} onClick={()=>void remove(item)}>Remove transfer</button></div>)}</details>}
    <p className="mp-small mp-muted">Kits contain public verification material, never the private signing key. They are not encrypted. Selecting a kit does not publish or share it.</p>
  </section>;
}

/** Capture trust, historical evidence and code permission are separate decisions. */
export function ReviewPlan({plan,busy,online,onBack,onStart}:{plan:Plan;busy:boolean;online:boolean;onBack:()=>void;onStart:(accept:boolean,code:boolean,removed:boolean)=>void}) {
  const [accept,setAccept]=useState(false),[code,setCode]=useState(false),[removed,setRemoved]=useState(false);
  const recovering=plan.kind==='recover';
  const title=plan.kind==='preserve'?'Keep a verified copy':plan.kind==='kit_export'?'Save a recovery kit':recovering?'Recover this model':'Verify the saved files';
  const blocked=busy||(!online&&plan.location_id!=='kit')||plan.expires_at*1000<Date.now()||(plan.requires_owner_acceptance&&!accept)||(plan.requires_code_permission&&!code)||((plan.deleted_aliases?.length??0)>0&&!removed);
  return <section className="mp-flow" aria-label="Review preservation plan"><button className="mp-button mp-quiet mp-back" onClick={onBack}>← Back to Library</button><span className="mp-kicker">{recovering?'Review recovery':plan.kind==='kit_export'?'Portable backup':plan.kind==='preserve'?'Preserve a model':'Check a copy'}</span><h2>{title}</h2><p className="mp-muted">{plan.model_id}</p>
    <div className="mp-summary"><div className="mp-line"><span>{recovering?'Recover to':'Storage'}</span><strong>{recovering?'This Core’s Model Store':plan.kind==='kit_export'?'Download after completion':'This device'}</strong></div><div className="mp-line"><span>Selected files</span><strong>{bytes(plan.size_bytes)}</strong></div><div className="mp-line"><span>Included members</span><strong>{plan.member_count}</strong></div>{plan.members.map(member=><div className="mp-line" key={member}><span>{member}</span><span>Included</span></div>)}</div>
    {(plan.existing_companions?.length??0)>0&&<p className="mp-notice">Matching companions already installed: {plan.existing_companions?.join(', ')}. Recovery prepares the complete verified generation and keeps the existing files intact for running readers.</p>}
    {plan.kind==='kit_export'&&<p className="mp-notice mp-warn">Download the completed kit and keep it on separate storage. A copy left only on this Core cannot protect against loss of this Core’s storage.</p>}
    {recovering?<><div className="mp-summary"><h3>Provenance &amp; trust</h3><p>{plan.publication==='registry-historical'?'Original registry signatures are retained. This is historical evidence; current registry status and revocations cannot be established offline.':plan.publication==='registry-fresh'?'Retained registry publication verified for this review.':'This is an owner capture. Original signed registry publication is unavailable for this copy.'}</p><p className="mp-small mp-muted">All captured files are hash-checked during restore. Original signed file coverage: {plan.file_coverage==='complete-sha256'?'complete':'partial or unavailable'}.</p><details><summary>Public capture fingerprints</summary>{plan.publisher_key_ids?.map(key=><code className="mp-key" key={key}>{key}</code>)}<p className="mp-small">Compare with an independently kept fingerprint when available. A kit and its keys can be replaced together; self-signatures alone do not identify the original owner.</p></details></div>
    {plan.requires_owner_acceptance&&<label className="mp-consent"><input type="checkbox" checked={accept} onChange={event=>setAccept(event.target.checked)}/><span>I trust this exact recovery copy and accept its stated provenance limits.</span></label>}
    {plan.requires_code_permission&&<label className="mp-consent"><input type="checkbox" checked={code} onChange={event=>setCode(event.target.checked)}/><span>Separately, I allow this model’s repository code to run on this Core.</span></label>}
    {(plan.deleted_aliases?.length??0)>0&&<label className="mp-consent"><input type="checkbox" checked={removed} onChange={event=>setRemoved(event.target.checked)}/><span>I want to restore these deliberately removed models: {plan.deleted_aliases?.join(', ')}.</span></label>}
    <p className="mp-small mp-muted">Permissions from the old Core are not reused. Existing models and deliberate removals are protected.</p></>:<p className="mp-muted">This copy stays private. File checks preserve captured identity; they do not grant new registry assurance or permission to run repository code.</p>}
    <div className="mp-finish"><span className="mp-small mp-muted">You can leave while Skulk works.</span><button className="mp-button mp-primary" disabled={Boolean(blocked)} onClick={()=>onStart(accept,code,removed)}>{busy?'Starting…':recovering?'Restore model':plan.kind==='kit_export'?'Create recovery kit':plan.kind==='preserve'?'Preserve model':'Check files'}</button></div>
  </section>;
}
