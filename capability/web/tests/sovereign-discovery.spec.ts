import {test,expect} from '@playwright/test';
const id='a'.repeat(64),source={source_id:id,name:'Followed publisher',enabled:true,freshness:'fresh',accepted_at:null,expires:null,last_error:null};
const listing={manifest_id:id,source_id:id,source_name:source.name,entry_id:'sample',name:'Useful model',summary:'A model for writing.',formats:['gguf'],workloads:['TextGeneration'],freshness:'fresh',has_candidate_keys:true,listed_source_count:2,suggested_mirror_count:2};
test.beforeEach(async({page})=>{
 await page.route('**/api/library*',r=>r.fulfill({json:{host_kind:'sovereign',copies:[],jobs:[],next_offset:null}}));
 await page.route('**/api/desktop-search',r=>r.fulfill({json:{catalog_id:null,results:[],total:0,next_offset:null}}));
 await page.route('**/api/desktop-source-read',r=>r.fulfill({json:{sources:[]}}));
 await page.route('**/api/desktop-source-search',r=>r.fulfill({json:{results:[],total:0,next_offset:null}}));
});

test('Find defaults to complete-map review and keeps unsupported signed listings inspectable',async({page})=>{
 const ready={card_id:id,name:'Complete model',publisher:'example',format:'gguf',quantization:'Q4',size_bytes:1024,workloads:['TextGeneration'],complete_file_map:true,download_support:'ready'};
 const unavailable={...ready,card_id:'b'.repeat(64),name:'Listing without a complete map',complete_file_map:false,download_support:'complete_signed_file_map_required'};
 await page.route('**/api/desktop-search',r=>{const only=r.request().postDataJSON().ready_only;return r.fulfill({json:{catalog_id:id,results:only?[ready]:[ready,unavailable],total:only?1:2,ready_total:1,matching_total:2,next_offset:null}});});
 await page.goto('/');await expect(page.getByRole('heading',{name:ready.name,exact:true})).toBeVisible();
 await expect(page.getByLabel('Preservation support',{exact:true})).toHaveValue('ready');
 await expect(page.getByRole('heading',{name:unavailable.name,exact:true})).toHaveCount(0);
 await page.getByLabel('Preservation support',{exact:true}).selectOption('all');
 const card=page.getByRole('article').filter({has:page.getByRole('heading',{name:unavailable.name,exact:true})});
 await expect(card.getByRole('button',{name:'Keep a copy',exact:true})).toBeDisabled();
 await expect(card).toContainText('verification details for every required file');
 await page.getByRole('button',{name:'About preservation support',exact:true}).click();
 await expect(page.getByRole('tooltip')).toContainText('does not test host availability');
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});

test('other followed-source routes need a changed review before any request starts',async({page})=>{
 let plans=0,starts=0;
 await page.route('**/api/desktop-source-read',r=>r.fulfill({json:{sources:[source]}}));
 await page.route('**/api/desktop-source-search',r=>r.fulfill({json:{results:[listing],total:1,next_offset:null}}));
 await page.route('**/api/desktop-plan',r=>{const body=r.request().postDataJSON();plans++;return r.fulfill({json:{...body,include_followed_mirrors:body.include_followed_mirrors===true,plan_id:(body.include_followed_mirrors?'b':'a').repeat(64),name:listing.name,size_bytes:0,expires_at:9999999999,mirrors:body.include_followed_mirrors?['https://first.example','https://second.example']:['https://first.example'],additional_mirror_count:1,mirror_sources:[{name:source.name,freshness:'fresh'}]}});});
 await page.route('**/api/desktop-start',r=>{starts++;return r.fulfill({json:{}});});
 await page.goto('/');await page.getByRole('button',{name:'Review signed details',exact:true}).click();
 await expect(page.getByText('https://second.example',{exact:true})).toHaveCount(0);
 await page.getByText('Other listed copies',{exact:true}).click();
 await expect(page.getByText('No address has been checked for availability.',{exact:false})).toBeVisible();
 await page.getByRole('checkbox',{name:'Also try other followed sources for this exact copy.',exact:true}).check();
 await page.getByRole('button',{name:'Review connection changes',exact:true}).click();
 await expect(page.getByText('https://second.example',{exact:true})).toBeVisible();expect(plans).toBe(2);expect(starts).toBe(0);
 await page.getByRole('button',{name:'Get signed model details',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Activity',exact:true})).toBeVisible();expect(starts).toBe(1);
});

for(const area of ['sharing','sources'])test(`${area} Previous returns to actual visited byte-bounded page offsets`,async({page})=>{
 const offsets:number[]=[];
 if(area==='sharing')await page.route('**/api/desktop-sharing-read',r=>{const offset=r.request().postDataJSON().offset??0;offsets.push(offset);return r.fulfill({json:{public_copies:[{public_id:String(offset).padStart(64,'a'),manifest_id:id,name:`Page ${offset}`,size_bytes:1,member_count:1}],next_offset:offset===0?3:offset===3?5:null}});});
 else{
  await page.route('**/api/desktop-source-read',r=>r.fulfill({json:{sources:[source]}}));
  await page.route('**/api/desktop-source-search',r=>{const offset=r.request().postDataJSON().offset??0;offsets.push(offset);return r.fulfill({json:{results:[{...listing,name:`Page ${offset}`}],total:6,next_offset:offset===0?3:offset===3?5:null}});});
 }
 await page.goto('/');if(area==='sharing')await page.getByRole('button',{name:'Sharing',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Page 0',exact:true})).toBeVisible();
 const more=area==='sharing'?'More public copies':'More source results',previous=area==='sharing'?'Previous public copies':'Previous source results';
 await page.getByRole('button',{name:more,exact:true}).click();await expect(page.getByRole('heading',{name:'Page 3',exact:true})).toBeVisible();
 await page.getByRole('button',{name:more,exact:true}).click();await expect(page.getByRole('heading',{name:'Page 5',exact:true})).toBeVisible();
 await page.getByRole('button',{name:previous,exact:true}).click();await expect(page.getByRole('heading',{name:'Page 3',exact:true})).toBeVisible();
 await page.getByRole('button',{name:previous,exact:true}).click();await expect(page.getByRole('heading',{name:'Page 0',exact:true})).toBeVisible();
 expect(offsets.slice(-5)).toEqual([0,3,5,3,0]);
});

test('HTTP-only mirror details show connection choices before connecting',async({page})=>{
 let starts=0;
 await page.route('**/api/desktop-source-read',r=>r.fulfill({json:{sources:[source]}}));
 await page.route('**/api/desktop-source-search',r=>r.fulfill({json:{results:[listing],total:1,next_offset:null}}));
 await page.route('**/api/desktop-plan',r=>r.fulfill({json:{...r.request().postDataJSON(),plan_id:id,name:listing.name,size_bytes:0,expires_at:9999999999,mirrors:['http://mirror.example'],additional_mirror_count:0}}));
 await page.route('**/api/desktop-start',r=>{starts++;return r.fulfill({json:{}});});
 await page.goto('/');await page.getByRole('button',{name:'Review signed details',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Connection choices',exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'Get signed model details',exact:true})).toBeDisabled();
 await expect(page.getByRole('checkbox',{name:'Allow unencrypted HTTP for this metadata request.',exact:true})).not.toBeChecked();expect(starts).toBe(0);
 await page.getByRole('checkbox',{name:'Allow unencrypted HTTP for this metadata request.',exact:true}).check();
 await page.getByRole('button',{name:'Review connection changes',exact:true}).click();
 await expect(page.getByRole('button',{name:'Get signed model details',exact:true})).toBeEnabled();expect(starts).toBe(0);
});

for(const area of ['sharing','sources'])test(`${area} More waits for the pending page without duplicating history`,async({page})=>{
 let release!:()=>void;
 const pending=new Promise<void>(resolve=>{release=resolve;});
 if(area==='sharing')await page.route('**/api/desktop-sharing-read',async r=>{
  const offset=r.request().postDataJSON().offset??0;if(offset===3)await pending;
  await r.fulfill({json:{public_copies:[{public_id:String(offset).padStart(64,'a'),manifest_id:id,name:`Page ${offset}`,size_bytes:1,member_count:1}],next_offset:offset===0?3:5}});
 });
 else{
  await page.route('**/api/desktop-source-read',r=>r.fulfill({json:{sources:[source]}}));
  await page.route('**/api/desktop-source-search',async r=>{
   const offset=r.request().postDataJSON().offset??0;if(offset===3)await pending;
   await r.fulfill({json:{results:[{...listing,name:`Page ${offset}`}],total:6,next_offset:offset===0?3:5}});
  });
 }
 await page.goto('/');if(area==='sharing')await page.getByRole('button',{name:'Sharing',exact:true}).click();
 const more=page.getByRole('button',{name:area==='sharing'?'More public copies':'More source results',exact:true});
 await expect(page.getByRole('heading',{name:'Page 0',exact:true})).toBeVisible();
 await more.click();await expect(more).toBeDisabled();
 release();await expect(page.getByRole('heading',{name:'Page 3',exact:true})).toBeVisible();await expect(more).toBeEnabled();
 await page.getByRole('button',{name:area==='sharing'?'Previous public copies':'Previous source results',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Page 0',exact:true})).toBeVisible();
});
