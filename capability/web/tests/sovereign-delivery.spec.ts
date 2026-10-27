import {createHash} from 'node:crypto';
import {test,expect} from '@playwright/test';
const id='a'.repeat(64),key='ed25519:'+id,copy={copy_id:id,manifest_id:id,name:'Image understanding model',size_bytes:1024,member_count:2};
const empty={host_kind:'sovereign',copies:[copy],jobs:[],next_offset:null};
test.beforeEach(async({page})=>{
 await page.route('**/api/library*',r=>r.fulfill({json:empty}));
 await page.route('**/api/desktop-copy-search',r=>r.fulfill({json:{results:[copy],stored_total:1,total:1,next_offset:null}}));
 await page.route('**/api/desktop-search',r=>r.fulfill({json:{catalog_id:null,results:[],total:0,next_offset:null}}));
 await page.route('**/api/desktop-source-read',r=>r.fulfill({json:{sources:[]}}));
 await page.route('**/api/desktop-source-search',r=>r.fulfill({json:{results:[],total:0,next_offset:null}}));
 await page.route('**/api/desktop-backup-read',r=>r.fulfill({json:{destinations:[]}}));
 await page.route('**/api/desktop-sharing-read',r=>r.fulfill({json:{public_copies:[]}}));
});

test('a sharing file is offline first and keys are not accepted by opening it',async({page})=>{
 let plans=0,starts=0;
 await page.route('**/api/desktop-upload-create',r=>r.fulfill({json:{upload_id:'b'.repeat(32),offset:0}}));
 await page.route('**/api/upload/*',r=>r.fulfill({json:{upload_id:'b'.repeat(32),offset:16}}));
 await page.route('**/api/desktop-upload-remove',r=>r.fulfill({json:{removed:true}}));
 await page.route('**/api/desktop-handoff-review',r=>r.fulfill({json:{reference_job_id:id,manifest_id:id,name:copy.name,size_bytes:1024,member_count:2,members:['example/model','example/projector'],keys:[key],threshold:1,revoked_keys:[],mirrors:['https://mirror.example'],from_handoff:true}}));
 await page.route('**/api/desktop-plan',r=>{plans++;return r.fulfill({json:{plan_id:id,kind:'source_receive',name:copy.name,size_bytes:1024,max_bytes:2048,expires_at:9999999999}});});
 await page.route('**/api/desktop-start',r=>{starts++;return r.fulfill({json:{}});});
 await page.goto('/');await page.getByRole('button',{name:'Find',exact:true}).click();
 await page.getByLabel('Open sharing file',{exact:true}).setInputFiles({name:'model.foxhandoff',mimeType:'application/json',buffer:Buffer.alloc(16)});
 const review=page.getByRole('button',{name:'Review download & preserve',exact:true});
 await expect(review).toBeDisabled();expect(plans).toBe(0);expect(starts).toBe(0);
 await expect(page.getByText('These are the sharing file’s capture keys.',{exact:false})).toBeVisible();
 await expect(page.getByRole('checkbox',{name:'Allow unencrypted HTTP for this model download.'})).not.toBeChecked();
 await page.getByRole('checkbox').filter({hasText:''}).first().check();
 await review.click();expect(plans).toBe(1);expect(starts).toBe(0);
 await expect(page.getByRole('button',{name:'Download & preserve',exact:true})).toBeDisabled();
});

test('a backup folder needs review and remembering it writes no model',async({page})=>{
 let setups=0,starts=0;
 await page.route('**/api/desktop-folder-choose',r=>r.fulfill({json:{path:'/Volumes/Other/Sovereign Backups'}}));
 await page.route('**/api/desktop-backup-review',r=>{expect(r.request().postDataJSON().path).toBe('/Volumes/Other/Sovereign Backups');return r.fulfill({json:{review_id:id,label:'My backup drive',path:'/Volumes/Other/Sovereign Backups',creates_folder:true,same_filesystem:false,free_bytes:10**10,expires_at:9999999999}});});
 await page.route('**/api/desktop-backup-setup',r=>{setups++;return r.fulfill({json:{}});});
 await page.route('**/api/desktop-start',r=>{starts++;return r.fulfill({json:{}});});
 await page.goto('/');await page.getByRole('button',{name:'Backups',exact:true}).click();await page.getByRole('button',{name:'Add backup folder',exact:true}).click();
 await page.getByRole('button',{name:'Choose folder',exact:true}).click();await expect(page.getByLabel('Folder path',{exact:true})).toHaveValue('/Volumes/Other/Sovereign Backups');
 await page.getByRole('button',{name:'Review folder',exact:true}).click();
 const remember=page.getByRole('button',{name:'Remember this folder',exact:true});await expect(remember).toBeDisabled();expect(setups).toBe(0);expect(starts).toBe(0);
 await page.getByRole('checkbox',{name:'Remember this folder. I will choose which models to back up, and when.',exact:true}).check();await remember.click();
 await expect(page.getByRole('heading',{name:'Backup folders',exact:true})).toBeVisible();expect(setups).toBe(1);expect(starts).toBe(0);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});

test('a failed backup plan keeps the folder selection open with its explanation',async({page})=>{
 await page.route('**/api/desktop-backup-read',r=>r.fulfill({json:{destinations:[{destination_id:id,label:'My NAS',path:'/Volumes/NAS/Backups',same_filesystem:false}]}}));
 await page.route('**/api/desktop-plan',r=>r.fulfill({status:409,json:{error:'backup_destination_changed'}}));
 await page.goto('/');await page.getByRole('button',{name:'Back up',exact:true}).click();await page.getByRole('button',{name:'Review backup to My NAS',exact:true}).click();
 await expect(page.getByRole('dialog')).toBeVisible();await expect(page.getByRole('alert')).toContainText('changed or disconnected');
});

test('public publication decodes original cards and starts no listener',async({page})=>{
 let starts=0;
 const originalCard={card_id:'sha256:'+id,card:{future_property:'Preserved original field',pipeline_tag:'image-text-to-text'}};
 const document=JSON.stringify({manifest:{members:[{member_id:'base',origin_repository:'example/model',retained_card:{schema_version:'foxlight.retained-card.v1',card_base64:Buffer.from(JSON.stringify(originalCard)).toString('base64'),registry_proof:null},files:[{path:'vision.gguf',size:1024}]}]},cards:[{member_id:'base',card:originalCard}]});
 const hash=createHash('sha256').update(document).digest('hex');
 await page.route('**/api/desktop-public-review/*',r=>r.fulfill({contentType:'application/json',body:document}));
 await page.route('**/api/desktop-sharing-review',r=>r.fulfill({json:{name:copy.name,already_public:false,members:[{member_id:'base',repository:'example/model',license_id:'',terms_reference:''}]}}));
 await page.route('**/api/desktop-plan',r=>{expect(r.request().postDataJSON()).toEqual({kind:'publish',copy_id:id,decisions:[{member_id:'base',license_id:'Apache-2.0',terms_reference:'LICENSE'}]});return r.fulfill({json:{plan_id:id,kind:'publish',name:copy.name,size_bytes:1024,expires_at:9999999999,public_manifest_id:id,public_review_sha256:hash}});});
 await page.route('**/api/desktop-start',r=>{starts++;return r.fulfill({json:{}});});
 await page.goto('/');await page.getByRole('button',{name:'Share',exact:true}).click();
 const review=page.getByRole('button',{name:'Review exact public files',exact:true});await expect(review).toBeDisabled();
 await page.getByLabel('License for base',{exact:true}).fill('Apache-2.0');await page.getByLabel('License file or terms reference for base',{exact:true}).fill('LICENSE');await review.click();
 await page.getByText('Original model card · base',{exact:true}).click();await expect(page.getByText('Preserved original field',{exact:false})).toBeVisible();
 await expect(page.getByText('Decoded original card fields, including properties Sovereign does not interpret.',{exact:false})).toBeVisible();
 await page.getByText('Files to publish · example/model',{exact:true}).click();await expect(page.getByText('vision.gguf · 1.0 KB',{exact:true})).toBeVisible();
 const create=page.getByRole('button',{name:'Create public copy',exact:true});await expect(create).toBeDisabled();expect(starts).toBe(0);
 await page.getByRole('checkbox',{name:'I reviewed these files, model-card fields and redistribution terms and want to create this public copy.',exact:true}).check();await create.click();
 await expect(page.getByRole('heading',{name:'Activity',exact:true})).toBeVisible();expect(starts).toBe(1);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});

test('a retained public copy requires fresh consent before a finite session',async({page})=>{
 let starts=0;
 await page.route('**/api/desktop-sharing-read',r=>r.fulfill({json:{public_copies:[{public_id:id,manifest_id:id,name:copy.name,size_bytes:1024,member_count:2}]}}));
 await page.route('**/api/desktop-plan',r=>r.fulfill({json:{plan_id:id,kind:'share',name:copy.name,size_bytes:1024,expires_at:9999999999,seconds:3600,daily_upload_bytes:67108864,monthly_upload_bytes:67108864}}));
 await page.route('**/api/desktop-start',r=>{starts++;return r.fulfill({json:{}});});
 await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();expect(starts).toBe(0);await page.getByRole('button',{name:'Review sharing',exact:true}).click();
 const start=page.getByRole('button',{name:'Start sharing for one hour',exact:true});await expect(start).toBeDisabled();await expect(page.getByText('Direct HTTP sharing is not anonymous or encrypted.',{exact:false})).toBeVisible();
 await page.getByRole('checkbox',{name:'Allow this public HTTP sharing session. Keep retained copies when it stops; do not restart sharing automatically.',exact:true}).check();await start.click();expect(starts).toBe(1);
 await expect(page.getByRole('heading',{name:'Activity',exact:true})).toBeVisible();
});

for(const ready of [false,true])test(`recipient handoff availability follows actual prepared selection ${ready}`,async({page})=>{
 await page.route('**/api/library*',r=>r.fulfill({json:{...empty,jobs:[{job_id:id,kind:'share',state:'running',name:copy.name,error_code:null,output:null,copy_id:null,sharing:{running:true,http_port:12345,handoff_ready:ready}}]}}));
 await page.goto('/');await page.getByRole('button',{name:'Activity',exact:true}).click();
 await expect(page.getByRole('link',{name:'Save sharing file for recipient',exact:true})).toHaveCount(ready?1:0);
 await expect(page.getByText('External reachability is not established.',{exact:false})).toBeVisible();
});


test('an explicit stop is a normal retained outcome rather than a failure warning',async({page})=>{
 await page.route('**/api/library*',r=>r.fulfill({json:{...empty,jobs:[{job_id:id,kind:'share',state:'cancelled',name:copy.name,error_code:'cancelled',output:null,copy_id:null,sharing:{running:false,http_port:null,handoff_ready:true}}]}}));
 await page.goto('/');await page.getByRole('button',{name:'Activity',exact:true}).click();
 await expect(page.getByText('Stopped at your request. Your saved copies and completed backups are kept.',{exact:true})).toBeVisible();
 await expect(page.getByRole('link',{name:'Save sharing file for recipient',exact:true})).toHaveCount(0);
 await expect(page.getByText('This action could not be completed.',{exact:false})).toHaveCount(0);
});


for(const destination of ['Find','Close backup selection'])test(`a late backup preview cannot reopen after ${destination}`,async({page})=>{
 let release!:()=>void,arrived!:()=>void,starts=0;
 const gate=new Promise<void>(resolve=>release=resolve),waiting=new Promise<void>(resolve=>arrived=resolve);
 await page.route('**/api/desktop-backup-read',r=>r.fulfill({json:{destinations:[{destination_id:id,label:'My NAS',path:'/Volumes/NAS/Backups',same_filesystem:false}]}}));
 await page.route('**/api/desktop-plan',async r=>{arrived();await gate;await r.fulfill({json:{plan_id:id,kind:'backup',name:copy.name,size_bytes:1024,expires_at:9999999999}});});
 await page.route('**/api/desktop-start',r=>{starts++;return r.fulfill({json:{}});});
 await page.goto('/');await page.getByRole('button',{name:'Back up',exact:true}).click();await page.getByRole('button',{name:'Review backup to My NAS',exact:true}).click();await waiting;
 await page.getByRole('button',{name:destination,exact:true}).click();
 const response=page.waitForResponse(r=>r.url().endsWith('/api/desktop-plan'));release();await (await response).finished();
 await page.waitForTimeout(250);await expect(page.getByRole('dialog')).toHaveCount(0);expect(starts).toBe(0);
});

test('an isolated LAN address needs a new review and fresh consent',async({page})=>{
 let plans=0,starts=0;
 await page.route('**/api/desktop-sharing-read',r=>r.fulfill({json:{public_copies:[{public_id:id,manifest_id:id,name:copy.name,size_bytes:1024,member_count:2}]}}));
 await page.route('**/api/desktop-plan',r=>{plans++;const value=r.request().postDataJSON();return r.fulfill({json:{plan_id:id,public_id:id,kind:'share',name:copy.name,size_bytes:1024,expires_at:9999999999,advertised_address:value.advertised_address??null}});});
 await page.route('**/api/desktop-start',r=>{starts++;return r.fulfill({json:{}});});
 await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();await page.getByRole('button',{name:'Review sharing',exact:true}).click();
 await page.getByText('Address for recipients · optional',{exact:true}).click();await page.getByLabel('Reachable IPv4 address',{exact:true}).fill('192.0.2.10');
 await page.getByRole('button',{name:'Review address change',exact:true}).click();
 await expect(page.getByRole('button',{name:'Start sharing for one hour',exact:true})).toBeDisabled();expect(plans).toBe(2);expect(starts).toBe(0);
 await page.getByRole('checkbox',{name:'Allow this public HTTP sharing session. Keep retained copies when it stops; do not restart sharing automatically.',exact:true}).check();
 await page.getByRole('button',{name:'Start sharing for one hour',exact:true}).click();await expect(page.getByRole('heading',{name:'Activity',exact:true})).toBeVisible();expect(starts).toBe(1);
});


test('changed public review metadata cannot reach publication consent',async({page})=>{
 let starts=0;
 await page.route('**/api/desktop-sharing-review',r=>r.fulfill({json:{name:copy.name,already_public:true,members:[]}}));
 await page.route('**/api/desktop-plan',r=>r.fulfill({json:{plan_id:id,kind:'publish',name:copy.name,size_bytes:1024,expires_at:9999999999,public_review_sha256:'0'.repeat(64)}}));
 await page.route('**/api/desktop-public-review/*',r=>r.fulfill({json:{manifest:{members:[]},cards:[]}}));
 await page.route('**/api/desktop-start',r=>{starts++;return r.fulfill({json:{}});});
 await page.goto('/');await page.getByRole('button',{name:'Share',exact:true}).click();await page.getByRole('button',{name:'Review exact public files',exact:true}).click();
 await expect(page.getByRole('alert')).toContainText('changed');await expect(page.getByRole('button',{name:'Create public copy',exact:true})).toHaveCount(0);expect(starts).toBe(0);
});

test('backup destinations on later pages remain selectable and forgettable',async({page})=>{
 const other='b'.repeat(64);let forgotten=false;
 await page.route('**/api/desktop-backup-read',r=>{const {offset}=r.request().postDataJSON();return r.fulfill({json:offset===1?{destinations:forgotten?[]:[{destination_id:other,label:'Other drive',path:'/Volumes/Other',same_filesystem:false}],next_offset:null}:{destinations:[{destination_id:id,label:'First drive',path:'/Volumes/First',same_filesystem:false}],next_offset:1}});});
 await page.route('**/api/desktop-backup-forget',r=>{expect(r.request().postDataJSON()).toEqual({destination_id:other});forgotten=true;return r.fulfill({json:{forgotten:true}});});
 await page.goto('/');await page.getByRole('button',{name:'Backups',exact:true}).click();
 const otherCard=page.getByRole('article').filter({has:page.getByRole('heading',{name:'Other drive',exact:true})});
 await otherCard.getByText('Folder & controls',{exact:true}).click();await otherCard.getByRole('button',{name:'Forget folder · keep all files',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Other drive',exact:true})).toHaveCount(0);
 await expect(page.getByRole('heading',{name:'First drive',exact:true})).toBeVisible();
});

test('Activity explains an isolated-LAN failure and cleanup stays an operator choice',async({page})=>{
 let cleaned=false;
 await page.route('**/api/library*',r=>r.fulfill({json:{...empty,jobs:[{job_id:id,kind:'share',state:'failed',name:copy.name,error_code:'sharing_address_required'},{job_id:'b'.repeat(64),kind:'publish',state:'interrupted',name:'Interrupted public copy',error_code:null,temporary_sharing_files:!cleaned}]}}));
 await page.route('**/api/desktop-sharing-work-remove',r=>{expect(r.request().postDataJSON()).toEqual({job_id:'b'.repeat(64)});cleaned=true;return r.fulfill({json:{removed:true}});});
 await page.goto('/');await page.getByRole('button',{name:'Activity',exact:true}).click();
 await expect(page.getByText('Sovereign could not choose an address.',{exact:false})).toBeVisible();
 await expect(page.getByText('Temporary publication files remain from this attempt.',{exact:false})).toBeVisible();expect(cleaned).toBe(false);
 await page.getByRole('button',{name:'Remove temporary sharing files',exact:true}).click();
 await expect(page.getByRole('button',{name:'Remove temporary sharing files',exact:true})).toHaveCount(0);
 await expect(page.getByRole('heading',{name:'Interrupted public copy',exact:true})).toBeVisible();
});
