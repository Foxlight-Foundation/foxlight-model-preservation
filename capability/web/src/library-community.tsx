import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {api,bytes,errorText,type Copy} from './client';
import {Announce,LicenceList,ShareChoice,listed} from './community-parts';
import {PeerAccess,type PeerGrant} from './peer';
import {Callout,Consent,Details,Icon,Pill,Progress,Row,Sheet,Step,type Tone} from './ui';

/** Core's persistent-mirror settings, as Mirror settings saves them. */
export interface MirrorConfiguration {
 name:string;disk_bytes:number;ingress_daily:number;egress_daily:number;egress_monthly:number;
 http:boolean;exposure:'loopback'|'network';port:number;ipfs:boolean;torrent:boolean;peer_setup_id:string|null;
 max_active:number;session_seconds:number;session_bytes:number;upload_rate:number;
}
interface HeldModel {manifest_id:string;state:'pending'|'retained'|'removed';pinned:boolean;blocked:boolean}
interface MirrorPage {
 configured:boolean;available:boolean;policy_id:string|null;configuration:MirrorConfiguration|null;paused:boolean;
 restart_expires:number|null;restart_allowed:boolean;controller_job_id:string|null;
 activity:{running:boolean;http_port:number|null;native_workers:number}|null;models:HeldModel[];model_count:number;
}
type Step_='stopping'|'configuring'|'keeping'|'permitting'|'starting';
interface Progress_ {state:'idle'|'working'|'done'|'failed'|'stopped';step:Step_|null;kept:number;total:number;skipped:{copy_id:string;reason:string}[];error_code:string|null;expires:number|null}
interface Switch {progress:Progress_;default_licenses:string[];defaults:MirrorConfiguration;consent_seconds:number;disclosure:number}
type Status='sharing'|'kept_back'|'pending'|'eligible'|'license_not_selected'|'no_license'|'unsupported'|'removed'|'review_unavailable';
interface Reviewed {copy_id:string;manifest_id:string;status:Status;licenses:string[]}

const gibibyte=1024**3;
const label:Record<Status,[string,Tone]>={sharing:['Sharing','live'],kept_back:['Kept back','muted'],pending:['Joining…','info'],eligible:['Can be shared','info'],license_not_selected:['Licence not in your list','muted'],no_license:['No licence found · stays private','muted'],unsupported:['Can’t be shared yet','muted'],removed:['Removed earlier · not shared','muted'],review_unavailable:['Terms couldn’t be read','bad']};
const shareable:Status[]=['eligible','sharing','pending','kept_back'];
const steps:Record<Step_,string>={stopping:'Pausing current sharing to apply your choices…',configuring:'Saving mirror settings…',keeping:'Keeping public copies for sharing…',permitting:'Recording your consent for one year…',starting:'Starting to share…'};
const shareError=(code:string)=>({
 community_nothing_to_share:'Nothing could be shared. A public copy joins only when every part has a licence on your list.',
 community_license_unidentified:'That doesn’t name a licence. Use its identifier, for example Apache-2.0 or MIT.',
 mirror_share_busy:'Sharing is already being set up. Wait for it to finish, or stop it.',
 mirror_transport_required:'Choose at least one way for people to download.',
 mirror_allowance_invalid:'The daily upload allowance can’t be larger than the monthly one.',
 mirror_unavailable:'Mirror storage is unavailable or busy. Reconnect it, then try again.',
 mirror_busy_stop_first:'Another mirror operation is running. Let it finish, or stop it under Advanced mirror controls.',
 license_not_selected:'licence not in your list',no_license:'no licence found',unsupported:'can’t be shared yet',removed:'removed earlier',
} as Record<string,string>)[code]??errorText(code);

/** Sovereign's one-switch community mirror, over this host's persistent mirror. */
export function LibraryKeepAvailable({copies,features,bittorrentAvailable,onDialogChange}:{copies:Copy[];features:Record<string,boolean>|null;bittorrentAvailable:boolean;onDialogChange?:(open:boolean)=>void}) {
 const [page,setPage]=useState<MirrorPage|null>(null),[state,setState]=useState<Switch|null>(null),[readError,setReadError]=useState('');
 const [setup,setSetup]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [reviewed,setReviewed]=useState<Reviewed[]>([]);
 const live=useRef(true);
 const supported=features?.['Persistent mirrors']===true&&features?.['Public copy review']===true;
 useEffect(()=>{live.current=true;return()=>{live.current=false;};},[]);
 useEffect(()=>{onDialogChange?.(setup);return()=>onDialogChange?.(false);},[setup,onDialogChange]);
 const working=state?.progress.state==='working';
 const refresh=useCallback(async()=>{
  try{const [snapshot,current]=await Promise.all([api<MirrorPage>('mirror-read',{offset:0}),api<Switch>('mirror-share-read',{})]);if(live.current){setPage(snapshot);setState(current);setReadError('');}}
  catch(reason){if(live.current)setReadError(reason instanceof Error?reason.message:'host_unavailable');}
 },[]);
 useEffect(()=>{if(!supported)return;void refresh();const timer=setInterval(()=>void refresh(),working?1000:4000);return()=>clearInterval(timer);},[supported,refresh,working]);
 const identities=useMemo(()=>copies.map(copy=>({copy_id:copy.copy_id,manifest_id:copy.manifest_id,member_count:copy.member_count})),[copies]);
 const review=useCallback(async(licenses:string[])=>{
  if(licenses.length===0||identities.length===0){setReviewed([]);return;}
  try{const value=await api<{copies:Reviewed[]}>('mirror-share-review',{licenses,copies:identities});if(live.current)setReviewed(value.copies);}
  catch(reason){if(live.current)setError(reason instanceof Error?reason.message:'host_unavailable');}
 },[identities]);
 useEffect(()=>{if(supported&&page)void review(state?.default_licenses??[]);},[supported,Boolean(page),Boolean(state),review]);
 async function stop(){setBusy(true);setError('');try{await api('mirror-share-stop',{});}catch(reason){if(live.current)setError(reason instanceof Error?reason.message:'host_unavailable');}finally{if(live.current){setBusy(false);void refresh();}}}
 async function keepBack(model:HeldModel,back:boolean){if(!page?.policy_id)return;setBusy(true);setError('');try{await api('mirror-start',{kind:'model',policy_id:page.policy_id,manifest_id:model.manifest_id,action:back?'block':'unblock',operation_id:crypto.randomUUID()});}catch(reason){if(live.current)setError(reason instanceof Error?reason.message:'host_unavailable');}finally{if(live.current){setBusy(false);void refresh();void review(state?.default_licenses??[]);}}}
 if(features===null)return null;
 if(!supported)return <section className="sk-card sk-community" aria-label="Help keep models available"><h2>Help keep models available</h2><p className="sk-hint">Update the preservation host to share public copies with one switch. Existing sharing sessions below remain available.</p></section>;
 const on=Boolean(page?.controller_job_id);
 const held=page?.models.filter(model=>model.state!=='removed')??[];
 const serving=held.filter(model=>!model.blocked).length;
 const configuration=page?.configuration??null;
 const progress=state?.progress;
 const name=(identity:string)=>copies.find(copy=>copy.manifest_id===identity)?.model_id??`Public copy ${identity.slice(0,12)}`;
 const status=(copy:Copy):Status=>reviewed.find(item=>item.manifest_id===copy.manifest_id)?.status??'review_unavailable';
 const eligible=copies.filter(copy=>shareable.includes(status(copy))).length;
 const soon=page?.restart_expires!=null&&page.restart_expires*1000-Date.now()<14*86400*1000;
 const alert=(error||readError)&&<Callout tone="bad" role="alert">{shareError(error||readError)}</Callout>;
 const reach=configuration&&[configuration.http?(configuration.exposure==='network'?`other devices over HTTP, port ${configuration.port||'chosen when sharing starts'}`:'this device only, over HTTP'):null,configuration.torrent?'public BitTorrent':null,configuration.ipfs?'public IPFS':null].filter((item):item is string=>!!item);
 return <section className="sk-card sk-community" aria-label="Help keep models available">
  <div className="sk-page-head"><div><h2>Help keep models available</h2><p>Let this host serve verified public copies to anyone who needs them. Every copy is checked byte for byte by whoever downloads it, so nobody has to trust this host.</p></div>
   {working?<Pill tone="info">Setting up…</Pill>:on?<Pill tone="live">{`Sharing ${serving} ${serving===1?'model':'models'}`}</Pill>:<Pill tone="muted">Off</Pill>}</div>
  {!setup&&alert}
  {!page&&!readError&&<p className="sk-hint" role="status">Reading this host’s mirror…</p>}
  {working&&progress&&<>
   <Progress label="Setting up sharing"/>
   <p role="status">{progress.step?steps[progress.step]:'Starting…'} {progress.step==='keeping'&&`${progress.kept} of ${progress.total} kept.`}</p>
   <div className="sk-actions"><button className="sk-btn sk-outline" disabled={busy} onClick={()=>void stop()}><Icon name="stop"/>Stop sharing</button></div>
  </>}
  {!working&&progress?.state==='failed'&&progress.error_code&&<Callout tone="bad">{shareError(progress.error_code)}</Callout>}
  {!working&&progress&&progress.skipped.length>0&&progress.state!=='idle'&&<Callout>Not shared: {listed(progress.skipped.map(item=>`${name(copies.find(copy=>copy.copy_id===item.copy_id)?.manifest_id??'')} (${shareError(item.reason)})`))}.</Callout>}
  {page&&!working&&(on?<>
   {configuration&&reach&&<Row k="People can download from">{listed(reach)}</Row>}
   {page.activity?.http_port&&<p className="sk-hint">This host is listening on port {page.activity.http_port}. That doesn’t prove people outside your network can reach it; your router and firewall decide that, and Skulk never changes them.</p>}
   {configuration&&<Row k="Upload limit">{bytes(configuration.egress_daily)} a day, {bytes(configuration.egress_monthly)} a month</Row>}
   {configuration&&<Row k="Storage allowance">{bytes(configuration.disk_bytes)}</Row>}
   <Row k="Keeps sharing until">{page.restart_allowed&&page.restart_expires?`${new Date(page.restart_expires*1000).toLocaleDateString()}, including after restarts, then asks again`:'You stop it, or this host restarts'}</Row>
   {soon&&<Callout>Sharing stops on {new Date(page.restart_expires!*1000).toLocaleDateString()}. Choose Change what’s shared to keep it on for a year.</Callout>}
   <Details summary={`Your public copies · ${serving} shared`}>
    <ul className="sk-community-list">{held.map(model=><li key={model.manifest_id}><span><strong>{name(model.manifest_id)}</strong></span> <Pill tone={model.blocked?'muted':model.state==='pending'?'info':'live'}>{model.blocked?'Kept back':model.state==='pending'?'Joining…':'Sharing'}</Pill> <button className="sk-btn sk-outline" disabled={busy} onClick={()=>void keepBack(model,!model.blocked)}>{model.blocked?'Share again':'Keep this one back'}</button></li>)}
     {copies.filter(copy=>!held.some(model=>model.manifest_id===copy.manifest_id)).map(copy=><li key={copy.copy_id}><span><strong>{copy.model_id}</strong> · {bytes(copy.size_bytes)}</span> <Pill tone="muted">{status(copy)==='eligible'?'Not shared yet':label[status(copy)][0]}</Pill></li>)}</ul>
    <p>Keeping a copy back only stops serving it. Nothing is deleted. New public copies join when you choose Change what’s shared.</p>
   </Details>
   <div className="sk-actions"><button className="sk-btn sk-outline" disabled={busy} onClick={()=>{setError('');setSetup(true);}}>Change what’s shared</button><button className="sk-btn sk-outline" disabled={busy} onClick={()=>void stop()}><Icon name="stop"/>Stop sharing</button></div>
  </>:<>
   <p>{copies.length===0?'Publish, receive or import a public copy first. Private copies stay private.':eligible>0?`${eligible} public ${eligible===1?'copy':'copies'} can be shared under the default licence list.`:'Public copies join when every part has a licence on your list.'} Nothing is shared until you turn this on.</p>
   {held.length>0&&<p className="sk-hint">{held.length} {held.length===1?'copy is':'copies are'} already kept for sharing on this host. Turning on keeps {held.length===1?'it':'them'} as {held.length===1?'it is':'they are'}.</p>}
   <div className="sk-actions"><button className="sk-btn sk-solid" disabled={busy||copies.length===0||!state||!page.available&&page.configured} onClick={()=>{setError('');setSetup(true);}}>Turn on</button></div>
  </>)}
  {setup&&page&&state&&<ShareSetup page={page} state={state} copies={copies} reviewed={reviewed} on={on} bittorrentAvailable={bittorrentAvailable} onReview={review} onClose={()=>{setSetup(false);void review(state.default_licenses);}} onStarted={value=>{setState(value);setSetup(false);void refresh();void review(state.default_licenses);}}/>}
 </section>;
}

/** One sheet: what, which licences, how people reach this host, then one consent. */
function ShareSetup({page,state,copies,reviewed,on,bittorrentAvailable,onReview,onClose,onStarted}:{page:MirrorPage;state:Switch;copies:Copy[];reviewed:Reviewed[];on:boolean;bittorrentAvailable:boolean;onReview:(licenses:string[])=>Promise<void>;onClose:()=>void;onStarted:(value:Switch)=>void}) {
 const base=page.configuration;
 const [share,setShare]=useState<'eligible'|'chosen'>('eligible'),[chosen,setChosen]=useState<string[]>(()=>copies.filter(copy=>page.models.some(model=>model.manifest_id===copy.manifest_id&&model.state!=='removed'&&!model.blocked)).map(copy=>copy.copy_id));
 const [licenses,setLicenses]=useState<string[]>(state.default_licenses);
 const [draft,setDraft]=useState<MirrorConfiguration>(()=>base?{...base}:{...state.defaults,torrent:bittorrentAvailable});
 const [peer,setPeer]=useState<PeerGrant|null>(null),[agreed,setAgreed]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [policy]=useState(page.policy_id);
 const first=useRef(true);
 useEffect(()=>{if(first.current){first.current=false;return;}const timer=setTimeout(()=>void onReview(licenses),300);return()=>clearTimeout(timer);},[licenses,onReview]);
 const status=(copy:Copy):Status=>reviewed.find(item=>item.manifest_id===copy.manifest_id)?.status??'review_unavailable';
 const candidates=copies.filter(copy=>shareable.includes(status(copy)));
 const selected=share==='eligible'?candidates:candidates.filter(copy=>chosen.includes(copy.copy_id));
 // Storage for a new mirror covers the selected copies with headroom; existing settings stay as reviewed.
 const needed=Math.ceil(selected.reduce((sum,copy)=>sum+copy.size_bytes,0)*1.25/gibibyte)*gibibyte;
 const configuration:MirrorConfiguration={...draft,disk_bytes:base?draft.disk_bytes:Math.max(draft.disk_bytes,needed),peer_setup_id:draft.ipfs?peer?.peer_setup_id??null:null};
 const network=draft.http&&draft.exposure==='network';
 const setNetwork=(value:boolean)=>setDraft(current=>value?{...current,http:true,exposure:'network'}:{...current,exposure:'loopback',http:Boolean(base?.http&&base.exposure==='loopback')});
 const numeric=(key:keyof MirrorConfiguration,value:number)=>setDraft(current=>({...current,[key]:value}));
 const validNumbers=[configuration.disk_bytes,configuration.ingress_daily,configuration.egress_daily,configuration.egress_monthly,configuration.session_bytes].every(value=>Number.isSafeInteger(value)&&value>0)&&configuration.disk_bytes>=256*1024**2&&configuration.egress_daily<=configuration.egress_monthly&&Number.isInteger(configuration.port)&&configuration.port>=0&&configuration.port<=65535&&Number.isInteger(configuration.max_active)&&configuration.max_active>=1&&configuration.max_active<=4&&Number.isInteger(configuration.session_seconds)&&configuration.session_seconds>0&&configuration.session_seconds<=86400&&Number.isSafeInteger(configuration.upload_rate)&&configuration.upload_rate>=1024&&configuration.upload_rate<=100*1024**2&&(!configuration.ipfs||Boolean(peer));
 const transports=[network?'other devices over HTTP':configuration.http?'this device over HTTP':null,configuration.torrent?'public BitTorrent':null,configuration.ipfs?'public IPFS':null].filter((item):item is string=>!!item);
 const ready=agreed&&licenses.length>0&&selected.length>0&&validNumbers&&transports.length>0;
 async function start(){
  setBusy(true);setError('');
  try{const value=await api<Switch>('mirror-share-start',{consent:true,disclosure:state.disclosure,policy_id:policy,share,licenses,copies:selected.map(copy=>({copy_id:copy.copy_id,manifest_id:copy.manifest_id,member_count:copy.member_count})),configuration});onStarted(value);}
  catch(reason){setError(reason instanceof Error?reason.message:'host_unavailable');}
  finally{setBusy(false);}
 }
 const field=(key:keyof MirrorConfiguration,text:string,scale:number,min:number,max?:number)=><label className="sk-field" key={key}><span>{text}</span><input className="sk-input" type="number" min={min} max={max} step="any" disabled={busy} value={(draft[key] as number)/scale} onChange={event=>numeric(key,Math.round(Number(event.target.value)*scale))}/></label>;
 return <Sheet kicker="Help keep models available" title="Share your public copies with everyone" back="Close sharing setup" onClose={onClose} footer={<button className="sk-btn sk-solid" disabled={busy||!ready} onClick={()=>void start()}>{busy?'Saving…':on?'Save changes':'Start sharing'}</button>}>
  {error&&<Callout tone="bad" role="alert">{shareError(error)}</Callout>}
  <Step n={1} title="What to share">
   <ShareChoice value={share} onChange={setShare} name="library-share" every={['Every public copy whose licence allows it','Copies you kept back stay kept back. You can keep any one back later.']} chosen={['Only the copies I choose','Pick them below.']}/>
   <ul className="sk-community-list" aria-label="Public copies">{copies.map(copy=>{const value=status(copy),[text,tone]=label[value],can=shareable.includes(value);return <li key={copy.copy_id}>
    {share==='chosen'&&can?<label><input type="checkbox" checked={chosen.includes(copy.copy_id)} onChange={event=>setChosen(current=>event.target.checked?[...current,copy.copy_id]:current.filter(item=>item!==copy.copy_id))}/> <strong>{copy.model_id}</strong></label>:<strong>{copy.model_id}</strong>}
    <span> · {bytes(copy.size_bytes)}{(reviewed.find(item=>item.copy_id===copy.copy_id)?.licenses.length??0)>0&&<> · {reviewed.find(item=>item.copy_id===copy.copy_id)!.licenses.join(', ')}</>}</span> <Pill tone={tone}>{text}</Pill>
   </li>;})}</ul>
   {copies.length===0&&<p>No public copies yet. Private copies stay private.</p>}
   <p className="sk-hint">Only what you agree to here is served. {share==='chosen'?'Copies you don’t choose':'Copies whose licence isn’t on your list'} stop being served; their files stay on this host.</p>
  </Step>
  <Step n={2} title="Licences that allow sharing">
   <p>A copy is shared only if every part has a licence on this list. Copies with no licence stay private. Whether the original download asked who you are doesn’t matter; the licence does.</p>
   <LicenceList licenses={licenses} onChange={setLicenses} defaults={state.default_licenses}/>
  </Step>
  <Step n={3} title="How people reach this host">
   <Announce checked={network} onChange={setNetwork} title="Accept downloads from other devices">Serves the shared copies over plain HTTP on port {configuration.port||'chosen when sharing starts'}. Anyone who can reach this host can download them and sees its network address. Downloads aren’t encrypted or anonymous. Skulk never changes your router or firewall, so people outside your network reach it only if you forward that port.</Announce>
   <Announce checked={configuration.torrent} disabled={!bittorrentAvailable&&!configuration.torrent} onChange={torrent=>setDraft(current=>({...current,torrent}))} title="Share on public BitTorrent">Joins public BitTorrent swarms for the shared copies, so people can download from everyone who has them. Other peers see this host’s public IP address and what it shares. It isn’t anonymous.{!bittorrentAvailable&&' This host has no compatible BitTorrent component, so this isn’t available.'}</Announce>
   <p className="sk-hint">This host isn’t announced on the BitTorrent DHT or Nostr relays. To point someone at it, use Share with someone below.</p>
   {transports.length===0&&<Callout tone="bad">Choose at least one way for people to download.</Callout>}
  </Step>
  <Step n={4} title="What others can see">
   <p>Anyone who connects can download the shared copies and will see this host’s network address. Sharing isn’t anonymous.</p>
   <Row k="Upload limit">{bytes(configuration.egress_daily)} a day, {bytes(configuration.egress_monthly)} a month</Row>
   <Row k="Storage allowance">{bytes(configuration.disk_bytes)}</Row>
   <Row k="Shares">{selected.length} public {selected.length===1?'copy':'copies'}</Row>
   <p>Sharing keeps running after this host restarts, for one year, then asks again. Stop it at any time with one click.</p>
  </Step>
  <Details summary="Advanced settings">
   <p>Sensible limits are already set. Kept copies use separate storage on this host, at least enough for the copies you share; reaching an allowance pauses new work and never deletes anything.</p>
   {field('disk_bytes','Storage allowance (GB)',gibibyte,0.25)}
   {field('ingress_daily','Download allowance per day (GB)',gibibyte,0.001)}
   {field('egress_daily','Upload allowance per day (GB)',gibibyte,0.001)}
   {field('egress_monthly','Upload allowance per month (GB)',gibibyte,0.001)}
   {field('port','HTTP port (0 chooses a free port)',1,0,65535)}
   {field('max_active','Simultaneous native workers',1,1,4)}
   {field('session_seconds','Native session duration (minutes)',60,1/60,1440)}
   {field('session_bytes','Upload reservation per native session (MB)',1024**2,1/1024**2)}
   {field('upload_rate','Native upload rate (MB/s)',1024**2,1/1024,100)}
   <Announce checked={draft.ipfs} onChange={ipfs=>{setPeer(null);setDraft(current=>({...current,ipfs}));}} title="Share on public IPFS">Publishes the shared copies to the public IPFS network through this host’s reviewed peer settings. Peers see this host’s addresses and what it shares. It isn’t anonymous.</Announce>
   {draft.ipfs&&<PeerAccess sharing heading={false} purpose="this mirror’s sharing" onChange={setPeer}/>}
   {!validNumbers&&<Callout tone="bad">Check the advanced settings: every allowance needs a positive value, and the daily upload allowance can’t exceed the monthly one.</Callout>}
  </Details>
  <Consent checked={agreed} onChange={setAgreed}>Keep sharing these public copies from this host, over {transports.length?listed(transports):'no transport yet'}, until I stop it or for one year.</Consent>
 </Sheet>;
}
