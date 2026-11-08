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
  return <><Row k="Free space needed while saving">{bytes(value.required_free_bytes)}</Row><Row k="Free space on this computer">{bytes(value.free_bytes)}</Row><p className="sk-hint">While saving, Sovereign keeps a temporary copy so it can check the download before it replaces anything. The extra room is freed when it finishes.</p>{insufficientWorkingSpace(value)&&<Callout tone="warn">There is not enough working space on this device. Free space or choose a smaller complete model, then review again. No download has started.</Callout>}</>;
}
