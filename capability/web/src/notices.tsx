import {useEffect,useState} from 'react';
import {createPortal} from 'react-dom';
import {Callout,Loading,Sheet} from './ui';

const NOTICES='THIRD_PARTY_NOTICES.txt';

type Block={kind:'heading'|'rule'|'text';text:string};
// A package's notice starts with its name and version, e.g. "react 19.3.1".
const PACKAGE=/^@?[a-z0-9][\w./-]* \d+\.\d+\S*( \(.*\))?$/;

/** The plain-text notices as readable blocks: hard-wrapped lines rejoined so
 *  they flow in the app's typeface, short lines (lists, titles) kept as is. */
export function noticeBlocks(text:string):Block[] {
  const blocks:Block[]=[];
  for(const paragraph of text.replace(/\r\n/g,'\n').split(/\n\s*\n/)){
    const lines=paragraph.split('\n').map(line=>line.trimEnd()).filter(line=>line.trim());
    while(lines.length&&PACKAGE.test(lines[0]!)){blocks.push({kind:'heading',text:lines.shift()!});}
    let current='';
    const flush=()=>{if(current.trim())blocks.push({kind:'text',text:current.trim()});current='';};
    for(const line of lines){
      if(/^[-=*_]{3,}$/.test(line.trim())){flush();blocks.push({kind:'rule',text:''});continue;}
      // A line that ran to the wrap width continues; a short one ends its line.
      current+=current&&!current.endsWith('\n')?' '+line.trim():line.trim();
      if(line.length<60)current+='\n';
    }
    flush();
  }
  return blocks;
}

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
        :<div className="sk-notices" tabIndex={0} role="document" aria-label="Third-party notices text">{noticeBlocks(text).map((block,index)=>block.kind==='heading'?<h3 key={index}>{block.text}</h3>:block.kind==='rule'?<hr key={index}/>:<p key={index}>{block.text}</p>)}</div>}
    </Sheet>,document.body)}
  </>;
}
