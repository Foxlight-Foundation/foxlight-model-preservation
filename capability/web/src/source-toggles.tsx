import {Help} from './ui';

export type ExtraSource='hub'|'pirate';

/** Extra search sources beside Browse's one search bar: on/off, each saying
 *  where search words go. Turning one on starts nothing until a search. */
export function SourceToggles({hub,pirate,busy,onChange}:{hub:boolean;pirate:boolean;busy:boolean;onChange:(source:ExtraSource,on:boolean)=>void}) {
  return <div className="sk-source-toggles" role="group" aria-label="Also search">
    <span className="sk-meta">Also search</span>
    <span className="sk-source-toggle"><label className="sk-switch"><input type="checkbox" role="switch" aria-checked={hub} checked={hub} disabled={busy} onChange={event=>onChange('hub',event.target.checked)}/><span>Hugging Face</span></label>
      <Help label="About Hugging Face search">Find any public model on Hugging Face, not only the listings saved on this device. Your search words go to huggingface.co. Sovereign still pins each model to one exact version and checks every file before keeping it.</Help></span>
    <span className="sk-source-toggle"><label className="sk-switch"><input type="checkbox" role="switch" aria-checked={pirate} checked={pirate} disabled={busy} onChange={event=>onChange('pirate',event.target.checked)}/><span>Pirate Face</span></label>
      <Help label="About Pirate Face search">Community torrents of Hugging Face models, including models Hugging Face has removed. Your search words go to pirateface.co. Sovereign checks every file against its fingerprint before keeping it.</Help></span>
  </div>;
}
