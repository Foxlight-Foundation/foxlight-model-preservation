import {useState} from 'react';
import {createPortal} from 'react-dom';
import {api,bytes} from './client';
import {Callout,Consent,Sheet} from './ui';

/** Delete one saved copy and its files, only after an explicit confirmation. */
export function RemoveCopy({copy,busy,explainError,onRemoved}:{copy:{copy_id:string;name:string;size_bytes:number};busy:boolean;explainError:(code:string)=>string;onRemoved:()=>Promise<void>}) {
  const [open,setOpen]=useState(false),[confirmed,setConfirmed]=useState(false),[working,setWorking]=useState(false),[error,setError]=useState('');
  function close(){setOpen(false);setConfirmed(false);setError('');}
  async function remove(){setWorking(true);setError('');try{await api('desktop-copy-remove',{copy_id:copy.copy_id,confirmed:true});close();await onRemoved();}catch(err){setError(err instanceof Error?err.message:'host_unavailable');}finally{setWorking(false);}}
  return <>
    <button className="sk-btn sk-quiet" disabled={busy} onClick={()=>setOpen(true)}>Remove from Library</button>
    {open&&createPortal(<Sheet kicker="Library" title={`Remove ${copy.name}?`} back="Keep this model" onClose={close} footer={<button className="sk-btn sk-solid" disabled={!confirmed||working} onClick={()=>void remove()}>{working?'Removing…':'Remove from Library'}</button>}>
      <p>This deletes the saved copy and its {bytes(copy.size_bytes)} of files from this Library. It can’t be undone.</p>
      <p>Recovery kits you saved and backups on other drives are kept, so you can bring it back from one of those. If this model is shared through <strong>Help keep models available</strong>, Sovereign stops sharing it unless another saved copy provides the same model.</p>
      <Consent checked={confirmed} onChange={setConfirmed}>Delete this saved copy and its files.</Consent>
      {error&&<Callout tone="bad" role="alert">{explainError(error)}</Callout>}
    </Sheet>,document.body)}
  </>;
}
