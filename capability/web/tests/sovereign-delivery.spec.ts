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

test('public publication reviews all card fields and starts no listener',async({page})=>{
 let starts=0;
 await page.route('**/api/desktop-sharing-review',r=>r.fulfill({json:{name:copy.name,already_public:false,members:[{member_id:'base',repository:'example/model',license_id:'',terms_reference:''}]}}));
 await page.route('**/api/desktop-plan',r=>{expect(r.request().postDataJSON()).toEqual({kind:'publish',copy_id:id,decisions:[{member_id:'base',license_id:'Apache-2.0',terms_reference:'LICENSE'}]});return r.fulfill({json:{plan_id:id,kind:'publish',name:copy.name,size_bytes:1024,expires_at:9999999999,public_manifest_id:id,public_manifest:{members:[{retained_card:{future_property:'Preserved original field'},files:[{path:'vision.gguf'}]}]}}});});
 await page.route('**/api/desktop-start',r=>{starts++;return r.fulfill({json:{}});});
 await page.goto('/');await page.getByRole('button',{name:'Share',exact:true}).click();
 const review=page.getByRole('button',{name:'Review exact public files',exact:true});await expect(review).toBeDisabled();
 await page.getByLabel('License for base',{exact:true}).fill('Apache-2.0');await page.getByLabel('License file or terms reference for base',{exact:true}).fill('LICENSE');await review.click();
 await page.getByText('All public model-card fields, files & terms',{exact:true}).click();await expect(page.getByText('Preserved original field',{exact:false})).toBeVisible();
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
