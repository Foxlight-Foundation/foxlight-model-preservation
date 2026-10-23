import {test,expect} from '@playwright/test';
test.beforeEach(async({page})=>{await page.route('**/api/core-features',route=>route.fulfill({json:{'Portable handoffs':false}}));});
const device={location_id:'device',label:'This device',purpose:'private',reachable:true,free_bytes:100000000,publisher_key_id:'capture-key'};
const nas={...device,location_id:'folder_nas',label:'My NAS'};
const model={model_id:'example/base',generation_id:'a'.repeat(64),size_bytes:100,verification:'custom'};
const copy={copy_id:'b'.repeat(64),model_id:model.model_id,location_id:'device',manifest_id:'c'.repeat(64),audience:'private',size_bytes:100,checked_at:1,member_count:2};
const review={review_id:'d'.repeat(64),label:'My backup',creates_folder:true,free_bytes:10000000,expires_at:9999999999};

test('Guided folder setup binds review and explicit consent without starting a copy',async({page})=>{
  let ready=false,effects=0,selected:unknown;
  await page.route('**/api/library?offset=*',route=>route.fulfill({json:{locations:ready?[device,nas]:[],default_location_id:ready?nas.location_id:null,models:[model],copies:[],jobs:[],next_offset:null}}));
  await page.route('**/api/folder-review',route=>{selected=route.request().postDataJSON();return route.fulfill({json:review});});
  await page.route('**/api/folder-setup',route=>{expect(route.request().postDataJSON()).toEqual({review_id:review.review_id,confirmed:true});ready=true;return route.fulfill({json:nas});});
  await page.route('**/api/plan',route=>route.fulfill({json:{plan_id:'f'.repeat(64),kind:'preserve',model_id:model.model_id,location_id:nas.location_id,size_bytes:100,members:[model.model_id],member_count:1,expires_at:9999999999}}));
  await page.route('**/api/start',route=>{effects++;return route.fulfill({json:{}});});
  await page.goto('/');
  await page.getByRole('button',{name:'Use a folder or NAS',exact:true}).click();
  await page.getByRole('button',{name:'Add folder or NAS',exact:true}).click();
  await page.getByLabel('Folder on this Core host').fill('/mounted/backup');
  await page.getByRole('button',{name:'Review storage',exact:true}).click();
  expect(selected).toEqual({path:'/mounted/backup',label:'My backup'});
  await expect(page.getByRole('button',{name:'Use this storage',exact:true})).toBeDisabled();
  await page.getByLabel('Use this folder for private model copies').check();
  await page.getByRole('button',{name:'Use this storage',exact:true}).click();
  await expect(page.getByText('Storage is ready for new copies.',{exact:false})).toBeVisible();
  await page.getByRole('button',{name:'Library',exact:true}).click();
  await expect(page.getByText('New copies go to My NAS',{exact:false})).toBeVisible();
  await page.getByRole('button',{name:'Preserve',exact:true}).click();
  await expect(page.getByText('My NAS',{exact:true})).toBeVisible();
  expect(effects).toBe(0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});

test('editing or leaving a pending folder review discards late preview and consent',async({page})=>{
  await page.route('**/api/library?offset=*',route=>route.fulfill({json:{locations:[device],default_location_id:'device',models:[],copies:[],jobs:[],next_offset:null}}));
  let release!:()=>void;
  const delayed=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/api/folder-review',async route=>{await delayed;await route.fulfill({json:review});});
  await page.goto('/');
  await page.getByRole('button',{name:'Storage',exact:true}).click();
  await page.getByRole('button',{name:'Add folder or NAS',exact:true}).click();
  await page.getByLabel('Folder on this Core host').fill('/first');
  await page.getByRole('button',{name:'Review storage',exact:true}).click();
  await page.getByLabel('Folder on this Core host').fill('/different');
  release();
  await expect(page.getByRole('button',{name:'Review storage',exact:true})).toBeEnabled();
  await expect(page.getByRole('button',{name:'Use this storage',exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'Library',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Your models',exact:true})).toBeVisible();
  await expect(page.getByLabel('Folder on this Core host')).toHaveCount(0);
});

test('offline default never falls back and does not block copies on other storage',async({page})=>{
  let chosen=nas.location_id,planned:unknown;
  await page.route('**/api/library?offset=*',route=>route.fulfill({json:{locations:[device,{...nas,reachable:false}],default_location_id:chosen,models:[model],copies:[copy],jobs:[],next_offset:null}}));
  await page.route('**/api/plan',route=>{planned=route.request().postDataJSON();return route.fulfill({json:{plan_id:'e'.repeat(64),kind:'check',location_id:'device',model_id:model.model_id,size_bytes:100,members:[model.model_id],member_count:1,expires_at:9999999999}});});
  await page.route('**/api/storage-default',route=>{expect(route.request().postDataJSON()).toEqual({location_id:'device'});chosen='device';return route.fulfill({json:device});});
  await page.goto('/');
  await expect(page.getByRole('button',{name:'Preserve',exact:true})).toBeDisabled();
  await expect(page.getByRole('button',{name:'Check copy',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'Check copy',exact:true}).click();
  expect(planned).toEqual({kind:'check',location_id:'device',copy_id:copy.copy_id});
  await page.getByRole('button',{name:'Storage',exact:true}).click();
  await page.getByRole('button',{name:'Use for new copies',exact:true}).click();
  await expect(page.getByText('Private · Destination for new copies',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Library',exact:true}).click();
  await expect(page.getByRole('button',{name:'Preserve',exact:true})).toBeEnabled();
});
