import {useState,type ReactNode} from 'react';
import {Callout,Details} from './ui';
import {familyUse,matchCount} from './counts';

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

/** The core's versioned licence-family definition, as the host sends it. */
export interface LicenceFamily {id:string;name:string;summary:string;default:boolean;licences:{id:string;name:string;note?:string}[]}
export interface LicenceDefinition {schema_version:string;version:number;families:LicenceFamily[];unidentified:string[];aliases:Record<string,string>}

/** Compare identifiers as the core does: case-insensitive, spaces as dashes, a few aliases. */
export const normalizeLicence=(value:string,definition?:LicenceDefinition|null)=>{const folded=value.trim().toLowerCase().split(/\s+/).filter(Boolean).join('-');return definition?.aliases[folded]??folded;};
const unidentified=(value:string,definition?:LicenceDefinition|null)=>(definition?.unidentified??['','unknown','other','none','noassertion']).includes(value);

/** Whether every licence of one copy is identified and selected. */
export function licenceMatches(licences:string[],selected:string[],definition?:LicenceDefinition|null):boolean{
 const chosen=new Set(selected.map(item=>normalizeLicence(item,definition)));
 const own=licences.map(item=>normalizeLicence(item,definition));
 return own.length>0&&own.every(item=>!unidentified(item,definition)&&chosen.has(item));
}

/** Say which families a saved selection covers, in a few words. */
export function describeLicences(selected:string[],definition?:LicenceDefinition|null):string{
 if(!definition)return listed(selected);
 const chosen=new Set(selected.map(item=>normalizeLicence(item,definition)));
 const known=new Set(definition.families.flatMap(family=>family.licences.map(item=>item.id)));
 const parts=definition.families.flatMap(family=>{const count=family.licences.filter(item=>chosen.has(item.id)).length;return count===0?[]:count===family.licences.length?[family.name]:[`some ${family.name.toLowerCase()}`];});
 const custom=[...chosen].filter(item=>!known.has(item));
 return listed([...parts,...custom])||'none';
}

/** Family switches with a per-licence checklist and a custom identifier field. */
export function LicenceFamilies({definition,licenses,onChange,copies,noun}:{definition:LicenceDefinition;licenses:string[];onChange:(next:string[])=>void;copies:{licenses:string[]}[];noun:[string,string]}){
 const [extra,setExtra]=useState(''),[invalid,setInvalid]=useState(false);
 const chosen=new Set(licenses.map(item=>normalizeLicence(item,definition)));
 const known=new Set(definition.families.flatMap(family=>family.licences.map(item=>item.id)));
 const custom=[...chosen].filter(item=>!known.has(item));
 const defaults=definition.families.filter(family=>family.default).flatMap(family=>family.licences.map(item=>item.id));
 const order=(next:Set<string>)=>[...definition.families.flatMap(family=>family.licences.map(item=>item.id)).filter(item=>next.has(item)),...[...next].filter(item=>!known.has(item))];
 const set=(ids:string[],on:boolean)=>{const next=new Set(chosen);for(const id of ids){if(on)next.add(id);else next.delete(id);}onChange(order(next));};
 const matching=copies.filter(item=>licenceMatches(item.licenses,licenses,definition)).length;
 function add(){const value=normalizeLicence(extra,definition);if(!value)return;if(unidentified(value,definition)){setInvalid(true);return;}setInvalid(false);set([value],true);setExtra('');}
 return <div className="sk-licence-families">
  <p className="sk-licence-count" role="status"><strong>{matchCount(matching,copies.length,noun)}</strong> Models with no licence, “other” or an unknown licence always stay private.</p>
  <ul>{definition.families.map(family=>{
   const ids=family.licences.map(item=>item.id),count=ids.filter(id=>chosen.has(id)).length,all=count===ids.length,some=count>0&&!all;
   const uses=copies.filter(item=>item.licenses.some(licence=>ids.includes(normalizeLicence(licence,definition)))).length;
   return <li key={family.id} className="sk-licence-family">
    <label className="sk-consent"><input type="checkbox" role="switch" aria-checked={all} checked={all} ref={node=>{if(node)node.indeterminate=some;}} onChange={event=>set(ids,event.target.checked)}/><span><strong>{family.name}</strong>{some&&<> · {count} of {ids.length}</>}<small>{family.summary}{uses>0&&` ${familyUse(uses,noun)}`}</small></span></label>
    <Details summary={`${family.name} licences · ${count} of ${ids.length} on`}>
     <ul className="sk-licence-checklist">{family.licences.map(item=><li key={item.id}><label><input type="checkbox" checked={chosen.has(item.id)} onChange={event=>set([item.id],event.target.checked)}/> <span>{item.name} <code>{item.id}</code>{item.note&&<small>{item.note}</small>}</span></label></li>)}</ul>
    </Details>
   </li>;})}</ul>
  {custom.length>0&&<ul className="sk-community-licences" aria-label="Custom licence identifiers">{custom.map(item=><li key={item} className="sk-licence-chip">{item}<button type="button" aria-label={`Remove ${item}`} onClick={()=>set([item],false)}>×</button></li>)}</ul>}
  <div className="sk-licence-add"><label className="sk-field">Custom licence id<input className="sk-input" value={extra} maxLength={200} placeholder="For example eupl-1.2" aria-invalid={invalid||undefined} onChange={event=>{setExtra(event.target.value);setInvalid(false);}} onKeyDown={event=>{if(event.key==='Enter'){event.preventDefault();add();}}}/></label><button type="button" className="sk-btn sk-outline" disabled={!extra.trim()} onClick={add}>Add</button></div>
  {invalid&&<p className="sk-hint" role="alert">That doesn’t name a licence, so it can’t be added. Copies with it stay private.</p>}
  {(defaults.some(id=>!chosen.has(id))||[...chosen].some(id=>!defaults.includes(id)))&&<button type="button" className="sk-btn sk-outline" onClick={()=>onChange(defaults)}>Use the default licences</button>}
 </div>;
}

const gib=1024**3;
/** Suggested limits when the owner turns limits on. */
export const SUGGESTED_LIMITS:[number,number]=[20*gib,300*gib];

/** Opt-in upload limits: off means no daily or monthly cap; uploads are still counted. */
export function UploadLimits({daily,monthly,onChange,required,disabled}:{daily:number|null;monthly:number|null;onChange:(daily:number|null,monthly:number|null)=>void;required?:ReactNode;disabled?:boolean}){
 const on=daily!==null||monthly!==null;
 const field=(text:string,value:number|null,update:(next:number)=>void)=><label className="sk-field"><span>{text}</span><input className="sk-input" type="number" min={0.001} step="any" disabled={disabled} value={value===null?'':value/gib} onChange={event=>update(Math.round(Number(event.target.value)*gib))}/></label>;
 const invalid=on&&!limitsValid(daily,monthly);
 return <div className="sk-upload-limits">
  <label className="sk-consent"><input type="checkbox" role="switch" aria-checked={on} checked={on} disabled={disabled||Boolean(required)} onChange={event=>event.target.checked?onChange(...SUGGESTED_LIMITS):onChange(null,null)}/><span><strong>Limit uploads</strong><small>{on?'Sharing pauses for the day or the month once a limit is reached, and resumes when it renews.':'No upload limit. Uploads are counted and shown here, but never stopped. Serving popular models can use a lot of bandwidth, and on a metered connection or paid hosting that can cost money.'}</small></span></label>
  {required&&<p className="sk-hint">{required}</p>}
  {on&&<div className="sk-upload-fields">{field('Upload limit per day (GB)',daily,next=>onChange(next,monthly))}{field('Upload limit per month (GB)',monthly,next=>onChange(daily,next))}</div>}
  {invalid&&<Callout tone="bad">Each limit needs a positive value, and the daily limit can’t be larger than the monthly one.</Callout>}
 </div>;
}

/** Whether a pair of limits can be saved: both off, or both positive with daily ≤ monthly. */
export const limitsValid=(daily:number|null,monthly:number|null)=>(daily===null&&monthly===null)||(daily!==null&&monthly!==null&&Number.isSafeInteger(daily)&&Number.isSafeInteger(monthly)&&daily>0&&monthly>0&&daily<=monthly);
