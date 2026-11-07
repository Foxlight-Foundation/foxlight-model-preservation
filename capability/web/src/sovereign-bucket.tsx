import {useEffect,useState} from 'react';
import {createPortal} from 'react-dom';
import {api,bytes,errorText} from './client';
import {Callout,Mono,Row,Sheet,Step,ago} from './ui';

type Field='account_id'|'jurisdiction'|'region'|'bucket'|'endpoint'|'public_url';
export interface BucketProvider {id:string;name:string;asks:Field[];optional?:Field[];choices?:Partial<Record<Field,{value:string;label:string}[]>>;note:string}
export interface BucketSummary {provider:string;provider_name:string;jurisdiction?:string|null;bucket:string;public_url:string;model_count:number;withdraw_count?:number;last_synced:number|null;last_error:string|null}
/** One model the bucket serves, from the app's own upload records. */
export interface BucketModel {manifest_id:string;name:string|null;size_bytes:number|null;synced_at:number|null;keys:string[]}
interface BucketContents {models:BucketModel[];total:number;offset:number;public_url:string|null}
type Connected<T>=T&{status:'ready'|'upload'|'public'|'disconnected'};

const fieldLabel:Record<Field,[string,string]>={
 account_id:['Account ID','The 32-character ID on your Cloudflare dashboard'],
 jurisdiction:['Jurisdiction',''],
 region:['Region','For example us-west-004'],
 bucket:['Bucket name','For example my-models'],
 endpoint:['Endpoint','https://storage.example.com'],
 public_url:['Public address','https://pub-….r2.dev'],
};
// Region formats differ by provider; show an example in that provider's form.
const regionExample:Record<string,string>={b2:'For example us-west-004',s3:'For example us-east-1',wasabi:'For example us-east-1',hetzner:'fsn1, nbg1 or hel1',other:'For example us-east-1'};
// A CDN in front of a private S3 bucket is the usual public address there.
const placeholder=(provider:string,name:Field)=>name==='region'?(regionExample[provider]??fieldLabel.region[1]):provider==='s3'&&name==='public_url'?'https://d1234abcd.cloudfront.net (optional)':fieldLabel[name][1];
const jurisdictionLabel:Record<string,string>={eu:'EU',fedramp:'FedRAMP',us:'US'};
const bucketError=(code:string)=>({bucket_details_incomplete:'Fill in every required field for this provider.',bucket_public_url_invalid:'The public address must start with https:// and contain no user name, password or ? part.',bucket_model_unavailable:'That model is no longer in the bucket. Refresh and try again.',bucket_handoff_unavailable:'Sovereign couldn’t prepare the file for this model. Its signed details are too large for one file or no longer readable; a recovery kit still works.',bucket_account_id_invalid:'That account ID doesn’t look right. It’s 32 letters and numbers, shown on your Cloudflare dashboard.',bucket_credentials_invalid:'Paste the access key and secret exactly as the provider showed them.',bucket_details_invalid:'Check the bucket name and addresses. Bucket names use lowercase letters, numbers and dashes, and addresses start with https://.',bucket_keychain_unavailable:'This computer’s keychain couldn’t store the key. Unlock it and try again.'} as Record<string,string>)[code]??errorText(code);

/** Keep a copy of shared models in the owner's own cloud bucket, reachable from anywhere. */
export function CloudStorage<T extends {bucket:BucketSummary|null;bucket_providers:BucketProvider[]}>({state,sharing,behindRouter,onState}:{state:T;sharing:boolean;behindRouter:boolean;onState:(value:T)=>void}) {
 const [open,setOpen]=useState(false),[provider,setProvider]=useState('r2'),[values,setValues]=useState<Record<string,string>>({});
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[result,setResult]=useState<'upload'|'public'|null>(null);
 const chosen=state.bucket_providers.find(item=>item.id===provider)??state.bucket_providers[0];
 const bucket=state.bucket;
 function start(){setProvider('r2');setValues({});setError('');setResult(null);setOpen(true);}
 async function send(action:string,body:Record<string,unknown>){setBusy(true);setError('');setResult(null);try{const value=await api<Connected<T>>(action,body);onState(value);if(value.status==='upload'||value.status==='public')setResult(value.status);else setOpen(false);}catch(err){setError(err instanceof Error?err.message:'host_unavailable');}finally{setBusy(false);}}
 // An optional field left empty is not sent, so the provider's default applies.
 const optional=(name:Field)=>!!chosen?.optional?.includes(name);
 function connect(){if(!chosen)return;const body:Record<string,string>={provider:chosen.id,access_key:values.access_key??'',secret_key:values.secret_key??''};for(const name of chosen.asks){const value=values[name]??'';if(value.trim()||!optional(name))body[name]=value;}void send('desktop-community-bucket-connect',body);}
 const complete=!!chosen&&[...chosen.asks.filter(name=>!optional(name)),'access_key','secret_key'].every(name=>(values[name]??'').trim());
 const set=(name:string)=>(event:{target:{value:string}})=>setValues(current=>({...current,[name]:event.target.value}));
 return <div className="sk-community-bucket">
  <h3>Cloud storage</h3>
  {bucket?<>
   <Row k="Keeping a copy in">{bucket.provider_name}{bucket.jurisdiction?` (${jurisdictionLabel[bucket.jurisdiction]??bucket.jurisdiction} jurisdiction)`:''} · {bucket.bucket}</Row>
   <Row k="People download from"><code>{bucket.public_url}</code></Row>
   <Row k="Models in the bucket">{bucket.model_count}{bucket.last_synced?` · checked ${new Date(bucket.last_synced*1000).toLocaleString()}`:sharing?' · uploading…':''}</Row>
   {!sharing&&((bucket.withdraw_count??bucket.model_count)>0
    ?<Callout tone={bucket.last_error?'warn':'info'}>Sharing is off, so Sovereign is taking your models out of the bucket.{bucket.last_error?` The bucket refused the last attempt (${errorText(bucket.last_error)}); it keeps trying.`:''}</Callout>
    :<Callout tone="ok">Sharing is off and your models are out of the bucket. You can disconnect it now.</Callout>)}
   {sharing&&(bucket.last_error==='bucket_upload_limit_reached'
    ?<Callout>Your upload limit is used up for now, so the rest uploads when it renews. Cloud uploads count toward the same daily and monthly limits as sharing from this computer.</Callout>
    :bucket.last_error&&<Callout tone="warn">The last upload didn’t finish ({errorText(bucket.last_error)}). It tries again on its own.</Callout>)}
   {bucket.model_count>0&&<BucketContentsList bucket={bucket}/>}
   <p>Disconnecting forgets the keys on this computer. Whatever is already in the bucket stays there{sharing?'; turn sharing off first if you want Sovereign to withdraw the models':''}.</p>
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
    {chosen.asks.map(name=>{const options=chosen.choices?.[name];const label=<>{fieldLabel[name][0]}{optional(name)&&<span className="sk-optional"> (optional)</span>}</>;
     return options?<label key={name} className="sk-field"><span>{label}</span><select className="sk-input" value={values[name]??''} onChange={set(name)}>{options.map(option=><option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
      :<label key={name} className="sk-field"><span>{label}</span><input className="sk-input" value={values[name]??''} placeholder={placeholder(chosen.id,name)} autoComplete="off" spellCheck={false} onChange={set(name)}/></label>;})}
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

const handoffHref=(id:string)=>new URL(`api/desktop-bucket-handoff/${id}`,new URL('.',location.href)).href;

/** What the bucket serves now, read from the app's own records (never a remote listing). */
function BucketContentsList({bucket}:{bucket:BucketSummary}) {
 const [contents,setContents]=useState<BucketContents|null>(null),[error,setError]=useState(''),[exporting,setExporting]=useState(false);
 // Re-read when the bucket changes: a model uploaded, withdrawn or re-checked.
 useEffect(()=>{let live=true;setError('');void api<BucketContents>('desktop-community-bucket-contents',{offset:0}).then(value=>{if(live)setContents(value);}).catch(err=>{if(live)setError(err instanceof Error?err.message:'host_unavailable');});return()=>{live=false;};},[bucket.model_count,bucket.last_synced,bucket.public_url]);
 async function more(){if(!contents)return;try{const next=await api<BucketContents>('desktop-community-bucket-contents',{offset:contents.models.length});setContents({...next,models:[...contents.models,...next.models]});}catch(err){setError(err instanceof Error?err.message:'host_unavailable');}}
 const models=contents?.models??[];
 const keys=[...new Set(models.flatMap(item=>item.keys))];
 return <div className="sk-bucket-contents">
  <h4>In the bucket now</h4>
  {error&&<Callout tone="warn">{bucketError(error)}</Callout>}
  {contents&&<ul className="sk-list" aria-label="Models in the bucket">{models.map(item=><li key={item.manifest_id} className="sk-bucket-model">
   <div className="sk-bucket-model-head"><strong>{item.name??'Unnamed model'}</strong><span className="sk-meta">{item.size_bytes!==null&&<span className="sk-num">{bytes(item.size_bytes)}</span>}{item.synced_at!==null?<span title={new Date(item.synced_at*1000).toLocaleString()}> · uploaded {ago(item.synced_at)}</span>:bucket.last_synced?<span title={new Date(bucket.last_synced*1000).toLocaleString()}> · checked {ago(bucket.last_synced)}</span>:null}</span></div>
   <Mono value={item.manifest_id} label={`manifest ID of ${item.name??'this model'}`}/>
  </li>)}</ul>}
  {contents&&contents.total>models.length&&<button className="sk-btn sk-quiet" onClick={()=>void more()}>Show more ({contents.total-models.length} left)</button>}
  <div className="sk-actions"><button className="sk-btn sk-outline" disabled={!contents||models.length===0} onClick={()=>setExporting(true)}>Export verification info</button></div>
  {exporting&&contents&&createPortal(<Sheet kicker="Cloud storage" title="Verification info for recipients" back="Close verification info" onClose={()=>setExporting(false)}>
   <p>Recipients need three things to download from this bucket and check every byte: its public address, the exact manifest ID, and the public key that signed the model. Each model’s sharing file below carries all three, together with the signed manifest. It holds no bucket keys and nothing private.</p>
   <Step n={1} title="Save a sharing file for each model">
    <Row k="Public address"><Mono value={contents.public_url??bucket.public_url} label="bucket public address"/></Row>
    <ul className="sk-list" aria-label="Sharing files">{models.map(item=><li key={item.manifest_id} className="sk-list-row"><span><strong>{item.name??'Unnamed model'}</strong><br/><code className="sk-meta">{item.manifest_id.slice(0,16)}…</code></span><a className="sk-btn sk-outline" href={handoffHref(item.manifest_id)} download={`${(item.name??'model').replace(/[^A-Za-z0-9._-]+/g,'-').slice(0,60)}.foxhandoff`} aria-label={`Save sharing file for ${item.name??item.manifest_id}`}>Save sharing file</a></li>)}</ul>
   </Step>
   <Step n={2} title="Confirm the signing key separately">
    <p>A file alone can’t prove who sent it. Tell recipients this verification key through a channel they already trust, such as a call or your website, so they can compare it before accepting.</p>
    {keys.map(key=><Mono key={key} value={key} label="signing key"/>)}
   </Step>
   <Step n={3} title="What recipients do">
    <p>In Sovereign: <strong>Find → Independent sources → Open sharing file</strong>, compare the key, then download. With the command-line tools: <code>model-preserve handoff-inspect --handoff FILE</code>, then <code>handoff-fetch --handoff FILE --store DIR --accept-key KEY</code>.</p>
   </Step>
  </Sheet>,document.body)}
 </div>;
}
