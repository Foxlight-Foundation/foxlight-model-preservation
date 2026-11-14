import {bytes} from './client';
import {minutes,useRate} from './download-progress';

export type StageName='download'|'sign'|'store'|'check';
export interface SaveStage {name:StageName;state:'done'|'active'|'waiting';done:number;total:number|null}

const names:Record<StageName,string>={download:'Download',sign:'Check & sign',store:'Store',check:'Final check'};
const doing:Record<StageName,string>={
 download:'Downloading the files from their source',
 sign:'Checking every file against its recorded hash and signing the copy',
 store:'Storing the signed copy in your Library',
 check:'Checking the stored copy once more before it’s kept',
};
const R=20,C=2*Math.PI*R;

/** A ring with its number inside: a tick when done, the percentage while
 *  running (or the step number while waiting). Without a known total it
 *  spins, so a running stage never looks still. */
function Ring({stage,index,percent}:{stage:SaveStage;index:number;percent:number|null}){
 const fill=stage.state==='done'?1:stage.state==='active'&&percent!==null?percent/100:0;
 const spinning=stage.state==='active'&&percent===null;
 return <span className="sk-ring" data-spinning={spinning||undefined}>
  <svg viewBox="0 0 48 48" aria-hidden="true">
   <circle className="sk-ring-track" cx="24" cy="24" r={R}/>
   <circle className="sk-ring-fill" cx="24" cy="24" r={R} strokeDasharray={C} strokeDashoffset={spinning?C*0.7:C*(1-fill)} transform="rotate(-90 24 24)"/>
  </svg>
  <span className="sk-ring-mid">{stage.state==='done'?<svg viewBox="0 0 24 24" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>:stage.state==='active'?(percent!==null?`${percent}%`:'…'):index+1}</span>
 </span>;
}

const percentOf=(stage:SaveStage)=>stage.state==='done'?100:stage.state==='waiting'?0:stage.total?
 // A running stage never claims 100% until the next one starts.
 Math.min(99,Math.floor(Math.min(stage.done,stage.total)/stage.total*100)):null;

/** The running stage in words and numbers; keyed by stage, so its rate and
 *  time left start again with each stage. */
function Active({stage,index,count}:{stage:SaveStage;index:number;count:number}){
 const rate=useRate(stage.done);
 const percent=percentOf(stage);
 const left=stage.total&&rate?minutes(Math.max(0,stage.total-stage.done)/rate):null;
 const amount=stage.name==='download'
  ?(stage.total?`${bytes(Math.min(stage.done,stage.total))} of ${bytes(stage.total)}`:`${bytes(stage.done)} received`)
  :(stage.total?null:`${bytes(stage.done)} processed`);
 const speed=stage.name==='download'&&rate?`${bytes(rate)}/s`:null;
 return <>
  <p className="sk-stage" role="status">Step {index+1} of {count}: {doing[stage.name]}…</p>
  <p className="sk-meta">{[percent!==null&&`${percent}%`,amount,left,speed].filter(Boolean).join(' · ')||'Starting…'}</p>
  {stage.name!=='download'&&<p className="sk-meta">Before the copy is kept, every byte is checked, signed, stored and checked again. Each step reads the whole model, so a large model takes a while. You can leave this running.</p>}
 </>;
}

/** Every stage of a save, each with its own ring and percentage, the active
 *  one explained: "Step 2 of 4 … 40% · about 6 min left". */
export function StageRings({stages}:{stages:SaveStage[]}){
 const index=stages.findIndex(stage=>stage.state==='active');
 return <div className="sk-stages">
  <ol aria-label="Progress by stage">{stages.map((stage,i)=>{const value=percentOf(stage);return <li key={stage.name} data-state={stage.state} aria-current={stage.state==='active'?'step':undefined}>
   <span role="progressbar" aria-label={names[stage.name]} aria-valuemin={0} aria-valuemax={100} aria-valuenow={value??undefined} aria-valuetext={stage.state==='done'?'Done':stage.state==='waiting'?'Waiting':value!==null?`${value}%`:'In progress'}><Ring stage={stage} index={i} percent={value}/></span>
   <span className="sk-stage-name">{names[stage.name]}</span>
  </li>;})}</ol>
  {index>=0?<Active key={stages[index].name} stage={stages[index]} index={index} count={stages.length}/>
   :<p className="sk-stage" role="status">Waiting for earlier work to finish…</p>}
 </div>;
}
