import {useEffect,useState} from 'react';
import {api} from './client';
import {Callout} from './ui';

/** The saved model a "Help keep this model available?" answer opened sharing for. */
export interface KeepAsk {copy_id:string;name:string}
interface Standing {consented:boolean;copies:{copy_id:string;status:string}[]}

/**
 * Asked once, after a save, while sharing is off: only for a model whose
 * licence already allows sharing. Agreeing opens the same sharing review;
 * nothing is shared until the person agrees there.
 */
export function KeepAvailableAsk({copyId,busy,onReview,onNotNow}:{copyId:string;busy:boolean;onReview:()=>void;onNotNow:()=>void}) {
 const [shareable,setShareable]=useState(false);
 useEffect(()=>{let active=true;setShareable(false);void api<Standing>('desktop-community-read',{}).then(value=>{if(active)setShareable(!value.consented&&value.copies.some(item=>item.copy_id===copyId&&item.status==='eligible'));}).catch(()=>{});return()=>{active=false;};},[copyId]);
 if(!shareable)return null;
 return <Callout tone="info"><strong>Help keep this model available?</strong> Its licence allows sharing. This computer can share it with people who want their own verified copy, even if it disappears from where you found it. Nothing is shared until you review what that involves and agree.<div className="sk-actions"><button className="sk-btn sk-solid" disabled={busy} onClick={onReview}>Review sharing</button><button className="sk-btn sk-quiet" disabled={busy} onClick={onNotNow}>Not now</button></div></Callout>;
}
