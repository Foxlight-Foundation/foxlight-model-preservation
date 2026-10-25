/** Exact public control projections; model bytes and signing keys stay in Core. */
export interface Location {location_id:string; label:string; purpose:'private'|'public'; reachable:boolean; free_bytes:number|null; publisher_key_id:string}
export interface Model {model_id:string; generation_id:string; size_bytes:number; verification:string}
export interface Copy {copy_id:string; model_id:string; location_id:string; manifest_id:string; audience:'private'|'public'; size_bytes:number; checked_at:number; member_count:number; publisher_key_ids?:string[]}
export interface Job {job_id:string; operation_id:string; plan_id:string; kind:string; model_id:string; state:string; stage:string; created_at:number; bytes_copied:number; bytes_hashed:number; expected_manifest_id:string|null; copy_id:string|null; error_code:string|null; location_id?:string|null; sharing_policy?:{peer_private?:boolean;public_network:boolean;duration_seconds:number;upload_limit_bytes:number;upload_rate_bytes:number;advertised_url:string|null}|null; sharing?:{handoff_sha256?:string|null;ready:boolean;listen_port:number;charged_bytes:number;accounting:string;connected_peers:number;metainfo_sha256?:string|null}|null}
export interface Page {default_location_id?:string|null; locations:Location[]; models:Model[]; copies:Copy[]; jobs:Job[]; uploads?:Upload[]; public_copies?:Copy[]; publishing?:Location|null; bittorrent_available?:boolean; temporary_work_jobs?:string[]; next_offset:number|null}
export interface Plan {archive_format?:'preservation-v1'|'pmf-v0.1';plan_id:string; kind:'preserve'|'check'|'kit_export'|'recover'|'mirror_import'|'http_share'|'torrent_share'|'peer_share'|'publish'|'receive'; receiving?:ReceivingPolicy|null; resume_job_id?:string|null; model_id:string; location_id:string; size_bytes:number; members:string[]; member_count:number; expires_at:number; upload_id?:string|null; historical?:boolean; existing_companions?:string[]; deleted_aliases?:string[]; publisher_key_ids?:string[]; requires_owner_acceptance?:boolean; requires_code_permission?:boolean; publication?:string|null; file_coverage?:string|null;manifest_id?:string|null;sharing?:{peer_private?:boolean;public_network:boolean;listen_port:number;duration_seconds:number;upload_rate_bytes:number;upload_limit_bytes:number;advertised_url:string|null}|null}

/** Same-origin ticket-scoped controls; no supplier or analytics requests. */
export async function api<T>(action:string, body?:unknown):Promise<T> {
  const url = new URL(`api/${action}`, new URL('.', window.location.href));
  const reply = await fetch(url, {method:body === undefined ? 'GET':'POST', cache:'no-store', headers:body === undefined ? {} : {'Content-Type':'application/json','X-Preservation-Action':'operator'}, ...(body === undefined ? {} : {body:JSON.stringify(body)})});
  const value = await reply.json();
  if (!reply.ok) throw new Error(typeof value.error === 'string' ? value.error : 'host_unavailable');
  return value as T;
}

/** Human sizes describe observations rather than invented progress percentages. */
export function bytes(value:number):string {return value < 1024 ? `${value} B` : value < 1024**2 ? `${(value/1024).toFixed(1)} KB` : value < 1024**3 ? `${(value/1024**2).toFixed(1)} MB` : value < 1024**4 ? `${(value/1024**3).toFixed(1)} GB` : `${(value/1024**4).toFixed(1)} TB`;}

/** Stable codes are translated here; raw exceptions never become product copy. */
export function errorText(code:string):string {
  const labels:Record<string,string> = {
    public_copy_review_changed:'The complete signed-copy review could not be verified. Refresh Library and review the exact selection again.',
    mirror_policy_changed:'Mirror settings or selections changed. Close this review and review the current choices again.',
    mirror_policy_review_required:'Review the current mirror policy before changing its settings.',
    mirror_not_configured:'Set up this host’s mirror storage and allowances first.',
    mirror_directory_changed:'The managed mirror storage changed. Reconnect its original storage before continuing.',
    mirror_resume_permission_required:'Review fresh restart permission for the current policy, then explicitly start sharing again.',
    previous_mirror_still_draining:'Earlier sharing is still stopping. Keep its retained copies and wait for the host to confirm the stop.',
    mirror_stop_incomplete:'The host could not confirm that all sharing stopped. Keep using Stop for this exact session and inspect host diagnostics.',
    mirror_busy_try_again:'Another mirror operation is still finishing. Wait for its receipt before reviewing new work.',
    mirror_previous_recovery_changed:'The earlier local-byte check no longer matches this model or is still active. Read its receipt before choosing another check.',
    public_mirror_pmf_transport_unavailable:'This mirror adapter cannot serve PMF directly yet. Keep the original archive; this operation does not convert it.',
    metadata_preview_too_large:'The complete review is too large for this Library view. Keep the original archive and use the standalone tools.',
    invalid_job_identity:'The job reply did not match this decision. Check activity before retrying the unchanged review.',
    source_i2p_policy_changed:'This source’s saved I2P policy changed. Reopen Sources and review its current settings.',
    source_i2p_policy_invalid:'This source’s protected I2P policy cannot be verified. Connections are blocked; retained evidence is kept.',
    i2p_metadata_mirror_required:'Provide a supported I2P metadata mirror from this source’s operator. No direct connection was attempted.',
    i2p_model_mirror_required:'This reference has no supported I2P model mirror. Ask for an I2P reference or use a recovery kit.',
    i2p_confirmation_required:'Review and allow I2P connections for this operation.',
    receive_resume_i2p_policy_required:'Resume requires the original I2P proxy. Update Core if the saved policy is unavailable, then confirm it again.',
    peer_runtime_confirmation_required:'Review the software source and explicitly allow this installation.',
    peer_runtime_plan_changed:'The peer support build changed. Check setup again and review its current plan.',
    peer_runtime_setup_failed:'Peer support could not be installed. Check Core’s connection and free storage, then review another attempt.',
    peer_runtime_job_capacity_reached:'Core has retained its maximum setup receipts. Use a compatible existing Kubo installation; retained authorization history was kept.',
    peer_runtime_job_not_found:'This setup receipt is unavailable for the current Library client.',
    peer_setup_required:'Set up peer connections, then explicitly allow this operation.',
    peer_setup_changed:'Peer settings changed. Review the current contacts and allow this operation again.',
    peer_network_confirmation_required:'Review and allow peer connections for this operation.',
    peer_runtime_required:'Set up verified peer support on Core, then review peer connections.',
    peer_scope_changed_new_receive_required:'This download was reviewed for a different private-address scope. Restore that scope to resume, or review a separate download.',
    peer_pinned_snapshot_required:'This peer source has no pinned recovery snapshot. Ask its operator for an updated source file.',
    peer_runtime_unavailable:'Set up verified peer support on Core, then review peer connections again.',
    peer_busy_try_again:'A peer operation is finishing. Wait for it to stop before changing connection settings.',
    peer_ipfs_model_hint_required:'This source has no supported peer model reference. Ask its operator for a peer invitation or recovery kit.',
    source_transport_changed:'This source was reviewed for a different transport. Reopen it and review its current connection settings.',
    source_transport_switch_unavailable:'This source has a retained connection policy. Reopen its original transport; changing it to direct HTTP is not supported yet.',
    source_document_too_large:'Choose a source bootstrap file up to 256 KB. Import complete offline caches with the standalone tools.',
    source_bootstrap_required:'Choose the source bootstrap file. Historical cache interchange is available in the standalone tools.',
    source_review_missing_or_expired:'This source review expired. Choose its file and review again.',
    source_trust_confirmation_required:'Confirm that you trust the supplier of this source file for listings.',
    discovery_busy_try_again:'A source update is still finishing. Wait for it to stop before changing sources.',
    discovery_job_capacity_reached:'Core’s metadata queue or retained history is full. Wait for active jobs to finish before submitting more work.',
    discovery_preview_too_large:'These listings exceed this screen’s preview limit. Use a more specific search or the standalone tools; the original signed metadata is retained.',
    candidate_bundle_keys_required:'This source has no model verification keys for an automatic handoff. Ask its operator for an invitation or use standalone tools with independently trusted keys.',
    public_model_mirror_required:'This source has no supported model reference for this transport. Use an invitation or a recovery kit from a trusted sender.',
    model_metadata_unavailable:'Signed model details could not be obtained. Check the model-detail network permissions and mirror availability, then review again.',
    source_model_reference_changed:'The source’s model reference changed. Search again and review the new exact selection.',
    discovery_outcome_uncertain:'The metadata worker stopped without a confirmed receipt. Your cached listings are retained; inspect their status before a new update.',
    discovery_deadline_exceeded:'This metadata attempt reached its time limit and stopped. Cached listings remain available; choose a new refresh to try again.',
    core_restarted:'Core restarted. Metadata work was interrupted and will not replay. Your accepted cache remains available.',
    storage_unavailable:'This folder could not be reached or created safely. Mount the drive, check access and review again.',
    storage_review_expired:'This storage review expired or changed. Review the folder again.',
    storage_review_changed:'The folder or its mounted parent changed after review. Reconnect it and review again.',
    storage_folder_not_empty:'Choose an empty dedicated folder. Existing unregistered files were kept.',
    storage_private_folder_required:'Choose a dedicated folder owned by the Core user with owner-only permissions. Shared storage permissions were left unchanged.',
    storage_overlaps_core:'Choose storage outside Core state, Model Store and custom-card folders.',
    storage_setup_incomplete:'Storage setup needs to finish. Review the same enrolled folder again; your files were kept.',
    storage_location_limit:'This Core already has 16 private destinations.',
    invalid_storage_selection:'Enter an absolute folder path on the Core host and a storage name without control characters.',
    public_metadata_too_large:'Choose an invitation up to 8 MB. For larger metadata, use the complete recovery kit instead.',
    public_metadata_refused:'The invitation or proposed public metadata could not be exported. Check the exact public copy and use credential-free HTTP(S) mirror addresses.',
    publisher_signing_key_unavailable:'The public signing key is unavailable. Your saved copies remain intact; reconnect its original protected custody before creating a new publication.',
    receiving_allowance_too_small:'Increase the download allowance to cover the complete signed model and companions, then review again.',
    receive_resume_inputs_fixed:'A resume keeps the original trust, sources and download allowance. Start a separately reviewed download to change them.',
    receive_work_unavailable:'This job no longer has its original partial files. Keep any completed copy, or review a new download with a new allowance.',
    deadline_exceeded:'This attempt reached its time limit. Review a resume in Activity to continue with the original remaining allowance.',
    public_destination_changed:'The public storage directory changed. Your files were kept. Reconnect the original storage before continuing.',
    temporary_job_not_stopped:'This job is still active or finishing. Wait for it to stop before clearing temporary files.',
    temporary_work_identity_changed:'These working files have changed. They were kept for inspection.',
    unsafe_temporary_directory:'These working files could not be safely identified. They were kept for inspection.',
    unknown_temporary_work:'These files have no matching custody record. Keep them for operator inspection.',
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
    unsupported_pmf_profile:'This PMF archive needs a compatible Skulk profile. Keep the kit; standalone PMF tools can recover all captured files and documents without Skulk.',
    public_pmf_adapter_required:'Public PMF mirroring needs a supported transport adapter. Create a reviewed public publication from a saved private copy to use current mirrors.',
    unsupported_model_card:'This reader cannot interpret the card. Keep the kit and use a compatible Skulk version. Standalone tools can still recover its captured files.',
    remove_unused_kit_uploads:'Remove an unused transfer to free an upload slot.',
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

export interface Upload {upload_id:string; size_bytes:number; offset:number; complete:boolean; purpose?:'kit'|'invitation'|'publication'|'source'}

export interface PublicationMember {member_id:string;role:string;repository:string;revision:string;format:string;size_bytes:number;file_count:number;license_id:string;terms_reference:string;notice_paths:string[]}
export interface PublicationSource {copy_id:string;manifest_id:string;name:string;workloads:string[];runtime_requirements:string[];members:PublicationMember[]}
export interface I2PProxy {host:'127.0.0.1'|'::1';port:number}
export interface InvitationPreview {receiving?:ReceivingPolicy|null;contacts?:string[];transport?:'http'|'peer';manifest_id:string;name:string;size_bytes:number;member_count:number;members:string[];publisher_key_ids:string[];threshold:number;mirrors:string[]}
export interface ReceivingPolicy {transport?:'http'|'peer'|'i2p';allow_i2p?:boolean;i2p_proxy?:I2PProxy|null;allow_peer_network?:boolean;peer_private?:boolean;mirrors:string[];max_bytes:number;deadline_seconds:number;allow_http:boolean;allow_private:boolean}

/** Metadata is bounded separately from weights and ordinary control JSON. */
export async function uploadMetadata(content:Blob,purpose:'invitation'|'publication'|'source'):Promise<Upload> {
  if(content.size===0||content.size>(purpose==='source'?256*1024:8*1024**2))throw new Error(purpose==='source'?'source_document_too_large':'public_metadata_too_large');
  let upload=await api<Upload>('upload-create',{size_bytes:content.size,purpose});
  while(upload.offset<content.size)upload=await uploadChunk(upload.upload_id,upload.offset,content.slice(upload.offset,upload.offset+4*1024**2));
  return upload;
}

/** Download public metadata, never a private control link or model-sized Blob. */
export async function saveInvitation(copy:Copy,mirrors:string[]):Promise<void> {
  const reply=await fetch(new URL(`api/invitation/${copy.copy_id}`,new URL('.',location.href)),{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json','X-Preservation-Action':'operator'},body:JSON.stringify({mirrors})});
  if(!reply.ok)throw new Error('public_metadata_refused');
  const data=await reply.blob();
  if(data.size>8*1024**2)throw new Error('public_metadata_too_large');
  const url=URL.createObjectURL(data),anchor=document.createElement('a');anchor.href=url;anchor.download=`${copy.manifest_id}.invitation.json`;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}

/** Save one portable metadata file; it contains no Library ticket or signing key. */
export async function saveHandoff(copy:Pick<Copy,'copy_id'|'manifest_id'>,route:{mirrors:string[]}|{sharing_job_id:string}):Promise<void> {
  const reply=await fetch(new URL(`api/handoff/${copy.copy_id}`,new URL('.',location.href)),{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json','X-Preservation-Action':'operator'},body:JSON.stringify(route)});
  if(!reply.ok)throw new Error(reply.status===404?'core_upgrade_required':'public_metadata_refused');
  const data=await reply.blob();
  if(data.size>8*1024**2)throw new Error('public_metadata_too_large');
  const url=URL.createObjectURL(data),anchor=document.createElement('a');anchor.href=url;anchor.download=`${copy.manifest_id}.foxhandoff`;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}

export function manifestDownload(plan:Plan):string{return new URL(`api/manifest/${plan.plan_id}`,new URL('.',location.href)).href;}

/** Finite defaults include room for metadata and retries, scaled to the copy. */
export function transferAllowance(size:number):number{return Math.min(Number.MAX_SAFE_INTEGER,Math.max(16*1024**2,size*3+8*1024**2));}

/** Transfer a bounded binary slice outside control JSON. */
export async function uploadChunk(id:string,offset:number,content:Blob):Promise<Upload>{const reply=await fetch(new URL(`api/upload/${id}?offset=${offset}`,new URL('.',location.href)),{method:'PUT',cache:'no-store',headers:{'Content-Type':'application/octet-stream','X-Preservation-Action':'operator'},body:content});const value=await reply.json();if(!reply.ok)throw new Error(typeof value.error==='string'?value.error:'kit_transfer_refused');return value as Upload;}

/** Stream a native browser download without allocating a weight-sized JS blob. */
export function kitDownload(job:Job):string{return new URL(`api/download/${job.job_id}`,new URL('.',location.href)).href;}

/** Standard metainfo and public verification keys; downloading joins no swarm. */
export function torrentDownload(job:Job):string{return new URL(`api/torrent/${job.job_id}`,new URL('.',location.href)).href;}
export function trustDownload(copy:Copy):string{return new URL(`api/trust/${copy.copy_id}`,new URL('.',location.href)).href;}

/** Cached metadata-source state; freshness never establishes registry trust. */
export interface ModelSource {transport?:'http'|'peer'|'i2p';i2p_policy_id?:string;i2p_proxy?:I2PProxy;snapshot_count?:number;source_id:string;name:string;enabled:boolean;freshness:'empty'|'fresh'|'retained'|'stale'|'imported'|'superseded-root'|'invalid';accepted_at:string|null;expires:string|null;last_error:string|null;root_version:number|null;catalog_id:string|null;bootstrap_id:string|null}
/** A metadata-only job remains owned by Core across page closure. */
export interface DiscoveryJob {job_id:string;owner_id:string;kind:'refresh'|'model_metadata';source_id:string;manifest_id:string|null;state:string;created_at:number;updated_at:number;error_code:string|null;upload_id:string|null}
/** Local source/activity read, with no refresh or supplier query. */
export interface DiscoveryPage {sources:ModelSource[];jobs:DiscoveryJob[];next_offset:number|null}
/** Exact file review performed offline before source enrollment. */
export interface SourceReview {contacts?:string[];transport?:'http'|'peer';ipns_name?:string|null;snapshot_count?:number;review_id:string;source_id:string;bootstrap_id:string;name:string;endpoints:string[];expires_at:number}
/** One attributed statement; original signed data stays in Core. */
export interface ModelSupplier {transport?:'http'|'peer'|'i2p';source_id:string;source_name:string;entry_id:string;name:string;summary:string;freshness:string;model_keys_available:boolean}
/** Exact identity grouping keeps conflicting source statements separate. */
export interface FoundModel {manifest_id:string;suppliers:ModelSupplier[];supplier_count:number;next_supplier_offset:number|null}
export interface DiscoveryResults {results:FoundModel[];next_offset:number|null}
