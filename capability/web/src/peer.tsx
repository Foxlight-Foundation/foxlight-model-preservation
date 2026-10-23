import {useEffect,useRef,useState} from 'react';
import {api,errorText} from './client';
import {RuntimeSetup} from './runtime';
import {Callout,Consent,Icon,Pill} from './ui';

/** A local connection policy grants no source, publisher or registry authority. */
export interface PeerState {runtime:'available'|'missing'|'incompatible';setup_id:string|null;bootstrap:string[];allow_private:boolean;active:boolean}
/** Consent is attached to the exact offline-reviewed Core connection settings. */
export interface PeerGrant {allow_peer_network:true;peer_setup_id:string}

/** Reusable setup and per-operation consent for receive-only native networking. */
export function PeerAccess({onChange,purpose='this operation',heading=true,compact=false}:{onChange:(grant:PeerGrant|null)=>void;purpose?:string;heading?:boolean;compact?:boolean}) {
  const [state,setState]=useState<PeerState|null>(null),[contacts,setContacts]=useState(''),[privateAddress,setPrivateAddress]=useState(false),[confirmed,setConfirmed]=useState(false),[allowed,setAllowed]=useState(false),[editing,setEditing]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const live=useRef(true),callback=useRef(onChange);callback.current=onChange;
  async function read(preserveDraft=false){setBusy(true);setError('');setAllowed(false);callback.current(null);try{const value=await api<PeerState>('peer-read',{});if(live.current){setState(value);if(!preserveDraft){setContacts(value.bootstrap.join('\n'));setPrivateAddress(value.allow_private);setEditing(!value.setup_id);}}}catch(err){if(live.current)setError(err instanceof Error?err.message:'host_unavailable');}finally{if(live.current)setBusy(false);}}
  useEffect(()=>{live.current=true;void read();return()=>{live.current=false;callback.current(null);};},[]);
  function revoke(){setAllowed(false);callback.current(null);}
  async function save(){revoke();setBusy(true);setError('');try{const value=await api<PeerState>('peer-setup',{bootstrap:contacts.split('\n').map(item=>item.trim()).filter(Boolean),allow_private:privateAddress,confirmed});if(live.current){setState(value);setEditing(false);setConfirmed(false);}}catch(err){if(live.current)setError(err instanceof Error?err.message:'host_unavailable');}finally{if(live.current)setBusy(false);}}
  const tone=!state?'muted':state.runtime==='available'?(state.setup_id?'ok':'info'):state.runtime==='missing'?'muted':'bad';
  const status=!state?'checking…':state.runtime==='available'?(state.setup_id?'ready':'needs contacts'):state.runtime==='missing'?'not installed':'incompatible';
  return <section className="sk-peer" data-compact={compact?'true':undefined} aria-label="Peer connections">
    <div className="sk-peer-head"><span className="sk-job-kind"><Icon name="peers"/></span>{heading?<h2>Peer connections</h2>:<p className="sk-peer-title">Peer connections</p>}<Pill tone={tone}>{status}</Pill></div>
    <p className="sk-hint">Core contacts peers to find or receive the selected content. Peers can see Core’s network address and requested content identifiers. This mode is not anonymous. Receiving starts no public sharing.</p>
    {state&&state.runtime!=='available'&&<Callout>{state.runtime==='missing'?'Kubo is not installed or has not been detected on this Core.':'This Core found an incompatible Kubo version.'} Use the reviewed setup below, or an existing compatible installation. Reading this screen starts no download or peer network.</Callout>}
    {state&&state.runtime!=='available'&&<RuntimeSetup level={heading?3:4} onReady={()=>void read(true)}/>}
    {(!state||state.runtime!=='available')&&<button className="sk-btn sk-quiet" disabled={busy} onClick={()=>void read(!!state)}><Icon name="refresh"/>{busy?'Checking locally…':'Check installed runtime again'}</button>}
    {state&&!editing&&<div className="sk-peer-saved"><p className="sk-meta"><span className="sk-num">{state.bootstrap.length}</span> starting {state.bootstrap.length===1?'contact':'contacts'} · {state.allow_private?'Local/private peer connections allowed':'Public addresses only'}{state.active?' · Another peer operation is active':''}</p><button className="sk-btn sk-quiet sk-sm" disabled={busy||state.active} onClick={()=>{revoke();setEditing(true);}}>Change peer contacts</button></div>}
    {state&&editing&&<div className="sk-peer-edit"><p className="sk-hint">Get starting contacts from a peer or mirror operator you trust to connect to. They help route requests; signed source metadata and model verification remain separate.</p><label className="sk-field"><span>Starting contacts (one per line, up to 8)</span><textarea className="sk-input sk-mono-input" rows={3} maxLength={4104} value={contacts} placeholder="/ip4/…/tcp/…/p2p/…" onChange={event=>{revoke();setContacts(event.target.value);}}/></label><Consent checked={privateAddress} onChange={value=>{revoke();setPrivateAddress(value);}}>Allow local or private peer addresses in a separate receive-only profile.</Consent><Consent checked={confirmed} onChange={setConfirmed}>I understand peers can see Core’s address and requested content identifiers.</Consent><div className="sk-actions"><button className="sk-btn sk-outline" disabled={busy||!confirmed||!contacts.trim()||state.active} onClick={()=>void save()}>{busy?'Saving…':'Save peer settings'}</button><span className="sk-hint">Saving stays offline. Network access needs separate consent below.</span></div></div>}
    {state?.setup_id&&state.runtime==='available'&&!editing&&<Consent checked={allowed} onChange={value=>{setAllowed(value);callback.current(value?{allow_peer_network:true,peer_setup_id:state.setup_id!}:null);}}>Allow peer connections for {purpose} when I start it.</Consent>}
    {error&&<Callout tone="bad" role="alert">{errorText(error)}</Callout>}
  </section>;
}
