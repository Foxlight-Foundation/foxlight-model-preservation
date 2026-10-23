import {useEffect,useRef,useState} from 'react';
import {api,bytes,errorText,uploadChunk,type Plan,type Upload} from './client';
import {Callout,Consent,Details,Icon,Label,Mono,Pill,Progress,Row,Sheet,Step} from './ui';

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
      for(const candidate of uploads.filter(item=>(item.purpose??'kit')==='kit'&&item.size_bytes===file.size).sort((a,b)=>b.offset-a.offset)) {
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
  return <Sheet kicker={publicImport?'Keep a community copy':'Recover your models'} title={publicImport?'Import a public bundle':'Restore from a recovery kit'} back={publicImport?'Back to Sharing':'Back to Library'} onClose={()=>{stop.current=true;onBack();}}>
    <p className="sk-lede">{publicImport?'Choose a signed public .foxkit. Every member must already be marked public. Importing keeps the original signatures and starts no sharing.':'Choose a recovery kit from a backup drive, NAS or your downloads. Skulk detects the format, and the original Core and registry don’t need to be around.'}</p>
    <label className="sk-drop" data-busy={busy?'true':undefined}><span className="sk-drop-icon"><Icon name="upload" size={22}/></span><span className="sk-drop-title">{busy?'Transferring…':publicImport?'Choose public bundle':'Choose recovery kit'}</span><span className="sk-drop-note">One file holds the complete model. Drop it here or browse.</span><input aria-label={publicImport?'Public bundle file':'Recovery kit file'} type="file" accept=".foxkit,application/octet-stream" disabled={busy} onChange={event=>{const file=event.target.files?.[0];if(file)void choose(file);event.target.value='';}}/></label>
    {progress&&<div role="status" className="sk-transfer"><div className="sk-transfer-line"><span><span className="sk-num">{bytes(progress.offset)}</span> of <span className="sk-num">{bytes(progress.size_bytes)}</span> transferred</span>{!busy&&!progress.complete&&<span className="sk-hint">Paused. Choose the same file to continue.</span>}</div><Progress value={progress.offset} max={progress.size_bytes} tone={busy?'live':progress.complete?'ok':'star'} label="Kit transfer progress"/>{busy&&<button className="sk-btn sk-quiet" onClick={()=>{stop.current=true;}}><Icon name="pause"/>Pause transfer</button>}</div>}
    {error&&<Callout tone="bad" role="alert">{errorText(error)} Completed transfer bytes are kept.</Callout>}
    <p className="sk-fine"><Icon name="info" size={14}/><span>{publicImport?'Core verifies every captured byte into a separate public copy. This doesn’t install the model or trust it to run.':'Next, you’ll review the model, its companions and their provenance before anything is restored.'}</span></p>
    <Details summary="Details">
      <p>{publicImport?'Core verifies every captured byte into a separate public copy. Cards remain unchanged, including metadata this version cannot load. This does not install or trust the model for inference.':'Skulk checks the signed model, required companions and retained provenance before making them available. Review the contents and permissions next.'}</p>
      <p>Kits contain public verification material, never the private signing key. They are not encrypted. Selecting a kit does not publish or share it.</p>
    </Details>
    {uploads.length>0&&<section aria-label="Retained kit transfers"><Label>Retained kit transfers · {uploads.length}</Label><p className="sk-hint">Select the same file to resume. Remove unused transfers after recovery to free storage.</p><ul className="sk-list">{uploads.map(item=><li className="sk-list-row sk-upload" key={item.upload_id}><span className="sk-list-main"><span><span className="sk-num">{bytes(item.offset)} / {bytes(item.size_bytes)}</span> · {item.complete?'Transferred':'Incomplete'}</span><Progress value={item.offset} max={item.size_bytes} tone={item.complete?'ok':'star'} label={`Retained transfer ${bytes(item.offset)} of ${bytes(item.size_bytes)}`}/></span><button className="sk-btn sk-quiet sk-sm" disabled={busy} onClick={()=>void remove(item)}><Icon name="trash" size={14}/>Remove transfer</button></li>)}</ul></section>}
  </Sheet>;
}

/** Capture trust, historical evidence and code permission are separate decisions. */
export function ReviewPlan({plan,busy,online,storageLabel,onBack,onStart}:{plan:Plan;busy:boolean;online:boolean;storageLabel?:string;onBack:()=>void;onStart:(accept:boolean,code:boolean,removed:boolean)=>void}) {
  const [accept,setAccept]=useState(false),[code,setCode]=useState(false),[removed,setRemoved]=useState(false);
  const recovering=plan.kind==='recover';
  const title=plan.kind==='preserve'?'Keep a verified copy':plan.kind==='kit_export'?'Save a recovery kit':recovering?'Recover this model':'Verify the saved files';
  const expired=plan.expires_at*1000<Date.now(),offline=!online&&plan.location_id!=='kit';
  const consents=(plan.requires_owner_acceptance&&!accept)||(plan.requires_code_permission&&!code)||((plan.deleted_aliases?.length??0)>0&&!removed);
  const blocked=busy||(!online&&plan.location_id!=='kit')||plan.expires_at*1000<Date.now()||(plan.requires_owner_acceptance&&!accept)||(plan.requires_code_permission&&!code)||((plan.deleted_aliases?.length??0)>0&&!removed);
  const note=expired?'This review expired. Close it and review again.':offline?'Reconnect this storage to continue.':consents?'Confirm each choice above to continue.':'You can leave while Skulk works.';
  const contents=<>
    <div className="sk-summary">
      {plan.archive_format&&<Row k="Format">{plan.archive_format==='pmf-v0.1'?'Portable Model Format (PMF)':'Preservation v1'}</Row>}
      <Row k={recovering?'Recover to':'Storage'}>{recovering?'This Core’s Model Store':plan.kind==='kit_export'?'Download after completion':storageLabel??plan.location_id}</Row>
      <Row k="Selected files"><span className="sk-num">{bytes(plan.size_bytes)}</span></Row>
      <Row k="Included members"><span className="sk-num">{plan.member_count}</span></Row>
    </div>
    <ul className="sk-members">{plan.members.map(member=><li key={member}><Icon name="check" size={14}/><span>{member}</span></li>)}</ul>
    {plan.archive_format==='pmf-v0.1'&&<p className="sk-fine"><Icon name="package" size={14}/><span>PMF keeps the complete model, companions, original cards and signed provenance together. Standalone tools can recover these files without Skulk. Runtime support and permissions are checked separately.</span></p>}
    {(plan.existing_companions?.length??0)>0&&<Callout>Matching companions already installed: {plan.existing_companions?.join(', ')}. Recovery prepares the complete verified generation and keeps the existing files intact for running readers.</Callout>}
    {plan.kind==='kit_export'&&<Callout tone="warn">Download the finished kit and keep it on separate storage. A kit left only on this Core can’t protect you if this Core’s storage is lost.</Callout>}
  </>;
  return <Sheet kicker={recovering?'Review recovery':plan.kind==='kit_export'?'Portable backup':plan.kind==='preserve'?'Preserve a model':'Check a copy'} title={title} subtitle={plan.model_id} back="Back to Library" onClose={onBack}
    footer={<><span className="sk-foot-note">{note}</span><button className="sk-btn sk-solid" disabled={Boolean(blocked)} onClick={()=>onStart(accept,code,removed)}>{busy?'Starting…':recovering?'Restore model':plan.kind==='kit_export'?'Create recovery kit':plan.kind==='preserve'?'Preserve model':'Check files'}</button></>}>
    {recovering?<>
      <Step n={1} title="What’s included">{contents}</Step>
      <Step n={2} title="Provenance & trust" aside={plan.publication==='registry-historical'?<Pill tone="info">historical evidence</Pill>:plan.publication==='registry-fresh'?<Pill tone="ok">registry verified</Pill>:<Pill tone="muted">owner capture</Pill>}>
        <p>{plan.publication==='registry-historical'?'Original registry signatures are retained. This is historical evidence; current registry status and revocations cannot be established offline.':plan.publication==='registry-fresh'?'Retained registry publication verified for this review.':'This is an owner capture. Original signed registry publication is unavailable for this copy.'}</p>
        <div className="sk-summary"><Row k="Signed file coverage">{plan.file_coverage==='complete-sha256'?'Complete':'Partial or unavailable'}</Row><Row k="During restore">Every captured file is hash-checked</Row></div>
        <Details summary="Public capture fingerprints"><div className="sk-keys">{plan.publisher_key_ids?.map(key=><Mono key={key} value={key} label="fingerprint"/>)}</div><p>Compare with an independently kept fingerprint when available. A kit and its keys can be replaced together; self-signatures alone do not identify the original owner.</p></Details>
      </Step>
      <Step n={3} title="Your permission">
        {!plan.requires_owner_acceptance&&!plan.requires_code_permission&&(plan.deleted_aliases?.length??0)===0&&<p>No extra permission is needed for this copy.</p>}
        {plan.requires_owner_acceptance&&<Consent checked={accept} onChange={setAccept}>I trust this exact recovery copy and accept its stated provenance limits.</Consent>}
        {plan.requires_code_permission&&<Consent checked={code} onChange={setCode} note="Leave this unticked to restore the files without allowing their code to run.">Separately, I allow this model’s repository code to run on this Core.</Consent>}
        {(plan.deleted_aliases?.length??0)>0&&<Consent checked={removed} onChange={setRemoved}>I want to restore these deliberately removed models: {plan.deleted_aliases?.join(', ')}.</Consent>}
        <p className="sk-fine"><Icon name="lock" size={14}/><span>Permissions from the old Core are not reused. Existing models and deliberate removals are protected.</span></p>
      </Step>
    </>:<>
      <Label>What’s included</Label>{contents}
      <p className="sk-fine"><Icon name="lock" size={14}/><span>{plan.kind==='kit_export'?'Exporting a kit starts no hosting.':'Nothing is shared or hosted.'} The copy keeps its original signed audience.</span></p>
      <Details summary="Details"><p>The copy keeps its original signed audience. Exporting a kit starts no hosting. File checks preserve captured identity; they do not grant new registry assurance or permission to run repository code.</p></Details>
    </>}
  </Sheet>;
}
