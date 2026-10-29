import {useCallback,useEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {api,bytes,errorText} from './client';
import {Callout,Consent,Details,Pill,Row,Sheet,Step,type Tone} from './ui';

type Status='sharing'|'eligible'|'failed'|'withheld'|'not_chosen'|'license_not_selected'|'no_license'|'unsupported';
interface CommunityCopy {copy_id:string;name:string;size_bytes:number;status:Status;licenses:string[];public_manifest_id:string|null;error_code?:string|null}
interface Policy {enabled:boolean;share:'eligible'|'chosen';licenses:string[];chosen:string[];withheld:string[];daily_upload_bytes:number;monthly_upload_bytes:number;port:number;consent_expires:number|null}
interface Reachability {status:'public_address'|'private_network'|'offline';local_address:string|null;port:number;mirror_url:string|null}
export interface Community {policy:Policy;consented:boolean;default_licenses:string[];copies:CommunityCopy[];sharing_count:number;running:boolean;http_port:number|null;reachability:Reachability;last_error:string|null;uploaded_today_bytes?:number;uploaded_this_month_bytes?:number}

const label:Record<Status,[string,Tone]>={sharing:['Sharing','live'],eligible:['Joining…','info'],failed:['Couldn’t join · will retry','bad'],withheld:['Kept back','muted'],not_chosen:['Not chosen','muted'],license_not_selected:['Licence not in your list','muted'],no_license:['No licence found · stays private','muted'],unsupported:['Can’t be shared yet','muted']};
const communityError=(code:string)=>({community_consent_required:'Confirm that you want this computer to keep serving before turning this on.',community_license_unidentified:'That doesn’t name a licence. Use its identifier, for example Apache-2.0 or MIT.',community_nothing_to_share:'Nothing to share yet. Models join here as soon as one of your backups has a licence on your list.',insufficient_space:'There isn’t enough free space to prepare a public copy. Your private backups are untouched.'} as Record<string,string>)[code]??errorText(code);

/** One switch that turns this computer into a long-lived mirror of its backups. */
export function HelpKeepAvailable({version}:{version:string|undefined}) {
 const [state,setState]=useState<Community|null>(null),[unavailable,setUnavailable]=useState(false);
 const [setup,setSetup]=useState(false),[agreed,setAgreed]=useState(false),[share,setShare]=useState<'eligible'|'chosen'>('eligible');
 const [licenses,setLicenses]=useState<string[]>([]),[extra,setExtra]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const live=useRef(true);
 useEffect(()=>{live.current=true;return()=>{live.current=false;};},[]);
 const refresh=useCallback(async()=>{try{const value=await api<Community>('desktop-community-read',{});if(live.current){setState(value);setUnavailable(false);}}catch{if(live.current)setUnavailable(true);}},[]);
 useEffect(()=>{void refresh();const timer=setInterval(()=>void refresh(),5000);return()=>clearInterval(timer);},[refresh,version]);
 async function act(action:string,body:Record<string,unknown>){setBusy(true);setError('');try{const value=await api<Community>(action,body);if(live.current)setState(value);return true;}catch(err){if(live.current)setError(err instanceof Error?err.message:'host_unavailable');return false;}finally{if(live.current)setBusy(false);}}
 function open(){if(!state)return;setLicenses(state.policy.licenses);setShare(state.policy.share);setAgreed(false);setExtra('');setError('');setSetup(true);}
 async function turnOn(){if(await act('desktop-community-configure',{enabled:true,consent:true,share,licenses}))setSetup(false);}
 function addLicense(){const value=extra.trim();if(value&&!licenses.some(item=>item.toLowerCase()===value.toLowerCase()))setLicenses(current=>[...current,value]);setExtra('');}
 if(unavailable||!state)return null;
 const {policy,reachability}=state;
 const on=state.consented;
 const eligible=state.copies.filter(item=>['sharing','eligible'].includes(item.status)).length;
 const alert=error&&<Callout tone="bad" role="alert">{communityError(error)}</Callout>;
 const reach=reachability.status==='public_address'
  ?<Callout tone="ok">This computer has a public address. People can download from <code>{reachability.mirror_url}</code> if your firewall allows connections on port {reachability.port}.</Callout>
  :reachability.status==='private_network'
  ?<Callout tone="warn">This computer is behind a router, so people outside your network can’t reach it directly yet. People on your network can use <code>{reachability.mirror_url}</code>. Sovereign never changes your router settings.</Callout>
  :<Callout tone="warn">This computer isn’t connected to a network right now. Sharing resumes when it is.</Callout>;
 return <section className="sk-card sk-community" aria-label="Help keep models available">
  <div className="sk-page-head"><div><h2>Help keep models available</h2><p>Let this computer serve verified copies of your backups to anyone who needs them. Every copy is checked byte for byte by whoever downloads it, so nobody has to trust this computer.</p></div>
   {on?<Pill tone={state.running?'live':'info'}>{state.running?`Sharing ${state.sharing_count} ${state.sharing_count===1?'model':'models'}`:'Starting…'}</Pill>:<Pill tone="muted">Off</Pill>}</div>
  {!setup&&alert}
  {on?<>
   {reach}
   {state.last_error&&state.last_error!=='community_nothing_to_share'&&<Callout tone="bad">{communityError(state.last_error)}</Callout>}
   {state.last_error==='community_nothing_to_share'&&<Callout>{communityError(state.last_error)}</Callout>}
   <Row k="Shares">{policy.share==='eligible'?'Every backup whose licence is on your list':'Only the backups you choose'}</Row>
   <Row k="Uploaded today">{bytes(state.uploaded_today_bytes??0)} of {bytes(policy.daily_upload_bytes)}</Row>
   <Row k="Uploaded this month">{bytes(state.uploaded_this_month_bytes??0)} of {bytes(policy.monthly_upload_bytes)}</Row>
   {policy.consent_expires&&<Row k="Keeps sharing until">{new Date(policy.consent_expires*1000).toLocaleDateString()}, then asks again</Row>}
   <Details summary={`Your backups · ${eligible} of ${state.copies.length} can be shared`}>
    <ul className="sk-community-list">{state.copies.map(item=>{const [text,tone]=label[item.status];const included=policy.share==='chosen'&&policy.chosen.includes(item.copy_id);return <li key={item.copy_id}>
     <span><strong>{item.name}</strong> · {bytes(item.size_bytes)}{item.licenses.length>0&&<> · {Array.from(new Set(item.licenses)).join(', ')}</>}</span> <Pill tone={tone}>{text}</Pill>
     {policy.share==='chosen'&&!['no_license','license_not_selected','unsupported'].includes(item.status)&&<button className="sk-btn sk-outline" disabled={busy} onClick={()=>void act('desktop-community-choose',{copy_id:item.copy_id,chosen:!included,withheld:false})}>{included?'Stop sharing':'Share this one'}</button>}
     {policy.share==='eligible'&&(item.status==='withheld'||['sharing','eligible'].includes(item.status))&&<button className="sk-btn sk-outline" disabled={busy} onClick={()=>void act('desktop-community-choose',{copy_id:item.copy_id,chosen:false,withheld:item.status!=='withheld'})}>{item.status==='withheld'?'Share again':'Keep this one back'}</button>}
    </li>;})}</ul>
    <p>Keeping a backup back, or turning sharing off, only stops serving it. Nothing is deleted.</p>
   </Details>
   <div className="sk-actions"><button className="sk-btn sk-outline" disabled={busy} onClick={open}>Change what’s shared</button><button className="sk-btn sk-outline" disabled={busy} onClick={()=>void act('desktop-community-configure',{enabled:false})}>Turn off</button></div>
  </>:<>
   <p>{eligible>0?`${eligible} ${eligible===1?'backup':'backups'} can be shared under your licence list.`:'Your backups join as soon as one has a licence on your list.'} Sharing uses almost no extra disk space: the mirror reuses your backup’s files.</p>
   <div className="sk-actions"><button className="sk-btn sk-solid" disabled={busy} onClick={open}>Turn on</button></div>
  </>}
  {setup&&createPortal(<Sheet kicker="Help keep models available" title="Share your backups with everyone" back="Close sharing setup" onClose={()=>setSetup(false)} footer={<button className="sk-btn sk-solid" disabled={busy||!agreed||licenses.length===0} onClick={()=>void (on?act('desktop-community-configure',{share,licenses}).then(ok=>ok&&setSetup(false)):turnOn())}>{busy?'Saving…':on?'Save changes':'Start sharing'}</button>}>
   {alert}
   <Step n={1} title="What to share">
    <div role="radiogroup" aria-label="What to share">
     <label className="sk-radio"><input type="radio" name="community-share" checked={share==='eligible'} onChange={()=>setShare('eligible')}/> <span><strong>Every backup whose licence allows it</strong><br/>New backups join on their own. You can keep any one back.</span></label>
     <label className="sk-radio"><input type="radio" name="community-share" checked={share==='chosen'} onChange={()=>setShare('chosen')}/> <span><strong>Only the backups I choose</strong><br/>Pick them from the list after you start.</span></label>
    </div>
   </Step>
   <Step n={2} title="Licences that allow sharing">
    <p>A model is shared only if every part has a licence on this list. Models with no licence stay private. Whether the original download asked who you are doesn’t matter; the licence does.</p>
    <ul className="sk-community-licences">{licenses.map(item=><li key={item} className="sk-licence-chip">{item}<button type="button" aria-label={`Remove ${item}`} onClick={()=>setLicenses(current=>current.filter(value=>value!==item))}>×</button></li>)}</ul>
    <div className="sk-licence-add"><label className="sk-field">Add a licence<input className="sk-input" value={extra} maxLength={200} placeholder="For example CC-BY-4.0" onChange={event=>setExtra(event.target.value)} onKeyDown={event=>{if(event.key==='Enter'){event.preventDefault();addLicense();}}}/></label><button className="sk-btn sk-outline" disabled={!extra.trim()} onClick={addLicense}>Add</button></div>
    {state.default_licenses.some(item=>!licenses.includes(item))&&<button className="sk-btn sk-outline" onClick={()=>setLicenses(current=>[...current,...state.default_licenses.filter(item=>!current.includes(item))])}>Restore the default licences</button>}
   </Step>
   <Step n={3} title="What others can see">
    <p>Anyone who connects can download the shared models and will see this computer’s network address. Direct sharing isn’t anonymous or encrypted.</p>
    <Row k="Upload limit">{bytes(policy.daily_upload_bytes)} a day, {bytes(policy.monthly_upload_bytes)} a month</Row>
    <Row k="Port">{policy.port}</Row>
    <p>Sharing keeps running after you restart this computer, for one year, then asks again. Turn it off at any time.</p>
   </Step>
   <Consent checked={agreed} onChange={setAgreed}>Keep sharing these models from this computer until I turn it off.</Consent>
  </Sheet>,document.body)}
 </section>;
}
