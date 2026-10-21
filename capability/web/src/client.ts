/** Exact public control projections; model bytes and signing keys stay in Core. */
export interface Location {location_id:string; label:string; purpose:'private'|'public'; reachable:boolean; free_bytes:number|null; publisher_key_id:string}
export interface Model {model_id:string; generation_id:string; size_bytes:number; verification:string}
export interface Copy {copy_id:string; model_id:string; location_id:string; manifest_id:string; audience:'private'|'public'; size_bytes:number; checked_at:number; member_count:number; publisher_key_ids?:string[]}
export interface Job {job_id:string; operation_id:string; plan_id:string; kind:string; model_id:string; state:string; stage:string; created_at:number; bytes_copied:number; bytes_hashed:number; expected_manifest_id:string|null; copy_id:string|null; error_code:string|null; location_id?:string|null; sharing_policy?:{public_network:boolean;duration_seconds:number;upload_limit_bytes:number;upload_rate_bytes:number;advertised_url:string|null}|null; sharing?:{ready:boolean;listen_port:number;charged_bytes:number;accounting:string;connected_peers:number;metainfo_sha256?:string|null}|null}
export interface Page {locations:Location[]; models:Model[]; copies:Copy[]; jobs:Job[]; uploads?:Upload[]; public_copies?:Copy[]; bittorrent_available?:boolean; next_offset:number|null}
export interface Plan {plan_id:string; kind:'preserve'|'check'|'kit_export'|'recover'|'mirror_import'|'http_share'|'torrent_share'; model_id:string; location_id:string; size_bytes:number; members:string[]; member_count:number; expires_at:number; upload_id?:string|null; historical?:boolean; existing_companions?:string[]; deleted_aliases?:string[]; publisher_key_ids?:string[]; requires_owner_acceptance?:boolean; requires_code_permission?:boolean; publication?:string|null; file_coverage?:string|null;manifest_id?:string|null;sharing?:{public_network:boolean;listen_port:number;duration_seconds:number;upload_rate_bytes:number;upload_limit_bytes:number;advertised_url:string|null}|null}

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
    public_bundle_required:'This kit includes private members. Use the standalone publication review to create a separate signed public bundle before mirroring it.',
    bittorrent_unavailable:'This Core build has no compatible BitTorrent adapter. Use an HTTP mirror, or a Core build with the qualified adapter.',
    host_restart_stopped_sharing:'Core restarted and sharing stopped. Review a new session to share again.',
    torrent_not_ready:'Torrent metadata is still being prepared. Wait for the file checks to finish.',
    public_participation_required:'Review the sharing exposure and explicitly accept it before starting.',
    recovery_index_unreadable:'The model index is unavailable or damaged. Your files were kept. Repair the index before clearing unused restore files.',
    recovery_job_not_stopped:'This restore is still active. Stop it and wait for confirmation before clearing temporary files.',
    recovery_cleanup_identity_changed:'These temporary files changed. They were kept for inspection.',
    core_upgrade_required:'This Core build does not include this preservation operation. Update Core on the model-store host, then reopen Model Preservation.',
    operation_not_available_in_this_host_version:'This operation requires an updated Core build on the model-store host.',
    existing_model_conflict:'An existing model uses one of these names. Your installation is kept. Restore on an empty Core or resolve the conflict first.',
    removed_model_conflict:'A model in this kit was deliberately removed on this Core. Restore on a fresh Core or explicitly reintroduce it through the installation workflow.',
    unsupported_model_card:'This reader cannot interpret the card. Keep the kit and use a compatible Skulk version. Standalone tools can still recover its captured files.',
    remove_unused_kit_uploads:'Remove an unused kit transfer to free an upload slot.',
    kit_upload_in_use:'A recovery job still needs this transfer. Stop or finish that job before removing it.',
    kit_transfer_refused:'Select the same file to resume, or remove an unused transfer and start again.',
    explicit_recovery_retry_required:'Review a new restore or kit export plan. Checking the source archive cannot establish that recovery finished.',
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

export interface Upload {upload_id:string; size_bytes:number; offset:number; complete:boolean}

/** Transfer a bounded binary slice outside control JSON. */
export async function uploadChunk(id:string,offset:number,content:Blob):Promise<Upload>{const reply=await fetch(new URL(`api/upload/${id}?offset=${offset}`,new URL('.',location.href)),{method:'PUT',cache:'no-store',headers:{'Content-Type':'application/octet-stream','X-Preservation-Action':'operator'},body:content});const value=await reply.json();if(!reply.ok)throw new Error(typeof value.error==='string'?value.error:'kit_transfer_refused');return value as Upload;}

/** Stream a native browser download without allocating a weight-sized JS blob. */
export function kitDownload(job:Job):string{return new URL(`api/download/${job.job_id}`,new URL('.',location.href)).href;}

/** Standard metainfo and public verification keys; downloading joins no swarm. */
export function torrentDownload(job:Job):string{return new URL(`api/torrent/${job.job_id}`,new URL('.',location.href)).href;}
export function trustDownload(copy:Copy):string{return new URL(`api/trust/${copy.copy_id}`,new URL('.',location.href)).href;}
