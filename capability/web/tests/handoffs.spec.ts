import {test,expect,type Page} from '@playwright/test';

const copy={copy_id:'1'.repeat(64),model_id:'community/portable',location_id:'public-copy',manifest_id:'2'.repeat(64),audience:'public',size_bytes:10000,checked_at:1,member_count:2,publisher_key_ids:['ed25519:'+'3'.repeat(64)]};
const setup='d'.repeat(64),hint='/ip4/127.0.0.1/tcp/4001/p2p/example';
const peer={runtime:'available',setup_id:setup,bootstrap:[hint],allow_private:false,active:false};
const plan={plan_id:'a'.repeat(64),kind:'peer_share',copy_id:copy.copy_id,model_id:copy.model_id,location_id:copy.location_id,manifest_id:copy.manifest_id,size_bytes:copy.size_bytes,members:['model','required companion'],member_count:2,expires_at:9999999999,publisher_key_ids:copy.publisher_key_ids,sharing:{public_network:true,peer_private:false,listen_port:0,duration_seconds:3600,upload_limit_bytes:1073741824,upload_rate_bytes:1048576,advertised_url:null}};
const sharingJob={job_id:'b'.repeat(64),plan_id:plan.plan_id,copy_id:null,expected_manifest_id:null,kind:'peer_share',model_id:copy.model_id,state:'running',stage:'sharing',bytes_copied:0,bytes_hashed:0,created_at:1,sharing_policy:plan.sharing,sharing:{ready:true,listen_port:0,charged_bytes:100,connected_peers:0,accounting:'observed-peer-wire',handoff_sha256:'c'.repeat(64)}};
async function base(page:Page){
 await page.route('**/api/core-features',r=>r.fulfill({json:{'Portable handoffs':true}}));
 await page.route('**/api/library?offset=*',r=>r.fulfill({json:{locations:[],models:[],copies:[],public_copies:[copy],publishing:null,jobs:[],uploads:[],bittorrent_available:true,next_offset:null}}));
 await page.route('**/api/peer-read',r=>r.fulfill({json:peer}));
 await page.route('**/api/discovery-read',r=>r.fulfill({json:{sources:[],jobs:[],next_offset:null}}));
 await page.route('**/api/discovery-search',r=>r.fulfill({json:{results:[],next_offset:null}}));
}

test('peer sharing needs fresh grants and its file survives reload and confirmed stop',async({page},info)=>{
 await base(page);let state='';let planned:Record<string,unknown>|null=null;let starts=0,downloads=0,references=0;
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
 await page.route('**/api/library?offset=*',r=>r.fulfill({json:{locations:[],models:[],copies:[],public_copies:[copy],publishing:null,uploads:[],jobs:state?[{...sharingJob,state,sharing:{...sharingJob.sharing,ready:state==='running'}}]:[],next_offset:null}}));
 await page.route('**/api/sharing-plan',r=>{planned=r.request().postDataJSON();return r.fulfill({json:plan});});
 await page.route('**/api/start',r=>{expect(r.request().postDataJSON()).toMatchObject({public_participation:true});starts++;state='running';return r.fulfill({json:sharingJob});});
 await page.route('**/api/sharing-reference',r=>{references++;expect(r.request().postDataJSON()).toEqual({job_id:sharingJob.job_id});return r.fulfill({json:{copy_id:copy.copy_id,manifest_id:copy.manifest_id}});});
 await page.route('**/api/handoff/*',r=>{downloads++;expect(r.request().postDataJSON()).toEqual({sharing_job_id:sharingJob.job_id});return r.fulfill({contentType:'application/octet-stream',body:'{"portable":"unchanged signed reference"}'});});
 await page.route('**/api/cancel',r=>{state='cancelling';return r.fulfill({json:{...sharingJob,state}});});
 await page.goto('/');await expect(page).toHaveTitle(/Preservation|Library/);await page.getByRole('button',{name:'Sharing',exact:true}).click();
 await page.getByRole('button',{name:'Share with someone',exact:true}).click();await page.getByRole('button',{name:'Share with peers',exact:true}).click();
 await expect(page.getByRole('button',{name:'Review sharing session',exact:true})).toBeDisabled();expect(starts).toBe(0);
 await page.getByLabel('Allow peer connections for sharing this public copy').check();await page.getByRole('button',{name:'Review sharing session',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Start this peer share'})).toBeVisible();await expect(page.getByRole('button',{name:'Start reviewed session'})).toBeDisabled();
 await page.getByLabel('I choose this sharing exposure').check();await page.getByRole('button',{name:'Start reviewed session'}).click();
 expect(planned).toMatchObject({kind:'peer_share',public_network:true,allow_peer_network:true,peer_setup_id:setup});
 await expect(page.getByRole('button',{name:'Download sharing file',exact:true})).toBeVisible();
 const saved=page.waitForEvent('download');await page.getByRole('button',{name:'Download sharing file',exact:true}).click();expect((await saved).suggestedFilename()).toBe(copy.manifest_id+'.foxhandoff');
 await expect(page.getByText('Attach the .foxhandoff file',{exact:false})).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);await page.screenshot({path:info.outputPath('peer-sharing-ready.png'),fullPage:true});
 await page.reload();await page.getByRole('button',{name:'Sharing',exact:true}).click();await expect(page.getByRole('button',{name:'Download sharing file',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Stop sharing',exact:true}).click();await expect(page.getByRole('button',{name:'Stopping…',exact:true})).toBeDisabled();state='cancelled';
 await expect(page.getByText('Stopped. Your verified public copy is kept.')).toBeVisible();
 const after=page.waitForEvent('download');await page.getByRole('button',{name:'Download sharing file',exact:true}).click();await after;
 expect(starts).toBe(1);expect(downloads).toBe(2);expect(references).toBeGreaterThanOrEqual(2);expect(errors).toEqual([]);
});

async function upload(page:Page){
 await page.route('**/api/upload-create',r=>r.fulfill({json:{upload_id:'f'.repeat(32),size_bytes:2,offset:0,complete:false}}));
 await page.route('**/api/upload/*',r=>r.fulfill({json:{upload_id:'f'.repeat(32),size_bytes:2,offset:2,complete:true}}));
 await page.route('**/api/upload-remove',r=>r.fulfill({json:{}}));
}

test('a model handoff prefills contacts without saving, private permission or model trust',async({page},info)=>{
 await base(page);await upload(page);let settings=0,plans=0;
 await page.route('**/api/peer-read',r=>r.fulfill({json:{...peer,setup_id:null,bootstrap:[]}}));
 await page.route('**/api/peer-setup',r=>{settings++;expect(r.request().postDataJSON()).toMatchObject({bootstrap:[hint],confirmed:true,allow_private:true});return r.fulfill({json:{...peer,allow_private:true}});});
 await page.route('**/api/invitation-inspect',r=>r.fulfill({json:{transport:'peer',contacts:[hint],name:'Portable model',manifest_id:copy.manifest_id,size_bytes:10000,member_count:2,members:['model','required companion'],publisher_key_ids:copy.publisher_key_ids,threshold:1,mirrors:['ipfs://example']}}));
 await page.route('**/api/delivery-plan',r=>{plans++;return r.fulfill({json:{...plan,kind:'receive'}});});
 await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();await page.getByRole('button',{name:'Receive shared model',exact:true}).click();
 await page.getByLabel('Invitation file',{exact:true}).setInputFiles({name:'model.foxhandoff',mimeType:'application/json',buffer:Buffer.from('{}')});
 await expect(page.getByLabel('Starting contacts')).toHaveValue(hint);await expect(page.getByLabel('Allow local or private peer addresses')).not.toBeChecked();await expect(page.getByLabel('I independently authenticated the exact bundle identity')).not.toBeChecked();
 await expect(page.getByRole('button',{name:'Review download',exact:true})).toBeDisabled();expect(settings).toBe(0);expect(plans).toBe(0);
 await page.screenshot({path:info.outputPath('handoff-recipient-drafts.png'),fullPage:true});
 await page.getByLabel('I independently authenticated the exact bundle identity').check();await page.getByLabel('I independently authenticated the required signature threshold').check();await page.getByLabel('I independently checked this fingerprint').check();
 await page.getByLabel('Allow local or private peer addresses').check();await page.getByLabel('I understand peers can see').check();await page.getByRole('button',{name:'Save peer settings',exact:true}).click();
 await expect(page.getByRole('button',{name:'Review download',exact:true})).toBeDisabled();await page.getByLabel('Allow peer connections for this model download').check();
 await expect(page.getByRole('button',{name:'Review download',exact:true})).toBeEnabled();expect(settings).toBe(1);expect(plans).toBe(0);
});

test('source handoff suggestions do not replace saved contacts or grant source trust',async({page})=>{
 await base(page);await upload(page);let saves=0,followed=0;
 await page.route('**/api/peer-setup',r=>{saves++;return r.fulfill({json:peer});});await page.route('**/api/source-follow',r=>{followed++;return r.fulfill({json:{}});});
 await page.route('**/api/source-review',r=>r.fulfill({json:{review_id:'4'.repeat(64),source_id:'5'.repeat(64),bootstrap_id:'6'.repeat(64),name:'Portable source',endpoints:[],transport:'peer',contacts:['/ip4/127.0.0.1/tcp/4999/p2p/new'],ipns_name:'k51-synthetic',snapshot_count:1,expires_at:9999999999}}));
 await page.goto('/');await page.getByRole('button',{name:'Find',exact:true}).click();await page.getByRole('button',{name:'Add a source',exact:true}).click();
 await page.getByLabel('Source file',{exact:true}).setInputFiles({name:'source.foxhandoff',mimeType:'application/json',buffer:Buffer.from('{}')});
 await expect(page.getByRole('button',{name:'Review contacts from this file'})).toBeVisible();expect(saves).toBe(0);expect(followed).toBe(0);
 await page.getByRole('button',{name:'Review contacts from this file'}).click();await expect(page.getByLabel('Starting contacts')).toHaveValue('/ip4/127.0.0.1/tcp/4999/p2p/new');
 await expect(page.getByLabel('Allow local or private peer addresses')).not.toBeChecked();await expect(page.getByLabel('I understand peers can see')).not.toBeChecked();await expect(page.getByRole('button',{name:'Save peer settings',exact:true})).toBeDisabled();expect(saves).toBe(0);expect(followed).toBe(0);
});
