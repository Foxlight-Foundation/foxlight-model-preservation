import {useState,type ReactNode} from 'react';
import {api} from './client';
import {HubToken} from './hub-find';
import {LibraryLocation,type LibraryState} from './library-location';
import {Callout,Icon,Mono,Pill} from './ui';

export interface SettingsPage {library?:LibraryState;hub_search?:boolean;hub_token?:string|null;pirate_face_search?:boolean;pirate_face_swarm?:boolean}

/** One choice with its plain explanation, current state and a single switch. */
function Choice({title,on,busy,children,onLabel,offLabel,explainError,onChange}:{title:string;on:boolean;busy:boolean;children:ReactNode;onLabel:string;offLabel:string;explainError:(code:string)=>string;onChange:(next:boolean)=>Promise<void>}) {
  const [working,setWorking]=useState(false),[error,setError]=useState('');
  async function change(next:boolean){setWorking(true);setError('');try{await onChange(next);}catch(err){setError(err instanceof Error?err.message:'host_unavailable');}finally{setWorking(false);}}
  return <article className="sk-card sk-setting" aria-label={title}>
    <div className="sk-model-head"><div className="sk-model-title"><h3>{title}</h3></div><Pill tone={on?'ok':'muted'}>{on?'On':'Off'}</Pill></div>
    {children}
    <div className="sk-actions"><button className={`sk-btn ${on?'sk-quiet':'sk-outline'}`} disabled={busy||working} onClick={()=>void change(!on)}>{on?offLabel:onLabel}</button></div>
    {error&&<Callout tone="bad" role="alert">Your choice wasn’t saved. {explainError(error)}</Callout>}
  </article>;
}

/** Every setting in one place: storage, search sources and privacy, sharing and appearance. */
export function SovereignSettings({page,busy,theme,registryFingerprint,backups,explainError,onToggleTheme,onSharing,onChange}:{page:SettingsPage|undefined;busy:boolean;theme:string;registryFingerprint?:string|null;backups:ReactNode;explainError:(code:string)=>string;onToggleTheme:()=>void;onSharing:()=>void;onChange:()=>Promise<void>}) {
  async function set(action:string,enabled:boolean){await api(action,{enabled});await onChange();}
  return <section aria-label="Settings">
    <div className="sk-page-head"><div><h1>Settings</h1><p>Where Sovereign keeps your models, where it looks for them and what it shares. Nothing here starts a download or sharing by itself.</p></div></div>

    <h2 className="sk-section-title" id="settings-storage">Storage</h2>
    <LibraryLocation library={page?.library} busy={busy} onChange={onChange}/>
    {backups}

    <h2 className="sk-section-title" id="settings-sources">Search sources and privacy</h2>
    <p className="sk-hint sk-pad">Foxlight’s public registry is a built-in source: updating listings contacts registry.foxlight.ai, and searching them stays on this device. Downloads contact the model’s host and its public CDNs, which can see your address and the files requested. Finding models community mirrors keep reads their signed lists; those mirrors can see your address, and your search words stay on this device.</p>
    <div className="sk-cards">
      <Choice title="Search Hugging Face" on={Boolean(page?.hub_search)} busy={busy} onLabel="Search Hugging Face too" offLabel="Stop searching Hugging Face" explainError={explainError} onChange={next=>set('desktop-hub-consent',next)}>
        <p>Find any public model on Hugging Face, not only saved listings. Your search words go to huggingface.co.</p>
      </Choice>
      <article className="sk-card sk-setting" aria-label="Hugging Face access token"><div className="sk-model-head"><div className="sk-model-title"><h3>Hugging Face access token</h3></div><Pill tone={page?.hub_token?'ok':'muted'}>{page?.hub_token?'Added':'None'}</Pill></div><HubToken user={page?.hub_token??null} busy={busy} explainError={explainError} onChange={onChange}/></article>
      <Choice title="Search Pirate Face" on={Boolean(page?.pirate_face_search)} busy={busy} onLabel="Search Pirate Face too" offLabel="Stop searching Pirate Face" explainError={explainError} onChange={next=>set('desktop-pirate-face-consent',next)}>
        <p>Find community torrents of Hugging Face models, including models Hugging Face has removed. Your search words go to pirateface.co.</p>
      </Choice>
      <Choice title="Swarm downloads" on={Boolean(page?.pirate_face_swarm)} busy={busy} onLabel="Turn on swarm downloads" offLabel="Turn off swarm downloads" explainError={explainError} onChange={next=>set('desktop-pirate-face-swarm',next)}>
        <p>Keep models from the Pirate Face swarm. Peers in a public swarm can see this computer’s address. Sovereign only downloads from them: it never shares from the swarm, and nothing keeps running after a save.</p>
      </Choice>
    </div>
    <p className="sk-hint sk-pad">Saving or restoring a model never runs it or grants permission to execute repository code, and receiving never starts sharing.</p>
    {registryFingerprint&&<Mono value={registryFingerprint} label="registry root fingerprint"/>}

    <h2 className="sk-section-title" id="settings-sharing">Sharing</h2>
    <article className="sk-card"><p><strong>Help keep models available</strong>, its licences, upload limits, public address and cloud storage live together under Sharing.</p><div className="sk-actions"><button className="sk-btn sk-outline" onClick={onSharing}><Icon name="share"/>Open sharing settings</button></div></article>

    <h2 className="sk-section-title" id="settings-appearance">Appearance</h2>
    <article className="sk-card sk-setting" aria-label="Theme"><div className="sk-model-head"><div className="sk-model-title"><h3>Theme</h3></div><Pill tone="muted">{theme==='night'?'Dark':'Light'}</Pill></div><div className="sk-actions"><button className="sk-btn sk-outline" onClick={onToggleTheme}><Icon name={theme==='night'?'sun':'moon'}/>{theme==='night'?'Use the light theme':'Use the dark theme'}</button></div></article>
  </section>;
}
