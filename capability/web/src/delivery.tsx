import {useEffect,useRef,useState} from 'react';
import {api,bytes,errorText,manifestDownload,transferAllowance,uploadMetadata,type Copy,type InvitationPreview,type Job,type Location,type Plan,type PublicationMember,type PublicationSource} from './client';
import {PeerAccess,type PeerGrant} from './peer';
import {Callout,Consent,Details,Icon,Mono,Pill,Row,Sheet,Step} from './ui';

const split=(value:string)=>value.split(/[\n,]/).map(item=>item.trim()).filter(Boolean);
type Terms={member:PublicationMember;license:string;reference:string;decision:string;notice:string};

/** A private kept copy becomes public only after complete member/terms review. */
export function Publish({copy,publisher,onBack,onReview,onChange}:{copy:Copy;publisher:Location|null|undefined;onBack:()=>void;onReview:(plan:Plan)=>void;onChange:()=>Promise<void>}) {
  const [source,setSource]=useState<PublicationSource|null>(null),[identity,setIdentity]=useState(publisher);
  const [terms,setTerms]=useState<Terms[]>([]),[name,setName]=useState(''),[workloads,setWorkloads]=useState(''),[runtime,setRuntime]=useState('');
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const live=useRef(true);
  useEffect(()=>{live.current=true;void api<PublicationSource>('publication-source',{copy_id:copy.copy_id}).then(result=>{if(!live.current)return;setSource(result);setName(result.name);setWorkloads(result.workloads.join('\n'));setRuntime(result.runtime_requirements.join('\n'));setTerms(result.members.map(member=>({member,license:/^(unknown|unresolved|unresolved-owner-capture)$/i.test(member.license_id)?'':member.license_id,reference:member.terms_reference,decision:'',notice:''})));}).catch(err=>{if(live.current)setError(err instanceof Error?err.message:'host_unavailable');});return()=>{live.current=false;};},[copy.copy_id]);
  async function setup(){setBusy(true);setError('');try{const result=await api<Location>('publisher-setup',{});if(live.current)setIdentity(result);await onChange();}catch(err){if(live.current)setError(err instanceof Error?err.message:'host_unavailable');}finally{if(live.current)setBusy(false);}}
  function update(index:number,patch:Partial<Terms>){setTerms(current=>current.map((item,i)=>i===index?{...item,...patch}:item));}
  async function prepare(){if(!source||!identity)return;setBusy(true);setError('');try{const metadata={name:name.trim(),workloads:split(workloads),runtime_requirements:split(runtime),members:terms.map(item=>({member_id:item.member.member_id,license_id:item.license.trim(),terms_reference:item.reference.trim(),decision:item.decision.trim(),notice_paths:[...item.member.notice_paths,...(item.notice.trim()?['NOTICE.publisher.txt']:[])]})),notices:terms.filter(item=>item.notice.trim()).map(item=>({member_id:item.member.member_id,text:item.notice}))};const upload=await uploadMetadata(new Blob([JSON.stringify(metadata)],{type:'application/json'}),'publication');if(!live.current)return;const plan=await api<Plan>('delivery-plan',{kind:'publish',location_id:copy.location_id,copy_id:copy.copy_id,destination_id:identity.location_id,upload_id:upload.upload_id});if(live.current)onReview(plan);}catch(err){if(live.current)setError(err instanceof Error?err.message:'host_unavailable');}finally{if(live.current)setBusy(false);}}
  const incomplete=!name.trim()||split(workloads).length===0||terms.some(item=>!item.license.trim()||!item.reference.trim()||!item.decision.trim()||(!item.member.notice_paths.length&&!item.notice.trim()));
  return <Sheet wide kicker="Publish a preserved model" title="Prepare a public copy" subtitle={<>{copy.model_id} · <span className="sk-num">{bytes(copy.size_bytes)}</span></>} back="Back to Library" onClose={onBack}
    footer={source?<><span className="sk-foot-note">{!identity?'Set up your publishing identity first.':incomplete?'Complete every member’s terms to continue.':'Nothing is hosted until you start a sharing session.'}</span><button className="sk-btn sk-solid" disabled={busy||!identity||incomplete} onClick={()=>void prepare()}>{busy?'Preparing review…':'Review public copy'}</button></>:undefined}>
    <p className="sk-lede">Check each model and companion’s redistribution terms. Core then signs a separate public copy with a separate publishing identity. Publishing opens no listener.</p>
    <Step n={1} title="Publishing identity" aside={identity?<Pill tone="ok">ready</Pill>:undefined}>
      {identity===undefined?<Callout>Update Core to enable Library publishing.</Callout>:identity===null?<><p>A separate protected signing key identifies your public copies. Keep its fingerprint somewhere independent; it doesn’t establish registry trust.</p><button className="sk-btn sk-outline" disabled={busy} onClick={()=>void setup()}><Icon name="key"/>Set up publishing</button></>:<Details summary="Public publishing fingerprint"><Mono value={identity.publisher_key_id} label="publishing fingerprint"/></Details>}
    </Step>
    {!source&&!error&&<p className="sk-hint sk-pad">Reading this copy’s members…</p>}
    {source&&<>
      <Step n={2} title="Public details">
        <label className="sk-field"><span>Public name</span><input className="sk-input" maxLength={200} value={name} onChange={e=>setName(e.target.value)}/></label>
        <Details summary="Workloads and runtime requirements"><label className="sk-field"><span>Workloads (one per line)</span><textarea className="sk-input" value={workloads} onChange={e=>setWorkloads(e.target.value)}/></label><label className="sk-field"><span>Runtime requirements (one per line)</span><textarea className="sk-input" value={runtime} onChange={e=>setRuntime(e.target.value)}/></label></Details>
      </Step>
      <Step n={3} title="Terms for every member">
        {terms.map((item,index)=><fieldset className="sk-member" key={item.member.member_id}><legend><span className="sk-num">{item.member.member_id}</span> · {item.member.role}</legend>
          <p className="sk-meta">{item.member.repository} · {item.member.format} · <span className="sk-num">{bytes(item.member.size_bytes)}</span> · {item.member.file_count} files</p>
          <Details summary="Original revision"><Mono value={item.member.revision} label="revision"/></Details>
          <div className="sk-fields"><label className="sk-field"><span>License identifier</span><input className="sk-input" maxLength={200} value={item.license} onChange={e=>update(index,{license:e.target.value})}/></label><label className="sk-field"><span>Terms reference</span><input className="sk-input" maxLength={2048} value={item.reference} onChange={e=>update(index,{reference:e.target.value})}/></label></div>
          <label className="sk-field"><span>Why you may redistribute this member</span><textarea className="sk-input" maxLength={4096} value={item.decision} onChange={e=>update(index,{decision:e.target.value})}/></label>
          {item.member.notice_paths.length>0&&<p className="sk-hint">Original notices are kept: {item.member.notice_paths.join(', ')}</p>}
          <label className="sk-field"><span>{item.member.notice_paths.length?'Additional publisher-supplied notice (optional)':'Publisher-supplied license / notice text (required)'}</span><textarea className="sk-input" rows={5} maxLength={65536} value={item.notice} onChange={e=>update(index,{notice:e.target.value})}/></label>
          <p className="sk-fine"><Icon name="info" size={14}/><span>Added text is labeled publisher supplied. It preserves no missing upstream proof and grants no rights. Original required notices cannot be removed.</span></p>
        </fieldset>)}
      </Step>
    </>}
    {error&&<Callout tone="bad" role="alert">{errorText(error)}</Callout>}
  </Sheet>;
}

/** Signed metadata is inspected offline before trust selection or mirror contact. */
export function Receive({resume,onBack,onReview}:{resume?:Job;onBack:()=>void;onReview:(plan:Plan)=>void}) {
  const [preview,setPreview]=useState<InvitationPreview|null>(null),[upload,setUpload]=useState(''),[keys,setKeys]=useState<string[]>([]);
  const [peerGrant,setPeerGrant]=useState<PeerGrant|null>(null);
  const [identityChecked,setIdentityChecked]=useState(false),[thresholdChecked,setThresholdChecked]=useState(false);
  const [allowance,setAllowance]=useState(0),[hours,setHours]=useState(24),[http,setHttp]=useState(false),[privateAddress,setPrivateAddress]=useState(false);
  const [busy,setBusy]=useState(false),[error,setError]=useState('');const sequence=useRef(0);
  useEffect(()=>{const current=++sequence.current;if(resume)void api<InvitationPreview>('receive-preview',{job_id:resume.job_id}).then(value=>{if(current===sequence.current)setPreview(value);}).catch(err=>{if(current===sequence.current)setError(err instanceof Error?err.message:'host_unavailable');});return()=>{sequence.current++;};},[resume?.job_id]);
  async function inspect(file:File){const current=++sequence.current;setBusy(true);setPreview(null);setKeys([]);setIdentityChecked(false);setThresholdChecked(false);setError('');try{const uploaded=await uploadMetadata(file,'invitation');if(current!==sequence.current)return;const value=await api<InvitationPreview>('invitation-inspect',{upload_id:uploaded.upload_id});if(current!==sequence.current)return;setUpload(uploaded.upload_id);setPreview(value);setAllowance(transferAllowance(value.size_bytes));setHttp(false);setPrivateAddress(false);}catch(err){if(current===sequence.current)setError(err instanceof Error?err.message:'host_unavailable');}finally{if(current===sequence.current)setBusy(false);}}
  async function prepare(){const current=sequence.current;setBusy(true);setError('');try{const plan=await api<Plan>('delivery-plan',resume?{kind:'receive',location_id:'invitation',resume_job_id:resume.job_id,...(preview?.transport==='peer'?peerGrant:{})}:{kind:'receive',location_id:'invitation',upload_id:upload,accepted_key_ids:keys,download_limit_bytes:allowance,download_deadline_seconds:Math.round(hours*3600),...(preview?.transport==='peer'?peerGrant:{allow_http:http,allow_private:privateAddress})});if(current===sequence.current)onReview(plan);}catch(err){if(current===sequence.current)setError(err instanceof Error?err.message:'host_unavailable');}finally{if(current===sequence.current)setBusy(false);}}
  const blocked=!preview||busy||(preview.transport==='peer'&&!peerGrant)||(!resume&&(!identityChecked||!thresholdChecked||keys.length<preview.threshold||!Number.isSafeInteger(allowance)||allowance<preview.size_bytes||!Number.isFinite(hours)||hours<=0||hours>24));
  return <Sheet kicker={resume?'Resume the same download':'Receive a community copy'} title={resume?'Review this download again':'Receive a shared model'} back="Back to Sharing" onClose={onBack}
    footer={preview?<><span className="sk-foot-note">{blocked&&!busy?'Confirm each check above to continue.':'Next you’ll review the exact download.'}</span><button className="sk-btn sk-solid" disabled={blocked} onClick={()=>void prepare()}>{busy?'Preparing review…':resume?'Review resume':'Review download'}</button></>:undefined}>
    <p className="sk-lede">{resume?'Core keeps the original invitation, partial files, trust and remaining allowance. A resume never refills that allowance.':'Choose the small invitation file your sender attached. Core checks its signed contents offline before contacting any mirror.'}</p>
    {!resume&&<label className="sk-drop sk-drop-sm" data-busy={busy?'true':undefined}><span className="sk-drop-icon"><Icon name="file" size={20}/></span><span className="sk-drop-title">{busy?'Inspecting…':'Choose invitation'}</span><span className="sk-drop-note">A .foxhandoff, .json or .foxpeer-model file, checked offline.</span><input aria-label="Invitation file" type="file" accept=".json,.foxpeer-model,.foxhandoff,application/json" disabled={busy} onChange={e=>{const file=e.target.files?.[0];if(file)void inspect(file);e.target.value='';}}/></label>}
    {preview&&<>
      <Step n={1} title={preview.name} aside={<Pill tone="info">signatures checked</Pill>}>
        <div className="sk-summary"><Row k="Size"><span className="sk-num">{bytes(preview.size_bytes)}</span></Row><Row k="Members"><span className="sk-num">{preview.member_count}</span></Row><Row k="Bundle"><Mono value={preview.manifest_id} label="manifest ID"/></Row></div>
        <Details summary="All included members"><ul className="sk-members">{preview.members.map((member,i)=><li key={i}><Icon name="check" size={14}/><span>{member}</span></li>)}</ul></Details>
        <p className="sk-sublabel">Mirrors named by the sender</p><div className="sk-keys">{preview.mirrors.map(mirror=><Mono key={mirror} value={mirror} label="mirror address"/>)}</div>
        <p className="sk-fine"><Icon name="globe" size={14}/><span>These addresses haven’t been contacted. When downloading, suppliers can see this host’s network address and what it requests; it isn’t anonymous.</span></p>
        <Details summary="Details"><p>These addresses have not been contacted. Suppliers can see Core’s network address and requested content. This download is not anonymous. Peer routing and cache overhead are outside the model object-byte allowance.</p></Details>
      </Step>
      <Step n={2} title="Authenticate the sender">
        <p>Check the exact bundle identity, key fingerprints and signature threshold with an independent source, such as a call or a trusted message. A file and the keys bundled with it can be replaced together, and these signatures don’t grant registry trust.</p>
        <div className="sk-summary"><Row k="Required signatures"><span className="sk-num">{preview.threshold}</span></Row></div>
        {!resume&&<><Consent checked={identityChecked} onChange={setIdentityChecked}>I independently authenticated the exact bundle identity shown above.</Consent><Consent checked={thresholdChecked} onChange={setThresholdChecked}>I independently authenticated the required signature threshold: {preview.threshold}.</Consent></>}
        {preview.publisher_key_ids.map(key=>resume?<div className="sk-keys" key={key}><Mono value={key} label="fingerprint"/></div>:<label className="sk-consent" key={key}><input type="checkbox" checked={keys.includes(key)} onChange={e=>setKeys(current=>e.target.checked?[...current,key]:current.filter(value=>value!==key))}/><span>I independently checked this fingerprint:<code className="sk-key">{key}</code></span></label>)}
        {resume&&<p className="sk-hint">The original accepted keys and policy remain bound to this job. Review them on the next screen.</p>}
      </Step>
      {!resume&&<Step n={3} title="Download limits">
        <p>Up to <span className="sk-num">{bytes(allowance)}</span> of conservative object-byte charges, including failed attempts, and up to <span className="sk-num">{hours}</span> hours for this attempt. The default leaves room for retries.</p>
        <Details summary="Adjust limits and network permissions"><div className="sk-fields"><label className="sk-field"><span>Download allowance (bytes)</span><input className="sk-input" type="number" min={1} max={Number.MAX_SAFE_INTEGER} step={1} value={allowance} onChange={e=>setAllowance(Number(e.target.value))}/></label><label className="sk-field"><span>Attempt deadline (hours, up to 24)</span><input className="sk-input" type="number" min={1/3600} max={24} step="any" value={hours} onChange={e=>setHours(Number(e.target.value))}/></label></div>{preview.transport!=='peer'&&<><Consent checked={http} onChange={setHttp}>Allow plaintext HTTP mirrors.</Consent><Consent checked={privateAddress} onChange={setPrivateAddress}>Allow mirrors on local or private network addresses.</Consent></>}</Details>
      </Step>}
      {preview.transport==='peer'&&<Step n={resume?3:4} title="Peer connections"><PeerAccess heading={false} contactHints={preview.contacts} onChange={setPeerGrant} purpose={resume?'resuming this download':'this model download'}/></Step>}
    </>}
    {error&&<Callout tone="bad" role="alert">{errorText(error)}</Callout>}
  </Sheet>;
}

/** Final fresh consent binds an immutable public publication or receiving plan. */
export function DeliveryReview({plan,busy,onBack,onStart}:{plan:Plan;busy:boolean;onBack:()=>void;onStart:(accept:boolean,participate:boolean)=>void}) {
  const [accept,setAccept]=useState(false);const publishing=plan.kind==='publish';const policy=plan.receiving;
  const expired=plan.expires_at*1000<Date.now();
  return <Sheet kicker={publishing?'Public publication':'Receive only'} title={publishing?'Sign and keep this public copy':plan.resume_job_id?'Resume this exact download':'Download this signed copy'} subtitle={<>{plan.model_id} · <span className="sk-num">{bytes(plan.size_bytes)}</span> · {plan.member_count} members</>} back={`Back to ${publishing?'Library':'Sharing'}`} onClose={onBack}
    footer={<><span className="sk-foot-note">{expired?'This review expired. Close it and review again.':accept?'You can leave while Skulk works.':'Confirm above to continue.'}</span><button className="sk-btn sk-solid" disabled={busy||!accept||expired} onClick={()=>onStart(!publishing&&accept,publishing&&accept)}>{busy?'Starting…':publishing?'Create signed public copy':plan.resume_job_id?'Resume download':'Download model'}</button></>}>
    <Step n={1} title="The exact copy">
      <ul className="sk-members">{plan.members.map((member,i)=><li key={i}><Icon name="check" size={14}/><span>{member}</span></li>)}</ul>
      {plan.member_count>plan.members.length&&<p className="sk-hint">All {plan.member_count} signed members shown in the invitation are included.</p>}
      <div className="sk-summary">{plan.manifest_id&&<Row k="Bundle"><Mono value={plan.manifest_id} label="manifest ID"/></Row>}{plan.publisher_key_ids?.map(key=><Row key={key} k={publishing?'Public publishing fingerprint':'Accepted fingerprint'}><Mono value={key} label="fingerprint"/></Row>)}</div>
      {publishing&&<a className="sk-btn sk-outline" href={manifestDownload(plan)} download={`${plan.manifest_id}.public-manifest.json`}><Icon name="file"/>Inspect full proposed public manifest</a>}
    </Step>
    {policy?<Step n={2} title="Exact download policy">
      <div className="sk-keys">{policy.mirrors.map(mirror=><Mono key={mirror} value={mirror} label="mirror address"/>)}</div>
      <div className="sk-summary"><Row k="Allowance"><span><span className="sk-num">{bytes(policy.max_bytes)}</span> cumulative object bytes</span></Row><Row k="Deadline"><span><span className="sk-num">{policy.deadline_seconds}</span> seconds per attempt</span></Row></div>
      {policy.transport==='peer'?<p>Peer receive only · {policy.peer_private?'Local/private peer addresses allowed':'Public peer addresses only'}. Routing and cache overhead are outside the object-byte allowance.</p>:<p>Plaintext HTTP: {policy.allow_http?'allowed':'blocked'} · Local/private addresses: {policy.allow_private?'allowed':'blocked'}.</p>}
      {plan.resume_job_id&&<p className="sk-hint">Same job and retained partial files. Earlier charges remain deducted from this allowance.</p>}
    </Step>:null}
    <Step n={policy?3:2} title={publishing?'What happens':'Your confirmation'}>
      <p>{publishing?'Core verifies all source files, signs a separate public generation and keeps it locally. Your private capture remains intact. No mirror or peer session starts.':'Core verifies the complete model, its companions and provenance into a public copy. It won’t install the model, run its code or start sharing.'}</p>
      {!publishing&&<p className="sk-fine"><Icon name="globe" size={14}/><span>Suppliers can see this host’s network address and what it requests.</span></p>}
      {!publishing&&<Details summary="Details"><p>Core verifies the complete model, companions and provenance into a public copy. It does not install the model, run repository code or start sharing. You can save a recovery kit or review a separate restore afterward. Suppliers can see Core’s network address and requested content.</p></Details>}
      <Consent checked={accept} onChange={setAccept}>{publishing?'I reviewed all member terms and the proposed public contents, and have permission to redistribute this exact public copy.':'I accept this exact signed copy, independently authenticated keys, provenance limits and download policy.'}</Consent>
    </Step>
  </Sheet>;
}
