import {test,expect} from '@playwright/test';

const hubResult={repository:'Qwen/Qwen-Test',name:'Qwen-Test',owner:'Qwen',downloads:5000,likes:10,task:'text-generation',library:'transformers',gated:false};
const onBoth={repository:'Qwen/Qwen-Test',name:'Qwen-Test',owner:'Qwen',size_bytes:56e9,seeds:118,infohash:'a'.repeat(40),revision:'1'.repeat(40)};
const rescued={repository:'gone-org/rare-model',name:'rare-model',owner:'gone-org',size_bytes:4e9,seeds:3,infohash:'b'.repeat(40),revision:null};

async function common(page:import('@playwright/test').Page,state:{pf:boolean;swarm:boolean}){
  await page.route('**/api/library*',r=>r.fulfill({json:{host_kind:'sovereign',hub_search:true,pirate_face_search:state.pf,pirate_face_swarm:state.swarm,copies:[],jobs:[],next_offset:null}}));
  await page.route('**/api/desktop-search',r=>r.fulfill({json:{catalog_id:null,results:[],total:0,next_offset:null}}));
  await page.route('**/api/desktop-source-read',r=>r.fulfill({json:{sources:[]}}));
  await page.route('**/api/desktop-source-search',r=>r.fulfill({json:{results:[],total:0,next_offset:null}}));
  await page.route('**/api/desktop-copy-search',r=>r.fulfill({json:{results:[],total:0,stored_total:0,next_offset:null}}));
  await page.route('**/api/desktop-held-refresh',r=>r.fulfill({json:{mirrors:0}}));
  await page.route('**/api/desktop-held-search',r=>r.fulfill({json:{source:'mirrors',results:[]}}));
  await page.route('**/api/desktop-hub-search',r=>r.fulfill({json:{source:'huggingface',results:[hubResult]}}));
}

test('Pirate Face search is a choice, and a model on both shows once with its seeds',async({page})=>{
  const state={pf:false,swarm:false};const calls:string[]=[];
  await common(page,state);
  await page.route('**/api/desktop-pirate-face-consent',r=>{state.pf=true;calls.push('consent');return r.fulfill({json:{pirate_face_search:true}});});
  await page.route('**/api/desktop-pirate-face-search',r=>{calls.push('search:'+r.request().postDataJSON().query);return r.fulfill({json:{source:'pirateface',results:[onBoth,rescued]}});});
  await page.goto('/');
  await page.getByRole('button',{name:'Search Pirate Face too'}).click();
  await expect.poll(()=>calls[0]).toBe('consent');
  await page.getByPlaceholder('Search Hugging Face by model name').fill('qwen');
  await expect.poll(()=>calls.find(call=>call.startsWith('search:'))).toBe('search:qwen');
  const hub=page.getByRole('article',{name:'Qwen/Qwen-Test'});
  await expect(hub).toHaveCount(1);
  await expect(hub.getByText('118 seeds on Pirate Face')).toBeVisible();
  const pirate=page.getByRole('region',{name:'Results from Pirate Face'});
  await expect(pirate.getByRole('article',{name:'gone-org/rare-model'})).toBeVisible();
  await expect(pirate.getByText('3 seeds on Pirate Face')).toBeVisible();
});

test('a rescued model needs swarm downloads, then an origin acknowledgement',async({page})=>{
  const state={pf:true,swarm:false};const plans:unknown[]=[];const starts:unknown[]=[];
  await common(page,state);
  await page.route('**/api/desktop-pirate-face-search',r=>r.fulfill({json:{source:'pirateface',results:[rescued]}}));
  await page.route('**/api/desktop-pirate-face-review',r=>r.fulfill({json:{listed:true,repository:rescued.repository,on_hub:false,seeders:3,infohash:rescued.infohash,revision:'c'.repeat(40),files:[{path:'config.json',size_bytes:100,weight:false},{path:'model.safetensors',size_bytes:4e9,weight:true}],variants:[],default_files:['config.json','model.safetensors']}}));
  await page.route('**/api/desktop-pirate-face-swarm',r=>{state.swarm=true;return r.fulfill({json:{pirate_face_swarm:true}});});
  await page.route('**/api/desktop-plan',r=>{plans.push(r.request().postDataJSON());return r.fulfill({json:{plan_id:'p'.repeat(64),kind:'receive',origin:'pirateface',name:'rare-model',size_bytes:4e9,file_count:2,details:{license:null,origin_check:'unconfirmed',seeders:3},members:[rescued.repository],required_free_bytes:1,free_bytes:10**13,terms:{accept:[],licences:[]}}});});
  await page.route('**/api/desktop-start',r=>{starts.push(r.request().postDataJSON());return r.fulfill({json:{job_id:'j'.repeat(64),kind:'receive',state:'queued',name:'rare-model'}});});
  await page.goto('/');
  await page.getByPlaceholder('Search Hugging Face by model name').fill('rare');
  const card=page.getByRole('article',{name:'gone-org/rare-model'});
  await card.getByRole('button',{name:'Choose files'}).click();
  await expect(card.getByText('Rescued: Hugging Face no longer has this model',{exact:false})).toBeVisible();
  await expect(card.getByText('shows this computer’s address',{exact:false})).toBeVisible();
  await card.getByRole('button',{name:'Turn on swarm downloads'}).click();
  await card.getByRole('button',{name:/Keep a copy/}).click();
  await expect.poll(()=>plans.length).toBe(1);
  expect(plans[0]).toEqual({kind:'receive',origin:'pirateface',repository:rescued.repository,files:['config.json','model.safetensors']});
  await expect(page.getByText('This copy comes from the Pirate Face swarm',{exact:false})).toBeVisible();
  const keep=page.getByRole('button',{name:'Keep a copy'}).last();
  await expect(keep).toBeDisabled();
  await page.getByText('I understand Sovereign couldn’t confirm where this copy came from',{exact:false}).click();
  await keep.click();
  await expect.poll(()=>starts.length).toBe(1);
  expect(starts[0]).toMatchObject({confirmed:true,accept_unconfirmed_origin:true});
});

test('a model Pirate Face lacks can be offered for rescue, only on request',async({page})=>{
  const state={pf:true,swarm:false};const rescues:unknown[]=[];
  await common(page,state);
  await page.route('**/api/desktop-pirate-face-search',r=>r.fulfill({json:{source:'pirateface',results:[]}}));
  await page.route('**/api/desktop-hub-review',r=>r.fulfill({json:{repository:hubResult.repository,revision:'d'.repeat(40),licence:'apache-2.0',gated:false,task:null,library:null,variants:[],files:[{path:'model.safetensors',size_bytes:1000,weight:true}],default_files:['model.safetensors']}}));
  await page.route('**/api/desktop-pirate-face-review',r=>r.fulfill({json:{listed:false,repository:hubResult.repository}}));
  await page.route('**/api/desktop-pirate-face-rescue',r=>{rescues.push(r.request().postDataJSON());return r.fulfill({json:{requested:true}});});
  await page.goto('/');
  await page.getByPlaceholder('Search Hugging Face by model name').fill('qwen');
  const card=page.getByRole('article',{name:'Qwen/Qwen-Test'});
  await card.getByRole('button',{name:'Choose files'}).click();
  await expect(card.getByText('Pirate Face doesn’t have a torrent of this model yet',{exact:false})).toBeVisible();
  expect(rescues).toEqual([]);
  await card.getByRole('button',{name:'Ask Pirate Face to rescue this'}).click();
  await expect(card.getByText('Asked Pirate Face to add this model.')).toBeVisible();
  expect(rescues).toEqual([{repository:'Qwen/Qwen-Test'}]);
});
