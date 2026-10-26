import {test,expect,type Page} from '@playwright/test';
test.beforeEach(async({page})=>{await page.route('**/api/core-features',route=>route.fulfill({json:{'Portable handoffs':false}}));});

const sourceId='a'.repeat(64),manifest='b'.repeat(64),jobId='c'.repeat(64),key='ed25519:'+'d'.repeat(64);
const source={source_id:sourceId,name:'Community archive',enabled:true,catalog_id:'e'.repeat(64),accepted_at:'2026-10-22T12:00:00Z',freshness:'fresh',root_version:1,last_error:null};
const job={job_id:jobId,operation_id:'chosen',owner_id:'owner',kind:'model_metadata',source_id:sourceId,state:'succeeded',stage:'complete',upload_id:'f'.repeat(32),error_code:null};
const supplier={source_id:sourceId,entry_id:'model',source_name:source.name,name:'Example model',summary:'Complete preserved model',freshness:'fresh',model_keys_available:true};
const result={manifest_id:manifest,suppliers:[supplier],supplier_count:1,next_supplier_offset:null};
const preview={manifest_id:manifest,name:'Example model',size_bytes:1024,member_count:2,members:['base: model','assistant: draft'],publisher_key_ids:[key],threshold:1,mirrors:['https://mirror.example.org/']};
async function base(page:Page,sources:object[]=[source]) {
 await page.route('**/api/library?offset=*',route=>route.fulfill({json:{locations:[],models:[],copies:[],jobs:[],uploads:[],next_offset:null}}));
 await page.route('**/api/discovery-read',route=>route.fulfill({json:{sources,jobs:[],next_offset:null}}));
 await page.route('**/api/discovery-search',route=>route.fulfill({json:{results:sources.length?[result]:[],next_offset:null}}));
}
async function find(page:Page){await page.goto('/');await page.getByRole('button',{name:'Find',exact:true}).click();await expect(page.getByRole('heading',{name:'Find a model',exact:true})).toBeVisible();}

test('Guided adds a reviewed source with separate metadata grants and one durable refresh',async({page})=>{
 await base(page,[]);const effects:Record<string,unknown>[]=[];let removed=0;
 await page.route('**/api/upload-create',route=>{expect(route.request().postDataJSON()).toMatchObject({purpose:'source'});return route.fulfill({json:{upload_id:'1'.repeat(32),size_bytes:2,offset:0,complete:false}});});
 await page.route('**/api/upload/*',route=>route.fulfill({json:{upload_id:'1'.repeat(32),size_bytes:2,offset:2,complete:true}}));
 await page.route('**/api/upload-remove',route=>{removed++;return route.fulfill({json:{}});});
 await page.route('**/api/source-review',route=>route.fulfill({json:{review_id:'2'.repeat(64),source_id:sourceId,bootstrap_id:'3'.repeat(64),name:source.name,endpoints:['https://catalog.example.org/'],expires_at:9999999999}}));
 await page.route('**/api/source-follow',route=>{effects.push(route.request().postDataJSON());return route.fulfill({json:source});});
 await page.route('**/api/discovery-start',route=>{effects.push(route.request().postDataJSON());return route.fulfill({json:{...job,kind:'refresh',state:'queued'}});});
 await page.goto('/');await page.getByRole('button',{name:'Find a shared model',exact:true}).click();await page.getByRole('button',{name:'Choose a source file',exact:true}).click();
 await page.getByLabel('Source file',{exact:true}).setInputFiles({name:'archive.foxsource',mimeType:'application/json',buffer:Buffer.from('{}')});
 await expect(page.getByRole('heading',{name:source.name})).toBeVisible();expect(effects).toHaveLength(0);
 await expect(page.getByRole('button',{name:'Trust source and get listings'})).toBeDisabled();await page.getByLabel('I trust the supplier').check();
 await page.getByRole('button',{name:'Trust source and get listings'}).click();await expect(page.getByRole('dialog')).toHaveCount(0);await expect(page.getByRole('heading',{name:'Find a model',exact:true})).toBeVisible();
 expect(effects[0]).toMatchObject({confirmed:true,allow_http:false,allow_private:false});expect(effects[1]).toMatchObject({kind:'refresh',source_id:sourceId});expect(removed).toBe(1);
});

test('local search downloads only after metadata review and exact copy consent',async({page})=>{
 await base(page);const starts:Record<string,unknown>[]=[];let planned:Record<string,unknown>|null=null,download:Record<string,unknown>|null=null;
 await page.route('**/api/discovery-start',route=>{starts.push(route.request().postDataJSON());return route.fulfill({json:job});});
 await page.route('**/api/discovery-observe',route=>route.fulfill({json:job}));
 await page.route('**/api/invitation-inspect',route=>route.fulfill({json:preview}));
 await page.route('**/api/delivery-plan',route=>{planned=route.request().postDataJSON();return route.fulfill({json:{plan_id:'4'.repeat(64),kind:'receive',model_id:'example/model',location_id:'invitation',size_bytes:1024,members:preview.members,member_count:2,manifest_id:manifest,publisher_key_ids:[key],expires_at:9999999999,receiving:{mirrors:preview.mirrors,max_bytes:20000000,deadline_seconds:86400,allow_http:false,allow_private:false}}});});
 await page.route('**/api/start',route=>{download=route.request().postDataJSON();return route.fulfill({json:{}});});
 await find(page);expect(starts).toHaveLength(0);await page.getByLabel('Search saved listings').fill('Example');await expect(page.getByText('Lists as Example model')).toBeVisible();expect(starts).toHaveLength(0);
 await page.getByRole('button',{name:'Review model',exact:true}).click();await expect(page.getByRole('dialog').getByRole('heading',{name:'Example model',exact:true})).toBeVisible();expect(starts[0]).toMatchObject({kind:'model_metadata',manifest_id:manifest,accept_source_keys:true,allow_http:false,allow_private:false});expect(download).toBeNull();
 await expect(page.getByText('assistant: draft',{exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'Review download',exact:true})).toBeDisabled();await page.getByLabel('Use this source’s saved reference').check();await page.getByRole('button',{name:'Review download',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Download this signed copy'})).toBeVisible();expect(planned).toMatchObject({accepted_key_ids:[key],allow_http:false,allow_private:false});expect(download).toBeNull();await page.getByLabel('I accept this exact signed copy').check();await page.getByRole('button',{name:'Download model',exact:true}).click();expect(download).toMatchObject({accept_owner_capture:true});expect(download).not.toHaveProperty('allow_repository_code');expect(download).not.toHaveProperty('public_participation');expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});

test('failed refresh retains searchable listings and a superseded authority cannot hand off',async({page})=>{
 await base(page,[{...source,freshness:'retained',last_error:'source_unavailable'}]);let updates=0;
 await page.route('**/api/discovery-start',route=>{updates++;return route.fulfill({json:{...job,kind:'refresh',state:'failed',error_code:'source_unavailable'}});});
 await find(page);await expect(page.getByText('Lists as Example model')).toBeVisible();await page.getByRole('button',{name:/Sources & updates/}).click();await expect(page.getByText('Saved listings · last update failed')).toBeVisible();await page.getByRole('button',{name:'Refresh listings'}).click();expect(updates).toBe(1);await expect(page.getByText('Lists as Example model')).toBeVisible();
 await page.route('**/api/discovery-search',route=>route.fulfill({json:{results:[{...result,suppliers:[{...supplier,freshness:'superseded-root'}]}],next_offset:null}}));await page.getByLabel('Search saved listings').fill('changed');await expect(page.getByRole('button',{name:'Review model',exact:true})).toBeDisabled();
});

test('leaving a pending model metadata action cannot reopen the review',async({page})=>{
 await base(page);let release!:()=>void;const pending=new Promise<void>(resolve=>{release=resolve;});await page.route('**/api/discovery-start',async route=>{await pending;await route.fulfill({json:job});});await find(page);await page.getByRole('button',{name:'Review model',exact:true}).click();await page.getByRole('button',{name:'Sharing',exact:true}).click();release();await expect(page.getByRole('heading',{name:'Standalone sharing tools'})).toBeVisible();await expect(page.getByRole('heading',{name:'Checking signed model details'})).toHaveCount(0);
});

test('oversized source refuses before upload and leaves source trust ungranted',async({page})=>{
 await base(page,[]);let uploads=0;await page.route('**/api/upload-create',route=>{uploads++;return route.fulfill({json:{}});});await find(page);await page.getByRole('button',{name:'Choose a source file',exact:true}).click();await page.getByLabel('Source file',{exact:true}).setInputFiles({name:'oversize.json',mimeType:'application/json',buffer:Buffer.alloc(256*1024+1)});await expect(page.getByRole('alert')).toContainText('256 KB');expect(uploads).toBe(0);await expect(page.getByRole('button',{name:'Trust source and get listings'})).toHaveCount(0);
});

test('source task descriptions remain attributed and do not contact suppliers while browsing',async({page})=>{
 await base(page);let effects=0;
 await page.route('**/api/discovery-search',route=>route.fulfill({json:{results:[{...result,suppliers:[{...supplier,workloads:['CodeGeneration']}]}],next_offset:null}}));
 await page.route('**/api/discovery-start',route=>{effects++;return route.fulfill({json:job});});
 await find(page);await expect(page.getByText('Coding',{exact:true})).toBeVisible();
 await expect(page.getByText(supplier.summary,{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'About model descriptions',exact:true}).click();
 await expect(page.getByRole('tooltip')).toContainText('do not amend the signed model card');
 expect(effects).toBe(0);
});
