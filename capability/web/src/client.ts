/** Exact public control projections; model bytes and signing keys stay in Core. */
export interface Location {location_id:string; label:string; purpose:'private'|'public'; reachable:boolean; free_bytes:number|null; publisher_key_id:string}
export interface Model {model_id:string; generation_id:string; size_bytes:number; verification:string}
export interface Copy {copy_id:string; model_id:string; location_id:string; manifest_id:string; audience:'private'|'public'; size_bytes:number; checked_at:number; member_count:number}
export interface Job {job_id:string; operation_id:string; plan_id:string; kind:string; model_id:string; state:string; stage:string; created_at:number; bytes_copied:number; bytes_hashed:number; expected_manifest_id:string|null; copy_id:string|null; error_code:string|null}
export interface Page {locations:Location[]; models:Model[]; copies:Copy[]; jobs:Job[]; next_offset:number|null}
export interface Plan {plan_id:string; kind:'preserve'|'check'; model_id:string; location_id:string; size_bytes:number; members:string[]; member_count:number; expires_at:number}

/** Same-origin ticket-scoped controls; no supplier or analytics requests. */
export async function api<T>(action:string, body?:unknown):Promise<T> {
  const url = new URL(`api/${action}`, new URL('.', window.location.href));
  const reply = await fetch(url, {method:body === undefined ? 'GET':'POST', cache:'no-store', headers:body === undefined ? {} : {'Content-Type':'application/json','X-Preservation-Action':'operator'}, ...(body === undefined ? {} : {body:JSON.stringify(body)})});
  const value = await reply.json();
  if (!reply.ok) throw new Error(typeof value.error === 'string' ? value.error : 'host_unavailable');
  return value as T;
}

/** Human sizes describe observations rather than invented progress percentages. */
export function bytes(value:number):string {return value < 1024 ? `${value} B` : value < 1024**2 ? `${(value/1024).toFixed(1)} KB` : value < 1024**3 ? `${(value/1024**2).toFixed(1)} MB` : `${(value/1024**3).toFixed(1)} GB`;}

/** Stable codes are translated here; raw exceptions never become product copy. */
export function errorText(code:string):string {
  const labels:Record<string,string> = {
    insufficient_space:'There is not enough free space for the private snapshot and archive. Free storage on the Core host and the selected archive, then review a new plan.',
    host_restart_before_publication:'Skulk restarted before this job could publish. Review a new plan to try again.',
    uncertain_history_needs_reconciliation:'Several interrupted jobs need review. Check their outcomes in Activity before preserving more models.',
    configure_model_store_on_this_host:'Open this capability on your model-store host. Configure Model Store in Skulk Settings if you have not set one up yet.',
    store_configuration_changed_restart_required:'The model-store configuration changed. Restart Skulk on this host before continuing.',
    archive_unavailable:'This storage is disconnected. Reconnect it to continue.',
    archive_identity_changed:'This storage has changed. Reconnect the original archive before continuing.',
    installed_selection_changed:'This installation changed. Refresh the Library and review a new plan.',
    store_busy_try_again:'The store is busy with another transfer. Try reviewing this plan again shortly.',
    request_changed:'This operation ID belongs to a different request. Return to the Library and review a new plan.',
    plan_missing_or_expired:'This review expired. Return to the Library and review a new plan.',
    invalid_or_unsafe:'The selected files or metadata could not be verified. Keep the source and check the installation before trying again.',
    worker_unavailable:'The byte worker did not complete. Check the retained job before trying again.',
    host_unavailable:'Skulk could not be reached. Your submitted jobs remain owned by Core; refresh to check their state.',
  }; return labels[code] ?? `The operation could not continue (${code.replaceAll('_',' ')}).`;
}
