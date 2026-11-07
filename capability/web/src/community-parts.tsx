import {useState,type ReactNode} from 'react';

/** Join items as prose: "a", "a and b", "a, b and c". */
export const listed=(items:string[])=>items.length<2?items.join(''):`${items.slice(0,-1).join(', ')} and ${items[items.length-1]}`;

/** An announcement or exposure choice, with what it reveals said before it is made. */
export function Announce({checked,onChange,title,disabled,children}:{checked:boolean;onChange:(value:boolean)=>void;title:string;disabled?:boolean;children:ReactNode}){
 return <label className="sk-consent"><input type="checkbox" checked={checked} disabled={disabled} onChange={event=>onChange(event.target.checked)}/><span><strong>{title}</strong><small>{children}</small></span></label>;
}

/** Share every licence-eligible copy, or only chosen ones. */
export function ShareChoice({value,onChange,name,every,chosen}:{value:'eligible'|'chosen';onChange:(value:'eligible'|'chosen')=>void;name:string;every:[string,string];chosen:[string,string]}){
 return <div role="radiogroup" aria-label="What to share">
  <label className="sk-radio"><input type="radio" name={name} checked={value==='eligible'} onChange={()=>onChange('eligible')}/> <span><strong>{every[0]}</strong><br/>{every[1]}</span></label>
  <label className="sk-radio"><input type="radio" name={name} checked={value==='chosen'} onChange={()=>onChange('chosen')}/> <span><strong>{chosen[0]}</strong><br/>{chosen[1]}</span></label>
 </div>;
}

/** The licence list a model must match in full before it is shared. */
export function LicenceList({licenses,onChange,defaults}:{licenses:string[];onChange:(next:string[])=>void;defaults:string[]}){
 const [extra,setExtra]=useState('');
 function add(){const value=extra.trim();if(value&&!licenses.some(item=>item.toLowerCase()===value.toLowerCase()))onChange([...licenses,value]);setExtra('');}
 return <>
  <ul className="sk-community-licences">{licenses.map(item=><li key={item} className="sk-licence-chip">{item}<button type="button" aria-label={`Remove ${item}`} onClick={()=>onChange(licenses.filter(value=>value!==item))}>×</button></li>)}</ul>
  <div className="sk-licence-add"><label className="sk-field">Add a licence<input className="sk-input" value={extra} maxLength={200} placeholder="For example CC-BY-4.0" onChange={event=>setExtra(event.target.value)} onKeyDown={event=>{if(event.key==='Enter'){event.preventDefault();add();}}}/></label><button className="sk-btn sk-outline" disabled={!extra.trim()} onClick={add}>Add</button></div>
  {defaults.some(item=>!licenses.includes(item))&&<button className="sk-btn sk-outline" onClick={()=>onChange([...licenses,...defaults.filter(item=>!licenses.includes(item))])}>Restore the default licences</button>}
 </>;
}
