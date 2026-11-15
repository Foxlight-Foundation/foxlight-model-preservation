import {useEffect,useState} from 'react';
import {createPortal} from 'react-dom';
import {Callout,Loading,Sheet} from './ui';

const NOTICES='THIRD_PARTY_NOTICES.txt';

/** The footer's Third-party notices, read in the app instead of leaving it for a bare text file. */
export function NoticesLink() {
  const [open,setOpen]=useState(false),[text,setText]=useState<string|null>(null),[failed,setFailed]=useState(false);
  useEffect(()=>{
    if(!open||text!==null)return;let active=true;setFailed(false);
    void fetch(NOTICES).then(response=>{if(!response.ok)throw new Error(String(response.status));return response.text();}).then(value=>{if(active)setText(value);}).catch(()=>{if(active)setFailed(true);});
    return()=>{active=false;};
  },[open,text]);
  return <>
    <a href={NOTICES} onClick={event=>{if(event.metaKey||event.ctrlKey||event.shiftKey||event.button!==0)return;event.preventDefault();setOpen(true);}}>Third-party notices</a>
    {open&&createPortal(<Sheet kicker="About" title="Third-party notices" subtitle="The open-source software in this interface, and the licences it is used under." back="Close third-party notices" onClose={()=>setOpen(false)} wide>
      {failed?<Callout tone="bad" role="alert">The notices couldn’t be loaded. <a href={NOTICES} target="_blank" rel="noreferrer">Open them as a text file</a>.</Callout>
        :text===null?<Loading size="page" label="Loading the notices…"/>
        :<pre className="sk-notices" tabIndex={0} aria-label="Third-party notices text">{text}</pre>}
    </Sheet>,document.body)}
  </>;
}
