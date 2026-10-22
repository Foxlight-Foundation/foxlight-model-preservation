import {useState} from 'react';
import {bytes,type Job,type Page} from './client';

/** Counts are logical regular-file lengths, never a promise of allocated/free space. */
export interface TemporaryWork {job_id:string;found:boolean;logical_bytes:number;files:number;complete:boolean;removed:boolean}

/** Show only exact stopped jobs that Core reports as having possible leftover work. */
export function TemporaryJobs({page,busy,onReview}:{page:Page;busy:boolean;onReview:(job:Job)=>void}) {
  const jobs=page.jobs.filter(job=>page.temporary_work_jobs?.includes(job.job_id));
  if(!jobs.length)return null;
  return <section aria-label="Unused temporary files"><h3>Unused temporary files</h3><p className="mp-muted">A stopped job may have left working files behind. Review them before clearing space.</p>{jobs.map(job=><div className="mp-summary" key={job.job_id}><strong>{job.model_id}</strong><p className="mp-small">{job.kind==='mirror_import'?'Public import':job.kind==='http_share'?'HTTP mirror':'BitTorrent'} · {job.state} · job {job.job_id.slice(0,12)}</p><button className="mp-button" disabled={busy} onClick={()=>onReview(job)}>Review temporary files</button></div>)}</section>;
}

/** Fresh acknowledgement clears staging only; archives, copies and metainfo stay kept. */
export function TemporaryFilesReview({job,report,busy,onBack,onClear}:{job:Job;report:TemporaryWork;busy:boolean;onBack:()=>void;onClear:()=>void}) {
  const [accept,setAccept]=useState(false);
  return <section className="mp-flow" aria-label="Review temporary files"><button className="mp-button mp-quiet" onClick={onBack}>← Back to Activity</button><span className="mp-kicker">Free space</span><h2>Clear this stopped job’s temporary files?</h2><p>{job.model_id}</p><div className="mp-summary"><p>{report.found?`${report.complete?'':'At least '}${bytes(report.logical_bytes)} in ${report.files} regular ${report.files===1?'file':'files'}`:'No temporary files remain for this job.'}</p><p className="mp-small mp-muted">File sizes describe working data, not guaranteed freed space. Your saved copies, model files, signing keys, recovery kits and torrent metadata are kept.</p><code className="mp-key">{report.job_id}</code></div>{report.found&&<label className="mp-consent"><input type="checkbox" checked={accept} onChange={event=>setAccept(event.target.checked)}/>Clear only this stopped job’s temporary files</label>}<button className="mp-button mp-primary" disabled={busy||!report.found||!accept} onClick={onClear}>{busy?'Clearing…':'Clear temporary files'}</button></section>;
}
