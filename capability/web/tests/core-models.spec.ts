import {test,expect} from '@playwright/test';
const id='a'.repeat(64),other='b'.repeat(64);
const details={description:'An installed writing model for draft text.',purposes:['chat'],repository:'example/writer',context_length:8192,license:'Apache-2.0'};
const copy={copy_id:other,model_id:'example/saved',manifest_id:other,location_id:'device',audience:'private',size_bytes:200,checked_at:1,member_count:2};
test('Core Library explains installed and orphaned saved models without effects',async({page})=>{
 let effects=0;
 await page.route('**/api/core-features',route=>route.fulfill({json:{'Portable handoffs':false}}));
 await page.route('**/api/library?offset=*',route=>route.fulfill({json:{locations:[{location_id:'device',label:'This device',purpose:'private',reachable:true,free_bytes:100000,publisher_key_id:'capture'}],models:[{model_id:'example/writer',generation_id:id,size_bytes:100,verification:'custom',details}],copies:[copy],copy_details:{[other]:{description:'A saved image model.',purposes:['images'],format:'safetensors'}},jobs:[],next_offset:null}}));
 page.on('request',request=>{if(/api\/(plan|start|delivery-plan|discovery-start)$/.test(request.url()))effects++;});
 await page.goto('/');
 await expect(page.getByText(details.description,{exact:true})).toBeVisible();
 await expect(page.getByText('A saved image model.',{exact:true})).toBeVisible();
 const card=page.getByRole('article',{name:'example/writer',exact:true});
 await card.getByText('About this model',{exact:true}).click();
 await expect(card.getByText('8,192 tokens, stated in card',{exact:true})).toBeVisible();
 await expect(card.getByText(details.license,{exact:true})).toBeVisible();
 await expect(card.getByRole('button',{name:'Preserve',exact:true})).toBeEnabled();
 await expect(page.getByRole('button',{name:'Restore',exact:true})).toBeEnabled();
 expect(effects).toBe(0);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});

for(const status of ['missing','unavailable','partial'])test(`installed ${status} metadata never claims an archive already exists`,async({page})=>{
 await page.route('**/api/core-features',route=>route.fulfill({json:{'Portable handoffs':false}}));
 await page.route('**/api/library?offset=*',route=>route.fulfill({json:{locations:[{location_id:'device',label:'This device',purpose:'private',reachable:true,free_bytes:100000,publisher_key_id:'capture'}],models:[{model_id:'example/unpreserved',generation_id:id,size_bytes:100,verification:'custom',...(status==='missing'?{}:{details:{metadata_status:status}})}],copies:[],jobs:[],next_offset:null}}));
 await page.goto('/');const card=page.getByRole('article',{name:'example/unpreserved',exact:true});
 await expect(card.getByText('No readable description is available for this installation.',{exact:true})).toBeVisible();
 await expect(card.getByText('No saved copy yet.',{exact:false})).toBeVisible();
 await card.getByText('About this model',{exact:true}).click();
 await expect(card.getByText('Your copy is kept',{exact:false})).toHaveCount(0);
 await expect(card.getByText('Its original documents stay with the archive.',{exact:false})).toHaveCount(0);
 await expect(card.getByRole('button',{name:'Preserve',exact:true})).toBeEnabled();
});
