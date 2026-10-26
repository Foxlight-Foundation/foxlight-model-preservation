import {ModelInformation} from './model-info';
import {SovereignBrand} from './brand';
import { StrictMode, useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { api, bytes, errorText, kitDownload, type Copy, type Job, type Location, type Model, type Page, type Plan } from './client';
import './style.css';
import {Sovereign} from './sovereign';
import {UploadKit,ReviewPlan} from './recovery';
import {Sharing,SharingReview,SharingSession} from './sharing';
import {Publish,Receive,DeliveryReview} from './delivery';
import {Storage} from './storage';
import {Find} from './discovery';
import {useI2PAvailability} from './i2p';
import {RetainedLibrary} from './retained-recovery';
import {TemporaryJobs,TemporaryFilesReview,type TemporaryWork} from './maintenance';
import {Callout,Empty,Icon,Label,Menu,Monogram,PageBanner,Pill,Progress,SheetHost,ago,initials,readTheme,useTheme,type IconName,type Tone} from './ui';

type Area = 'library'|'storage'|'activity'|'sharing'|'find';
type Review = {plan:Plan; operationId:string};
const active = (job:Job) => ['queued','running','cancelling'].includes(job.state);
const stoppedRecovery = (job:Job) => job.kind==='recover'&&['interrupted','failed','cancelled'].includes(job.state);
const areas:{id:Area;label:string;icon:IconName}[] = [{id:'library',label:'Library',icon:'library'},{id:'find',label:'Find',icon:'search'},{id:'sharing',label:'Sharing',icon:'share'},{id:'activity',label:'Activity',icon:'activity'},{id:'storage',label:'Storage',icon:'storage'}];
const kindLabel = (kind:string) => kind==='preserve'?'Preserve':kind==='recover'?'Restore':kind==='kit_export'?'Recovery kit':kind==='mirror_import'?'Public import':kind==='publish'?'Publish':kind==='receive'?'Receive':'Check';
const liveLabel:Record<string,string> = {preserve:'preserving…',recover:'restoring…',kit_export:'saving kit…',mirror_import:'importing…',publish:'publishing…',receive:'downloading…',check:'checking…'};
/** One honest status per job; amber only while Core is doing the work. */
export function jobStatus(job:Job):[Tone,string] {
  if(job.state==='cancelling')return ['live','stopping…'];
  if(job.state==='queued')return ['live','queued'];
  if(job.state==='running')return ['live',liveLabel[job.kind]??'running'];
  if(job.state==='succeeded')return ['ok','done'];
  if(job.state==='failed')return ['bad','failed'];
  if(job.state==='cancelled')return ['muted','stopped'];
  if(job.state==='interrupted')return ['info','interrupted'];
  return ['muted',job.state.replaceAll('_',' ')];
}

/** Choose the actual host before mounting any Core-specific controls. */
function App() {
  const [host,setHost]=useState<'sovereign'|'core'|null>(null),[failure,setFailure]=useState('');
  useEffect(()=>{let live=true;void api<Page&{host_kind?:string}>('library?offset=0').then(value=>{if(live)setHost(value.host_kind==='sovereign'?'sovereign':'core');}).catch(error=>{if(live)setFailure(error instanceof Error?error.message:'host_unavailable');});return()=>{live=false;};},[]);
  if(host==='sovereign')return <Sovereign/>;
  if(host==='core')return <CoreApp/>;
  return <main className="sk-main"><p role={failure?'alert':'status'}>{failure?errorText(failure):'Opening your Library…'}</p></main>;
}

/** Real Library/Guided interface; all effects begin with explicit reviewed controls. */
function CoreApp() {
  useEffect(()=>{document.title='Sovereign · Skulk Library';},[]);
  const i2pAvailable=useI2PAvailability();
  const [page,setPage] = useState<Page|null>(null);
  const [area,setArea] = useState<Area>('library');
  const [review,setReview] = useState<Review|null>(null);
  const [restoreOpen,setRestoreOpen] = useState(false);
  const [publicImport,setPublicImport] = useState(false);
  const [publishCopy,setPublishCopy]=useState<Copy|null>(null);
  const [receiveOpen,setReceiveOpen]=useState(false),[resumeJob,setResumeJob]=useState<Job|undefined>();
  const [cleanup,setCleanup] = useState<{job:Job;report:TemporaryWork}|null>(null);
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState('');
  const [loadError,setLoadError] = useState('');
  const [notice,setNotice] = useState('');
  const [retainedOverlay,setRetainedOverlay] = useState(false);
  const [offset,setOffset] = useState(0);
  const [theme,toggleTheme] = useTheme();
  const lastRead = useRef(0);
  const navigation = useRef(0);
  const refresh = useCallback(async () => {
    const sequence = ++lastRead.current;
    try {const result = await api<Page>(`library?offset=${offset}`); if(sequence === lastRead.current) {setPage(result);setLoadError('');}}
    catch(err) {if(sequence === lastRead.current) setLoadError(err instanceof Error ? err.message : 'host_unavailable');}
  },[offset]);
  useEffect(() => {void refresh(); const timer=setInterval(() => void refresh(),2000); return () => {clearInterval(timer); lastRead.current++;};},[refresh]);
  function navigate(next:Area) {navigation.current++;setCleanup(null);setRestoreOpen(false);setPublicImport(false);setPublishCopy(null);setReceiveOpen(false);setResumeJob(undefined);setReview(null);setArea(next);sessionStorage.removeItem('preservation-review-v1');}
  async function effect(action:()=>Promise<void>,isCurrent:()=>boolean=()=>true) {setBusy(true);setError('');try {await action();}catch(err){if(isCurrent())setError(err instanceof Error ? err.message : 'host_unavailable');}finally{setBusy(false);}}
  async function setup() {await effect(async()=>{await api('setup',{});await refresh();setNotice('This device is ready. Choose a model to preserve.');});}
  async function plan(kind:'preserve'|'check'|'kit_export'|'recover', selection:Model|Copy|Job) {
    const selected=navigation.current;
    await effect(async()=> {
      const locationId = 'location_id' in selection && selection.location_id ? selection.location_id : 'job_id' in selection ? (page?.copies.find(copy=>copy.copy_id===selection.copy_id)?.location_id??'device') : (page?.default_location_id??page?.locations[0]?.location_id);
      const body = kind === 'preserve' && 'generation_id' in selection ? {kind,location_id:locationId,model_id:selection.model_id,generation_id:selection.generation_id} : {kind,location_id:locationId,copy_id:'copy_id' in selection && selection.copy_id ? selection.copy_id : 'job_id' in selection ? selection.job_id : null,...(kind==='recover'?{historical:true}:{})};
      const result = await api<Plan>('plan',body);
      // Retain the exact submission identity across response loss/page reload.
      if(selected!==navigation.current)return;
      const operationId=crypto.randomUUID();
      const next={plan:result,operationId}; setReview(next);sessionStorage.setItem('preservation-review-v1',JSON.stringify(next));
    },()=>selected===navigation.current);
  }
  useEffect(()=>{try{const saved=JSON.parse(sessionStorage.getItem('preservation-review-v1')??'null') as Review|null;if(saved?.plan?.plan_id && saved.operationId && saved.plan.expires_at*1000>Date.now())setReview(saved);}catch{/* Discard malformed browser-only view state. */}},[]);
  async function start(accept=false,code=false,removed=false,participate=false) {if(!review)return;const selected=review,epoch=navigation.current;await effect(async()=>{const kind=selected.plan.kind;await api<Job>(selected.plan.resume_job_id?'resume-receive':'start',{...(selected.plan.resume_job_id?{job_id:selected.plan.resume_job_id}:{}),plan_id:selected.plan.plan_id,operation_id:selected.operationId,...(kind==='recover'?{accept_owner_capture:accept,allow_repository_code:code,restore_deleted:removed}:kind==='mirror_import'||kind==='receive'?{accept_owner_capture:accept}:selected.plan.sharing||kind==='publish'?{public_participation:participate}:{})});if(epoch===navigation.current){navigate(kind==='mirror_import'||selected.plan.sharing?'sharing':'activity');setNotice('Your job is tracked in Skulk. You can close this page and return later.');}await refresh();},()=>epoch===navigation.current);}
  function openReview(result:Plan) {const next={plan:result,operationId:crypto.randomUUID()};setReview(next);setRestoreOpen(false);setPublicImport(false);setPublishCopy(null);setReceiveOpen(false);setResumeJob(undefined);sessionStorage.setItem('preservation-review-v1',JSON.stringify(next));}
  const viewEpoch=navigation.current;
  function currentReview(result:Plan){if(viewEpoch===navigation.current)openReview(result);}
  function openReceive(job?:Job){navigate('sharing');setResumeJob(job);setReceiveOpen(true);}
  function openPublish(copy:Copy){navigate('library');setPublishCopy(copy);}
  async function retry(job:Job) {const epoch=navigation.current;await effect(async()=>{const result=await api<Plan>('retry-plan',{job_id:job.job_id});if(epoch===navigation.current)openReview(result);},()=>epoch===navigation.current);}
  async function reclaim(job:Job) {await effect(async()=>{const result=await api<{indexed_generation_retained:boolean}>('reclaim',{job_id:job.job_id});setNotice(result.indexed_generation_retained?'Temporary files checked. Your indexed models were kept. Review again to continue.':'Unused recovery files cleared. Review again to choose a fresh restore.');await refresh();});}
  async function inspectWork(job:Job) {const selected=++navigation.current;await effect(async()=>{const report=await api<TemporaryWork>('work-preview',{job_id:job.job_id});if(selected===navigation.current)setCleanup({job,report});},()=>selected===navigation.current);}
  async function clearWork() {if(!cleanup)return;const job=cleanup.job;await effect(async()=>{const result=await api<TemporaryWork>('reclaim-work',{job_id:job.job_id});setNotice(result.removed?'Temporary files cleared. Your kept copies and sharing metadata are intact.':'No temporary files remain for this job.');setCleanup(null);await refresh();});}
  async function cancel(job:Job) {await effect(async()=>{await api('cancel',{job_id:job.job_id});await refresh();});}
  const defaultId=page?.default_location_id??page?.locations[0]?.location_id??null;
  const location=page?.locations.find(item=>item.location_id===defaultId);
  const available=(locationId:string|undefined)=>!loadError&&Boolean(page?.locations.find(item=>item.location_id===locationId)?.reachable);
  const planOnline=review?.plan.location_id==='kit'||available(review?.plan.location_id)||page?.public_copies?.some(copy=>copy.location_id===review?.plan.location_id)===true;
  const online=Boolean(location?.reachable)&&!loadError;
  const count=page?.jobs.filter(active).length??0;
  const guided=Boolean(page)&&page!.locations.length===0&&area==='library'&&!page!.jobs.some(job=>job.kind==='recover');

  // The same precedence as before decides which reviewed flow is open; it now slides over the area instead of replacing it.
  const sheet:ReactNode = !page ? null : publishCopy ? <Publish key={publishCopy.copy_id} copy={publishCopy} publisher={page.publishing} onBack={()=>navigate('library')} onReview={currentReview} onChange={refresh}/> : receiveOpen ? <Receive i2pAvailable={i2pAvailable} key={resumeJob?.job_id??'new'} resume={resumeJob} onBack={()=>navigate('sharing')} onReview={currentReview}/> : restoreOpen||publicImport ? <UploadKit publicImport={publicImport} uploads={page.uploads??[]} onChange={refresh} onReview={currentReview} onBack={()=>{setRestoreOpen(false);setPublicImport(false);}}/> : cleanup ? <TemporaryFilesReview key={cleanup.job.job_id} job={cleanup.job} report={cleanup.report} busy={busy} onBack={()=>setCleanup(null)} onClear={()=>void clearWork()}/> : review&&(review.plan.kind==='publish'||review.plan.kind==='receive') ? <DeliveryReview key={review.plan.plan_id} plan={review.plan} busy={busy} onBack={()=>navigate(review.plan.kind==='publish'?'library':'sharing')} onStart={(accept,participate)=>void start(accept,false,false,participate)}/> : review&&(review.plan.kind==='mirror_import'||review.plan.sharing) ? <SharingReview key={review.plan.plan_id} plan={review.plan} busy={busy} onBack={()=>{setReview(null);sessionStorage.removeItem('preservation-review-v1');setArea('sharing');}} onStart={(accept,participate)=>void start(accept,false,false,participate)}/> : review ? <ReviewPlan key={review.plan.plan_id} plan={review.plan} busy={busy} online={Boolean(planOnline)} storageLabel={page.locations.find(item=>item.location_id===review.plan.location_id)?.label} back={`Back to ${areas.find(item=>item.id===area)?.label??'Library'}`} onBack={()=>{setReview(null);sessionStorage.removeItem('preservation-review-v1');}} onStart={(accept,code,removed)=>void start(accept,code,removed)}/> : null;

  const banner = <>{(error||loadError) && <div className="sk-banner" data-tone="bad" role="alert"><Icon name="alert"/><span>{errorText(error||loadError)}</span><button className="sk-btn sk-quiet sk-sm" onClick={()=>void refresh()}><Icon name="refresh" size={14}/>Refresh</button></div>}
    {notice && <div className="sk-banner" data-tone="ok" role="status"><Icon name="check"/><span>{notice}</span><button className="sk-icon-btn sk-sm" aria-label="Dismiss message" onClick={()=>setNotice('')}><Icon name="close" size={14}/></button></div>}</>;

  return <SheetHost banner={banner}><div className="sk-app">
    <a className="sk-skip" href="#library-main">Skip to content</a>
    <header className="sk-header"><div className="sk-header-inner">
      <span className="sk-brand"><SovereignBrand theme={theme}/><span className="sk-product">via Skulk</span></span>
      <nav className="sk-nav" aria-label="Library areas">{areas.map(item=><button key={item.id} className="sk-nav-item" aria-current={area===item.id?'page':undefined} aria-describedby={item.id==='activity'&&count?'activity-count':undefined} onClick={()=>navigate(item.id)}><Icon name={item.icon} size={18}/><span>{item.label}</span>{item.id==='activity'&&count>0&&<span className="sk-count" aria-hidden="true">{count}</span>}</button>)}</nav>
      {count>0&&<span id="activity-count" className="sk-sr">{count} {count===1?'job':'jobs'} in progress</span>}
      <div className="sk-header-actions">
        <button className="sk-btn sk-quiet sk-restore" aria-label="Restore from recovery kit" title="Restore from recovery kit" onClick={()=>{navigate('library');setRestoreOpen(true);}}><Icon name="restore"/><span>Restore</span></button>
        <button className="sk-icon-btn" aria-label={theme==='night'?'Switch to Noon theme':'Switch to Night theme'} title={theme==='night'?'Switch to Noon':'Switch to Night'} onClick={toggleTheme}><Icon name={theme==='night'?'sun':'moon'} size={18}/></button>
      </div>
    </div></header>
    <main id="library-main" className="sk-main" inert={Boolean(sheet)}>
      <PageBanner/>
      {!page ? <div className="sk-loading" aria-busy={!(error||loadError)}><SovereignBrand theme={theme} symbol/><p>{(error||loadError)?'Library unavailable.':'Opening your Library…'}</p></div>
      : area==='find' ? <Find i2pAvailable={i2pAvailable} onReview={currentReview} uploads={page.uploads??[]} onChange={refresh}/>
      : area==='library' ? <><div inert={retainedOverlay}>{guided?<Guided theme={theme} busy={busy} onDevice={()=>void setup()} onFolder={()=>navigate('storage')} onFind={()=>navigate('find')}/>:<LibraryArea page={page} location={location} online={online} busy={busy} available={available} onStorage={()=>navigate('storage')} onPlan={(kind,selection)=>void plan(kind,selection)} onPublish={openPublish}/>}</div><RetainedLibrary blocked={Boolean(sheet)} onOverlayChange={setRetainedOverlay} onSharing={()=>navigate('sharing')}/></>
      : area==='storage' ? <Storage locations={page.locations} defaultId={defaultId} busy={busy} onDevice={()=>void setup()} onChange={refresh}/>
      : area==='activity' ? <ActivityArea page={page} busy={busy} available={available} onCancel={job=>void cancel(job)} onCheck={job=>void plan('check',job)} onReclaim={job=>void reclaim(job)} onRetry={job=>void retry(job)} onReceive={openReceive} onSharing={()=>navigate('sharing')} onInspect={job=>void inspectWork(job)}/>
      : <Sharing page={page} busy={busy} onReceive={()=>openReceive()} onKit={copy=>void plan('kit_export',copy)} onRestore={copy=>void plan('recover',copy)} onImport={()=>{setPublicImport(true);setReview(null);}} onReview={currentReview} onStop={job=>void cancel(job)}/>}
      {page&&!review&&(offset>0||page.next_offset!==null)&&<div className="sk-pager">{offset>0&&<button className="sk-btn sk-outline" onClick={()=>setOffset(Math.max(0,offset-4))}>Previous page</button>}{page.next_offset!==null&&<button className="sk-btn sk-outline" onClick={()=>setOffset(page.next_offset!)}>Next page</button>}</div>}
      <footer className="sk-footer"><span><Icon name="lock" size={14}/>Your storage belongs to this model-store host.</span><span>Nothing is shared until you review and start it.</span><a href="THIRD_PARTY_NOTICES.txt">Third-party notices</a></footer>
    </main>
    {sheet}
  </div></SheetHost>;
}

/** First run: two clear storage choices, plus finding a shared model. */
function Guided({theme,busy,onDevice,onFolder,onFind}:{theme:'night'|'noon';busy:boolean;onDevice:()=>void;onFolder:()=>void;onFind:()=>void}) {
  return <section className="sk-guided" aria-labelledby="guided-title">
    <div className="sk-guided-hero"><span className="sk-guided-mark"><SovereignBrand theme={theme} symbol/></span><p className="sk-label">Get started · step 1 of 2</p><h1 id="guided-title">Keep your models within reach.</h1><p className="sk-lede">Preserve a verified copy of any installed model, together with the files it needs to work. First, choose where copies live.</p></div>
    <div className="sk-choices">
      <button className="sk-choice" disabled={busy} aria-labelledby="choice-device" aria-describedby="choice-device-note" onClick={onDevice}><span className="sk-choice-icon"><Icon name="device" size={22}/></span><span className="sk-choice-title" id="choice-device">Use this device</span><span className="sk-choice-note" id="choice-device-note">Skulk creates private storage and a protected signing key on this model-store host.</span><span className="sk-choice-go">{busy?'Preparing…':'Set up in one step'}<Icon name="arrow" size={14}/></span></button>
      <button className="sk-choice" aria-labelledby="choice-folder" aria-describedby="choice-folder-note" onClick={onFolder}><span className="sk-choice-icon"><Icon name="folder" size={22}/></span><span className="sk-choice-title" id="choice-folder">Use a folder or NAS</span><span className="sk-choice-note" id="choice-folder-note">Pick a dedicated folder on a connected drive. You review it before anything is created.</span><span className="sk-choice-go">Choose a folder<Icon name="storage" size={14}/></span></button>
    </div>
    <p className="sk-guided-note"><Icon name="lock" size={14}/>Setup never hosts or shares anything.</p>
    <div className="sk-guided-alt"><span>Looking for a model someone shared?</span><button className="sk-btn sk-outline" onClick={onFind}><Icon name="search"/>Find a shared model</button></div>
  </section>;
}

/** Library: installed models with their saved copies, plus copies whose model is no longer installed. */
function LibraryArea({page,location,online,busy,available,onStorage,onPlan,onPublish}:{page:Page;location:Location|undefined;online:boolean;busy:boolean;available:(locationId:string|undefined)=>boolean;onStorage:()=>void;onPlan:(kind:'preserve'|'check'|'kit_export'|'recover',selection:Model|Copy)=>void;onPublish:(copy:Copy)=>void}) {
  const orphans=page.copies.filter(copy=>!page.models.some(model=>model.model_id===copy.model_id));
  const label=(id:string)=>page.locations.find(item=>item.location_id===id)?.label??'Unavailable storage';
  return <section aria-labelledby="library-title">
    <div className="sk-page-head"><div><h1 id="library-title">Your models</h1><p>Preserve installed models with everything they need, and keep their copies checked.</p></div>
      <p className="sk-destination"><Icon name={location?.location_id==='device'?'device':'folder'} size={14}/><span>New copies go to {location?.label??'unavailable storage'}</span><button className="sk-link" onClick={onStorage}>Change</button></p></div>
    {!online&&<Callout tone="warn" role="status">{location?'Your storage is disconnected or has changed. Reconnect it to preserve or check files.':'Your recovered models are ready. Set up private storage in Storage before preserving more.'}</Callout>}
    {page.models.length===0&&page.copies.length===0&&<Empty icon="package" title="No installed models yet">Install a model in Skulk’s Model Store, then come back to preserve it. Only installations with a complete supported record can be preserved.</Empty>}
    <div className="sk-cards">
      {page.models.map(model=><ModelCard key={model.model_id} model={model} jobs={page.jobs.filter(job=>job.model_id===model.model_id)} copies={page.copies.filter(copy=>copy.model_id===model.model_id)} label={label} disabled={busy||!online} onPreserve={()=>onPlan('preserve',model)} copyAvailable={copy=>!busy&&available(copy.location_id)} onCheck={copy=>onPlan('check',copy)} onKit={copy=>onPlan('kit_export',copy)} onPublish={onPublish}/>)}
      {orphans.map(copy=>{const enabled=!busy&&available(copy.location_id);return <article className="sk-card sk-model" key={copy.copy_id} aria-label={copy.model_id}>
        <div className="sk-model-head"><Monogram text={initials(copy.model_id)}/><div className="sk-model-title"><h2><ModelName id={copy.model_id}/></h2><p className="sk-meta">Saved copy · <span className="sk-num">{bytes(copy.size_bytes)}</span> · model not installed</p></div><div className="sk-model-side"><Pill tone="ok">verified</Pill><button className="sk-btn sk-outline" disabled={!enabled} onClick={()=>onPlan('recover',copy)}><Icon name="restore"/>Restore</button></div></div>
        <ModelInformation details={page.copy_details?.[copy.copy_id]}/><ul className="sk-copies"><CopyRow copy={copy} label={label(copy.location_id)} enabled={enabled} checkLabel="Check files" onCheck={()=>onPlan('check',copy)} onKit={()=>onPlan('kit_export',copy)} onPublish={()=>onPublish(copy)}/></ul>
      </article>;})}
    </div>
  </section>;
}

function ModelName({id}:{id:string}) {const slash=id.lastIndexOf('/');return slash<0?<>{id}</>:<><span className="sk-owner">{id.slice(0,slash+1)}</span>{id.slice(slash+1)}</>;}

/** Every card owns its exact model/copies; actions never depend on another selection. */
function ModelCard({model,jobs,copies,label,disabled,copyAvailable,onPreserve,onCheck,onKit,onPublish}:{model:Model;jobs:Job[];copies:Copy[];label:(id:string)=>string;disabled:boolean;copyAvailable:(copy:Copy)=>boolean;onPreserve:()=>void;onCheck:(copy:Copy)=>void;onKit:(copy:Copy)=>void;onPublish:(copy:Copy)=>void}) {
  const working=jobs.find(job=>active(job)&&['preserve','check','kit_export','recover'].includes(job.kind));
  const unresolved=jobs.some(job=>job.kind==='preserve'&&job.state==='interrupted'&&job.expected_manifest_id);
  const [tone,status]:[Tone,string]=working?['live',liveLabel[working.kind]??'working…']:unresolved?['info','needs check']:copies.length?['ok','verified']:['muted','not preserved'];
  return <article className="sk-card sk-model" data-live={working?'true':undefined} aria-label={model.model_id}>
    <div className="sk-model-head"><Monogram text={initials(model.model_id)}/><div className="sk-model-title"><h2><ModelName id={model.model_id}/></h2><p className="sk-meta">Installed · <span className="sk-num">{bytes(model.size_bytes)}</span> · {model.verification==='registry_verified'?'registry verified':'owner capture'}</p></div><div className="sk-model-side"><Pill tone={tone}>{status}</Pill><button className="sk-btn sk-outline" disabled={disabled} onClick={onPreserve}><Icon name="shield"/>Preserve</button></div></div>
    <ModelInformation details={model.details} installed/>
    {working&&<Progress label={`${model.model_id} ${liveLabel[working.kind]??'working'}`}/>}
    {copies.length>0?<><Label>Saved copies · {copies.length}</Label><ul className="sk-copies">{copies.map(copy=><CopyRow key={copy.copy_id} copy={copy} label={label(copy.location_id)} enabled={copyAvailable(copy)} checkLabel="Check copy" onCheck={()=>onCheck(copy)} onKit={()=>onKit(copy)} onPublish={()=>onPublish(copy)}/>)}</ul></>
    :<p className="sk-hint">{unresolved?'An earlier preserve was interrupted. Check its outcome in Activity.':'No saved copy yet. Preserve keeps a verified copy, including required companion files.'}</p>}
  </article>;
}

function CopyRow({copy,label,enabled,checkLabel,onCheck,onKit,onPublish}:{copy:Copy;label:string;enabled:boolean;checkLabel:string;onCheck:()=>void;onKit:()=>void;onPublish:()=>void}) {
  return <li className="sk-copy-row"><span className="sk-copy-where"><Icon name="storage" size={14}/><span>{label}</span></span><span className="sk-copy-meta"><span className="sk-num">{bytes(copy.size_bytes)}</span><span>{copy.member_count} {copy.member_count===1?'member':'members'}</span><span title={new Date(copy.checked_at*1000).toLocaleString()}>checked {ago(copy.checked_at)}</span></span>
    <span className="sk-copy-actions"><button className="sk-btn sk-quiet" disabled={!enabled} onClick={onCheck}><Icon name="check"/>{checkLabel}</button><Menu label={`More actions for the copy on ${label}`} items={[{label:'Save recovery kit',icon:'download',disabled:!enabled,onSelect:onKit},{label:'Publish',icon:'globe',disabled:!enabled,onSelect:onPublish}]}/></span></li>;
}

/** Activity: Core-owned jobs, live work first. */
function ActivityArea({page,busy,available,onCancel,onCheck,onReclaim,onRetry,onReceive,onSharing,onInspect}:{page:Page;busy:boolean;available:(locationId:string|undefined)=>boolean;onCancel:(job:Job)=>void;onCheck:(job:Job)=>void;onReclaim:(job:Job)=>void;onRetry:(job:Job)=>void;onReceive:(job:Job)=>void;onSharing:()=>void;onInspect:(job:Job)=>void}) {
  const card=(job:Job)=>job.kind==='http_share'||job.kind==='torrent_share'||job.kind==='peer_share'?<SharingSession key={job.job_id} job={job} busy={busy} onStop={()=>onCancel(job)}/>:<JobCard key={job.job_id} job={job} page={page} busy={busy} available={available} onCancel={onCancel} onCheck={onCheck} onReclaim={onReclaim} onRetry={onRetry} onReceive={onReceive} onSharing={onSharing}/>;
  const live=page.jobs.filter(active),rest=page.jobs.filter(job=>!active(job));
  return <section aria-labelledby="activity-title">
    <div className="sk-page-head"><div><h1 id="activity-title">Activity</h1><p>Jobs run in Skulk Core. Closing this page doesn’t stop them.</p></div></div>
    {page.jobs.length===0&&<Empty icon="activity" title="Nothing running">Jobs you start appear here, and keep going even if you close this page.</Empty>}
    {live.length>0&&<><Label as="h2">In progress · {live.length}</Label><div className="sk-cards">{live.map(card)}</div></>}
    {rest.length>0&&<><Label as="h2">Earlier</Label><div className="sk-cards">{rest.map(card)}</div></>}
    <TemporaryJobs page={page} busy={busy} onReview={onInspect}/>
  </section>;
}

function JobCard({job,page,busy,available,onCancel,onCheck,onReclaim,onRetry,onReceive,onSharing}:{job:Job;page:Page;busy:boolean;available:(locationId:string|undefined)=>boolean;onCancel:(job:Job)=>void;onCheck:(job:Job)=>void;onReclaim:(job:Job)=>void;onRetry:(job:Job)=>void;onReceive:(job:Job)=>void;onSharing:()=>void}) {
  const [tone,status]=jobStatus(job);
  return <article className="sk-card sk-job" data-live={active(job)?'true':undefined} aria-label={`${kindLabel(job.kind)} ${job.model_id}`}>
    <div className="sk-job-head"><span className="sk-job-kind"><Icon name={job.kind==='preserve'?'shield':job.kind==='recover'?'restore':job.kind==='kit_export'?'download':job.kind==='receive'?'download':job.kind==='publish'||job.kind==='mirror_import'?'globe':'search'}/></span><div className="sk-model-title"><h3><ModelName id={job.model_id}/></h3><p className="sk-meta">{kindLabel(job.kind)} · {job.stage.replaceAll('_',' ')} · started {ago(job.created_at)}</p></div><Pill tone={tone}>{status}</Pill></div>
    {active(job)?<Progress label={`${kindLabel(job.kind)} ${job.model_id}`}/>:job.state==='succeeded'?<Progress value={1} max={1} tone="ok" label={`${kindLabel(job.kind)} ${job.model_id} finished`}/>:null}
    <p className="sk-readout"><span className="sk-num">{bytes(job.bytes_copied)}</span> copied<span className="sk-sep">·</span><span className="sk-num">{bytes(job.bytes_hashed)}</span> read for checks</p>
    {job.error_code&&<Callout tone="bad">{errorText(job.error_code)}</Callout>}
    {job.state==='succeeded'&&job.kind==='recover'&&<p className="sk-done"><Icon name="check"/><span>Model and companions restored</span></p>}
    <div className="sk-actions">
      {job.state==='succeeded'&&job.kind==='kit_export'&&<a className="sk-btn sk-outline" href={kitDownload(job)} download><Icon name="download"/>Download recovery kit</a>}
      {job.state==='interrupted'&&job.kind==='preserve'&&job.expected_manifest_id&&<button className="sk-btn sk-outline" disabled={busy||!available(job.location_id??'device')} onClick={()=>onCheck(job)}><Icon name="check"/>Check outcome</button>}
      {(stoppedRecovery(job)||(job.kind==='kit_export'&&job.state==='interrupted'))&&<button className="sk-btn sk-outline" disabled={busy} onClick={()=>onRetry(job)}><Icon name="refresh"/>Review again</button>}
      {stoppedRecovery(job)&&<button className="sk-btn sk-quiet" disabled={busy} onClick={()=>onReclaim(job)}><Icon name="trash"/>Clear unused restore files</button>}
      {job.kind==='receive'&&['failed','cancelled','interrupted'].includes(job.state)&&page.temporary_work_jobs?.includes(job.job_id)&&<button className="sk-btn sk-outline" disabled={busy} onClick={()=>onReceive(job)}><Icon name="download"/>Review download resume</button>}
      {job.state==='succeeded'&&['publish','receive','mirror_import'].includes(job.kind)&&<button className="sk-btn sk-outline" onClick={onSharing}><Icon name="share"/>Use or share this public copy</button>}
      {job.state==='succeeded'&&!['publish','receive','mirror_import'].includes(job.kind)&&job.copy_id&&<button className="sk-btn sk-quiet" disabled={busy||!available(job.location_id??'device')} onClick={()=>onCheck(job)}><Icon name="check"/>Check this copy</button>}
      {active(job)&&<button className="sk-btn sk-quiet" disabled={busy||job.state==='cancelling'} onClick={()=>onCancel(job)}><Icon name="stop"/>{job.state==='cancelling'?'Stopping…':'Stop job'}</button>}
    </div>
  </article>;
}

// Apply a remembered theme before the first paint of the app shell.
{const saved=readTheme();if(saved)document.documentElement.dataset.theme=saved;}
createRoot(document.getElementById('root')!).render(<StrictMode><App/></StrictMode>);
