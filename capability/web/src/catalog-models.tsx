import {useState} from 'react';
import {bytes} from './client';
import {ModelInformation,modalityLabels,type ModelDetails} from './model-info';
import {Details,Help,Mono} from './ui';

export interface CatalogListing {details?:ModelDetails;download_support?:string;selection?:string;file_label?:string|null;publisher?:string;card_id:string;name:string;format:string;quantization:string;size_bytes:number|null;workloads:string[];complete_file_map:boolean}

const sentence=(items:string[]|undefined)=>{const words=(items??[]).filter(item=>Object.hasOwn(modalityLabels,item)).map(item=>modalityLabels[item]).join(', ');return words?words[0].toUpperCase()+words.slice(1):'';};

/** Summary of the result in focus; the card itself keeps every detail and the review action. */
function SelectedModel({variants}:{variants:CatalogListing[]}) {
  const first=variants[0],inputs=sentence(first.details?.input_modalities),outputs=sentence(first.details?.output_modalities);
  const version=variants.length===1?[first.format.toUpperCase(),first.quantization,first.size_bytes?bytes(first.size_bytes):''].filter(Boolean).join(' · '):`${variants.length} file versions`;
  return <aside className="sk-selected" aria-label="Selected model summary"><p className="sk-label">Selected</p><p className="sk-selected-name">{first.name}</p>
    <dl>{inputs&&<><dt>Works with</dt><dd>{inputs}</dd></>}{outputs&&<><dt>Produces</dt><dd>{outputs}</dd></>}<dt>Version</dt><dd>{version}</dd>{first.publisher&&<><dt>Publisher</dt><dd>{first.publisher} · signed listing</dd></>}</dl>
    <p>A saved copy is a set of files, not a running model. Sovereign does not check whether it runs on your device.</p></aside>;
}

/** Group only matching descriptions; every file variant keeps its exact review. */
export function CatalogModels({models,busy,onReview,explainError}:{models:CatalogListing[];busy:boolean;onReview:(model:CatalogListing)=>void;explainError:(code:string)=>string}) {
  const [chosen,setChosen]=useState<string|null>(null);
  const groups=new Map<string,CatalogListing[]>();
  for(const model of models){const common={...model.details,format:null,quantization:null};const key=JSON.stringify([model.name,model.publisher,model.workloads,common]);const existing=groups.get(key);if(existing)existing.push(model);else groups.set(key,[model]);}
  const selected=chosen!==null&&groups.has(chosen)?chosen:[...groups.keys()][0];
  return <div className="sk-results"><div className="sk-cards">{[...groups].map(([key,variants])=>{
    const first=variants[0],multiple=variants.length>1;
    const sizes=variants.map(model=>model.size_bytes).filter((size):size is number=>size!=null&&size>0);
    const choices=<div className="sk-variants">{variants.map(model=><section className="sk-variant" key={model.card_id} aria-label={`${model.format} ${model.quantization} version`}>
        <div className="sk-variant-heading"><h3>{model.format.toUpperCase()}{model.quantization&&` · ${model.quantization}`} <Help label="About this file variant">This is the file format and weight-precision label from the card. Use a variant supported by your model app. Smaller files are not a guarantee that the model will run on your device.</Help></h3><span className="sk-meta">{model.size_bytes?`${bytes(model.size_bytes)} main files`:'Size not listed'}</span></div>
        {model.file_label&&<p className="sk-hint sk-wrap">{model.file_label}</p>}
        <p className="sk-hint">Required companion files and the complete total are shown before you download. <Help label="About download size">File size is storage space, not the memory needed to run the model. The final review includes required companion files.</Help></p>
        <div className="sk-actions"><button className="sk-btn sk-solid" disabled={busy||Boolean(model.download_support&&model.download_support!=='ready')} onClick={()=>onReview(model)}>Keep a copy</button>{model.download_support&&model.download_support!=='ready'&&<p className="sk-hint">{explainError(model.download_support)}</p>}</div>
        <Details summary="Verification details"><p>This listing was authenticated against Foxlight’s saved catalog. That checks where the card came from; it does not rate model quality or prove it will run on your device.</p><Mono value={model.card_id} label="card identity"/><p>Keep this fingerprint when you need to compare the exact card with a trusted source. The complete download is reviewed separately.</p></Details>
      </section>)}</div>;
    const isSelected=key===selected;
    return <article key={key} className="sk-card sk-result" data-selected={isSelected?'true':undefined} onFocusCapture={()=>setChosen(key)} onPointerDown={()=>setChosen(key)}>
      <div className="sk-job-head"><span className="sk-result-mark" aria-hidden="true">{isSelected?'✦':'·'}</span><div className="sk-model-title"><h2>{first.name}</h2><p className="sk-meta">{first.publisher&&<>{first.publisher} · </>}<span className="sk-signed">signed listing</span></p></div>{sizes.length>0&&<span className="sk-size">{multiple?`from ${bytes(Math.min(...sizes))}`:bytes(sizes[0])}</span>}</div>
      <ModelInformation details={{...first.details,format:null,quantization:null}} tasks={first.workloads}/>
      {multiple&&<p className="sk-hint">{variants.length} file versions on this page{sizes.length?` · main files from ${bytes(Math.min(...sizes))}`:''}. Choose one supported by the app you plan to use. <Help label="About model versions">These are separate file variants of the same model description, not extra models you need to download together. Precision and file format can affect size and compatibility. Sovereign does not recommend a variant or run it on your device.</Help></p>}
      {multiple?<Details summary={`Choose a file version · ${variants.length} on this page`}>{choices}</Details>:choices}
    </article>;
  })}</div>{selected!==undefined&&<SelectedModel key={selected} variants={groups.get(selected)!}/>}</div>;
}
