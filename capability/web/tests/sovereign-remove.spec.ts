import {test,expect} from '@playwright/test';

const copy={copy_id:'c'.repeat(64),name:'tiny-model',size_bytes:5_000_000,member_count:1,manifest_id:'m'.repeat(64),created_at:1,details:{}};
const done={job_id:'a'.repeat(64),kind:'kit_export',state:'succeeded',name:'tiny-model',error_code:null,output:null,copy_id:null,created_at:2};
const working={job_id:'b'.repeat(64),kind:'receive',state:'running',name:'big-model',error_code:null,output:null,copy_id:null,created_at:3,stage:'downloading'};

async function common(page:import('@playwright/test').Page,state:{copies:unknown[];jobs:unknown[]}){
  await page.route('**/api/library*',r=>r.fulfill({json:{host_kind:'sovereign',copies:state.copies,copy_count:state.copies.length,jobs:state.jobs,next_offset:null}}));
  await page.route('**/api/desktop-search',r=>r.fulfill({json:{catalog_id:null,results:[],total:0,next_offset:null}}));
  await page.route('**/api/desktop-source-read',r=>r.fulfill({json:{sources:[]}}));
  await page.route('**/api/desktop-copy-search',r=>r.fulfill({json:{results:state.copies,total:state.copies.length,stored_total:state.copies.length,next_offset:null}}));
}

test('finished Activity entries can be removed one by one or all at once',async({page})=>{
  const state={copies:[] as unknown[],jobs:[done,working] as unknown[]};const calls:unknown[]=[];
  await common(page,state);
  await page.route('**/api/desktop-activity-remove',r=>{const body=r.request().postDataJSON();calls.push(body);state.jobs=state.jobs.filter(job=>(job as {state:string}).state==='running');return r.fulfill({json:{removed:1}});});
  await page.goto('/');
  await page.getByRole('button',{name:'Activity',exact:true}).click();
  const finished=page.locator('.sk-job',{hasText:'tiny-model'});
  const live=page.locator('.sk-job',{hasText:'big-model'});
  await expect(live.getByRole('button',{name:'Remove from Activity'})).toHaveCount(0);
  await page.getByRole('button',{name:'Clear finished'}).click();
  await page.getByRole('button',{name:'Clear all finished entries'}).click();
  await expect.poll(()=>calls[0]).toEqual({finished:true});
  await expect(finished).toHaveCount(0);
  await expect(live).toBeVisible();
});

test('a saved model is removed only after confirming',async({page})=>{
  const state={copies:[copy] as unknown[],jobs:[] as unknown[]};const calls:unknown[]=[];
  await common(page,state);
  await page.route('**/api/desktop-copy-remove',r=>{calls.push(r.request().postDataJSON());state.copies=[];return r.fulfill({json:{removed:copy.copy_id}});});
  await page.goto('/');
  await page.getByRole('button',{name:'Library',exact:true}).click();
  await page.getByRole('button',{name:'Remove from Library'}).first().click();
  const remove=page.getByRole('button',{name:'Remove from Library'}).last();
  await expect(remove).toBeDisabled();
  await page.getByText('Delete this saved copy and its files.').click();
  await remove.click();
  await expect.poll(()=>calls[0]).toEqual({copy_id:copy.copy_id,confirmed:true});
});

test('a running save shows how much is left',async({page})=>{
  const job={...working,bytes_received:5_000_000_000,bytes_reused:1_000_000_000,bytes_total:30_000_000_000};
  await common(page,{copies:[],jobs:[job]});
  await page.goto('/');
  await page.getByRole('button',{name:'Activity',exact:true}).click();
  const tile=page.locator('.sk-job',{hasText:'big-model'});
  await expect(tile.getByText(/of 27\.9 GB · 20%/)).toBeVisible();
  await expect(tile.getByRole('progressbar',{name:'Download progress'})).toBeVisible();
});


test('progress counts what is kept, not every byte ever received',async({page})=>{
  // 25 GB received in total, but a restart discarded most: 6 GB is kept.
  const job={...working,bytes_received:25_000_000_000,bytes_reused:0,bytes_done:6_000_000_000,bytes_total:30_000_000_000};
  await common(page,{copies:[],jobs:[job]});
  await page.goto('/');
  await page.getByRole('button',{name:'Activity',exact:true}).click();
  await expect(page.locator('.sk-job',{hasText:'big-model'}).getByText(/of 27\.9 GB · 20%/)).toBeVisible();
});

test('after the download a save names each finishing step, with its own progress',async({page},info)=>{
  // The case that looked stuck: downloaded, then hashing every file for the signed copy.
  const base={...working,bytes_received:0,bytes_done:30_000_000_000,bytes_total:30_000_000_000,bytes_copied:0};
  const state={copies:[] as unknown[],jobs:[{...base,stage:'inventory',bytes_hashed:12_000_000_000,finishing:{step:1,steps:3,name:'sign'}}] as unknown[]};
  await common(page,state);
  await page.goto('/');
  await page.getByRole('button',{name:'Activity',exact:true}).click();
  const tile=page.locator('.sk-job',{hasText:'big-model'});
  await expect(tile.getByText('Step 1 of 3: Checking and signing the downloaded files…')).toBeVisible();
  await expect(tile.getByText('Finding the files included in this copy…')).toHaveCount(0);
  await expect(tile.getByText(/You can leave this running\./)).toBeVisible();
  await expect(tile.getByRole('progressbar',{name:'Download progress'})).toHaveCount(0);
  await expect(tile.getByRole('progressbar',{name:'Checking progress'})).toBeVisible();
  await expect(tile.getByText(/11\.2 GB of 27\.9 GB checked · 40%/)).toBeVisible();
  await page.screenshot({path:info.outputPath('save-finishing.png')});
  state.jobs=[{...base,stage:'copying',bytes_hashed:0,bytes_copied:3_000_000_000,finishing:{step:2,steps:3,name:'store'}}];
  await expect(tile.getByText('Step 2 of 3: Storing the copy in your Library…')).toBeVisible({timeout:10_000});
  await expect(tile.getByRole('progressbar',{name:'Writing progress'})).toBeVisible();
  await expect(tile.getByText(/of 27\.9 GB written/)).toBeVisible();
  state.jobs=[{...base,stage:'complete',bytes_hashed:0,finishing:{step:3,steps:3,name:'check'}}];
  await expect(tile.getByText('Step 3 of 3: Checking the stored copy…')).toBeVisible({timeout:10_000});
});

test('an older host without finishing steps still never shows a frozen download bar',async({page})=>{
  await common(page,{copies:[],jobs:[{...working,stage:'checking',bytes_done:30_000_000_000,bytes_total:30_000_000_000,bytes_hashed:6_000_000_000}]});
  await page.goto('/');
  await page.getByRole('button',{name:'Activity',exact:true}).click();
  const tile=page.locator('.sk-job',{hasText:'big-model'});
  await expect(tile.getByText('Checking the files and signatures…')).toBeVisible();
  await expect(tile.getByRole('progressbar',{name:'Checking progress'})).toBeVisible();
});
