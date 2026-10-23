import {useState} from 'react';
import {bytes,type Job,type Page} from './client';
import {Consent,Icon,Label,Mono,Sheet} from './ui';

/** Counts are logical regular-file lengths, never a promise of allocated/free space. */
export interface TemporaryWork {job_id:string;found:boolean;logical_bytes:number;files:number;complete:boolean;removed:boolean}

/** Show only exact stopped jobs that Core reports as having possible leftover work. */
export function TemporaryJobs({page,busy,onReview}:{page:Page;busy:boolean;onReview:(job:Job)=>void}) {
  const jobs=page.jobs.filter(job=>page.temporary_work_jobs?.includes(job.job_id));
  if(!jobs.length)return null;
  return <section aria-label="Unused temporary files"><Label as="h2">Unused temporary files</Label><p className="sk-hint sk-pad">A stopped job may have left working files behind. Review them before clearing space.</p><div className="sk-cards">{jobs.map(job=><article className="sk-card sk-job" key={job.job_id} aria-label={`Temporary files for ${job.model_id}`}><div className="sk-job-head"><span className="sk-job-kind"><Icon name="trash"/></span><div className="sk-model-title"><h3>{job.model_id}</h3><p className="sk-meta">{job.kind==='mirror_import'?'Public import':job.kind==='http_share'?'HTTP mirror':'BitTorrent'} · {job.state} · job <span className="sk-num">{job.job_id.slice(0,12)}</span></p></div><button className="sk-btn sk-outline" disabled={busy} onClick={()=>onReview(job)}>Review temporary files</button></div></article>)}</div></section>;
}

/** Fresh acknowledgement clears staging only; archives, copies and metainfo stay kept. */
export function TemporaryFilesReview({job,report,busy,onBack,onClear}:{job:Job;report:TemporaryWork;busy:boolean;onBack:()=>void;onClear:()=>void}) {
  const [accept,setAccept]=useState(false);
  return <Sheet kicker="Free space" title="Clear this stopped job’s temporary files?" subtitle={job.model_id} back="Back to Activity" onClose={onBack}
    footer={<><span className="sk-foot-note">{report.found?accept?'Only this job’s working files are removed.':'Confirm above to continue.':'Nothing to clear.'}</span><button className="sk-btn sk-solid" disabled={busy||!report.found||!accept} onClick={onClear}>{busy?'Clearing…':'Clear temporary files'}</button></>}>
    <div className="sk-summary sk-big"><p className="sk-big-value">{report.found?<>{report.complete?'':'At least '}<span className="sk-num">{bytes(report.logical_bytes)}</span> in {report.files} regular {report.files===1?'file':'files'}</>:'No temporary files remain for this job.'}</p><p className="sk-hint">File sizes describe working data, not guaranteed freed space.</p></div>
    <p className="sk-fine"><Icon name="lock" size={14}/><span>Your saved copies, model files, signing keys, recovery kits and torrent metadata are kept.</span></p>
    <div className="sk-keys"><Mono value={report.job_id} label="job ID"/></div>
    {report.found&&<Consent checked={accept} onChange={setAccept}>Clear only this stopped job’s temporary files</Consent>}
  </Sheet>;
}
