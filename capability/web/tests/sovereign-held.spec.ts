import {test,expect} from '@playwright/test';

const manifest='d'.repeat(64);
const held={manifest,name:'Rare model Q4_K_M',origin_repository:'gone-org/rare-model',origin_revision:'e'.repeat(40),format:'gguf',size_bytes:5_000_000,file_count:3,licence:'apache-2.0',workloads:['chat'],mirrors:['https://one.example','https://two.example'],mirror_count:2};

test('copies kept by community mirrors are found after browsing, and saved by their identity',async({page})=>{
  const calls:string[]=[];const plans:unknown[]=[];const starts:unknown[]=[];
  await page.route('**/api/library*',r=>r.fulfill({json:{host_kind:'sovereign',hub_search:true,copies:[],jobs:[],next_offset:null}}));
  await page.route('**/api/desktop-search',r=>r.fulfill({json:{catalog_id:'c'.repeat(64),results:[],total:0,next_offset:null}}));
  await page.route('**/api/desktop-source-read',r=>r.fulfill({json:{sources:[]}}));
  await page.route('**/api/desktop-source-search',r=>r.fulfill({json:{results:[],total:0,next_offset:null}}));
  await page.route('**/api/desktop-copy-search',r=>r.fulfill({json:{results:[],total:0,stored_total:0,next_offset:null}}));
  await page.route('**/api/desktop-held-refresh',r=>{calls.push('refresh');return r.fulfill({json:{mirrors:2}});});
  await page.route('**/api/desktop-held-search',r=>{calls.push('search:'+r.request().postDataJSON().query);return r.fulfill({json:{source:'mirrors',results:[held]}});});
  await page.route('**/api/desktop-hub-search',r=>r.fulfill({json:{source:'huggingface',results:[{repository:'gone-org/rare-model',name:'rare-model',owner:'gone-org',downloads:10,likes:0,task:null,library:null,gated:false}]}}));
  await page.route('**/api/desktop-plan',r=>{plans.push(r.request().postDataJSON());return r.fulfill({json:{plan_id:'p'.repeat(64),kind:'receive',origin:'mirror',name:held.name,size_bytes:5_000_000,file_count:3,details:{license:'apache-2.0',origin_check:'unconfirmed',mirrors:['one.example','two.example']},members:['gone-org/rare-model'],required_free_bytes:1,free_bytes:10**12,terms:{accept:[],licences:[]}}});});
  await page.route('**/api/desktop-start',r=>{starts.push(r.request().postDataJSON());return r.fulfill({json:{job_id:'j'.repeat(64),kind:'receive',state:'queued',name:held.name}});});
  await page.goto('/');
  await page.getByPlaceholder('Search by name or use').fill('rare');
  const card=page.getByRole('article',{name:held.name});
  await expect(card).toBeVisible();
  await expect(card.getByText('Kept by 2 mirrors')).toBeVisible();
  await expect(page.getByText('Community mirrors keep 1 version')).toBeVisible();
  expect(calls[0]).toBe('refresh');
  await card.getByRole('button',{name:'Keep a copy'}).click();
  await expect.poll(()=>plans.length).toBe(1);
  expect(plans[0]).toEqual({kind:'receive',origin:'mirror',manifest});
  await expect(page.getByText('Hugging Face couldn’t confirm where it came from',{exact:false})).toBeVisible();
  // Nothing independent vouches for it: keeping it needs an explicit acknowledgement.
  const keep=page.getByRole('button',{name:'Keep a copy'}).last();
  await expect(keep).toBeDisabled();
  await page.getByText('I understand Sovereign couldn’t confirm where this copy came from',{exact:false}).click();
  await expect(keep).toBeEnabled();
  await keep.click();
  await expect.poll(()=>starts.length).toBe(1);
  expect(starts[0]).toMatchObject({plan_id:'p'.repeat(64),confirmed:true,accept_unconfirmed_origin:true});
});

test('with Hugging Face search on by default, a first launch contacts nothing until a search',async({page})=>{
  const calls:string[]=[];
  await page.route('**/api/library*',r=>r.fulfill({json:{host_kind:'sovereign',hub_search:true,copies:[],jobs:[],next_offset:null}}));
  await page.route('**/api/desktop-search',r=>r.fulfill({json:{catalog_id:null,results:[],total:0,next_offset:null}}));
  await page.route('**/api/desktop-source-read',r=>r.fulfill({json:{sources:[]}}));
  await page.route('**/api/desktop-copy-search',r=>r.fulfill({json:{results:[],total:0,stored_total:0,next_offset:null}}));
  await page.route('**/api/desktop-held-refresh',r=>{calls.push('refresh');return r.fulfill({json:{mirrors:2}});});
  await page.route('**/api/desktop-held-search',r=>{calls.push('held:'+r.request().postDataJSON().query);return r.fulfill({json:{source:'mirrors',results:[]}});});
  await page.route('**/api/desktop-hub-search',r=>{calls.push('hub:'+r.request().postDataJSON().query);return r.fulfill({json:{source:'huggingface',results:[]}});});
  await page.goto('/');
  const search=page.getByPlaceholder('Search Hugging Face by model name');
  await expect(search).toBeVisible();
  await page.waitForTimeout(1500);
  expect(calls).toEqual([]);
  await search.fill('qwen');
  await expect.poll(()=>calls).toEqual(expect.arrayContaining(['refresh','hub:qwen']));
  await expect(page.getByText('Your search words also go to huggingface.co. You can turn this off in Settings.')).toBeVisible();
});

test('clearing the first search while mirrors are looked up never leaves it stuck',async({page})=>{
  let release:()=>void=()=>{};const refreshed=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/api/library*',r=>r.fulfill({json:{host_kind:'sovereign',hub_search:true,copies:[],jobs:[],next_offset:null}}));
  await page.route('**/api/desktop-search',r=>r.fulfill({json:{catalog_id:null,results:[],total:0,next_offset:null}}));
  await page.route('**/api/desktop-source-read',r=>r.fulfill({json:{sources:[]}}));
  await page.route('**/api/desktop-copy-search',r=>r.fulfill({json:{results:[],total:0,stored_total:0,next_offset:null}}));
  await page.route('**/api/desktop-held-refresh',async r=>{await refreshed;return r.fulfill({json:{mirrors:2}});});
  await page.route('**/api/desktop-held-search',r=>r.fulfill({json:{source:'mirrors',results:[held]}}));
  await page.route('**/api/desktop-hub-search',r=>r.fulfill({json:{source:'huggingface',results:[]}}));
  await page.goto('/');
  const search=page.getByPlaceholder('Search Hugging Face by model name');
  await search.fill('rare');
  await expect(page.getByText('Looking for models that community mirrors keep…')).toBeVisible();
  await search.fill('');
  release();
  await page.waitForTimeout(300);
  await search.fill('rare');
  await expect(page.getByRole('article',{name:held.name})).toBeVisible();
  await expect(page.getByText('Looking for models that community mirrors keep…')).toHaveCount(0);
});
