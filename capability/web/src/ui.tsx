import {createContext,useCallback,useContext,useEffect,useId,useRef,useState,type KeyboardEvent,type ReactNode} from 'react';
import {createPortal} from 'react-dom';

/** Feather-style inline icons; no icon font, no network request. */
const paths:Record<string,ReactNode> = {
  library:<><polyline points="21 8 21 21 3 21 3 8"/><rect x="1" y="3" width="22" height="5" rx="1"/><line x1="10" y1="12" x2="14" y2="12"/></>,
  search:<><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></>,
  share:<><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></>,
  activity:<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>,
  storage:<><line x1="22" y1="12" x2="2" y2="12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/></>,
  restore:<><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></>,
  sun:<><circle cx="12" cy="12" r="4.5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></>,
  moon:<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>,
  close:<><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></>,
  more:<><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/></>,
  copy:<><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></>,
  check:<polyline points="20 6 9 17 4 12"/>,
  arrow:<><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></>,
  shield:<><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><polyline points="9 12 11 14 15 10"/></>,
  folder:<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>,
  device:<><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></>,
  download:<><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></>,
  upload:<><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></>,
  refresh:<><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></>,
  send:<><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></>,
  globe:<><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></>,
  radio:<><circle cx="12" cy="12" r="2"/><path d="M16.24 7.76a6 6 0 0 1 0 8.49m-8.48-.01a6 6 0 0 1 0-8.49m11.31-2.82a10 10 0 0 1 0 14.14m-14.14 0a10 10 0 0 1 0-14.14"/></>,
  alert:<><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></>,
  info:<><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></>,
  key:<path d="m21 2-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.78 7.78 5.5 5.5 0 0 1 7.78-7.78zm0 0L15.5 7.5m0 0 3 3L22 7l-3-3m-3.5 3.5L19 4"/>,
  plus:<><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></>,
  pause:<><rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/></>,
  stop:<rect x="5" y="5" width="14" height="14" rx="2"/>,
  file:<><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></>,
  users:<><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></>,
  trash:<><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></>,
  lock:<><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></>,
  package:<><path d="M16.5 9.4 7.5 4.21M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></>,
  peers:<><circle cx="12" cy="5" r="3"/><circle cx="4.5" cy="18" r="3"/><circle cx="19.5" cy="18" r="3"/><path d="M12 8v4m0 0-5.5 3.5M12 12l5.5 3.5"/></>,
};
export type IconName = keyof typeof paths;
export function Icon({name,size=16}:{name:IconName;size?:number}) {
  return <svg className="sk-icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">{paths[name]}</svg>;
}

/** The Skulk mark from the design system, drawn in the current text colour. */
export function SkulkMark({size=26}:{size?:number}) {
  return <svg className="sk-mark" width={size} height={size} viewBox="0 0 740 740" fill="currentColor" aria-hidden="true" focusable="false"><path d="M79.19,41.2c-7.2,19-13.7,46.4-17.7,74.8-2.2,15.9-2.6,79.4-.5,96,4.9,39.5,13.5,73.4,28.8,113.2q1.8,4.6-4.8,18c-8.5,17.1-13,28.4-21.3,54-12.1,37.5-25.7,60.5-55.2,93.5q-6.2,7,.4,15.4c30.7,39,79.4,83.1,127.8,115.6,27.2,18.2,70.4,41.5,99,53.3,49,20.1,99.3,32,135.5,32,32.7-.1,83.6-12,128.8-30.1,17-6.8,55.4-25.8,72.7-36,60.3-35.3,121.2-87,158.4-134.4q7-8.8-2.1-18.9c-16.7-18.5-28.7-35.8-38.6-55.3-6.9-13.7-9.4-20.2-15.7-40.8-5.6-18.1-11.2-31.9-19.8-49.1q-6.5-13-3.1-21.4c31.9-78.8,38.5-169.7,18.4-250-5-19.7-11.4-38-13.4-38-2.5,0-21.1,8.1-34.9,15.3-52.5,27.1-106.7,72.1-145.8,121-4.5,5.6-8.6,10.6-9,11.1-.4.4-7.4-.8-15.6-2.7-29.8-7.1-48-9.1-80.8-9.1s-54.3,2.4-83.8,9.6q-13.1,3.2-16.1-.7c-45.9-57.8-100.8-103.8-157.1-131.8-10.9-5.4-28.3-12.7-30.3-12.7-.6,0-2.4,3.7-4.2,8.2ZM125.09,110.7c17.7,11,48.9,37.7,66.9,57.2,11.7,12.7,28.2,32.4,28.2,33.6,0,.5-3.9,3-8.6,5.5-26.4,13.7-62,42.7-80.6,65.7-2.8,3.4-5.5,6.3-5.9,6.3-2,0-10.2-29.4-13.8-49.7-7.2-40.2-7.4-91.4-.6-117.7.8-3.1,1.5-5.9,1.5-6.1,0-1.7,5,.3,12.9,5.2ZM629.09,108.7c6.2,23.3,7.2,70.4,2.1,106.2-3.3,24.1-13.3,64.1-15.9,64.1-.5,0-1.6-1.1-2.5-2.5s-7.9-8.9-15.4-16.7c-21-21.5-40.7-37.1-64.9-51-6.8-3.9-12.3-7.4-12.3-7.7.1-1.1,13.1-16.6,25.2-29.8,16.3-17.8,37.5-37.3,54-49.7,10.1-7.6,24.7-16.6,26.9-16.6,1.3,0,2.1,1.2,2.8,3.7ZM189.39,361c83.6,7.9,134.7,66,150.9,171.1,1.1,7.3,2.2,13.5,2.4,13.7s3.6-.3,7.6-1.2c12.1-2.8,36.4-2,45.5,1.4,1.3.5,2.4.6,2.4.2s.9-6.4,2-13.4c12.2-79.4,43.3-131.2,93.8-156.3,28.1-14,68.6-19.7,102.2-14.4,9.6,1.5,9.5,1.4,12.9,9.1,3,6.7,4.8,11.8,16.5,48.8,11.7,36.6,29.8,60.6,58.4,77.4q8.2,4.8,3.3,10.5c-8.6,9.9-37.3,37.4-50.7,48.6-38.8,32.3-74.4,55.4-117.8,76.6-48.3,23.5-94.9,37.7-137.5,41.9-37.9,3.8-104.8-14.3-165.9-44.9-40.2-20.1-73-41.6-109.7-72-14.6-12.1-57.5-53.7-57.5-55.8,0-.6,1.4-1.7,3.2-2.4,5.8-2.4,20.3-12.5,27.2-18.9,9.7-9.1,18.7-21.7,26-36.1,6-11.9,8.4-18.7,18.6-51.9,2.3-7.4,5.9-17.2,8-21.6q3.8-8.2,9.1-8.9c17.2-2.4,33.9-2.9,49.1-1.5Z"/><path d="M209.39,427.6c-15.2,4-27,15.2-31.3,29.7-1.7,5.5-2.1,8.7-1.7,16.6.3,8.9.7,10.5,4.1,17.4,4,8.1,12.5,17,19.7,20.7,30.6,15.6,67-6.4,67-40.5,0-8.2-2.3-17-6.3-23.8-3.1-5.2-12.4-14-17.8-16.8-9.4-4.8-23.3-6.2-33.7-3.3ZM212.99,442.7c10,7.5,9.3,21.2-1.3,26.3-8.4,4.1-16,1.4-20.3-7.3-1.9-3.7-2.2-5.5-1.7-8.7.8-4.7,4.6-9.7,9-11.6,3.9-1.8,11-1.2,14.3,1.3Z"/><path d="M506.29,427.6c-12.4,3.3-23.6,12.9-29.4,25.1-3.1,6.5-3.2,7.4-3.2,18.9s0,12.1,3.8,19.6c16.9,34.2,65.5,34.2,82.4,0,3.8-7.5,3.8-7.7,3.8-19.6s-.1-12.4-3.2-18.9c-3.8-8-12.2-17.3-19.1-21-10.1-5.5-23.9-7.1-35.1-4.1ZM510.09,442.8c10,7.3,9.1,21.1-1.7,26.3-14,6.8-28-9.7-19-22.4,4.5-6.3,14.8-8.2,20.7-3.9Z"/><path d="M351.89,556.5c-8.5,1.7-16.5,6-19.5,10.5-1.9,2.8-2.2,4.3-1.7,7.4,1.4,9.3,12.2,22.9,24.3,30.6q7.1,4.5,7.1,12.3c.1,9.6-1.4,12.3-9.1,16.1-11.1,5.5-21.5,1.4-27.3-10.7-2.6-5.6-7.3-6.7-11.7-2.9-5.7,4.8,3.5,20.6,15.7,27,4.3,2.3,6.3,2.7,13.5,2.7,9.8,0,15.7-1.8,22.6-7.1q4.4-3.4,8.8,0c6.9,5.3,12.8,7.1,22.6,7.1,7.2,0,9.3-.4,13.5-2.7,8.7-4.5,17.5-15.9,17.5-22.6,0-4.1-2.9-6.4-7.7-6-3.4.3-4.1.8-6.3,5-3.3,6.1-5.7,8.6-10.4,11-7.9,4-19.8.4-24.6-7.5-1.5-2.3-2-5-2-9.9q0-6.6,5.8-9.8c8.4-4.8,20.5-17.3,24.2-25.1,3.6-7.7,3.7-10.5.7-15-6.5-9.6-34-14.7-56-10.4Z"/></svg>;
}

export type Tone = 'ok'|'live'|'info'|'muted'|'bad';
/** One status per object; amber (`live`) is reserved for work in flight. */
export function Pill({tone,children}:{tone:Tone;children:ReactNode}) {
  return <span className="sk-pill" data-tone={tone}><span className="sk-dot" aria-hidden="true"/>{children}</span>;
}

/** Mono section label, e.g. "SAVED COPIES · 2". */
export function Label({children,as:Tag='p'}:{children:ReactNode;as?:'p'|'h2'|'h3'|'h4'}) {return <Tag className="sk-label">{children}</Tag>;}

/** Monogram tile for a model or object. */
export function Monogram({text,size}:{text:string;size?:'lg'}) {return <span className="sk-monogram" data-size={size} aria-hidden="true">{text}</span>;}
export const initials=(id:string)=>(id.split('/').pop()??id).replace(/[^A-Za-z0-9]/g,'').slice(0,2).toUpperCase()||'··';

/** Copy a machine value without selecting it; quietly absent where the browser forbids clipboard access. */
export function CopyButton({value,label}:{value:string;label:string}) {
  const [copied,setCopied]=useState(false);
  useEffect(()=>{if(!copied)return;const timer=setTimeout(()=>setCopied(false),1600);return()=>clearTimeout(timer);},[copied]);
  if(typeof navigator==='undefined'||!navigator.clipboard?.writeText)return null;
  return <button type="button" className="sk-copy" aria-label={copied?`${label} copied`:`Copy ${label}`} title={copied?'Copied':`Copy ${label}`} onClick={()=>{void navigator.clipboard.writeText(value).then(()=>setCopied(true)).catch(()=>setCopied(false));}}><Icon name={copied?'check':'copy'} size={14}/><span className="sk-sr" aria-live="polite">{copied?'Copied':''}</span></button>;
}

/** Explanations work on hover, keyboard focus and touch without a network call. */
export function Help({label,children}:{label:string;children:ReactNode}) {
  const id=useId(),[open,setOpen]=useState(false),[position,setPosition]=useState({left:12,top:12});
  const pinned=useRef(false),container=useRef<HTMLSpanElement>(null),tip=useRef<HTMLSpanElement>(null),leave=useRef<ReturnType<typeof setTimeout>|null>(null);
  const keep=()=>{if(leave.current!==null){clearTimeout(leave.current);leave.current=null;}};
  const later=()=>{keep();if(!pinned.current&&!container.current?.contains(document.activeElement))leave.current=setTimeout(()=>setOpen(false),200);};
  useEffect(()=>()=>{if(leave.current!==null)clearTimeout(leave.current);},[]);
  useEffect(()=>{if(!open)return;const dismiss=()=>{pinned.current=false;setOpen(false);};const outside=(event:PointerEvent)=>{if(event.target instanceof Node&&!container.current?.contains(event.target)&&!tip.current?.contains(event.target))dismiss();};const escape=(event:globalThis.KeyboardEvent)=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();dismiss();}};document.addEventListener('pointerdown',outside,true);document.addEventListener('keydown',escape,true);return()=>{document.removeEventListener('pointerdown',outside,true);document.removeEventListener('keydown',escape,true);};},[open]);
  const show=(button:HTMLButtonElement)=>{keep();const rect=button.getBoundingClientRect(),width=Math.min(288,window.innerWidth-24);setPosition({left:Math.max(12,Math.min(rect.left,window.innerWidth-width-12)),top:Math.max(12,Math.min(rect.bottom+8,window.innerHeight-232))});setOpen(true);};
  return <span className="sk-help" ref={container}><button type="button" className="sk-help-button" aria-label={label} aria-describedby={open?id:undefined} aria-expanded={open} onMouseEnter={event=>show(event.currentTarget)} onMouseLeave={later} onFocus={event=>show(event.currentTarget)} onClick={event=>{if(pinned.current){pinned.current=false;setOpen(false);}else{pinned.current=true;show(event.currentTarget);}}} onBlur={()=>{pinned.current=false;setOpen(false);}} onKeyDown={event=>{if(event.key==='Escape'&&open){event.stopPropagation();event.preventDefault();pinned.current=false;setOpen(false);}}}><Icon name="info" size={15}/></button>{open&&createPortal(<span ref={tip} id={id} role="tooltip" className="sk-help-tip" style={position} onMouseEnter={keep} onMouseLeave={later}>{children}</span>,document.body)}</span>;
}

/** Fingerprints stay exact and copyable, with their meaning beside the value. */
export function Mono({value,label,copy=true}:{value:string;label:string;copy?:boolean}) {
  const key=value.startsWith('ed25519:'),fingerprint=/^[0-9a-f]{64}$/.test(value)||/^(card_|bundle_)/.test(value);
  return <span className="sk-identity">{(key||fingerprint)&&<span className="sk-identity-caption">{key?'Verification key':'Digital fingerprint'}<Help label={key?'About verification keys':'About digital fingerprints'}>{key?'This key checks whether a signature matches this copy. Compare the full key with information from a person or publisher you already trust before accepting it. A matching signature alone does not prove who the signer is.':'This long code is calculated from the saved contents and helps detect changes. You usually do not need to read it. Compare the complete code when someone you trust asks you to verify an exact copy. It does not rate quality or safety.'}</Help></span>}<span className="sk-value"><code>{value}</code>{copy&&<CopyButton value={value} label={label}/>}</span></span>;
}

/** Key/value row inside a summary card. */
export function Row({k,children}:{k:ReactNode;children:ReactNode}) {return <div className="sk-row"><span className="sk-row-k">{k}</span><span className="sk-row-v">{children}</span></div>;}

/** Numbered step in a multi-part review. */
export function Step({n,title,children,aside}:{n:number;title:ReactNode;children:ReactNode;aside?:ReactNode}) {
  return <section className="sk-step"><div className="sk-step-head"><span className="sk-step-n" aria-hidden="true">{n}</span><h3>{title}</h3>{aside}</div><div className="sk-step-body">{children}</div></section>;
}

/** Long fine print stays available behind a native disclosure. */
export function Details({summary='Details',children,className}:{summary?:ReactNode;children:ReactNode;className?:string}) {
  return <details className={`sk-details${className?' '+className:''}`}><summary>{summary}</summary><div className="sk-details-body">{children}</div></details>;
}

/** Calm callouts; `warn` uses danger-tinted ink, never amber. */
export function Callout({tone='info',icon,children,role}:{tone?:'info'|'warn'|'bad'|'ok';icon?:IconName;children:ReactNode;role?:'alert'|'status'}) {
  return <div className="sk-callout" data-tone={tone} role={role}><Icon name={icon??(tone==='ok'?'check':tone==='info'?'info':'alert')}/><div>{children}</div></div>;
}

/** A consent is always a separate, explicit, unchecked-by-default checkbox. */
export function Consent({checked,onChange,children,disabled,note}:{checked:boolean;onChange:(value:boolean)=>void;children:ReactNode;disabled?:boolean;note?:ReactNode}) {
  return <label className="sk-consent"><input type="checkbox" checked={checked} disabled={disabled} onChange={event=>onChange(event.target.checked)}/><span>{children}{note&&<small>{note}</small>}</span></label>;
}

/** Real progress when a bound exists; an honest sweep when it does not. */
export function Progress({value,max,tone='live',label}:{value?:number;max?:number;tone?:'live'|'ok'|'star';label:string}) {
  const known=value!==undefined&&max!==undefined&&max>0;
  const ratio=known?Math.max(0,Math.min(1,value/max)):undefined;
  return <div className="sk-progress" data-tone={tone} data-indeterminate={known?undefined:'true'} role="progressbar" aria-label={label} aria-valuemin={known?0:undefined} aria-valuemax={known?100:undefined} aria-valuenow={ratio!==undefined?Math.round(ratio*100):undefined}><span style={ratio!==undefined?{width:`${ratio*100}%`}:undefined}/></div>;
}

/** Empty states: a friendly sentence and one way forward. */
export function Empty({icon,title,children,action}:{icon:IconName;title:ReactNode;children?:ReactNode;action?:ReactNode}) {
  return <div className="sk-empty"><span className="sk-empty-icon"><Icon name={icon} size={22}/></span><h2>{title}</h2>{children&&<p>{children}</p>}{action}</div>;
}

const SheetStack=createContext<{stack:string[];register:(id:string)=>()=>void;banner:ReactNode}>({stack:[],register:()=>()=>undefined,banner:null});
/** Tracks open sheets so app-level messages render once, on the topmost surface. */
export function SheetHost({banner,children}:{banner:ReactNode;children:ReactNode}) {
  const [stack,setStack]=useState<string[]>([]);
  const register=useCallback((id:string)=>{setStack(current=>[...current,id]);return()=>setStack(current=>current.filter(item=>item!==id));},[]);
  return <SheetStack.Provider value={{stack,register,banner}}>{children}</SheetStack.Provider>;
}
/** Renders app-level messages in page flow only while no sheet covers it. */
export function PageBanner() {const {stack,banner}=useContext(SheetStack);return stack.length?null:<>{banner}</>;}

let locks=0,lastFocused:HTMLElement|null=null;
// Controls that start an async review are disabled while it loads, so remember the last focused control to return to.
if(typeof document!=='undefined')document.addEventListener('focusin',event=>{if(event.target instanceof HTMLElement&&event.target!==document.body)lastFocused=event.target;},true);
/** A right-side sheet under the header (full screen on phones). The header stays usable: leaving an area discards the sheet. */
export function Sheet({kicker,title,subtitle,back,onClose,footer,children,wide}:{kicker?:ReactNode;title:ReactNode;subtitle?:ReactNode;back:string;onClose:()=>void;footer?:ReactNode;children:ReactNode;wide?:boolean}) {
  const id=useId(),heading=useRef<HTMLHeadingElement>(null),panel=useRef<HTMLElement>(null),close=useRef(onClose);close.current=onClose;
  const {stack,register,banner}=useContext(SheetStack),top=useRef(false);
  top.current=stack[stack.length-1]===id;
  useEffect(()=>register(id),[id,register]);
  useEffect(()=>{
    const active=document.activeElement instanceof HTMLElement&&document.activeElement!==document.body?document.activeElement:null;
    const opener=active??lastFocused;
    locks++;document.documentElement.classList.add('sk-locked');
    const frame=requestAnimationFrame(()=>{const current=document.activeElement;if(!panel.current?.contains(current)&&(current===active||current===document.body))heading.current?.focus({preventScroll:true});});
    function key(event:globalThis.KeyboardEvent){if(event.key==='Escape'&&top.current&&!event.defaultPrevented){event.preventDefault();close.current();}}
    document.addEventListener('keydown',key);
    const sheet=panel.current;
    return()=>{cancelAnimationFrame(frame);document.removeEventListener('keydown',key);if(--locks===0)document.documentElement.classList.remove('sk-locked');
      // Return focus only when it was lost with the sheet, never away from a chosen header control.
      const lost=document.activeElement===document.body||document.activeElement===null||Boolean(sheet&&sheet.contains(document.activeElement));
      if(lost&&opener?.isConnected&&!opener.closest('[inert]'))opener.focus({preventScroll:true});};
  },[]);
  return <div className="sk-layer" data-wide={wide?'true':undefined}><div className="sk-scrim" aria-hidden="true" onClick={()=>close.current()}/><section ref={panel} className="sk-sheet" role="dialog" aria-labelledby={id}>
    <div className="sk-sheet-head"><div className="sk-sheet-titles">{kicker&&<p className="sk-label">{kicker}</p>}<h2 id={id} ref={heading} tabIndex={-1}>{title}</h2>{subtitle&&<p className="sk-sheet-sub">{subtitle}</p>}</div><button type="button" className="sk-icon-btn" aria-label={back} title={back} onClick={()=>close.current()}><Icon name="close" size={18}/></button></div>
    <div className="sk-sheet-body">{stack[stack.length-1]===id&&banner}{children}</div>
    {footer&&<div className="sk-sheet-foot">{footer}</div>}
  </section></div>;
}

export interface MenuItem {label:string;onSelect?:()=>void;href?:string;download?:string;disabled?:boolean;icon?:IconName}
/** Quiet "⋯" menu for secondary actions; full keyboard support. */
export function Menu({label,items}:{label:string;items:MenuItem[]}) {
  const [open,setOpen]=useState(false),root=useRef<HTMLDivElement>(null),trigger=useRef<HTMLButtonElement>(null),menuId=useId();
  const enabled=()=>Array.from(root.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not([disabled])')??[]);
  useEffect(()=>{
    if(!open)return;
    requestAnimationFrame(()=>enabled()[0]?.focus());
    function away(event:PointerEvent){if(!root.current?.contains(event.target as Node))setOpen(false);}
    document.addEventListener('pointerdown',away);return()=>document.removeEventListener('pointerdown',away);
  },[open]);
  function keys(event:KeyboardEvent){
    const items=enabled(),index=items.indexOf(document.activeElement as HTMLElement);
    if(event.key==='Escape'){event.preventDefault();event.nativeEvent.preventDefault();setOpen(false);trigger.current?.focus();}
    else if(event.key==='ArrowDown'){event.preventDefault();items[(index+1)%items.length]?.focus();}
    else if(event.key==='ArrowUp'){event.preventDefault();items[(index-1+items.length)%items.length]?.focus();}
    else if(event.key==='Home'){event.preventDefault();items[0]?.focus();}
    else if(event.key==='End'){event.preventDefault();items[items.length-1]?.focus();}
    else if(event.key==='Tab')setOpen(false);
  }
  return <div className="sk-menu" ref={root} onKeyDown={open?keys:undefined}>
    <button ref={trigger} type="button" className="sk-icon-btn sk-bordered" aria-label={label} title={label} aria-haspopup="menu" aria-expanded={open} aria-controls={open?menuId:undefined} onClick={()=>setOpen(value=>!value)} onKeyDown={event=>{if(event.key==='ArrowDown'&&!open){event.preventDefault();setOpen(true);}}}><Icon name="more" size={18}/></button>
    {open&&<div className="sk-menu-pop" role="menu" id={menuId} aria-label={label}>{items.map(item=>item.href?<a key={item.label} role="menuitem" className="sk-menu-item" href={item.href} download={item.download} onClick={()=>setOpen(false)}>{item.icon&&<Icon name={item.icon}/>}{item.label}</a>:<button key={item.label} type="button" role="menuitem" className="sk-menu-item" disabled={item.disabled} onClick={()=>{setOpen(false);item.onSelect?.();}}>{item.icon&&<Icon name={item.icon}/>}{item.label}</button>)}</div>}
  </div>;
}

/** Friendly relative time for a Unix timestamp in seconds. */
export function ago(seconds:number):string {
  const delta=Math.round(seconds-Date.now()/1000),abs=Math.abs(delta);
  const format=new Intl.RelativeTimeFormat(undefined,{numeric:'auto'});
  if(abs<60)return format.format(delta,'second');
  if(abs<3600)return format.format(Math.round(delta/60),'minute');
  if(abs<86400)return format.format(Math.round(delta/3600),'hour');
  if(abs<86400*45)return format.format(Math.round(delta/86400),'day');
  return new Date(seconds*1000).toLocaleDateString(undefined,{year:'numeric',month:'short',day:'numeric'});
}

export type Theme='night'|'noon';
/** Night by default and when the system prefers dark; a manual choice is remembered per browser. */
export function useTheme():[Theme,()=>void] {
  const [stored,setStored]=useState<Theme|null>(()=>readTheme());
  const [system,setSystem]=useState<Theme>(()=>typeof matchMedia==='function'&&matchMedia('(prefers-color-scheme: light)').matches?'noon':'night');
  useEffect(()=>{if(typeof matchMedia!=='function')return;const media=matchMedia('(prefers-color-scheme: light)');const listener=(event:MediaQueryListEvent)=>setSystem(event.matches?'noon':'night');media.addEventListener('change',listener);return()=>media.removeEventListener('change',listener);},[]);
  const theme=stored??system;
  useEffect(()=>{if(stored)document.documentElement.dataset.theme=stored;else delete document.documentElement.dataset.theme;},[stored]);
  return [theme,()=>{const next:Theme=theme==='night'?'noon':'night';setStored(next);try{localStorage.setItem('skulk-library-theme',next);}catch{/* Preference stays for this page only. */}}];
}
export function readTheme():Theme|null {try{const value=localStorage.getItem('skulk-library-theme');return value==='night'||value==='noon'?value:null;}catch{return null;}}
