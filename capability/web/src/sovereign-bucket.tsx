import {useState} from 'react';
import {createPortal} from 'react-dom';
import {api,errorText} from './client';
import {Callout,Row,Sheet,Step} from './ui';

type Field='account_id'|'region'|'bucket'|'endpoint'|'public_url';
export interface BucketProvider {id:string;name:string;asks:Field[];note:string}
export interface BucketSummary {provider:string;provider_name:string;bucket:string;public_url:string;model_count:number;last_synced:number|null;last_error:string|null}
type Connected<T>=T&{status:'ready'|'upload'|'public'|'disconnected'};

const fieldLabel:Record<Field,[string,string]>={
 account_id:['Account ID','The 32-character ID on your Cloudflare dashboard'],
 region:['Region','For example us-west-004'],
 bucket:['Bucket name','For example my-models'],
 endpoint:['Endpoint','https://storage.example.com'],
 public_url:['Public address','https://pub-….r2.dev'],
};
const bucketError=(code:string)=>({bucket_details_incomplete:'Fill in every field for this provider.',bucket_account_id_invalid:'That account ID doesn’t look right. It’s 32 letters and numbers, shown on your Cloudflare dashboard.',bucket_credentials_invalid:'Paste the access key and secret exactly as the provider showed them.',bucket_details_invalid:'Check the bucket name and addresses. Bucket names use lowercase letters, numbers and dashes, and addresses start with https://.',bucket_keychain_unavailable:'This computer’s keychain couldn’t store the key. Unlock it and try again.'} as Record<string,string>)[code]??errorText(code);

/** Keep a copy of shared models in the owner's own cloud bucket, reachable from anywhere. */
export function CloudStorage<T extends {bucket:BucketSummary|null;bucket_providers:BucketProvider[]}>({state,behindRouter,onState}:{state:T;behindRouter:boolean;onState:(value:T)=>void}) {
 const [open,setOpen]=useState(false),[provider,setProvider]=useState('r2'),[values,setValues]=useState<Record<string,string>>({});
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[result,setResult]=useState<'upload'|'public'|null>(null);
 const chosen=state.bucket_providers.find(item=>item.id===provider)??state.bucket_providers[0];
 const bucket=state.bucket;
 function start(){setProvider('r2');setValues({});setError('');setResult(null);setOpen(true);}
 async function send(action:string,body:Record<string,unknown>){setBusy(true);setError('');setResult(null);try{const value=await api<Connected<T>>(action,body);onState(value);if(value.status==='upload'||value.status==='public')setResult(value.status);else setOpen(false);}catch(err){setError(err instanceof Error?err.message:'host_unavailable');}finally{setBusy(false);}}
 function connect(){if(!chosen)return;const body:Record<string,string>={provider:chosen.id,access_key:values.access_key??'',secret_key:values.secret_key??''};for(const name of chosen.asks)body[name]=values[name]??'';void send('desktop-community-bucket-connect',body);}
 const complete=!!chosen&&[...chosen.asks,'access_key','secret_key'].every(name=>(values[name]??'').trim());
 const set=(name:string)=>(event:{target:{value:string}})=>setValues(current=>({...current,[name]:event.target.value}));
 return <div className="sk-community-bucket">
  <h3>Cloud storage</h3>
  {bucket?<>
   <Row k="Keeping a copy in">{bucket.provider_name} · {bucket.bucket}</Row>
   <Row k="People download from"><code>{bucket.public_url}</code></Row>
   <Row k="Models in the bucket">{bucket.model_count}{bucket.last_synced?` · checked ${new Date(bucket.last_synced*1000).toLocaleString()}`:' · uploading…'}</Row>
   {bucket.last_error&&<Callout tone="warn">The last upload didn’t finish ({errorText(bucket.last_error)}). It tries again on its own.</Callout>}
   <p>Disconnecting forgets the keys on this computer. Whatever is already in the bucket stays there; turn sharing off first if you want Sovereign to withdraw the models.</p>
   {error&&!open&&<Callout tone="bad" role="alert">{bucketError(error)}</Callout>}
   <div className="sk-actions"><button className="sk-btn sk-outline" disabled={busy} onClick={()=>void send('desktop-community-bucket-disconnect',{})}>Disconnect</button></div>
  </>:<>
   <p>{behindRouter?'People outside your network can’t reach this computer. ':''}Keep a copy in your own cloud bucket and people anywhere can download from it, even while this computer is off. Sovereign uploads what you share and keeps it up to date.</p>
   <div className="sk-actions"><button className="sk-btn sk-outline" onClick={start}>Set up cloud storage</button></div>
  </>}
  {open&&chosen&&createPortal(<Sheet kicker="Help keep models available" title="Keep a copy in cloud storage" back="Close cloud storage setup" onClose={()=>setOpen(false)} footer={<button className="sk-btn sk-solid" disabled={busy||!complete} onClick={connect}>{busy?'Checking…':'Check and connect'}</button>}>
   {error&&<Callout tone="bad" role="alert">{bucketError(error)}</Callout>}
   {result==='upload'&&<Callout tone="bad" role="alert">The upload didn’t work. Check the access key, the secret and the bucket name, and that the key is allowed to write to this bucket.</Callout>}
   {result==='public'&&<Callout tone="bad" role="alert">Uploading worked, but nobody could download from the public address. Turn on public access for the bucket and check the address you pasted.</Callout>}
   <Step n={1} title="Where to keep it">
    <div role="radiogroup" aria-label="Storage provider">{state.bucket_providers.map(item=><label key={item.id} className="sk-radio"><input type="radio" name="bucket-provider" checked={item.id===chosen.id} onChange={()=>{setProvider(item.id);setResult(null);}}/> <span><strong>{item.name}</strong>{item.id==='r2'&&' · recommended'}</span></label>)}</div>
   </Step>
   <Step n={2} title={`Your ${chosen.name} bucket`}>
    <p>{chosen.note}</p>
    {chosen.asks.map(name=><label key={name} className="sk-field">{fieldLabel[name][0]}<input className="sk-input" value={values[name]??''} placeholder={fieldLabel[name][1]} autoComplete="off" spellCheck={false} onChange={set(name)}/></label>)}
   </Step>
   <Step n={3} title="A key that can write to it">
    <p>Create an access key limited to this one bucket. Sovereign keeps it in this computer’s keychain and sends it only to {chosen.name}.</p>
    <label className="sk-field">Access key ID<input className="sk-input" value={values.access_key??''} autoComplete="off" spellCheck={false} onChange={set('access_key')}/></label>
    <label className="sk-field">Secret access key<input className="sk-input" type="password" value={values.secret_key??''} autoComplete="new-password" spellCheck={false} onChange={set('secret_key')}/></label>
    <p>Before saving anything, Sovereign uploads a small test file, downloads it from the public address and removes it, so you know it works.</p>
   </Step>
  </Sheet>,document.body)}
 </div>;
}
