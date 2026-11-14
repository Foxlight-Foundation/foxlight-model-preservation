import {useEffect,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import {api,bytes,errorText} from './client';
import {Callout,Consent,Icon,Row,Sheet,Step} from './ui';

export interface LibraryMove {state:'moving'|'done'|'stopped'|'failed';items_done:number;items_total:number;bytes_done:number;bytes_total:number;error:string|null}
export interface LibraryState {path:string|null;system:boolean;available:boolean;private:boolean;free_bytes:number|null;previous:string[];elsewhere_copies:number;elsewhere_bytes:number;move:LibraryMove|null}
interface LibraryReview {review_id:string;path:string;exists:boolean;reattach:boolean;same_drive:boolean;free_bytes:number;expires_at:number}

export const libraryError=(code:string)=>({library_storage_unavailable:'The drive that holds your Library isn’t connected. Connect it to save, export or share copies kept there.',library_folder_unavailable:'That drive or folder can’t be reached. Connect or mount it, then choose it again.',library_folder_inside_sovereign:'Choose a folder outside Sovereign’s own data folder.',library_folder_not_empty:'Choose a new folder, or an empty one. Sovereign won’t mix your copies with other files.',library_folder_not_yours:'That folder belongs to another user account on this computer. Choose a folder you own.',library_folder_belongs_to_another:'That folder holds another Sovereign’s Library. Each Library folder belongs to one Sovereign; choose another folder.',library_folder_changed:'The folder changed after it was reviewed. Review it again.',library_moving:'Copies are moving to the new drive. Wait for the move to finish or stop it first.',library_busy_try_again:'Wait for saves, exports and sharing sessions to finish, then move your copies.',library_move_unsupported_file:'A copy contains a file that can’t be moved safely. It stays where it is and keeps working.',library_move_verification_failed:'A moved file didn’t read back correctly from the new drive. The original stays where it is and keeps working. Check the drive, then try again.',folder_picker_busy:'A folder chooser is already open. Complete or cancel it first.',folder_picker_timed_out:'Folder selection timed out. Choose the folder again when you’re ready.',folder_picker_unavailable:'A folder picker isn’t available here. Type the folder’s path below.'} as Record<string,string>)[code]??errorText(code);

type Bridge={postMessage:(body:Record<string,never>)=>Promise<string|null>};

/** Where model copies are kept: this computer’s drive, or any connected drive, NAS or folder. */
export function LibraryLocation({library,busy,onChange}:{library:LibraryState|undefined;busy:boolean;onChange:()=>Promise<void>}) {
  const [open,setOpen]=useState(false),[path,setPath]=useState(''),[review,setReview]=useState<LibraryReview|null>(null),[confirmed,setConfirmed]=useState(false),[working,setWorking]=useState(false),[error,setError]=useState('');
  const live=useRef(true);
  useEffect(()=>{live.current=true;return()=>{live.current=false;};},[]);
  const moving=library?.move?.state==='moving';
  // While copies move, keep the progress current.
  useEffect(()=>{if(!moving)return;const timer=setInterval(()=>void onChange().catch(()=>undefined),1500);return()=>clearInterval(timer);},[moving,onChange]);
  if(!library)return null;
  async function work(fn:()=>Promise<void>){setWorking(true);setError('');try{await fn();}catch(err){if(live.current)setError(err instanceof Error?err.message:'host_unavailable');}finally{if(live.current)setWorking(false);}}
  function reset(){setReview(null);setConfirmed(false);setError('');}
  function close(){reset();setPath('');setOpen(false);}
  async function choose(){await work(async()=>{const bridge=(window as unknown as {webkit?:{messageHandlers?:{chooseLibraryFolder?:Bridge}}}).webkit?.messageHandlers?.chooseLibraryFolder;const result=bridge?await bridge.postMessage({}):(await api<{path:string|null}>('desktop-library-folder-choose',{})).path;if(live.current&&result){reset();setPath(result);}});}
  async function inspect(){await work(async()=>{const value=await api<LibraryReview>('desktop-library-review',{path:path.trim()});if(live.current){setReview(value);setConfirmed(false);}});}
  async function setup(){if(!review||!confirmed)return;await work(async()=>{await api('desktop-library-setup',{review_id:review.review_id,confirmed:true});await onChange();if(live.current)close();});}
  const where=library.system?'This computer’s drive':library.path;
  const move=library.move;
  const disabled=busy||working;
  return <section className="sk-card sk-library-location" aria-label="Where model copies are kept">
    <div className="sk-model-head"><Icon name="storage"/><div className="sk-model-title"><h2>Where model copies are kept</h2><p className="sk-meta"><span className="sk-wrap">{where}</span>{library.available&&library.free_bytes!=null&&` · ${bytes(library.free_bytes)} free`}</p></div></div>
    {!library.available&&<Callout tone="bad" role="alert">The drive that holds your Library isn’t connected: <span className="sk-wrap">{library.path}</span>. Connect it to save new models or use the copies kept there. Sovereign won’t save to this computer’s drive instead unless you choose that.</Callout>}
    {library.available&&!library.private&&<p className="sk-hint">This drive can’t limit access to your user account, so other people who can use the drive can read the copies on it. Sovereign checks every file against its fingerprint before sharing or exporting it.</p>}
    {moving&&move&&<div role="status"><p>Moving copies to {where}: {move.items_done} of {move.items_total} · {bytes(move.bytes_done)} of {bytes(move.bytes_total)}. Each file is read back from the new drive before the original is removed; copies keep working meanwhile.</p><progress max={Math.max(1,move.bytes_total)} value={move.bytes_done}/><div className="sk-actions"><button className="sk-btn sk-quiet" disabled={disabled} onClick={()=>void work(async()=>{await api('desktop-library-move-stop',{});await onChange();})}>Stop moving</button></div></div>}
    {!moving&&move?.state==='failed'&&<Callout tone="bad" role="alert">{libraryError(move.error??'host_unavailable')}</Callout>}
    {!moving&&library.available&&library.elsewhere_copies>0&&<div><p>{library.elsewhere_copies} {library.elsewhere_copies===1?'copy is':'copies are'} ({bytes(library.elsewhere_bytes)}) still kept in an earlier location. {library.elsewhere_copies===1?'It keeps':'They keep'} working there.</p><div className="sk-actions"><button className="sk-btn sk-outline" disabled={disabled} onClick={()=>void work(async()=>{await api('desktop-library-move',{});await onChange();})}>Move {library.elsewhere_copies===1?'it':'them'} to {library.system?'this computer’s drive':'this location'}</button></div></div>}
    {error&&!open&&<Callout tone="bad" role="alert">{libraryError(error)}</Callout>}
    <div className="sk-actions"><button className="sk-btn sk-outline" disabled={disabled||moving} onClick={()=>{reset();setOpen(true);}}><Icon name="folder"/>Change location</button>{!library.system&&<button className="sk-btn sk-quiet" disabled={disabled||moving} onClick={()=>void work(async()=>{await api('desktop-library-use-home',{});await onChange();})}>Keep new copies on this computer</button>}</div>
    {open&&createPortal(<Sheet kicker="Library" title="Where to keep model copies" back="Close" onClose={close} footer={<button className="sk-btn sk-solid" disabled={disabled||(!review&&!path.trim())||Boolean(review&&(!confirmed||review.expires_at*1000<=Date.now()))} onClick={()=>void (review?setup():inspect())}>{working?'Checking…':review?'Keep new copies here':'Review folder'}</button>}>
      <Step n={1} title="Choose a drive, network share or folder"><p>Connect the drive or mount the network share first. Sovereign creates one dedicated folder for your model copies. Its settings and keys stay on this computer.</p><button className="sk-btn sk-outline" disabled={disabled} onClick={()=>void choose()}><Icon name="folder"/>Choose folder</button><label className="sk-field">Folder path<input className="sk-input" value={path} autoComplete="off" spellCheck={false} onChange={event=>{reset();setPath(event.target.value);}} placeholder="/Volumes/Models/Sovereign Library"/></label></Step>
      {review&&<Step n={2} title="Review"><Row k="Folder"><span className="sk-wrap">{review.path}</span></Row><Row k="Available space">{bytes(review.free_bytes)}</Row><p>{review.reattach?'This folder already holds this Sovereign’s Library. Its copies are used again.':review.exists?'Uses this empty folder.':'Creates this folder.'} New saves go here from now on. Copies you already have keep working where they are, and you can move them here afterwards: each file is read back from the new drive before the original is removed.</p>{review.same_drive&&<Callout tone="warn">This is on the same drive as Sovereign’s own data, so it won’t free any space there.</Callout>}<Consent checked={confirmed} onChange={setConfirmed}>Keep new model copies in this folder.</Consent></Step>}
      {error&&<Callout tone="bad" role="alert">{libraryError(error)}</Callout>}
    </Sheet>,document.body)}
  </section>;
}
