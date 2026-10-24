import {useEffect,useState} from 'react';
import {api,type I2PProxy,type ModelSource} from './client';
import {Callout,Consent,Mono} from './ui';

export const DEFAULT_I2P_PROXY:I2PProxy={host:'127.0.0.1',port:4444};

/** Select one route offline; selecting it alone never grants a connection. */
export function I2PChoice({selected,onChange,label='Download connection'}:{selected:boolean;onChange:(value:boolean)=>void;label?:string}){return <label className="sk-field"><span>{label}</span><select className="sk-input" value={selected?'i2p':'http'} onChange={event=>onChange(event.target.value==='i2p')}><option value="http">Direct HTTP(S)</option><option value="i2p">I2P only</option></select></label>;}

/** Capability comes from actual request/Resume schemas, never a version guess. */
export function useI2PAvailability():boolean {
  const [available,setAvailable]=useState(false);
  useEffect(()=>{let live=true;void api<Record<string,boolean>>('core-features',{}).then(value=>{if(live)setAvailable(value['I2P preservation jobs']===true);}).catch(()=>{if(live)setAvailable(false);});return()=>{live=false;};},[]);
  return available;
}

/** Offline UI validation only; Core remains authoritative before any request. */
export function i2pEndpoint(value:string):boolean {
  if(value.length>2048||!/^http:\/\/[a-z2-7]{51}[aq]\.b32\.i2p(?::[1-9][0-9]{0,4})?(?:\/[A-Za-z0-9._~/-]*)?$/.test(value))return false;
  try{new URL(value);const path=value.match(/^http:\/\/[^/]+(\/.*)?$/)?.[1]??'';return !path.includes('//')&&!path.split('/').some(part=>part==='.'||part==='..');}catch{return false;}
}

/** The saved source route cannot be silently replaced by direct/peer routing. */
export function i2pSourceGrant(source:ModelSource|undefined):{transport:'i2p';allow_i2p:true;i2p_policy_id:string} {
  if(source?.transport!=='i2p'||!source.i2p_policy_id||!source.i2p_proxy)throw new Error('source_i2p_policy_changed');
  return {transport:'i2p',allow_i2p:true,i2p_policy_id:source.i2p_policy_id};
}

/** Finite selected requests only; this text promises no anonymity or router trust. */
export function I2PDisclosure(){return <><p className="sk-hint">Only this selected operation uses the I2P HTTP proxy. There is no direct fallback. Other Core traffic keeps its own settings. Mirrors still see requested content and timing and may recognize the I2P client destination.</p><Callout>Pilot: network privacy and sustained performance are not yet qualified. Use an I2P proxy you trust on the Core host. This screen does not install or authenticate a router.</Callout></>;}

/** A local proxy choice and fresh confirmation grant neither model trust nor uploads. */
export function I2PAccess({proxy,onProxy,confirmed,onConfirm,locked=false,purpose}:{proxy:I2PProxy;onProxy:(value:I2PProxy)=>void;confirmed:boolean;onConfirm:(value:boolean)=>void;locked?:boolean;purpose:string}) {
  const valid=Number.isSafeInteger(proxy.port)&&proxy.port>=1&&proxy.port<=65535;
  return <section aria-label="I2P connections">
    <I2PDisclosure/>
    {locked?<p>Original proxy on the Core host: <Mono value={`${proxy.host==='::1'?'[::1]':proxy.host}:${proxy.port}`} label="saved I2P proxy"/>. Resume keeps this proxy and the original remaining allowance.</p>:<div className="sk-fields"><label className="sk-field"><span>I2P proxy address on Core</span><select className="sk-input" value={proxy.host} onChange={event=>{onConfirm(false);onProxy({...proxy,host:event.target.value as I2PProxy['host']});}}><option value="127.0.0.1">127.0.0.1</option><option value="::1">::1</option></select></label><label className="sk-field"><span>I2P proxy port</span><input className="sk-input" type="number" min={1} max={65535} step={1} value={Number.isNaN(proxy.port)?'':proxy.port} onChange={event=>{onConfirm(false);onProxy({...proxy,port:Number(event.target.value)});}}/></label></div>}
    {!valid&&<p role="alert">Choose a proxy port from 1 to 65535.</p>}
    <Consent checked={confirmed&&valid} onChange={value=>onConfirm(value&&valid)}>Allow I2P connections through this proxy for {purpose}. This starts no payload sharing.</Consent>
  </section>;
}
