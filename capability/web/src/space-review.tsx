import {bytes} from './client';
import {Callout,Row} from './ui';

interface WorkingSpace {required_free_bytes?:number;free_bytes?:number}

/** The host observes the reserve; the worker independently enforces it again. */
export function insufficientWorkingSpace(value:WorkingSpace):boolean {
  return value.required_free_bytes!=null&&value.free_bytes!=null&&value.free_bytes<value.required_free_bytes;
}

/** Explain temporary storage equally for registry and independent-source copies. */
export function WorkingSpaceReview({value}:{value:WorkingSpace}) {
  if(value.required_free_bytes==null||value.free_bytes==null)return null;
  return <><Row k="Free space needed during download">{bytes(value.required_free_bytes)}</Row><Row k="Available on this device at review">{bytes(value.free_bytes)}</Row><p className="sk-hint">Extra space keeps working copies separate while Sovereign checks the complete model. Space is checked again before work starts.</p>{insufficientWorkingSpace(value)&&<Callout tone="warn">There is not enough working space on this device. Free space or choose a smaller complete model, then review again. No download has started.</Callout>}</>;
}
