import {test,expect} from '@playwright/test';

const revision='c'.repeat(40);
const repository='example-org/tiny-model-GGUF';
const review={source:'huggingface',repository,revision,licence:'apache-2.0',gated:false,task:'text-generation',library:null,
  variants:['tiny-Q4_K_M.gguf','tiny-Q8_0.gguf'],
  files:[{path:'README.md',size_bytes:1000,weight:false},{path:'tiny-Q4_K_M.gguf',size_bytes:4_000_000,weight:true},{path:'tiny-Q8_0.gguf',size_bytes:8_000_000,weight:true}],
  default_files:['README.md','tiny-Q4_K_M.gguf']};

test('Hugging Face search waits for consent, then plans the exact reviewed files',async({page})=>{
  let consent=false;const searches:string[]=[];const plans:unknown[]=[];
  await page.route('**/api/library*',r=>r.fulfill({json:{host_kind:'sovereign',hub_search:consent,copies:[],jobs:[],next_offset:null}}));
  await page.route('**/api/desktop-search',r=>r.fulfill({json:{catalog_id:null,results:[],total:0,next_offset:null}}));
  await page.route('**/api/desktop-source-read',r=>r.fulfill({json:{sources:[]}}));
  await page.route('**/api/desktop-source-search',r=>r.fulfill({json:{results:[],total:0,next_offset:null}}));
  await page.route('**/api/desktop-copy-search',r=>r.fulfill({json:{results:[],total:0,stored_total:0,next_offset:null}}));
  await page.route('**/api/desktop-hub-consent',r=>{consent=r.request().postDataJSON().enabled;return r.fulfill({json:{hub_search:consent}});});
  await page.route('**/api/desktop-hub-search',r=>{searches.push(r.request().postDataJSON().query);return r.fulfill({json:{source:'huggingface',results:[{repository,name:'tiny-model-GGUF',owner:'example-org',downloads:12000,likes:3,task:'text-generation',library:null,gated:false}]}});});
  await page.route('**/api/desktop-hub-review',r=>{const body=r.request().postDataJSON();return r.fulfill({json:body.variant==='tiny-Q8_0.gguf'?{...review,default_files:['README.md','tiny-Q8_0.gguf']}:review});});
  await page.route('**/api/desktop-plan',r=>{plans.push(r.request().postDataJSON());return r.fulfill({json:{plan_id:'p'.repeat(64),kind:'receive',origin:'hub',name:repository,size_bytes:8_001_000,file_count:2,details:{license:'apache-2.0'},members:[repository],required_free_bytes:1,free_bytes:10**12,terms:{accept:[],licences:[]}}});});
  await page.goto('/');
  // Nothing goes to Hugging Face before the person chooses it.
  await expect(page.getByRole('heading',{name:'Also search Hugging Face?'})).toBeVisible();
  expect(searches).toEqual([]);
  await page.getByRole('button',{name:'Search Hugging Face too'}).click();
  await page.getByPlaceholder('Search Hugging Face by model name').fill('tiny');
  await expect(page.getByRole('article',{name:repository})).toBeVisible();
  expect(searches).toEqual(['tiny']);
  await page.getByRole('button',{name:'Choose files'}).click();
  await expect(page.getByText('Licence: apache-2.0')).toBeVisible();
  await page.getByRole('combobox').selectOption('tiny-Q8_0.gguf');
  await page.getByText(/Files · 2 of 3 selected/).click();
  await expect(page.getByRole('checkbox',{name:'tiny-Q8_0.gguf'})).toBeChecked();
  await expect(page.getByRole('checkbox',{name:'tiny-Q4_K_M.gguf'})).not.toBeChecked();
  await page.getByRole('button',{name:/Keep a copy · /}).click();
  await expect.poll(()=>plans.length).toBe(1);
  expect(plans[0]).toEqual({kind:'receive',origin:'hub',repository,revision,files:['README.md','tiny-Q8_0.gguf']});
  await expect(page.getByText('pinned to one exact version',{exact:false})).toBeVisible();
});

test('a Hugging Face token is entered privately, shown only as the account, and removable',async({page})=>{
  let user:string|null=null;const sent:unknown[]=[];
  await page.route('**/api/library*',r=>r.fulfill({json:{host_kind:'sovereign',hub_search:true,hub_token:user,copies:[],jobs:[],next_offset:null}}));
  await page.route('**/api/desktop-search',r=>r.fulfill({json:{catalog_id:'c'.repeat(64),results:[],total:0,next_offset:null}}));
  await page.route('**/api/desktop-source-read',r=>r.fulfill({json:{sources:[]}}));
  await page.route('**/api/desktop-source-search',r=>r.fulfill({json:{results:[],total:0,next_offset:null}}));
  await page.route('**/api/desktop-copy-search',r=>r.fulfill({json:{results:[],total:0,stored_total:0,next_offset:null}}));
  await page.route('**/api/desktop-hub-token-set',r=>{sent.push(r.request().postDataJSON());user='someone';return r.fulfill({json:{hub_token:user}});});
  await page.route('**/api/desktop-hub-token-remove',r=>{user=null;return r.fulfill({json:{hub_token:null}});});
  const token='hf_'+'A1b2C3d4'.repeat(4);
  await page.goto('/');
  await page.getByText('Source and privacy').click();
  const field=page.getByLabel('Hugging Face token');
  await expect(field).toHaveAttribute('type','password');
  await expect(page.getByRole('button',{name:'Save token'})).toBeDisabled();
  await field.fill(token);
  await page.getByRole('button',{name:'Save token'}).click();
  await expect(page.getByText('Connected to Hugging Face as',{exact:false})).toBeVisible();
  expect(sent).toEqual([{token}]);
  expect(await page.content()).not.toContain(token);
  await page.getByRole('button',{name:'Remove Hugging Face token'}).click();
  await expect(page.getByLabel('Hugging Face token')).toBeVisible();
});
