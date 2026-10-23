import {useEffect,useRef,useState} from 'react';
import {api,bytes,errorText,type Location} from './client';
import {Callout,Consent,Details,Empty,Icon,Mono,Pill,Row,Sheet,Step} from './ui';

interface FolderReview {review_id:string;label:string;creates_folder:boolean;free_bytes:number;expires_at:number}

/** Operator-only storage setup; paths are never saved in browser state or copy controls. */
export function Storage({locations,defaultId,busy,onDevice,onChange}:{locations:Location[];defaultId:string|null;busy:boolean;onDevice:()=>void;onChange:()=>Promise<void>}) {
  const [adding,setAdding]=useState(false),[path,setPath]=useState(''),[label,setLabel]=useState('My backup');
  const [review,setReview]=useState<FolderReview|null>(null),[accepted,setAccepted]=useState(false);
  const [pending,setPending]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const sequence=useRef(0),mounted=useRef(true);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;sequence.current++;};},[]);
  function reset(){sequence.current++;setReview(null);setAccepted(false);setError('');}
  async function requestReview(){const current=++sequence.current;setPending(true);setError('');setReview(null);setAccepted(false);try{const result=await api<FolderReview>('folder-review',{path,label});if(mounted.current&&current===sequence.current)setReview(result);}catch(error){if(mounted.current&&current===sequence.current)setError(error instanceof Error?error.message:'host_unavailable');}finally{if(mounted.current)setPending(false);}}
  async function confirm(){if(!review||!accepted)return;const current=sequence.current;setPending(true);setError('');try{await api('folder-setup',{review_id:review.review_id,confirmed:true});await onChange();if(mounted.current&&current===sequence.current){reset();setAdding(false);setPath('');setNotice('Storage is ready for new copies. Your existing copies stay where they are.');}}catch(error){if(mounted.current&&current===sequence.current){setError(error instanceof Error?error.message:'host_unavailable');setAccepted(false);}}finally{if(mounted.current)setPending(false);}}
  async function choose(locationId:string){setPending(true);setError('');try{await api('storage-default',{location_id:locationId});await onChange();}catch(error){if(mounted.current)setError(error instanceof Error?error.message:'host_unavailable');}finally{if(mounted.current)setPending(false);}}
  const alert=error&&<Callout tone="bad" role="alert">{errorText(error)}</Callout>;
  const close=()=>{reset();setAdding(false);setPath('');};
  return <>
    <section className="sk-storage" aria-labelledby="storage-title" inert={adding}>
      <div className="sk-page-head"><div><h1 id="storage-title">Storage &amp; keys</h1><p>Storage belongs to this Core host and survives Library upgrades. Your signing key stays in protected Core storage.</p></div>
        <div className="sk-page-actions"><button className="sk-btn sk-outline" disabled={busy||pending} onClick={()=>{reset();setAdding(true);setNotice('');}}><Icon name="plus"/>Add folder or NAS</button>{!locations.some(location=>location.location_id==='device')&&<button className="sk-btn sk-quiet" disabled={busy||pending} onClick={onDevice}><Icon name="device"/>Use this device</button>}</div></div>
      {!adding&&alert}
      {notice&&<Callout tone="ok" role="status">{notice}</Callout>}
      {locations.length===0&&<Empty icon="storage" title="No storage yet">Set up storage before preserving an installed model. Restoring a recovery kit needs no storage setup.</Empty>}
      <div className="sk-cards sk-grid">{locations.map(location=>{const chosen=defaultId===location.location_id;return <article className="sk-card sk-location" data-default={chosen?'true':undefined} key={location.location_id} aria-label={location.label}>
        <div className="sk-job-head"><span className="sk-job-kind"><Icon name={location.location_id==='device'?'device':'folder'}/></span><div className="sk-model-title"><h2>{location.label}</h2><p className="sk-meta">Private · {chosen?'Destination for new copies':'Existing copies keep this destination'}</p></div>{location.reachable?<Pill tone="ok">connected</Pill>:<Pill tone="bad">unavailable</Pill>}</div>
        <div className="sk-summary"><Row k="Available space">{location.free_bytes===null?'Unavailable':<span className="sk-num">{bytes(location.free_bytes)}</span>}</Row><Row k="Status">{location.reachable?'Connected':'Unavailable or still checking'}</Row></div>
        <Details summary="Capture identity"><Mono value={location.publisher_key_id} label="capture fingerprint"/><p>The private key stays on Core. Public publishing uses a separate identity. Keep an independent record of this fingerprint for recovery.</p></Details>
        {!chosen&&<div className="sk-actions"><button className="sk-btn sk-outline" disabled={busy||pending||!location.reachable} onClick={()=>void choose(location.location_id)}>Use for new copies</button></div>}
      </article>;})}</div>
    </section>
    {adding&&<Sheet kicker="Storage" title="Add folder or NAS" back="Back to storage" onClose={close}
      footer={review?<><span className="sk-foot-note">Setup copies no models and starts no sharing.</span><button className="sk-btn sk-solid" disabled={busy||pending||!accepted||review.expires_at*1000<=Date.now()} onClick={()=>void confirm()}>Use this storage</button></>:<><span className="sk-foot-note">Nothing is created until you confirm.</span><button className="sk-btn sk-solid" disabled={busy||pending||!path.trim()||!label.trim()} onClick={()=>void requestReview()}>{pending?'Reviewing…':'Review storage'}</button></>}>
      <Step n={1} title="Choose a folder">
        <p>Choose a dedicated folder on the machine running Core. Mount your NAS or remote drive first; the browser can’t pick that machine’s folders for you.</p>
        <label className="sk-field"><span>Folder on this Core host</span><input className="sk-input sk-mono-input" value={path} autoComplete="off" spellCheck={false} placeholder="/Volumes/Backup/foxlight-models" onChange={event=>{reset();setPath(event.target.value);}}/></label>
        <label className="sk-field"><span>Storage name</span><input className="sk-input" value={label} maxLength={80} autoComplete="off" onChange={event=>{reset();setLabel(event.target.value);}}/></label>
        <p className="sk-fine"><Icon name="info" size={14}/><span>Use an empty private folder, or a new folder directly inside an existing connected drive. Setup never creates missing mount paths or changes shared-folder permissions.</span></p>
      </Step>
      {review&&<Step n={2} title="Review and confirm">
        <div className="sk-summary"><Row k="Name">{review.label}</Row><Row k="Folder"><code className="sk-key">{path}</code></Row><Row k="Available"><span className="sk-num">{bytes(review.free_bytes)}</span></Row><Row k="Setup">{review.creates_folder?'Creates one private folder':'Uses the reviewed private folder'}</Row></div>
        <Callout tone="warn">Private copies aren’t encrypted by this feature. Protect access and encryption on the storage you choose. Only public verification keys go with the archive.</Callout>
        <Consent checked={accepted} onChange={setAccepted}>Use this folder for private model copies and as the destination for new Preserve reviews.</Consent>
      </Step>}
      {alert}
    </Sheet>}
  </>;
}
