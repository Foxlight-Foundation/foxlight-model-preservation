import {test,expect,type Page} from '@playwright/test';

const sourceId='a'.repeat(64),manifest='b'.repeat(64),jobId='c'.repeat(64),key='ed25519:'+'d'.repeat(64);
const mirror='http://'+'a'.repeat(52)+'.b32.i2p/models';
const proxy={host:'127.0.0.1',port:4445};
const source={source_id:sourceId,name:'Independent I2P archive',enabled:true,transport:'i2p',i2p_policy_id:'e'.repeat(64),i2p_proxy:proxy,catalog_id:'f'.repeat(64),accepted_at:'2026-10-24T12:00:00Z',freshness:'fresh',root_version:1,last_error:null};
const supplier={source_id:sourceId,entry_id:'model',source_name:source.name,name:'Portable model',summary:'Complete companion closure',freshness:'fresh',model_keys_available:true,transport:'i2p'};
const metadata={job_id:jobId,owner_id:'owner',kind:'model_metadata',source_id:sourceId,state:'succeeded',upload_id:'1'.repeat(32),error_code:null};
const preview={manifest_id:manifest,name:'Portable model',size_bytes:1024,member_count:2,members:['model: model','tokenizer: tokenizer'],publisher_key_ids:[key],threshold:1,mirrors:[mirror]};
const policy={transport:'i2p',allow_i2p:true,i2p_proxy:proxy,mirrors:[mirror],max_bytes:20000000,deadline_seconds:3600,allow_http:false,allow_private:false};
const plan={plan_id:'2'.repeat(64),kind:'receive',model_id:'example/portable',location_id:'invitation',size_bytes:1024,members:preview.members,member_count:2,manifest_id:manifest,publisher_key_ids:[key],expires_at:9999999999,receiving:policy};
const errors=new WeakMap<Page,string[]>();

async function inventory(page:Page,jobs:{job_id:string}[]=[],available=true){
 await page.route('**/api/core-features',route=>route.fulfill({json:{'Portable handoffs':false,'I2P preservation jobs':available}}));
 await page.route('**/api/library?offset=*',route=>route.fulfill({json:{locations:[],models:[],copies:[],public_copies:[],publishing:null,uploads:[],jobs,temporary_work_jobs:jobs.map(job=>job.job_id),next_offset:null}}));
}
async function find(page:Page){await page.goto('/');await page.getByRole('button',{name:'Find',exact:true}).click();await expect(page.getByRole('heading',{name:'Find a model',exact:true})).toBeVisible();}
async function fileIngress(page:Page){
 await page.route('**/api/upload-create',route=>route.fulfill({json:{upload_id:'1'.repeat(32),size_bytes:2,offset:0,complete:false}}));
 await page.route('**/api/upload/*',route=>route.fulfill({json:{upload_id:'1'.repeat(32),size_bytes:2,offset:2,complete:true}}));
 await page.route('**/api/upload-remove',route=>route.fulfill({json:{}}));
}
test.beforeEach(async({page})=>{const observed:string[]=[];errors.set(page,observed);page.on('pageerror',error=>observed.push(error.message));page.on('console',message=>{if(message.type()==='error')observed.push(message.text());});});
test.afterEach(async({page})=>{expect(errors.get(page)).toEqual([]);await expect(page.locator('vite-error-overlay')).toHaveCount(0);expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);});

test('Guided follows I2P explicitly and saved source refresh stays I2P after reload',async({page})=>{
 await inventory(page);await fileIngress(page);let followed=false;const follows:Record<string,unknown>[]=[],starts:Record<string,unknown>[]=[];
 await page.route('**/api/discovery-read',route=>route.fulfill({json:{sources:followed?[source]:[],jobs:[],next_offset:null}}));
 await page.route('**/api/discovery-search',route=>route.fulfill({json:{results:[],next_offset:null}}));
 await page.route('**/api/source-review',route=>route.fulfill({json:{review_id:'3'.repeat(64),source_id:sourceId,bootstrap_id:'4'.repeat(64),name:source.name,endpoints:[mirror],expires_at:9999999999}}));
 await page.route('**/api/source-follow',route=>{followed=true;follows.push(route.request().postDataJSON());return route.fulfill({json:source});});
 await page.route('**/api/discovery-start',route=>{starts.push(route.request().postDataJSON());return route.fulfill({json:{...metadata,kind:'refresh',state:'queued'}});});
 await find(page);await page.getByRole('button',{name:'Choose a source file',exact:true}).click();
 await page.getByLabel('Source file',{exact:true}).setInputFiles({name:'archive.foxsource',mimeType:'application/json',buffer:Buffer.from('{}')});
 await expect(page.getByRole('combobox',{name:'Source connection',exact:true})).toHaveValue('i2p');
 await page.getByLabel('I trust the supplier').check();await expect(page.getByRole('button',{name:'Trust source and get listings'})).toBeDisabled();
 await page.getByLabel('I2P proxy port',{exact:true}).fill('4445');await page.getByLabel('Allow I2P connections through this proxy').check();
 expect(follows).toHaveLength(0);expect(starts).toHaveLength(0);await page.getByRole('button',{name:'Trust source and get listings'}).click();
 await expect(page.getByRole('dialog')).toHaveCount(0);expect(follows[0]).toMatchObject({allow_i2p:true,i2p_proxy:proxy,i2p_endpoints:[mirror]});
 expect(starts[0]).toMatchObject({transport:'i2p',allow_i2p:true,i2p_policy_id:source.i2p_policy_id});expect(starts[0]).not.toHaveProperty('allow_http');
 await page.reload();await page.getByRole('button',{name:'Find',exact:true}).click();await page.getByRole('button',{name:/Sources & updates/}).click();
 await expect(page.getByRole('article',{name:source.name,exact:true}).getByText(/^I2P source/)).toBeVisible();await page.getByRole('button',{name:'Refresh listings',exact:true}).click();
 await expect.poll(()=>starts.length).toBe(2);expect(starts[1]).toMatchObject({transport:'i2p',allow_i2p:true,i2p_policy_id:source.i2p_policy_id});
});

test('I2P source metadata and model download keep independent consent and no direct grant',async({page})=>{
 await inventory(page);const starts:Record<string,unknown>[]=[],downloads:Record<string,unknown>[]=[];let reviewed:Record<string,unknown>|null=null;
 await page.route('**/api/discovery-read',route=>route.fulfill({json:{sources:[source],jobs:[],next_offset:null}}));
 await page.route('**/api/discovery-search',route=>route.fulfill({json:{results:[{manifest_id:manifest,suppliers:[supplier],supplier_count:1,next_supplier_offset:null}],next_offset:null}}));
 await page.route('**/api/discovery-start',route=>{starts.push(route.request().postDataJSON());return route.fulfill({json:metadata});});
 await page.route('**/api/discovery-observe',route=>route.fulfill({json:metadata}));
 await page.route('**/api/invitation-inspect',route=>route.fulfill({json:preview}));
 await page.route('**/api/delivery-plan',route=>{reviewed=route.request().postDataJSON();return route.fulfill({json:plan});});
 await page.route('**/api/start',route=>{downloads.push(route.request().postDataJSON());return route.fulfill({json:{}});});
 await find(page);expect(starts).toHaveLength(0);await page.getByRole('button',{name:'Review model',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Portable model',exact:true})).toBeVisible();expect(starts[0]).toMatchObject({transport:'i2p',allow_i2p:true,i2p_policy_id:source.i2p_policy_id,accept_source_keys:true});
 await page.getByLabel('Use this source’s saved reference').check();await expect(page.getByRole('button',{name:'Review download',exact:true})).toBeDisabled();
 await page.getByLabel('Allow I2P connections through this proxy').check();await page.getByRole('button',{name:'Review download',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Download this signed copy'})).toBeVisible();expect(reviewed).toMatchObject({allow_i2p:true,i2p_proxy:proxy,accepted_key_ids:[key]});expect(reviewed).not.toHaveProperty('allow_http');expect(downloads).toHaveLength(0);
 await page.getByLabel('I accept this exact signed copy').check();await page.getByRole('button',{name:'Download model',exact:true}).click();expect(downloads[0]).toMatchObject({accept_owner_capture:true});expect(downloads[0]).not.toHaveProperty('allow_repository_code');expect(downloads[0]).not.toHaveProperty('public_participation');
});

test('Resume reads original I2P proxy after reload and cannot edit or refill it',async({page})=>{
 const job={job_id:jobId,operation_id:'original',plan_id:'5'.repeat(64),kind:'receive',model_id:'example/portable',state:'interrupted',stage:'receiving',created_at:1,bytes_copied:0,bytes_hashed:0,expected_manifest_id:manifest,copy_id:null,error_code:'host_restart_before_publication'};
 await inventory(page,[job]);await page.route('**/api/receive-preview',route=>route.fulfill({json:{...preview,receiving:policy}}));let reviewed:Record<string,unknown>|null=null,resumed:Record<string,unknown>|null=null;
 await page.route('**/api/delivery-plan',route=>{reviewed=route.request().postDataJSON();return route.fulfill({json:{...plan,resume_job_id:jobId}});});
 await page.route('**/api/resume-receive',route=>{resumed=route.request().postDataJSON();return route.fulfill({json:job});});
 await page.goto('/');await page.reload();await page.getByRole('button',{name:'Activity',exact:true}).click();await page.getByRole('button',{name:'Review download resume'}).click();
 await expect(page.getByText('127.0.0.1:4445',{exact:true})).toBeVisible();await expect(page.getByLabel('I2P proxy port',{exact:true})).toHaveCount(0);await expect(page.getByRole('button',{name:'Review resume',exact:true})).toBeDisabled();
 await page.getByLabel('Allow I2P connections through this proxy').check();await page.getByRole('button',{name:'Review resume',exact:true}).click();
 expect(reviewed).toEqual({kind:'receive',location_id:'invitation',resume_job_id:jobId,allow_i2p:true,i2p_proxy:proxy});await page.getByLabel('I accept this exact signed copy').check();await page.getByRole('button',{name:'Resume download',exact:true}).click();expect(resumed).toMatchObject({job_id:jobId,accept_owner_capture:true});
});

test('an older Core never downgrades an I2P source or exposes unsupported controls',async({page})=>{
 await inventory(page,[],false);let starts=0;
 await page.route('**/api/discovery-read',route=>route.fulfill({json:{sources:[source],jobs:[],next_offset:null}}));
 await page.route('**/api/discovery-search',route=>route.fulfill({json:{results:[{manifest_id:manifest,suppliers:[supplier],supplier_count:1,next_supplier_offset:null}],next_offset:null}}));
 await page.route('**/api/discovery-start',route=>{starts++;return route.fulfill({json:{}});});
 await find(page);await page.getByRole('button',{name:/Sources & updates/}).click();await expect(page.getByRole('button',{name:'Refresh listings',exact:true})).toBeDisabled();await expect(page.getByRole('button',{name:'Review model',exact:true})).toBeDisabled();expect(starts).toBe(0);
});

test('invalid I2P endpoints block source enrollment before effects',async({page})=>{
 await inventory(page);await fileIngress(page);let follows=0;
 await page.route('**/api/discovery-read',route=>route.fulfill({json:{sources:[],jobs:[],next_offset:null}}));await page.route('**/api/discovery-search',route=>route.fulfill({json:{results:[],next_offset:null}}));
 await page.route('**/api/source-review',route=>route.fulfill({json:{review_id:'3'.repeat(64),source_id:sourceId,bootstrap_id:'4'.repeat(64),name:source.name,endpoints:[mirror],expires_at:9999999999}}));
 await page.route('**/api/source-follow',route=>{follows++;return route.fulfill({json:source});});
 await find(page);await page.getByRole('button',{name:'Choose a source file',exact:true}).click();await page.getByLabel('Source file',{exact:true}).setInputFiles({name:'archive.foxsource',mimeType:'application/json',buffer:Buffer.from('{}')});await page.getByLabel('I trust the supplier').check();
 for(const endpoint of ['http://ordinary.example/','http://'+'a'.repeat(51)+'b.b32.i2p/',mirror+'/../escape',mirror+'//duplicate',mirror+':99999']){
  await page.getByLabel('I2P metadata endpoints').fill(endpoint);await page.getByLabel('Allow I2P connections through this proxy').check();await expect(page.getByRole('button',{name:'Trust source and get listings'})).toBeDisabled();
 }
 expect(follows).toBe(0);
});

test('I2P-only invitations on old Core require upgrade rather than direct receiving',async({page})=>{
 await inventory(page,[],false);await fileIngress(page);let plans=0;
 await page.route('**/api/invitation-inspect',route=>route.fulfill({json:preview}));
 await page.route('**/api/delivery-plan',route=>{plans++;return route.fulfill({json:plan});});
 await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();await page.getByRole('button',{name:'Receive shared model'}).click();
 await page.getByLabel('Invitation file',{exact:true}).setInputFiles({name:'model.foxhandoff',mimeType:'application/json',buffer:Buffer.from('{}')});
 await expect(page.getByText('Update Core to use these I2P-only mirrors.')).toBeVisible();
 await page.getByLabel('I independently authenticated the exact bundle identity').check();await page.getByLabel('I independently authenticated the required signature threshold').check();await page.getByLabel('I independently checked this fingerprint').check();
 await expect(page.getByRole('button',{name:'Review download',exact:true})).toBeDisabled();expect(plans).toBe(0);
});
