import {useCallback,useEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {api,bytes,errorText} from './client';
import {CloudStorage,type BucketProvider,type BucketSummary} from './sovereign-bucket';
import {Callout,Consent,Details,Pill,Row,Sheet,Step,type Tone} from './ui';
import {Announce,LicenceFamilies,LicenceList,ShareChoice,UploadLimits,describeLicences,limitsValid,listed,type LicenceDefinition} from './community-parts';

type Status='sharing'|'eligible'|'failed'|'withheld'|'not_chosen'|'license_not_selected'|'no_license'|'unsupported';
interface CommunityCopy {copy_id:string;name:string;size_bytes:number;status:Status;licenses:string[];public_manifest_id:string|null;error_code?:string|null}
interface Policy {enabled:boolean;share:'eligible'|'chosen';licenses:string[];chosen:string[];withheld:string[];daily_upload_bytes:number|null;monthly_upload_bytes:number|null;port:number;consent_expires:number|null;public_url?:string|null;announce_dht?:boolean;publish_nostr?:boolean;nostr_relays?:string[]}
interface Reachability {status:'public_address'|'private_network'|'offline';local_address:string|null;port:number;mirror_url:string|null}
type Reason='awaiting_review'|'address_invalid'|'turned_off'|'not_sharing'|'dht_unavailable'|'nothing_shared'|'no_reachable_address'|'address_not_plain_http'|'no_relays';
export interface Community {policy:Policy;consented:boolean;default_licenses:string[];licence_families?:LicenceDefinition;copies:CommunityCopy[];sharing_count:number;running:boolean;http_port:number|null;reachability:Reachability;last_error:string|null;bucket:BucketSummary|null;bucket_providers:BucketProvider[];uploaded_today_bytes?:number;uploaded_this_month_bytes?:number;dht_available?:boolean;dht_announcing?:boolean;nostr_publishing?:boolean;announce_reasons?:{dht:Reason[];nostr:Reason[]};announce_review_required?:boolean;address_invalid?:boolean}
type CheckOutcome='too_slow'|'this_mirror'|'other_mirror'|'no_card'|'not_a_mirror'|'http_error'|'private_address'|'unreachable';
interface AddressCheck {address:string;outcome:CheckOutcome;http_status:number|null}

const label:Record<Status,[string,Tone]>={sharing:['Sharing','live'],eligible:['Joining…','info'],failed:['Couldn’t join · will retry','bad'],withheld:['Kept back','muted'],not_chosen:['Not chosen','muted'],license_not_selected:['Licence not in your list','muted'],no_license:['No licence found · stays private','muted'],unsupported:['Can’t be shared yet','muted']};
const communityError=(code:string)=>({community_address_port_invalid:'That public address has a port that can’t be used. Use a number from 1 to 65535, or leave the port out.',community_address_invalid:'That public address doesn’t look right. Use a plain address such as https://models.example.org, with no user name or password.',community_address_required:'Add a public address first.',community_consent_required:'Confirm that you want this computer to keep serving before turning this on.',community_license_unidentified:'That doesn’t name a licence. Use its identifier, for example Apache-2.0 or MIT.',community_nothing_to_share:'Nothing to share yet. Models join here as soon as one of your backups has a licence on your list.',community_upload_limit_invalid:'The daily upload limit can’t be larger than the monthly one.',community_policy_version:'This sharing setting was saved by a newer version of Sovereign. Update Sovereign to change it.',insufficient_space:'There isn’t enough free space to prepare a public copy. Your private backups are untouched.'} as Record<string,string>)[code]??errorText(code);

const dhtWhy:Record<Reason,string>={
 awaiting_review:'waiting for your review',
 address_invalid:'the public address saved earlier has a port that can’t be used, so it was set aside; set it again',
 turned_off:'you turned it off',
 not_sharing:'sharing is off',
 dht_unavailable:'this copy of Sovereign doesn’t include the BitTorrent component',
 nothing_shared:'nothing is being shared yet',
 no_reachable_address:'people outside your network can’t reach this computer and no public address is set',
 address_not_plain_http:'the DHT can only carry this computer’s own public address or a plain http:// address, so your https:// address is announced through Nostr and other mirrors instead',
 no_relays:'no relays are set',
};
const nostrWhy:Record<Reason,string>={...dhtWhy,no_reachable_address:'people outside your network can’t reach this computer, no public address is set and there’s no cloud storage'};
const why=(reasons:Reason[],text:Record<Reason,string>)=>listed(reasons.map(item=>text[item]));
function checkResult(result:AddressCheck,port:number):[string,'ok'|'warn'|'bad']{
 const at=result.address;
 return ({
  this_mirror:[`This computer reached ${at} and found this mirror there.`,'ok'],
  other_mirror:[`${at} answers, but with a different mirror’s details. Check that it points at this computer.`,'warn'],
  no_card:[`${at} answers like a mirror but doesn’t list this mirror yet. If sharing just started, try again in a few minutes.`,'warn'],
  not_a_mirror:[`${at} answered, but not as a Sovereign mirror. Check that it forwards to port ${port} on this computer.`,'warn'],
  http_error:[`${at} answered with an error (HTTP ${result.http_status??'?'}). Check that it forwards to port ${port} on this computer.`,'warn'],
  private_address:[`${at} leads to a private network address, so people outside your network can’t use it. The check only follows public routes, as anyone else would.`,'warn'],
  too_slow:[`${at} answered too slowly, so the check stopped. People downloading from it would likely find it slow too.`,'warn'],
  unreachable:[`This computer couldn’t reach ${at}. Many routers can’t loop back to their own public address, so this doesn’t prove other people can’t reach it either.`,'bad'],
 } as Record<CheckOutcome,[string,'ok'|'warn'|'bad']>)[result.outcome];
}

/** One switch that turns this computer into a long-lived mirror of its backups. */
export function HelpKeepAvailable({version,onDialogChange}:{version:string|undefined;onDialogChange?:(open:boolean)=>void}) {
 const [state,setState]=useState<Community|null>(null),[unavailable,setUnavailable]=useState(false);
 const [setup,setSetup]=useState<false|'all'|'announce'>(false),[agreed,setAgreed]=useState(false),[share,setShare]=useState<'eligible'|'chosen'>('eligible');
 const [licenses,setLicenses]=useState<string[]>([]),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [limits,setLimits]=useState<[number|null,number|null]>([null,null]);
 const [address,setAddress]=useState(''),[dht,setDht]=useState(true),[nostr,setNostr]=useState(true);
 const [checking,setChecking]=useState(false),[check,setCheck]=useState<AddressCheck|null>(null);
 const live=useRef(true);
 useEffect(()=>{live.current=true;return()=>{live.current=false;};},[]);
 // Setup is a modal step: the page behind it becomes inert while it is open.
 useEffect(()=>{onDialogChange?.(!!setup);return()=>onDialogChange?.(false);},[setup,onDialogChange]);
 const loaded=useRef(false);
 // Hide only when this core never answered; a transient failure keeps the last
 // state, so an open setup sheet (and its Close control) is never torn away.
 const refresh=useCallback(async()=>{try{const value=await api<Community>('desktop-community-read',{});if(live.current){loaded.current=true;setState(value);setUnavailable(false);}}catch{if(live.current&&!loaded.current)setUnavailable(true);}},[]);
 useEffect(()=>{void refresh();const timer=setInterval(()=>void refresh(),5000);return()=>clearInterval(timer);},[refresh,version]);
 async function act(action:string,body:Record<string,unknown>){setBusy(true);setError('');try{const value=await api<Community>(action,body);if(live.current)setState(value);return true;}catch(err){if(live.current)setError(err instanceof Error?err.message:'host_unavailable');return false;}finally{if(live.current)setBusy(false);}}
 function open(mode:'all'|'announce'='all'){if(!state)return;setLicenses(state.policy.licenses);setShare(state.policy.share);setLimits([state.policy.daily_upload_bytes,state.policy.monthly_upload_bytes]);setAddress(state.policy.public_url??'');setDht(state.policy.announce_dht??true);setNostr(state.policy.publish_nostr??true);setAgreed(false);setError('');setSetup(mode);}
 // What people find is chosen with its disclosure on screen; sending the
 // disclosure version records that the owner saw it (with the consent, or later).
 const findChoices=()=>({public_url:address.trim()||null,announce_dht:dht,publish_nostr:nostr,announce_disclosure:1});
 const choices=()=>({share,licenses,daily_upload_bytes:limits[0],monthly_upload_bytes:limits[1],...findChoices()});
 async function turnOn(){if(await act('desktop-community-configure',{enabled:true,consent:true,...choices()}))setSetup(false);}
 async function checkAddress(){setChecking(true);setCheck(null);setError('');try{const value=await api<AddressCheck>('desktop-community-check-address',{});if(live.current)setCheck(value);}catch(err){if(live.current)setError(err instanceof Error?err.message:'host_unavailable');}finally{if(live.current)setChecking(false);}}
 if(unavailable||!state)return null;
 const {policy,reachability}=state;
 const on=state.consented;
 const eligible=state.copies.filter(item=>['sharing','eligible'].includes(item.status)).length;
 const alert=error&&<Callout tone="bad" role="alert">{communityError(error)}</Callout>;
 const relays=policy.nostr_relays??[];
 const typed=address.trim();
 const plainHttp=/^http:\/\//i.test(typed),badAddress=typed!==''&&!/^https?:\/\/\S+$/i.test(typed);
 const announced=[dht&&'the BitTorrent DHT',nostr&&`${relays.length} Nostr ${relays.length===1?'relay':'relays'}`].filter((item):item is string=>!!item);
 const reasons=state.announce_reasons??{dht:[],nostr:[]};
 const findable=<ul className="sk-community-announce" aria-label="How people find this mirror">
  <li>{state.dht_announcing?'Announced on the BitTorrent DHT. Anyone can look up this computer’s address there as a Sovereign mirror.':`Not announced on the BitTorrent DHT: ${why(reasons.dht,dhtWhy)}.`}</li>
  <li>{state.nostr_publishing?`Published to ${relays.length} Nostr ${relays.length===1?'relay':'relays'}. Anyone can read this mirror’s addresses there.`:`Not published to Nostr relays: ${why(reasons.nostr,nostrWhy)}.`}</li>
 </ul>;
 const announceStep=(n:number)=><Step n={n} title="How people find this computer">
   <label className="sk-field">Public address (optional)<input className="sk-input" value={address} maxLength={2048} placeholder="https://models.example.org" autoComplete="off" spellCheck={false} aria-invalid={badAddress||undefined} onChange={event=>setAddress(event.target.value)}/></label>
   <p>Only if people outside your network reach this computer through your own address, such as a port forward or a reverse proxy. https:// is preferred. Leave it empty otherwise.</p>
   {badAddress&&<Callout tone="bad">Start the address with https:// (or http://), with no spaces.</Callout>}
   {plainHttp&&<Callout tone="warn">Plain http:// isn’t encrypted: anyone along the way can see which models are downloaded. Use https:// if you can.</Callout>}
   <Announce checked={dht} onChange={setDht} title="Announce on the BitTorrent DHT">Publishes this computer’s public IP address and mirror port on the public BitTorrent DHT as a Sovereign mirror, so people with no other contacts can find it. Anyone can look it up, and it isn’t anonymous. It only happens while this computer is reachable from outside.{state.dht_available===false&&' This copy of Sovereign doesn’t include the BitTorrent component, so nothing is announced there for now.'}</Announce>
   <Announce checked={nostr} onChange={setNostr} title="Publish to Nostr relays">Publishes this mirror’s signed card, with this computer’s address (and your cloud storage address, if any), to {relays.length} public Nostr {relays.length===1?'relay':'relays'}{relays.length>0&&<> ({relays.map(item=>item.replace(/^wss:\/\//,'')).join(', ')})</>}. Anyone can read it, and it isn’t anonymous.</Announce>
  </Step>;
 const reach=reachability.status==='public_address'
  ?<Callout tone="ok">This computer has a public address. People can download from <code>{reachability.mirror_url}</code> if your firewall allows connections on port {reachability.port}.</Callout>
  :reachability.status==='private_network'&&policy.public_url
  ?<Callout tone="info">This computer is behind a router. People outside your network can reach it only through the public address below, if your router or proxy forwards it here. People on your network can use <code>{reachability.mirror_url}</code>. Sovereign never changes your router settings.</Callout>
  :reachability.status==='private_network'
  ?<Callout tone="warn">This computer is behind a router, so people outside your network can’t reach it directly yet. People on your network can use <code>{reachability.mirror_url}</code>. Sovereign never changes your router settings. Cloud storage below lets people anywhere download.</Callout>
  :<Callout tone="warn">This computer isn’t connected to a network right now. Sharing resumes when it is.</Callout>;
 return <section className="sk-card sk-community" aria-label="Help keep models available">
  <div className="sk-page-head"><div><h2>Help keep models available</h2><p>Let this computer serve verified copies of your backups to anyone who needs them. Every copy is checked byte for byte by whoever downloads it, so nobody has to trust this computer.</p></div>
   {on?<Pill tone={state.running?'live':'info'}>{state.running?`Sharing ${state.sharing_count} ${state.sharing_count===1?'model':'models'}`:'Starting…'}</Pill>:<Pill tone="muted">Off</Pill>}</div>
  {!setup&&alert}
  {on?<>
   {reach}
   {state.announce_review_required&&<Callout tone="warn">Sharing continues as before, but this computer isn’t announced on the BitTorrent DHT or Nostr relays until you choose. An earlier version turned both on without saying what they reveal.<div className="sk-actions"><button className="sk-btn sk-solid" disabled={busy} onClick={()=>open('announce')}>Choose how people find this computer</button></div></Callout>}
   {state.last_error&&state.last_error!=='community_nothing_to_share'&&<Callout tone="bad">{communityError(state.last_error)}</Callout>}
   {state.last_error==='community_nothing_to_share'&&<Callout>{communityError(state.last_error)}</Callout>}
   {policy.public_url&&<>
    <Row k="Public address"><code>{policy.public_url}</code></Row>
    {/^http:/i.test(policy.public_url)&&<Callout tone="warn">This address uses plain http://, so downloads aren’t encrypted and anyone along the way can see which models are fetched. https:// is better if you can set it up.</Callout>}
    <div className="sk-actions"><button className="sk-btn sk-outline" disabled={checking} onClick={()=>void checkAddress()}>{checking?'Checking…':'Check address'}</button></div>
    {check&&(()=>{const [text,tone]=checkResult(check,policy.port);return <Callout tone={tone} role="status">{text} This is a best-effort check from this computer, inside your own network. It can’t prove that people elsewhere can reach it.</Callout>;})()}
   </>}
   {state.announce_reasons&&findable}
   <Row k="Shares">{policy.share==='eligible'?'Every backup whose licence is on your list':'Only the backups you choose'}</Row>
   <Row k="Licences">{describeLicences(policy.licenses,state.licence_families)}</Row>
   <Row k="Uploaded today">{bytes(state.uploaded_today_bytes??0)}{policy.daily_upload_bytes===null?' · no limit':<> of {bytes(policy.daily_upload_bytes)}</>}</Row>
   <Row k="Uploaded this month">{bytes(state.uploaded_this_month_bytes??0)}{policy.monthly_upload_bytes===null?' · no limit':<> of {bytes(policy.monthly_upload_bytes)}</>}</Row>
   {policy.consent_expires&&<Row k="Keeps sharing until">{new Date(policy.consent_expires*1000).toLocaleDateString()}, then asks again</Row>}
   <Details summary={`Your backups · ${eligible} of ${state.copies.length} can be shared`}>
    <ul className="sk-community-list">{state.copies.map(item=>{const [text,tone]=label[item.status];const included=policy.share==='chosen'&&policy.chosen.includes(item.copy_id);return <li key={item.copy_id}>
     <span><strong>{item.name}</strong> · {bytes(item.size_bytes)}{item.licenses.length>0&&<> · {Array.from(new Set(item.licenses)).join(', ')}</>}</span> <Pill tone={tone}>{text}</Pill>
     {policy.share==='chosen'&&!['no_license','license_not_selected','unsupported'].includes(item.status)&&<button className="sk-btn sk-outline" disabled={busy} onClick={()=>void act('desktop-community-choose',{copy_id:item.copy_id,chosen:!included,withheld:false})}>{included?'Stop sharing':'Share this one'}</button>}
     {policy.share==='eligible'&&(item.status==='withheld'||['sharing','eligible','failed'].includes(item.status))&&<button className="sk-btn sk-outline" disabled={busy} onClick={()=>void act('desktop-community-choose',{copy_id:item.copy_id,chosen:false,withheld:item.status!=='withheld'})}>{item.status==='withheld'?'Share again':'Keep this one back'}</button>}
    </li>;})}</ul>
    <p>Keeping a backup back, or turning sharing off, only stops serving it. Nothing is deleted.</p>
   </Details>
   <CloudStorage state={state} sharing behindRouter={reachability.status!=='public_address'} onState={value=>{if(live.current)setState(value);}}/>
   <div className="sk-actions"><button className="sk-btn sk-outline" disabled={busy} onClick={()=>open()}>Change what’s shared</button><button className="sk-btn sk-outline" disabled={busy} onClick={()=>void act('desktop-community-configure',{enabled:false})}>Turn off</button></div>
  </>:<>
   <p>{eligible>0?`${eligible} ${eligible===1?'backup':'backups'} can be shared under your licence list.`:'Your backups join as soon as one has a licence on your list.'} Sharing uses almost no extra disk space: the mirror reuses your backup’s files.</p>
   <div className="sk-actions"><button className="sk-btn sk-solid" disabled={busy} onClick={()=>open()}>Turn on</button></div>
   {/* A connected bucket stays visible while off: its withdrawal and Disconnect. */}
   {state.bucket&&<CloudStorage state={state} sharing={false} behindRouter={false} onState={value=>{if(live.current)setState(value);}}/>}
  </>}
  {setup==='announce'&&createPortal(<Sheet kicker="Help keep models available" title="Choose how people find this computer" back="Close announcement review" onClose={()=>setSetup(false)} footer={<button className="sk-btn sk-solid" disabled={busy||badAddress} onClick={()=>void act('desktop-community-configure',findChoices()).then(ok=>ok&&setSetup(false))}>{busy?'Saving…':'Save these choices'}</button>}>
   {alert}
   <p>Sharing stays on exactly as it is. Choose whether this computer also announces itself publicly. Nothing is announced until you save.</p>
   {announceStep(1)}
  </Sheet>,document.body)}
  {setup==='all'&&createPortal(<Sheet kicker="Help keep models available" title="Share your backups with everyone" back="Close sharing setup" onClose={()=>setSetup(false)} footer={<button className="sk-btn sk-solid" disabled={busy||!agreed||licenses.length===0||badAddress||!limitsValid(...limits)} onClick={()=>void (on?act('desktop-community-configure',choices()).then(ok=>ok&&setSetup(false)):turnOn())}>{busy?'Saving…':on?'Save changes':'Start sharing'}</button>}>
   {alert}
   <Step n={1} title="What to share">
    <ShareChoice value={share} onChange={setShare} name="community-share" every={['Every backup whose licence allows it','New backups join on their own. You can keep any one back.']} chosen={['Only the backups I choose','Pick them from the list after you start.']}/>
   </Step>
   <Step n={2} title="Licences that allow sharing">
    <p>A model is shared only if every part has a licence that’s on. Whether the original download asked who you are doesn’t matter; the licence does.</p>
    {state.licence_families?<LicenceFamilies definition={state.licence_families} licenses={licenses} onChange={setLicenses} copies={state.copies.filter(item=>item.status!=='unsupported')} noun={['backup','backups']}/>:<LicenceList licenses={licenses} onChange={setLicenses} defaults={state.default_licenses}/>}
   </Step>
   {announceStep(3)}
   <Step n={4} title="What others can see">
    <p>Anyone who connects can download the shared models and will see this computer’s network address. Direct sharing isn’t anonymous or encrypted.</p>
    <UploadLimits daily={limits[0]} monthly={limits[1]} onChange={(daily,monthly)=>setLimits([daily,monthly])}/>
    <Row k="Port">{policy.port}</Row>
    <p>Sharing keeps running after you restart this computer, for one year, then asks again. Turn it off at any time.</p>
   </Step>
   <Consent checked={agreed} onChange={setAgreed}>Keep sharing these models from this computer until I turn it off{announced.length?`, and announce it publicly on ${listed(announced)}`:', without announcing it publicly'}.</Consent>
  </Sheet>,document.body)}
 </section>;
}
