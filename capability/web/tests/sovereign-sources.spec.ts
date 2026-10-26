import {test,expect} from '@playwright/test';
const id='a'.repeat(64),source={source_id:id,name:'Independent source',enabled:true,freshness:'fresh',accepted_at:null,expires:null,last_error:null};
const model={manifest_id:id,source_id:id,source_name:source.name,entry_id:'sample',name:'Independent model',summary:'Saved listing',formats:[],workloads:[],freshness:'fresh',has_candidate_keys:true};

test.beforeEach(async({page})=>{
 await page.route('**/api/library*',route=>route.fulfill({json:{host_kind:'sovereign',copies:[],jobs:[],next_offset:null}}));
 await page.route('**/api/desktop-search',route=>route.fulfill({json:{catalog_id:null,results:[],total:0,next_offset:null}}));
});

test('a pre-removal search cannot restore an unfollowed source',async({page})=>{
 let enabled=true,held=false,release!:()=>void,arrived!:()=>void;
 const gate=new Promise<void>(resolve=>release=resolve),waiting=new Promise<void>(resolve=>arrived=resolve);
 await page.route('**/api/desktop-source-read',route=>route.fulfill({json:{sources:enabled?[source]:[]}}));
 await page.route('**/api/desktop-source-search',async route=>{
  const snapshot=enabled;
  if(route.request().postDataJSON().query==='held'&&!held){held=true;arrived();await gate;}
  await route.fulfill({json:{results:snapshot?[model]:[],total:snapshot?1:0,next_offset:null}});
 });
 await page.route('**/api/desktop-source-remove',route=>{enabled=false;return route.fulfill({json:{removed:true}});});
 await page.goto('/');await expect(page.getByRole('heading',{name:model.name,exact:true})).toBeVisible();
 await page.getByText('Sources & updates',{exact:true}).click();
 await page.getByRole('searchbox',{name:'Search followed sources',exact:true}).fill('held');
 await waiting;
 await page.getByRole('button',{name:'Stop following',exact:true}).click();
 await expect(page.getByRole('button',{name:'Stop following',exact:true})).toHaveCount(0);
 const oldResponse=page.waitForResponse(response=>response.url().endsWith('/api/desktop-source-search')&&response.request().postDataJSON().query==='held');
 release();await (await oldResponse).finished();
 // Give the deliberately delayed Promise.all response a render turn.
 await page.waitForTimeout(300);
 await expect(page.getByRole('heading',{name:model.name,exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Stop following',exact:true})).toHaveCount(0);
});

for(const pause of ['follow','plan'])test(`dismissed review stays closed after delayed ${pause}`,async({page})=>{
 let followed=false,release!:()=>void,arrived!:()=>void,started=0;
 const gate=new Promise<void>(resolve=>release=resolve),waiting=new Promise<void>(resolve=>arrived=resolve);
 await page.route('**/api/desktop-source-read',route=>route.fulfill({json:{sources:followed?[{...source,freshness:'empty'}]:[]}}));
 await page.route('**/api/desktop-source-search',route=>route.fulfill({json:{results:[],total:0,next_offset:null}}));
 await page.route('**/api/desktop-upload-create',route=>route.fulfill({json:{upload_id:'b'.repeat(32),offset:0,size_bytes:16}}));
 await page.route('**/api/upload/*',route=>route.fulfill({json:{upload_id:'b'.repeat(32),offset:16,size_bytes:16,complete:true}}));
 await page.route('**/api/desktop-upload-remove',route=>route.fulfill({json:{removed:true}}));
 await page.route('**/api/desktop-source-review',route=>route.fulfill({json:{review_id:id,name:source.name,source_id:id,bootstrap_id:id,endpoints:['https://source.invalid'],candidate_root_keys:[id],root_threshold:1,cached:false}}));
 await page.route('**/api/desktop-source-follow',async route=>{if(pause==='follow'){arrived();await gate;}followed=true;await route.fulfill({json:{...source,freshness:'empty'}});});
 await page.route('**/api/desktop-plan',async route=>{if(pause==='plan'){arrived();await gate;}await route.fulfill({json:{plan_id:id,kind:'source_refresh',name:source.name,expires_at:9999999999,size_bytes:0}});});
 await page.route('**/api/desktop-start',route=>{started++;return route.fulfill({json:{}});});
 await page.goto('/');await page.getByLabel('Add source file',{exact:true}).setInputFiles({name:'source.foxsource',mimeType:'application/json',buffer:Buffer.alloc(16)});
 await page.getByRole('checkbox',{name:'I checked this source and trust it for model listings. Following starts no connections or sharing.',exact:true}).check();
 await page.getByRole('button',{name:'Follow source',exact:true}).click();await waiting;
 await page.getByRole('button',{name:'Close source review',exact:true}).click();
 const response=page.waitForResponse(value=>value.url().endsWith(pause==='follow'?'/api/desktop-source-follow':'/api/desktop-plan'));
 release();await (await response).finished();
 await expect(page.getByText('Sources & updates',{exact:true})).toBeVisible();
 await page.waitForTimeout(300);
 await expect(page.getByRole('dialog')).toHaveCount(0);expect(started).toBe(0);
});
