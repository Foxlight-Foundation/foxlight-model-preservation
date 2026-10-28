import {useEffect,useState} from 'react';
import {api} from './client';
import {Callout,CopyButton,Details,Icon,Mono,Sheet,Step} from './ui';

interface RecoveryInfo {name:string;manifest_id:string;threshold:number;keys:string[];revoked_keys:string[]}

/** First-launch choices are navigation only; only Browse requests public metadata. */
export function NewcomerChoices({busy,onBrowse,onRecover,onReceive}:{busy:boolean;onBrowse:()=>void;onRecover:()=>void;onReceive:()=>void}) {
  return <section className="sk-start" aria-label="Start with Sovereign">
    <div className="sk-start-intro"><h2>What would you like to do?</h2><p>Keep a complete model, including its companion files and original documents. No account or Skulk needed.</p></div>
    <div className="sk-start-options">
      <article className="sk-card"><Icon name="search" size={24}/><h3>Find something useful</h3><p>Browse descriptions for writing, images, voice and more. Choose a model before any model files download.</p><button className="sk-btn sk-solid" disabled={busy} onClick={onBrowse}>Browse public models</button><p className="sk-hint">No account or source-file setup. This contacts Foxlight for signed listings only.</p></article>
      <article className="sk-card"><Icon name="restore" size={24}/><h3>Recover a saved model</h3><p>Have a .foxkit file? Open it to review the model, companions and sender’s verification key. You can recover offline.</p><button className="sk-btn sk-outline" disabled={busy} onClick={onRecover}>Choose a recovery kit</button></article>
      <article className="sk-card"><Icon name="send" size={24}/><h3>Receive from someone</h3><p>Have a .foxhandoff sharing file? Review the sender and connection before downloading. Opening it starts no connection.</p><button className="sk-btn sk-outline" disabled={busy} onClick={onReceive}>Choose a sharing file</button></article>
    </div>
    <p className="sk-hint">Sovereign keeps and restores model files. To run a model, you’ll also need a compatible model application. Receiving never turns on sharing.</p>
  </section>;
}

/** A reusable plain-language path; no step grants trust or starts work. */
export function JourneyGuide({onClose}:{onClose:()=>void}) {
  return <Sheet kicker="Getting started" title="Your model, kept safe" back="Close getting started" onClose={onClose}>
    <Step n={1} title="Find a useful model"><p>In Find, search for what you want to do. Check what the model works with and produces. Choose a file version supported by the application you plan to use; download size is not the memory needed to run it.</p></Step>
    <Step n={2} title="Keep the complete copy"><p>Choose Keep a copy, review the files, companions, storage and source, then confirm. Activity shows the work. A completed copy appears in Library; you can search it offline.</p></Step>
    <Step n={3} title="Protect it beyond this computer"><p>In Library, Save recovery kit creates one .foxkit containing the complete archive, original model cards and signed evidence. Save it on another drive, or use Back up to save to a remembered folder. A copy on the same computer does not protect against losing it.</p></Step>
    <Step n={4} title="Share deliberately"><p>Share offers two paths: send a complete recovery kit using a service or drive you choose, or review a separate public mirror. Kits may contain private capture details. Neither path starts a mirror without its own explicit choice.</p></Step>
    <Step n={5} title="Recover on a new computer"><p>Install Sovereign and open the kit. Compare its verification key with the sender through a channel you already trust, then explicitly accept this exact capture. After import, choose Restore files in Library. The sender and original registry can be offline.</p><p>Restore creates a new folder with the captured files. It does not install a model application or run model code.</p></Step>
    <Callout>Software signing, model verification and permission to run code are separate. A matching capture key identifies a particular signed copy; it does not establish upstream authorship or registry approval.</Callout>
  </Sheet>;
}

/** Exact public verification information retained with a saved copy; never private keys. */
export function RecoveryGuide({copyId,onClose}:{copyId:string;onClose:()=>void}) {
  const [info,setInfo]=useState<RecoveryInfo|null>(null),[failed,setFailed]=useState(false);
  useEffect(()=>{let active=true;void api<RecoveryInfo>('desktop-copy-recovery-info',{copy_id:copyId}).then(value=>{if(active)setInfo(value);}).catch(()=>{if(active)setFailed(true);});return()=>{active=false;};},[copyId]);
  const instructions=info?[
    'Sovereign recovery instructions',info.name,
    'Install Sovereign from https://github.com/Foxlight-Foundation/foxlight-model-preservation',
    'Open recovery kit, review this exact archive and compare the full verification keys through a separately trusted channel.',
    `Exact archive: ${info.manifest_id}`,`Required capture signatures: ${info.threshold}`,
    ...info.keys.map(key=>`${info.revoked_keys.includes(key)?'Revoked key (do not accept)':'Capture verification key'}: ${key}`),
    'These are public key identifiers, not private signing keys. They do not establish the sender’s identity by themselves.',
    'After explicit acceptance and import, open Library and choose Restore files. No original Core, registry or Skulk is required.',
    'The kit contains the complete captured model, companions, original cards and signed evidence. Restoring does not install a runtime or run model code.',
    'Keep the kit on another drive. It is not encrypted and may contain private capture details. This guide contains no model files.',
  ].join('\n\n'):'';
  return <Sheet kicker="Keep separately from your kit" title="Recovery instructions" back="Close recovery instructions" onClose={onClose}>
    {failed?<Callout tone="bad" role="alert">The saved copy’s instructions could not be read. Close this view and try again. Your files are kept; no verification keys were accepted.</Callout>:!info?<p role="status">Reading this saved copy’s verification information…</p>:<>
      <h3>{info.name}</h3><p>Save these instructions separately. A recipient must confirm the full verification key with you through an existing trusted conversation, rather than trusting a key merely because it arrived with the kit.</p>
      <div className="sk-actions"><CopyButton value={instructions} label="recovery instructions"/><span>Copy recovery instructions to paste into your own note or message.</span></div>
      <Details summary="Instructions to save or send"><label className="sk-field">Select and copy these instructions<textarea className="sk-input" aria-label="Recovery instructions to save or send" readOnly rows={10} value={instructions}/></label><p>You can select and copy this text even when the app cannot access your clipboard.</p></Details>
      <Step n={1} title="Open the complete kit"><p>On the recipient’s computer, install Sovereign and choose Open recovery kit. Opening it does not accept its sender or start networking.</p></Step>
      <Step n={2} title="Compare the exact copy and keys"><Details summary="Full verification information"><Mono value={info.manifest_id} label="exact archive"/><p>{info.threshold} capture {info.threshold===1?'signature is':'signatures are'} required.</p>{info.keys.map(key=><Mono key={key} value={key} label={info.revoked_keys.includes(key)?'revoked capture key':'capture verification key'}/>)}</Details><p>These identifiers are safe to share. They contain no private signing key. Matching them does not establish registry approval, model quality or permission to execute code.</p></Step>
      <Step n={3} title="Import, then restore"><p>After independently checking the keys and accepting this exact capture, import it. In Library, choose Restore files and open the completed folder from Activity. The model, companions and original provenance stay together without Skulk or the original registry.</p></Step>
      <Callout tone="warn">A kit is not encrypted and may contain private capture information. Send it only after reviewing what you are sharing. These instructions contain no model files and cannot recover a model without its kit.</Callout>
    </>}
  </Sheet>;
}
