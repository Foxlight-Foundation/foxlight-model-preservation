import {Details,Help,Row} from './ui';

/** Descriptions are captured claims, independent of integrity and runtime trust. */
export interface ModelDetails {
  description?:string|null;publisher?:string|null;repository?:string|null;base_model?:string|null;
  format?:string|null;quantization?:string|null;workloads?:string[];capabilities?:string[];purposes?:string[];
  context_length?:number|null;languages?:string[];license?:string|null;attribution?:string|null;license_notice?:string|null;runtime_requirements?:string[];
  metadata_source?:string;metadata_status?:string;
  feature_ids?:string[];input_modalities?:string[];output_modalities?:string[];
  claim_sources?:string[];artifact_notes?:string[];
  analysis_notes?:string[];
}
export const purposes:Record<string,{label:string;description:string;words:string}>={
  chat:{label:'Chat & writing',description:'Generates written responses to text prompts.',words:'chat writing write email summarize summarise questions conversation text'},
  coding:{label:'Coding',description:'The saved metadata lists support for working with code.',words:'coding code programming software'},
  images:{label:'Images & editing',description:'Creates or changes images from the inputs described in its card.',words:'image images pictures drawing art edit editing'},
  vision:{label:'Understanding images',description:'Uses image inputs to produce written descriptions or answers.',words:'vision understand describe photos photograph visual pictures image screenshot text questions'},
  speech:{label:'Speech & transcription',description:'Works with spoken audio. Check the listed tasks for the direction it supports.',words:'speech voice audio transcription transcribe translation speak listening'},
  music:{label:'Music & sound',description:'Generates music or sound from prompts.',words:'music sound songs song'},
  video:{label:'Video generation',description:'Creates video from the inputs listed in its metadata.',words:'video videos animation animate movie'},
  search:{label:'Search & embeddings',description:'Represents text for uses such as similarity search and document retrieval.',words:'search embedding embeddings similarity retrieval documents'},
  reasoning:{label:'Reasoning & problem solving',description:'Lists support for working through problems that take several steps.',words:'reasoning thinking solve problems problem solving logic mathematics math'},
  'video-understanding':{label:'Understanding video',description:'Uses video inputs to produce written descriptions or answers.',words:'understand video videos clips questions text visual'},
};
const taskPurpose:Record<string,string[]>={textgeneration:['chat'],textgenerate:['chat'],text:['chat'],imagetexttotext:['chat','vision'],code:['coding'],codegeneration:['coding'],textcode:['coding'],texttoimage:['images'],imagetoimage:['images'],imagegen:['images'],imagegenerate:['images'],imageedit:['images'],vision:['vision'],visionunderstand:['vision'],visualquestionanswering:['vision'],imagetotext:['vision'],speechtotext:['speech'],speechtranslation:['speech'],texttospeech:['speech'],stt:['speech'],tts:['speech'],texttomusic:['music'],texttoaudio:['music'],texttovideo:['video'],imagetovideo:['video'],referencetovideo:['video'],videogen:['video'],videogenerate:['video'],textembedding:['search'],textembed:['search'],embedding:['search'],reranking:['search'],featureextraction:['search'],thinking:['reasoning'],reasoning:['reasoning'],textreason:['reasoning'],videotexttotext:['chat','video-understanding'],videototext:['video-understanding'],videounderstand:['video-understanding']};
const normalize=(value:string)=>value.toLowerCase().replace(/[^a-z0-9]/g,'');
export const modalityLabels:Record<string,string>={text:'written text',image:'pictures',video:'video',audio:'audio',embedding:'information for document search',scores:'relevance scores'};
const sourceLabels:Record<string,string>={upstream_structured:'Source model information',artifact_manifest:'Files listed by the source',agent_analysis:'Automated interpretation'};
/** Older hosts still provide useful category labels from their existing tasks. */
export function modelPurposes(details?:ModelDetails,tasks:string[]=[]):string[] {
  // New hosts classify complete metadata before bounding their excerpts.
  if(details?.feature_ids!==undefined)return [...new Set(details.purposes??[])].filter(id=>Object.hasOwn(purposes,id));
  const workloads=details?.workloads??tasks;
  const declarations=[...workloads,...(details?.capabilities??[]).filter(item=>normalize(item)!=='text'||!workloads.length||workloads.some(task=>['textgeneration','imagetexttotext'].includes(normalize(task))))];
  return [...new Set([...(details?.purposes??[]),...declarations.flatMap(task=>Object.hasOwn(taskPurpose,normalize(task))?taskPurpose[normalize(task)]:[])])].filter(id=>Object.hasOwn(purposes,id));
}
/** Describe captured uses, never a model name, quality rating or runtime test. */
function descriptionFor(details:ModelDetails|undefined,ids:string[]):string {
  if(details?.description)return details.description;
  const features=details?.feature_ids??[];
  let description='';
  if(ids.includes('vision')&&ids.includes('chat'))description='Can work with pictures as well as text, and respond in writing. Useful for asking questions about images and discussing written material.';
  else if(ids.includes('video-understanding'))description='Uses video inputs to produce written descriptions or answers. Useful for asking questions about a clip.';
  else if(ids.includes('vision'))description=purposes.vision.description;
  else if(features.includes('image-generate')&&features.includes('image-edit'))description='Creates pictures from written descriptions and can change existing images.';
  else if(features.includes('image-edit'))description='Changes an existing picture using the inputs described in its metadata.';
  else if(features.includes('image-generate'))description=details?.input_modalities?.includes('text')?'Creates pictures from written descriptions.':'Produces pictures from the inputs listed below.';
  else if(features.includes('speech-transcribe')&&features.includes('speech-synthesize'))description='Can turn spoken audio into text and read written text aloud.';
  else if(features.includes('speech-transcribe'))description='Turns spoken audio into written text.';
  else if(features.includes('speech-translate'))description='Translates spoken audio into written text.';
  else if(features.includes('speech-synthesize'))description='Reads written text aloud as speech.';
  else if(features.includes('audio-understand'))description='Works with audio recordings and gives written responses.';
  else if(features.includes('embed'))description='Helps an app find documents with similar meaning, for search and retrieval.';
  else if(features.includes('rerank'))description='Helps an app order search results by relevance.';
  else if(features.includes('video-generate'))description=details?.output_modalities?.includes('audio')?'Creates video with audio from the inputs listed below.':'Creates video from the inputs listed below.';
  else if(features.includes('sound-generate'))description='Creates music or sound from the inputs listed below.';
  else description=ids.slice(0,2).map(id=>purposes[id].description).join(' ');
  if(ids.includes('reasoning')&&!description.includes(purposes.reasoning.description))description+=' Its metadata also lists reasoning support for working through problems.';
  return description.trim();
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
export function ModelInformation({details,tasks=[],format,quantization,installed=false}:{details?:ModelDetails;tasks?:string[];format?:string;quantization?:string;installed?:boolean}) {
  const fromListing=details?.metadata_source==='source-listing';
  const ids=modelPurposes(details,tasks),known=!['unavailable','partial'].includes(details?.metadata_status??'');
  const workloads=details?.workloads??tasks;
  const description=descriptionFor(details,ids);
  const inputs=(details?.input_modalities??[]).filter(item=>Object.hasOwn(modalityLabels,item)),outputs=(details?.output_modalities??[]).filter(item=>Object.hasOwn(modalityLabels,item));
  const sources=(details?.claim_sources??[]).filter(item=>Object.hasOwn(sourceLabels,item)),notes=details?.artifact_notes??[];
  return <div className="sk-model-info">
    <div className="sk-purpose-tags">{ids.length?ids.map(id=><span className="sk-tag" key={id}>{purposes[id].label}</span>):<span className="sk-hint">Purpose not described</span>}<Help label="About model descriptions">{fromListing?'These descriptions come from this source’s listing. They do not amend the signed model card or establish registry trust.':'These uses come from the captured model card and structured source information. Additional automated review notes are labeled separately. These descriptions are not tests of quality, safety or compatibility with your device. A signature authenticates the saved information; it does not prove every claim.'}</Help></div>
    <p className="sk-model-description">{description||(installed?'No readable description is available for this installation.':fromListing?'This source has not supplied a readable description. Review its signed model details before downloading.':'This copy has no readable description yet. Its original documents stay with the archive.')}</p>
    {Boolean(inputs.length||outputs.length)&&<div className="sk-model-io" aria-label="Model inputs and outputs">{inputs.length>0&&<span><strong>Works with:</strong> {inputs.map(item=>modalityLabels[item]).join(', ')}</span>}{outputs.length>0&&<span><strong>Produces:</strong> {outputs.map(item=>modalityLabels[item]).join(', ')}</span>}<Help label="About model inputs and outputs">These are input and output types declared in the captured metadata. Understanding a picture and creating a picture are different capabilities. A compatible model app and any required companion files are still needed.</Help></div>}
    {notes.includes('vision-companion-unlisted')?<p className="sk-capability-note" data-tone="live"><span>Image companion not listed · text only unless you add it</span><Help label="About image companion files" title="Companion file">An extra file some models need for a task. Image understanding is reported, but this model reads pictures only with its image companion (an image projector), which is not listed with this download. Without it, a saved copy is for text only. Review the complete download before saving it for use with pictures; a file being available at the original host does not mean it comes with this download.</Help></p>:notes.includes('vision-files-unconfirmed')&&<p className="sk-capability-note">Image understanding is reported. This listing does not confirm which files are needed for picture inputs.</p>}
    {notes.includes('artifact-incomplete')&&<p className="sk-capability-note">Some reported capabilities are marked incomplete for this file version. Review their required files before relying on those capabilities.</p>}
    {details?.attribution&&<p className="sk-meta">Model attribution: {details.attribution}</p>}
    {details?.license_notice&&<p className="sk-hint">License note: {details.license_notice}</p>}
    <Details summary="About this model">
      {!known&&<p>{installed?'Some installed model details could not be read. Preserve still checks the complete installed record.':fromListing?'Some source details are unavailable. Review the signed model details before downloading.':'Some saved model details could not be read. Your copy is kept; restoring and exporting still perform their usual verification.'}</p>}
      <Row k="Model source">{details?.repository||details?.publisher||'Not listed in the saved metadata'}</Row>
      {details?.base_model&&<Row k="Based on">{details.base_model}</Row>}
      {workloads.length>0&&<Row k="Declared tasks">{workloads.map(task=>task.replace(/([a-z])([A-Z])/g,'$1 $2').replaceAll('-',' ')).join(', ')}</Row>}
      {sources.length>0&&<Row k={<>Capability information <Help label="About capability information">These labels describe where the captured claims came from. A source can list files at the original host without proving this selected download includes every file. Automated interpretations can be mistaken.</Help></>}>{sources.map(item=>sourceLabels[item]).join('; ')}</Row>}
      {sources.includes('agent_analysis')&&<p className="sk-hint">Automated interpretations are kept with the original documents. The capability labels above use the model card and structured source information.</p>}
      {Boolean(details?.analysis_notes?.length)&&<Details summary="Automated review notes"><p className="sk-hint">These notes are unverified interpretations. They may describe internal components or contain mistakes.</p>{details?.analysis_notes?.map((note,index)=><p key={index}>{note}</p>)}</Details>}
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
