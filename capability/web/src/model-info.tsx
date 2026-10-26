import {Details,Help,Row} from './ui';

/** Descriptions are captured claims, independent of integrity and runtime trust. */
export interface ModelDetails {
  description?:string|null;publisher?:string|null;repository?:string|null;base_model?:string|null;
  format?:string|null;quantization?:string|null;workloads?:string[];capabilities?:string[];purposes?:string[];
  context_length?:number|null;languages?:string[];license?:string|null;attribution?:string|null;license_notice?:string|null;runtime_requirements?:string[];
  metadata_source?:string;metadata_status?:string;
}
export const purposes:Record<string,{label:string;description:string;words:string}>={
  chat:{label:'Chat & writing',description:'Generates written responses to text prompts.',words:'chat writing write email summarize summarise questions conversation text'},
  coding:{label:'Coding',description:'The saved metadata lists support for working with code.',words:'coding code programming software'},
  images:{label:'Images & editing',description:'Creates or changes images from the inputs described in its card.',words:'image images pictures drawing art edit editing'},
  vision:{label:'Understanding images',description:'The saved metadata lists support for understanding image inputs.',words:'vision understand describe photos photograph visual'},
  speech:{label:'Speech & transcription',description:'Works with spoken audio. Check the listed tasks for the direction it supports.',words:'speech voice audio transcription transcribe translation speak listening'},
  music:{label:'Music & sound',description:'Generates music or sound from prompts.',words:'music sound songs song'},
  video:{label:'Video',description:'Generates video from the text, image or reference inputs listed in its card.',words:'video videos animation animate movie'},
  search:{label:'Search & embeddings',description:'Represents text for uses such as similarity search and document retrieval.',words:'search embedding embeddings similarity retrieval documents'},
};
const taskPurpose:Record<string,string>={textgeneration:'chat',text:'chat',code:'coding',texttoimage:'images',imagetoimage:'images',imagegen:'images',imageedit:'images',vision:'vision',imagetotext:'vision',speechtotext:'speech',speechtranslation:'speech',texttospeech:'speech',stt:'speech',tts:'speech',texttomusic:'music',texttoaudio:'music',texttovideo:'video',imagetovideo:'video',referencetovideo:'video',videogen:'video',textembedding:'search',embedding:'search',featureextraction:'search'};
/** Older hosts still provide useful category labels from their existing tasks. */
export function modelPurposes(details?:ModelDetails,tasks:string[]=[]):string[] {
  return [...new Set([...(details?.purposes??[]),...[...(details?.workloads??tasks),...(details?.capabilities??[])].map(task=>taskPurpose[task.toLowerCase().replace(/[^a-z0-9]/g,'')]).filter(Boolean)])].filter(id=>id in purposes);
}
/** Search only the metadata already on this device; no query leaves the host. */
export function matchesModel(name:string,details:ModelDetails|undefined,query:string,purpose:string):boolean {
  const ids=modelPurposes(details);
  if(purpose&&!ids.includes(purpose))return false;
  const text=[name,JSON.stringify(details??{}),...ids.map(id=>purposes[id].words)].join(' ').toLocaleLowerCase();
  return query.toLocaleLowerCase().split(/\s+/).every(term=>text.includes(term));
}
export function PurposeFilter({id,value,onChange}:{id:string;value:string;onChange:(value:string)=>void}) {
  return <label className="sk-field sk-purpose" htmlFor={id}><span id={`${id}-label`}>What would you like to do?</span><select id={id} aria-labelledby={`${id}-label`} className="sk-input" value={value} onChange={event=>onChange(event.target.value)}><option value="">All model types</option>{Object.entries(purposes).map(([key,item])=><option key={key} value={key}>{item.label}</option>)}</select></label>;
}
/** Human-readable model context, with technical attributes kept available. */
export function ModelInformation({details,tasks=[],format,quantization}:{details?:ModelDetails;tasks?:string[];format?:string;quantization?:string}) {
  const ids=modelPurposes(details,tasks),known=!['unavailable','partial'].includes(details?.metadata_status??'');
  const workloads=details?.workloads??tasks;
  return <div className="sk-model-info">
    <div className="sk-purpose-tags">{ids.length?ids.map(id=><span className="sk-tag" key={id}>{purposes[id].label}</span>):<span className="sk-hint">Purpose not described</span>}<Help label="About model descriptions">These are descriptions and capabilities stated in the saved card or archive. Checking a signature does not prove their quality, safety or compatibility with your device.</Help></div>
    <p className="sk-model-description">{details?.description||ids.slice(0,2).map(id=>purposes[id].description).join(' ')||'This copy has no readable description yet. Its original documents stay with the archive.'}</p>
    {details?.attribution&&<p className="sk-meta">Model attribution: {details.attribution}</p>}
    {details?.license_notice&&<p className="sk-hint">License note: {details.license_notice}</p>}
    <Details summary="About this model">
      {!known&&<p>Some saved model details could not be read. Your copy is kept; restoring and exporting still perform their usual verification.</p>}
      <Row k="Model source">{details?.repository||details?.publisher||'Not listed in the saved metadata'}</Row>
      {details?.base_model&&<Row k="Based on">{details.base_model}</Row>}
      {workloads.length>0&&<Row k="Declared tasks">{workloads.map(task=>task.replace(/([a-z])([A-Z])/g,'$1 $2').replaceAll('-',' ')).join(', ')}</Row>}
      {(details?.format||format)&&<Row k={<>File format <Help label="About file formats">The app you use to run a model must support this format. Saving a model in Sovereign does not install a compatible runtime.</Help></>}>{details?.format||format}</Row>}
      {(details?.quantization||quantization)&&<Row k={<>Weight precision <Help label="About weight precision">This identifies how the model weights are stored. Different variants can have different file sizes and quality. It is not a device compatibility rating.</Help></>}>{details?.quantization||quantization}</Row>}
      {details?.context_length!=null&&<Row k={<>Text context <Help label="About text context">The card’s stated maximum amount of text considered at once, measured in tokens (small pieces of text). A compatible app and enough memory are still needed.</Help></>}>{details.context_length.toLocaleString()} tokens, stated in card</Row>}
      {Boolean(details?.languages?.length)&&<Row k="Listed languages">{details?.languages?.join(', ')}</Row>}
      <Row k="License">{details?.license||'Not listed here. Check the model’s included license and terms before using or sharing it.'}</Row>
      {Boolean(details?.runtime_requirements?.length)&&<><p className="sk-sublabel">Notes about running this model</p><ul>{details?.runtime_requirements?.map((item,index)=><li key={index}>{item}</li>)}</ul></>}
      <p className="sk-hint">A saved model is a set of files, not a running app. Restore its files to use them with a compatible model app. Sovereign has not checked whether it runs on this device.</p>
    </Details>
  </div>;
}
