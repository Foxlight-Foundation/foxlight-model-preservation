import {useEffect, useRef, useState} from 'react';
import {api, bytes, errorText, type Copy, type PublicationMember} from './client';
import {PeerAccess, type PeerGrant} from './peer';
import {RetainedRecovery, type RetainedModel} from './retained-recovery';
import {Callout, Consent, Details, Icon, Label, Menu, Mono, Pill, Progress, Row, Sheet, Step, ago} from './ui';

interface MirrorConfiguration {
  name:string; disk_bytes:number; ingress_daily:number; egress_daily:number;
  egress_monthly:number; http:boolean; exposure:'loopback'|'network'; port:number;
  ipfs:boolean; torrent:boolean; peer_setup_id:string|null; max_active:number;
  session_seconds:number; session_bytes:number; upload_rate:number;
}
type MirrorModel=RetainedModel;
interface MirrorActivity {running:boolean; http_port:number|null; native_workers:number}
interface MirrorPage {
  schema_version:'skulk.preservation-mirror.v1'; configured:boolean; available:boolean;
  policy_id:string|null; configuration:MirrorConfiguration|null; paused:boolean;
  restart_expires:number|null; restart_allowed:boolean; controller_job_id:string|null;
  activity:MirrorActivity|null; models:MirrorModel[]; model_count:number; next_offset:number|null;
}
interface MirrorJob {
  job_id:string; owner_id:string; operation_id:string; kind:string; state:string;
  created_at:number; updated_at:number; manifest_id:string|null; activity:MirrorActivity|null;
  error_code:string|null; bytes_hashed:number; bytes_copied:number; bytes_received:number;
}
interface MirrorHistory {jobs:MirrorJob[]; next_offset:number|null}
interface PublicReview {
  schema_version:'skulk.preservation-public-review.v1'; copy_id:string; manifest_id:string;
  archive_format:'preservation-v1'|'pmf-v0.1'; name:string; size_bytes:number;
  workloads:string[]; runtime_requirements:string[]; members:PublicationMember[];
}
type ModelAction='pin'|'unpin'|'block'|'unblock'|'retry';
type MirrorDecision =
  | {kind:'configure'; policy_id:string|null; configuration:MirrorConfiguration}
  | {kind:'retain'; policy_id:string; copy_id:string; manifest_id:string; confirm_redistribution:true; confirm_reintake:boolean}
  | {kind:'run'; policy_id:string; public_participation:true; mode:'finite'|'resume'; seconds:number}
  | {kind:'grant_resume'; policy_id:string; confirm_resume:true; resume_expires:number}
  | {kind:'revoke_resume'; policy_id:string}
  | {kind:'model'; policy_id:string; manifest_id:string; action:ModelAction}
  | {kind:'prune'; policy_id:string; manifest_id:string; confirm_delete_manifest:string};
type Dialog =
  | {kind:'configure'; policy_id:string|null; configuration:MirrorConfiguration|null}
  | {kind:'recovery'; policy_id:string; model:MirrorModel}
  | {kind:'retain'; policy_id:string; copy:Copy; removed:boolean}
  | {kind:'run'|'grant_resume'|'revoke_resume'; policy_id:string; configuration:MirrorConfiguration; expires:number|null}
  | {kind:'model'|'prune'; policy_id:string; model:MirrorModel; name:string; action?:ModelAction};
const active=(job:MirrorJob)=>['queued','running','cancelling'].includes(job.state);
const titles:Record<string,string>={configure:'Save mirror settings',retain:'Keep public copy',run:'Share retained copies',grant_resume:'Allow sharing after restart',revoke_resume:'Remove restart permission',model:'Change retained selection',prune:'Remove mirror copy'};
const gibibyte=1024**3;

/** Durable sharing controls; opening, polling and saving settings stay offline. */
export function PersistentMirrors({copies,features,blocked,onOverlayChange,bittorrentAvailable}:{copies:Copy[];features:Record<string,boolean>|null;blocked:boolean;onOverlayChange:(open:boolean)=>void;bittorrentAvailable:boolean}) {
  const [page,setPage]=useState<MirrorPage|null>(null),[history,setHistory]=useState<MirrorHistory|null>(null);
  const [offset,setOffset]=useState(0),[historyOffset,setHistoryOffset]=useState(0),[revision,setRevision]=useState(0);
  const [dialog,setDialog]=useState<Dialog|null>(null),[submitting,setSubmitting]=useState(false),[stopping,setStopping]=useState<Set<string>>(()=>new Set()),[error,setError]=useState(''),[readError,setReadError]=useState('');
  const [checking,setChecking]=useState<string|null>(null);
  const live=useRef(true),pending=useRef<{key:string;id:string}|null>(null),overlay=useRef(onOverlayChange);overlay.current=onOverlayChange;
  const supported=features?.['Persistent mirrors']===true;
  const recoverySupported=features?.['Retained recovery']===true;
  useEffect(()=>{live.current=true;return()=>{live.current=false;};},[]);
  useEffect(()=>{overlay.current(Boolean(dialog));return()=>overlay.current(false);},[Boolean(dialog)]);
  useEffect(()=>{
    if(!supported)return;
    let current=true,timer:number|undefined;
    async function read(){
      try{
        const [snapshot,jobs]=await Promise.all([
          api<MirrorPage>('mirror-read',{offset}),
          api<MirrorHistory>('mirror-history',{offset:historyOffset}),
        ]);
        if(current){setPage(snapshot);setHistory(jobs);setReadError('');}
      }catch(reason){if(current)setReadError(reason instanceof Error?reason.message:'host_unavailable');}
      finally{if(current)timer=window.setTimeout(()=>void read(),2000);}
    }
    void read();
    return()=>{current=false;window.clearTimeout(timer);};
  },[supported,offset,historyOffset,revision]);

  /** Retain the operation identity after a lost reply; never automatically replay. */
  async function submit(decision:MirrorDecision){
    const key=JSON.stringify(decision);
    if(pending.current?.key!==key)pending.current={key,id:crypto.randomUUID()};
    const operation=pending.current.id;
    setSubmitting(true);setError('');
    try{
      const job=await api<MirrorJob>('mirror-start',{...decision,operation_id:operation});
      if(job.operation_id!==operation)throw new Error('invalid_job_identity');
      pending.current=null;
      if(live.current){setDialog(null);setHistory(current=>({jobs:[job,...(current?.jobs??[]).filter(item=>item.job_id!==job.job_id)].slice(0,20),next_offset:current?.next_offset??null}));setRevision(value=>value+1);}
    }catch(reason){if(live.current)setError(reason instanceof Error?reason.message:'host_unavailable');}
    finally{if(live.current)setSubmitting(false);}
  }
  /** Stop the exact observed controller, including failed receipts still owning work. */
  async function stop(jobId:string){
    setStopping(current=>new Set(current).add(jobId));setError('');
    try{await api<MirrorJob>('mirror-stop',{job_id:jobId});}
    catch(reason){if(live.current)setError(reason instanceof Error?reason.message:'host_unavailable');}
    finally{if(live.current){setStopping(current=>{const next=new Set(current);next.delete(jobId);return next;});setRevision(value=>value+1);}}
  }
  const controller=page?.controller_job_id??null;
  const working=Boolean(controller)||Boolean(history?.jobs.some(active));
  const configuration=page?.configuration??null,policy=page?.policy_id??null;
  const name=(identity:string)=>page?.models.find(model=>model.manifest_id===identity)?.name||copies.find(copy=>copy.manifest_id===identity)?.model_id||`Retained model ${identity.slice(0,12)}`;
  async function reviewIntake(copy:Copy){
    if(!policy)return;
    setChecking(copy.copy_id);setError('');
    try{
      const exact=await api<{manifest_id:string;configured:boolean;available:boolean;policy_id:string|null;model:MirrorModel|null}>('mirror-selection',{manifest_id:copy.manifest_id});
      if(!exact.available||!exact.configured)throw new Error('mirror_selection_unavailable');
      if(exact.policy_id!==policy)throw new Error('mirror_policy_changed');
      if(exact.model&&exact.model.state!=='removed')throw new Error('mirror_selection_already_kept');
      if(live.current)open({kind:'retain',policy_id:policy,copy,removed:exact.model?.state==='removed'});
    }catch(reason){if(live.current)setError(reason instanceof Error?reason.message:'host_unavailable');}
    finally{if(live.current)setChecking(null);}
  }
  function open(next:Dialog){setError('');setDialog(next);}
  if(features===null)return null;
  if(!supported)return <section aria-labelledby="persistent-mirrors-title"><h2 id="persistent-mirrors-title">Keep models available</h2><p className="sk-hint">Update the preservation host to manage a persistent mirror here. Existing finite sessions remain above.</p></section>;
  return <>
    <section aria-labelledby="persistent-mirrors-title" inert={blocked||Boolean(dialog)}>
      <div className="sk-page-head"><div><h2 id="persistent-mirrors-title">Keep models available</h2><p>Retain public copies here, then choose when and how to share them.</p></div><div className="sk-page-actions"><button className="sk-btn sk-outline" disabled={!page||working||Boolean(page.configured&&!page.available)} onClick={()=>open({kind:'configure',policy_id:policy,configuration})}><Icon name="storage"/>{page?.configured?'Mirror settings':'Set up mirror'}</button></div></div>
      {(error||readError)&&!dialog&&<Callout tone="bad" role="alert">{errorText(error||readError)}</Callout>}
      {!page&&<p className="sk-hint" role="status">Reading this host’s mirror…</p>}
      {page&&<>
        <article className="sk-card">
          <div className="sk-model-head"><div className="sk-model-title"><h3>{configuration?.name??'Your mirror'}</h3><p className="sk-meta">{page.configured?'Separate retained copies on this host':'Choose storage and transfer allowances to begin'}</p></div><Pill tone={controller?'live':'muted'}>{controller?'active':'stopped'}</Pill></div>
          {configuration&&<><Row k="Storage allowance">{bytes(configuration.disk_bytes)}</Row><Row k="Upload allowances">{bytes(configuration.egress_daily)} per day · {bytes(configuration.egress_monthly)} per month</Row><Row k="Transports">{[configuration.http?'HTTP':null,configuration.ipfs?'IPFS':null,configuration.torrent?'BitTorrent':null].filter(Boolean).join(' · ')||'None selected'}</Row></>}
          {page.activity&&<Details summary="Local sharing activity"><p className="sk-hint">Local observation: {page.activity.http_port?`HTTP port ${page.activity.http_port} · `:''}{page.activity.native_workers} native {page.activity.native_workers===1?'worker':'workers'}. This does not establish public reachability or independent replicas.</p></Details>}
          {!page.available&&page.configured&&<Callout tone="warn">Mirror storage is unavailable or busy. Retained copies are kept. Stop remains available while a controller is owned.</Callout>}
          <div className="sk-actions">
            {controller?<button className="sk-btn sk-outline" disabled={stopping.has(controller)} onClick={()=>void stop(controller)}><Icon name="stop"/>{stopping.has(controller)?'Stopping actual sharing…':'Stop sharing'}</button>:<button className="sk-btn sk-solid" disabled={!policy||!configuration||working||!page.available||!(configuration.http||configuration.ipfs||configuration.torrent)} onClick={()=>{if(policy&&configuration)open({kind:'run',policy_id:policy,configuration,expires:page.restart_expires});}}><Icon name="share"/>Start sharing</button>}
            <button className="sk-btn sk-quiet" disabled={!policy||!configuration||working} onClick={()=>{if(policy&&configuration)open({kind:'grant_resume',policy_id:policy,configuration,expires:page.restart_expires});}}>Restart permission</button>
            {page.restart_expires!==null&&<button className="sk-btn sk-quiet" disabled={!policy||!configuration||working} onClick={()=>{if(policy&&configuration)open({kind:'revoke_resume',policy_id:policy,configuration,expires:page.restart_expires});}}>Remove restart permission</button>}
          </div>
          <p className="sk-hint">Stop keeps retained copies and clears running intent. Saving settings or granting restart permission never starts sharing.</p>
          {page.restart_expires!==null&&<p className="sk-hint">Stored permission expires {new Date(page.restart_expires*1000).toLocaleString()}. {page.restart_allowed?'The current unpaused policy permits resume.':'Sharing remains subject to the current policy and a separate Start.'}</p>}
        </article>
        <Label as="h3">Mirror selections{page.available?` · ${page.model_count}`:''}</Label>
        {!page.available&&page.configured&&<p className="sk-hint">Reconnect mirror storage to inspect its retained selections. An unavailable observation does not mean the mirror is empty.</p>}
        {page.available&&page.models.length===0&&<p className="sk-hint">No retained selections yet. Choose a public Library copy below.</p>}
        <div className="sk-cards">{page.models.map(model=><article className="sk-card" key={model.manifest_id} aria-label={`Mirror copy ${name(model.manifest_id)}`}>
          <div className="sk-model-head"><div className="sk-model-title"><h3>{name(model.manifest_id)}</h3><p className="sk-meta">{model.state}{model.pinned?' · protected from removal':''}{model.blocked?' · blocked from serving':''}</p></div><Menu label={`Mirror controls for ${name(model.manifest_id)}`} items={model.state==='removed'?[]:[
            {label:model.pinned?'Unpin mirror copy':'Pin mirror copy',icon:'lock',disabled:working,onSelect:()=>{if(policy)open({kind:'model',policy_id:policy,model,name:name(model.manifest_id),action:model.pinned?'unpin':'pin'});}},
            {label:model.blocked?'Allow future serving':'Block future serving',icon:'pause',disabled:working,onSelect:()=>{if(policy)open({kind:'model',policy_id:policy,model,name:name(model.manifest_id),action:model.blocked?'unblock':'block'});}},
            ...(model.state==='pending'?[{label:'Retry retained intake',icon:'refresh',disabled:working,onSelect:()=>{if(policy)open({kind:'model',policy_id:policy,model,name:name(model.manifest_id),action:'retry'});}}]:[]),
            {label:'Remove mirror copy',icon:'trash',disabled:working||model.pinned,onSelect:()=>{if(policy)open({kind:'prune',policy_id:policy,model,name:name(model.manifest_id)});}},
          ]}/></div>
          {model.error_code&&<Callout tone="warn">{errorText(model.error_code)}</Callout>}
          {model.state==='retained'&&recoverySupported&&<button className="sk-btn sk-outline" disabled={!page.available||!policy} onClick={()=>{if(policy)open({kind:'recovery',policy_id:policy,model});}}><Icon name="download"/>Save recovery kit</button>}
          <Details summary="Exact retained identity"><Mono value={model.manifest_id} label="retained manifest identity"/><p>Source changes and stopping sharing do not delete this selection. Removed identities stay recorded; returning one requires a new explicit intake.</p></Details>
        </article>)}</div>
        <div className="sk-actions">{offset>0&&<button className="sk-btn sk-quiet" onClick={()=>setOffset(0)}>First retained selections</button>}{page.next_offset!==null&&<button className="sk-btn sk-quiet" onClick={()=>setOffset(page.next_offset!)}>Next retained selections</button>}</div>
        <Details summary="Keep another public copy">
          {!recoverySupported&&<Callout>Update the preservation host for exact retained-selection lookup and recovery kits. Existing sharing and Stop controls remain available.</Callout>}
          {!features?.['Public copy review']&&<Callout>Update the preservation host to review every member’s signed terms before retaining a new copy. Existing Stop controls remain available.</Callout>}
          {copies.length===0&&<p>Publish, receive or import a public copy first. Private captures require a separate public publication review.</p>}
          {copies.map(copy=>{const held=page.models.find(model=>model.manifest_id===copy.manifest_id);return <div className="sk-row" key={copy.copy_id}><span>{copy.model_id}<small className="sk-meta"> · {bytes(copy.size_bytes)} · {copy.member_count} members</small></span><button className="sk-btn sk-outline sk-sm" disabled={!policy||!page.available||working||Boolean(checking)||!recoverySupported||!features?.['Public copy review']||held?.state==='retained'||held?.state==='pending'} onClick={()=>void reviewIntake(copy)}>{checking===copy.copy_id?'Checking retained identity…':held?.state==='retained'?'Kept here':held?.state==='pending'?'Intake pending':'Keep available'}</button></div>;})}
        </Details>
        <Details summary="Mirror activity and decisions">
          <p>Jobs belong to the host and continue when this page closes. These receipts report local work, not community replicas or anonymous access.</p>
          {history?.jobs.map(job=><article className="sk-card" key={job.job_id}><Row k={titles[job.kind]??job.kind}><Pill tone={job.state==='succeeded'?'ok':job.state==='failed'?'bad':active(job)?'live':'muted'}>{job.state}</Pill></Row><p className="sk-meta">{ago(job.updated_at)}</p>{active(job)&&<Progress label="Owned mirror work"/>}{(job.bytes_hashed>0||job.bytes_copied>0)&&<p className="sk-hint">Checked {bytes(job.bytes_hashed)} · copied {bytes(job.bytes_copied)}</p>}{job.error_code&&<Callout tone="warn">{errorText(job.error_code)}</Callout>}{(active(job)||job.state==='failed')&&<button className="sk-btn sk-outline" disabled={stopping.has(job.job_id)} onClick={()=>void stop(job.job_id)}><Icon name="stop"/>{stopping.has(job.job_id)?'Stopping…':active(job)?'Stop this operation':'Confirm this operation is stopped'}</button>}<Details summary="Receipt identity"><Mono value={job.job_id} label="mirror job identity"/></Details></article>)}
          {historyOffset>0&&<button className="sk-btn sk-quiet" onClick={()=>setHistoryOffset(0)}>Latest decisions</button>}{history?.next_offset!==null&&history?.next_offset!==undefined&&<button className="sk-btn sk-quiet" onClick={()=>setHistoryOffset(history.next_offset!)}>Older decisions</button>}
        </Details>
      </>}
    </section>
    {dialog&&<MirrorDialog key={JSON.stringify(dialog)} selection={dialog} busy={submitting} error={error} onClose={()=>setDialog(null)} onSubmit={submit} bittorrentAvailable={bittorrentAvailable}/>}
  </>;
}

/** Each sheet freezes its reviewed policy and keeps retry inputs unchanged. */
function MirrorDialog({selection,busy,error,onClose,onSubmit,bittorrentAvailable}:{selection:Dialog;busy:boolean;error:string;onClose:()=>void;onSubmit:(decision:MirrorDecision)=>Promise<void>;bittorrentAvailable:boolean}) {
  if(selection.kind==='recovery')return <RetainedRecovery model={selection.model} policyId={selection.policy_id} onClose={onClose}/>;
  if(selection.kind==='configure')return <MirrorSettings selection={selection} busy={busy} error={error} onClose={onClose} onSubmit={onSubmit} bittorrentAvailable={bittorrentAvailable}/>;
  if(selection.kind==='retain')return <RetainReview selection={selection} busy={busy} error={error} onClose={onClose} onSubmit={onSubmit}/>;
  return <MirrorDecisionSheet selection={selection} busy={busy} error={error} onClose={onClose} onSubmit={onSubmit}/>;
}

/** Offline configuration has separate transport choices and bounded resources. */
function MirrorSettings({selection,busy,error,onClose,onSubmit,bittorrentAvailable}:{selection:Extract<Dialog,{kind:'configure'}>;busy:boolean;error:string;onClose:()=>void;onSubmit:(decision:MirrorDecision)=>Promise<void>;bittorrentAvailable:boolean}) {
  const [draft,setDraft]=useState<MirrorConfiguration>(()=>selection.configuration?{...selection.configuration}:{name:'My community mirror',disk_bytes:50*gibibyte,ingress_daily:20*gibibyte,egress_daily:10*gibibyte,egress_monthly:100*gibibyte,http:false,exposure:'loopback',port:8080,ipfs:false,torrent:false,peer_setup_id:null,max_active:2,session_seconds:3600,session_bytes:64*1024**2,upload_rate:1024**2});
  const [peer,setPeer]=useState<PeerGrant|null>(null);
  const numeric=(key:keyof MirrorConfiguration,value:number)=>setDraft(current=>({...current,[key]:value}));
  const valid=Boolean(draft.name.trim())&&draft.name.length<=200&&[draft.disk_bytes,draft.ingress_daily,draft.egress_daily,draft.egress_monthly,draft.session_bytes].every(value=>Number.isSafeInteger(value)&&value>0)&&draft.disk_bytes>=256*1024**2&&Number.isInteger(draft.port)&&draft.port>=0&&draft.port<=65535&&Number.isInteger(draft.max_active)&&draft.max_active>=1&&draft.max_active<=4&&Number.isInteger(draft.session_seconds)&&draft.session_seconds>0&&draft.session_seconds<=86400&&Number.isSafeInteger(draft.upload_rate)&&draft.upload_rate>=1024&&draft.upload_rate<=100*1024**2&&(!draft.ipfs||Boolean(peer));
  return <Sheet kicker="Keep models available" title="Mirror settings" back="Close mirror settings" onClose={onClose} footer={<><button className="sk-btn sk-quiet" disabled={busy} onClick={onClose}>Cancel</button><button className="sk-btn sk-solid" disabled={busy||!valid} onClick={()=>void onSubmit({kind:'configure',policy_id:selection.policy_id,configuration:{...draft,name:draft.name.trim(),peer_setup_id:draft.ipfs?peer!.peer_setup_id:null}})}>{busy?'Saving…':'Save settings'}</button></>}>
    <Step n={1} title="Storage and transfer allowances">
      <p>Retained copies use separate managed storage on this host. Reaching an allowance stops new work; it never authorizes deletion.</p>
      <label className="sk-field"><span>Mirror name</span><input className="sk-input" maxLength={200} disabled={busy} value={draft.name} onChange={event=>setDraft(current=>({...current,name:event.target.value}))}/></label>
      {([['disk_bytes','Storage allowance (GB)'],['ingress_daily','Download allowance per day (GB)'],['egress_daily','Upload allowance per day (GB)'],['egress_monthly','Upload allowance per month (GB)']] as const).map(([key,label])=><label className="sk-field" key={key}><span>{label}</span><input className="sk-input" type="number" min={key==='disk_bytes'?0.25:0.001} step="any" disabled={busy} value={draft[key]/gibibyte} onChange={event=>numeric(key,Math.round(Number(event.target.value)*gibibyte))}/></label>)}
      <p className="sk-hint">Keeping an existing local copy spends no network download allowance. Native upload allowances are reserved before each session; short wire-traffic overshoot is possible.</p>
    </Step>
    <Step n={2} title="Choose sharing transports">
      <p>Each is optional. Saving these choices starts no listener or peer network.</p>
      <Consent checked={draft.http} disabled={busy} onChange={http=>setDraft(current=>({...current,http}))}>HTTP mirror</Consent>
      {draft.http&&<><Consent checked={draft.exposure==='network'} disabled={busy} onChange={network=>setDraft(current=>({...current,exposure:network?'network':'loopback'}))}>Accept HTTP connections beyond this device.</Consent><label className="sk-field"><span>HTTP port (0 chooses a free port)</span><input className="sk-input" type="number" min={0} max={65535} step={1} disabled={busy} value={draft.port} onChange={event=>numeric('port',Number(event.target.value))}/></label><p className="sk-hint">{draft.exposure==='loopback'?'Only this device can connect.':'Firewall, hostname and routing setup remain under your control. A local listener does not prove public reachability.'}</p></>}
      <Consent checked={draft.torrent} disabled={busy||(!bittorrentAvailable&&!draft.torrent)} onChange={torrent=>setDraft(current=>({...current,torrent}))}>Public BitTorrent sharing</Consent>
      {!bittorrentAvailable&&<p className="sk-hint">This host has no compatible BitTorrent adapter. Other transports remain available.</p>}
      <Consent checked={draft.ipfs} disabled={busy} onChange={ipfs=>{setPeer(null);setDraft(current=>({...current,ipfs}));}}>Public IPFS sharing</Consent>
      {draft.ipfs&&<PeerAccess sharing heading={false} purpose="this mirror’s reviewed future sharing sessions" onChange={setPeer}/>}
      {(draft.ipfs||draft.torrent||draft.exposure==='network')&&<Callout tone="warn">Public sharing can expose this host’s network address and shared content. These transports do not provide anonymity.</Callout>}
    </Step>
    <Details summary="Native sharing limits">
      <label className="sk-field"><span>Simultaneous native workers</span><input className="sk-input" type="number" min={1} max={4} step={1} disabled={busy} value={draft.max_active} onChange={event=>numeric('max_active',Number(event.target.value))}/></label>
      <label className="sk-field"><span>Native session duration (minutes)</span><input className="sk-input" type="number" min={1/60} max={1440} step="any" disabled={busy} value={draft.session_seconds/60} onChange={event=>numeric('session_seconds',Math.round(Number(event.target.value)*60))}/></label>
      <label className="sk-field"><span>Upload reservation per native session (MB)</span><input className="sk-input" type="number" min={1/1024**2} step="any" disabled={busy} value={draft.session_bytes/1024**2} onChange={event=>numeric('session_bytes',Math.round(Number(event.target.value)*1024**2))}/></label>
      <label className="sk-field"><span>Native upload rate (MB/s)</span><input className="sk-input" type="number" min={1/1024} max={100} step="any" disabled={busy} value={draft.upload_rate/1024**2} onChange={event=>numeric('upload_rate',Math.round(Number(event.target.value)*1024**2))}/></label>
    </Details>
    <Callout>Saving changes clears prior restart permission. Retained copies remain. Review Start separately when you want to share.</Callout>
    {error&&<Callout tone="bad" role="alert">{errorText(error)}</Callout>}
  </Sheet>;
}

/** Read complete terms offline before enabling the exact-copy intake consent. */
function RetainReview({selection,busy,error,onClose,onSubmit}:{selection:Extract<Dialog,{kind:'retain'}>;busy:boolean;error:string;onClose:()=>void;onSubmit:(decision:MirrorDecision)=>Promise<void>}) {
  const [review,setReview]=useState<PublicReview|null>(null),[readError,setReadError]=useState(''),[confirmed,setConfirmed]=useState(false),[reintake,setReintake]=useState(false);
  const copy=selection.copy;
  useEffect(()=>{let current=true;void api<PublicReview>('mirror-copy-review',{copy_id:copy.copy_id,manifest_id:copy.manifest_id,member_count:copy.member_count}).then(value=>{if(current)setReview(value);}).catch(reason=>{if(current)setReadError(reason instanceof Error?reason.message:'host_unavailable');});return()=>{current=false;};},[copy.copy_id,copy.manifest_id,copy.member_count]);
  const supported=review?.archive_format==='preservation-v1';
  return <Sheet kicker="Keep models available" title={`Keep ${review?.name??copy.model_id}`} back="Close retained-copy review" onClose={onClose} footer={<><button className="sk-btn sk-quiet" disabled={busy} onClick={onClose}>Cancel</button><button className="sk-btn sk-solid" disabled={busy||!supported||!confirmed||(selection.removed&&!reintake)} onClick={()=>void onSubmit({kind:'retain',policy_id:selection.policy_id,copy_id:copy.copy_id,manifest_id:copy.manifest_id,confirm_redistribution:true,confirm_reintake:reintake})}>{busy?'Submitting…':'Keep this public copy'}</button></>}>
    {!review&&!readError&&<p role="status">Authenticating the complete signed selection locally…</p>}
    {readError&&<Callout tone="bad" role="alert">{errorText(readError)}</Callout>}
    {review&&<>
      <Step n={1} title="Complete signed selection"><Row k="Declared model files">{bytes(review.size_bytes)} · {review.members.length} members</Row><Details summary="Exact model identity"><Mono value={review.manifest_id} label="selected manifest identity"/><p>Original cards, companions and signed provenance remain intact. This offline metadata read is not a fresh check of every file.</p></Details></Step>
      <Step n={2} title="Review every member’s terms">{review.members.map(member=><article className="sk-card sk-mirror-member" key={member.member_id}><h3>{member.member_id} · {member.role}</h3><Row k="Declared terms">{member.license_id}</Row><Row k="Terms reference">{member.terms_reference}</Row><p className="sk-meta">{member.repository} · {bytes(member.size_bytes)} · {member.file_count} files</p>{member.notice_paths.length>0&&<p className="sk-hint">Required notices: {member.notice_paths.join(', ')}</p>}</article>)}<p>These are the original signed declarations and references, not a new grant of redistribution rights.</p><Consent checked={confirmed} disabled={busy||!supported} onChange={setConfirmed}>I reviewed the terms for every listed member and may redistribute this complete selection.</Consent></Step>
      {!supported&&<Callout tone="warn">Direct public PMF transport is not available in this mirror adapter yet. Keep the original PMF archive; this review does not flatten or convert it.</Callout>}
      {selection.removed&&<Consent checked={reintake} disabled={busy} onChange={setReintake}>I deliberately want to return this previously removed exact selection to the mirror.</Consent>}
      <Callout>Keeping a copy starts no sharing. It uses the reviewed storage allowance and clears previous restart permission. Choose Start separately.</Callout>
    </>}
    {error&&<Callout tone="bad" role="alert">{errorText(error)}</Callout>}
  </Sheet>;
}

/** Start, standing permission, model controls and deletion remain separate decisions. */
function MirrorDecisionSheet({selection,busy,error,onClose,onSubmit}:{selection:Exclude<Dialog,{kind:'configure'}|{kind:'retain'}>;busy:boolean;error:string;onClose:()=>void;onSubmit:(decision:MirrorDecision)=>Promise<void>}) {
  const [confirmed,setConfirmed]=useState(false),[hours,setHours]=useState(1),[standing,setStanding]=useState(false),[permissionHours,setPermissionHours]=useState(8),[opened]=useState(()=>Math.floor(Date.now()/1000));
  const validHours=Number.isFinite(hours)&&hours>0&&hours<=24&&Number.isInteger(hours*3600);
  const validPermission=Number.isFinite(permissionHours)&&permissionHours>0&&permissionHours<=24&&Number.isInteger(permissionHours*3600);
  const isModel=selection.kind==='model'||selection.kind==='prune';
  const label=selection.kind==='prune'?'Remove mirror copy':selection.kind==='model'?({pin:'Pin mirror copy',unpin:'Unpin mirror copy',block:'Block future serving',unblock:'Allow future serving',retry:'Retry retained intake'}[selection.action??'retry']):titles[selection.kind];
  const valid=confirmed&&(selection.kind!=='run'||validHours)&&(selection.kind!=='grant_resume'||validPermission)&&(selection.kind!=='model'||Boolean(selection.action));
  function decide(){
    if(!valid)return;
    if(selection.kind==='run')void onSubmit({kind:'run',policy_id:selection.policy_id,public_participation:true,mode:standing?'resume':'finite',seconds:Math.round(hours*3600)});
    else if(selection.kind==='grant_resume')void onSubmit({kind:'grant_resume',policy_id:selection.policy_id,confirm_resume:true,resume_expires:opened+Math.round(permissionHours*3600)});
    else if(selection.kind==='revoke_resume')void onSubmit({kind:'revoke_resume',policy_id:selection.policy_id});
    else if(selection.kind==='prune')void onSubmit({kind:'prune',policy_id:selection.policy_id,manifest_id:selection.model.manifest_id,confirm_delete_manifest:selection.model.manifest_id});
    else if(selection.kind==='model'&&selection.action)void onSubmit({kind:'model',policy_id:selection.policy_id,manifest_id:selection.model.manifest_id,action:selection.action});
  }
  return <Sheet kicker="Keep models available" title={label} subtitle={isModel?selection.name:undefined} back="Close mirror decision" onClose={onClose} footer={<><button className="sk-btn sk-quiet" disabled={busy} onClick={onClose}>Cancel</button><button className="sk-btn sk-solid" disabled={busy||!valid} onClick={decide}>{busy?'Submitting…':label}</button></>}>
    {selection.kind==='run'&&<><p>Serve the retained, unblocked selections using these reviewed settings:</p><Row k="Mirror">{selection.configuration.name}</Row><Row k="Transports">{[selection.configuration.http?`HTTP (${selection.configuration.exposure==='network'?'network access':'this device only'})`:null,selection.configuration.ipfs?'Public IPFS':null,selection.configuration.torrent?'Public BitTorrent':null].filter(Boolean).join(' · ')}</Row><Row k="Upload allowances">{bytes(selection.configuration.egress_daily)} per day · {bytes(selection.configuration.egress_monthly)} per month</Row><label className="sk-field"><span>Share for up to (hours)</span><input className="sk-input" type="number" min={1/3600} max={24} step="any" value={hours} disabled={busy||standing} onChange={event=>setHours(Number(event.target.value))}/></label>{selection.expires!==null&&selection.expires>opened&&<Consent checked={standing} disabled={busy} onChange={setStanding}>Use separately saved restart permission until {new Date(selection.expires*1000).toLocaleString()} instead of a finite run. The host rechecks the exact current permission.</Consent>}<Callout tone="warn">Public peers and mirrors can expose addresses and shared content. This does not establish anonymity, public reachability or independent custody.</Callout></>}
    {selection.kind==='grant_resume'&&<><p>Permit a separately started mirror to resume after the host restarts. This permission alone starts no network or sharing.</p><label className="sk-field"><span>Permission duration (hours)</span><input className="sk-input" type="number" min={1/3600} max={24} step="any" value={permissionHours} disabled={busy} onChange={event=>setPermissionHours(Number(event.target.value))}/></label><p>Permission expires {new Date((opened+Math.round(permissionHours*3600))*1000).toLocaleString()}. Policy changes and new intake require fresh review.</p></>}
    {selection.kind==='revoke_resume'&&<p>Remove saved restart permission. Retained models remain. Use Stop to withdraw current serving and clear running intent.</p>}
    {isModel&&<Details summary="Exact selected model"><Mono value={selection.model.manifest_id} label="selected retained manifest"/></Details>}
    {selection.kind==='prune'&&<Callout tone="warn">Delete this mirror’s retained bytes for this exact selection. Library copies, recipient copies and verification history remain. A pinned copy must be unpinned first; running sharing must be stopped.</Callout>}
    {selection.kind==='model'&&<p>{selection.action==='retry'?'Try the same retained selection again under its unchanged intake and transfer allowance. No new source or trust is accepted.':selection.action==='pin'?'Protect this mirror copy from explicit removal.':selection.action==='unpin'?'Remove deletion protection. This does not delete any bytes.':selection.action==='block'?'Block this model from future serving. Retained bytes stay here.':'Allow future serving of this retained selection under the reviewed mirror policy. This starts no sharing.'}</p>}
    <Consent checked={confirmed} disabled={busy} onChange={setConfirmed}>{selection.kind==='run'?'Allow this mirror to serve these retained public copies with the reviewed exposure and limits.':selection.kind==='grant_resume'?'Allow this exact policy to resume sharing after restart until the selected expiry.':selection.kind==='prune'?'Delete only this exact retained mirror copy.':`Confirm: ${label.toLowerCase()}.`}</Consent>
    {error&&<Callout tone="bad" role="alert">{errorText(error)}</Callout>}
  </Sheet>;
}
