import { test, expect, type Page } from '@playwright/test';
test.beforeEach(async({page})=>{await page.route('**/api/core-features',route=>route.fulfill({json:{'Portable handoffs':false}}));});

const location={location_id:'device',label:'This device',purpose:'private',reachable:true,free_bytes:1000000,publisher_key_id:'capture-key'};
const models=[{model_id:'example/alpha',generation_id:'a'.repeat(64),size_bytes:100,verification:'custom'},{model_id:'example/beta',generation_id:'b'.repeat(64),size_bytes:200,verification:'custom'}];
const copies=models.map((model,index)=>({copy_id:String(index+1).repeat(64),model_id:model.model_id,location_id:'device',manifest_id:String(index+3).repeat(64),audience:'private',size_bytes:100,checked_at:1,member_count:2}));
const jobs=copies.map((copy,index)=>({job_id:String(index+5).repeat(64),operation_id:`operation-${index}`,plan_id:'f'.repeat(64),kind:'preserve',model_id:copy.model_id,state:'succeeded',stage:'complete',created_at:1,bytes_copied:100,bytes_hashed:100,expected_manifest_id:copy.manifest_id,copy_id:copy.copy_id,error_code:null}));
async function mock(page:Page,reachable=true) {
  await page.route('**/api/library?offset=*',route=>route.fulfill({json:{locations:[{...location,reachable}],models,copies,jobs,next_offset:null}}));
}

test('completed actions bind the actual job copy and show its reviewed target',async({page})=>{
  await mock(page);
  let selected:unknown;
  await page.route('**/api/plan',async route=>{selected=route.request().postDataJSON();await route.fulfill({json:{plan_id:'a'.repeat(64),kind:'check',model_id:'example/beta',location_id:'device',size_bytes:100,members:['example/beta','example/companion'],member_count:2,expires_at:Math.floor(Date.now()/1000)+600}});});
  await page.goto('/');
  await page.getByRole('button',{name:'Activity',exact:true}).click();
  const beta=page.getByRole('article').filter({hasText:'example/beta'});
  await beta.getByRole('button',{name:'Check this copy'}).click();
  await expect(page.getByRole('heading',{name:'Verify the saved files'})).toBeVisible();
  expect(selected).toEqual({kind:'check',location_id:'device',copy_id:copies[1].copy_id});
  await expect(page.getByText('example/companion',{exact:true})).toBeVisible();
});

test('disconnected storage blocks checks and preserve; sharing stays receive-only',async({page})=>{
  await mock(page,false);
  let effects=0;
  await page.route('**/api/start',route=>{effects++;return route.fulfill({json:{}})});
  await page.goto('/');
  await expect(page.getByRole('button',{name:'Preserve',exact:true}).first()).toBeDisabled();
  await expect(page.getByRole('button',{name:'Check copy',exact:true}).first()).toBeDisabled();
  await page.getByRole('button',{name:'Sharing',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Standalone sharing tools'})).toBeVisible();
  expect(effects).toBe(0);
  await expect(page.getByRole('button',{name:/Join swarm|Start sharing/})).toHaveCount(0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});

test('lost submission response retains the exact operation identity across reload',async({page})=>{
  await mock(page);
  await page.route('**/api/plan',route=>route.fulfill({json:{plan_id:'a'.repeat(64),kind:'preserve',model_id:'example/alpha',location_id:'device',size_bytes:100,members:['example/alpha'],member_count:1,expires_at:Math.floor(Date.now()/1000)+600}}));
  const starts:{operation_id:string}[]=[];
  await page.route('**/api/start',async route=>{starts.push(route.request().postDataJSON());await route.fulfill(starts.length===1?{status:503,json:{error:'host_unavailable'}}:{json:jobs[0]});});
  await page.goto('/');
  await page.getByRole('button',{name:'Preserve',exact:true}).first().click();
  await page.getByRole('button',{name:'Preserve model',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('Skulk could not be reached');
  await page.reload();
  await page.getByRole('button',{name:'Preserve model',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Activity',exact:true})).toBeVisible();
  expect(starts).toHaveLength(2);
  expect(starts[0].operation_id).toBe(starts[1].operation_id);
});

const recoveryPlan={archive_format:'pmf-v0.1',plan_id:'a'.repeat(64),kind:'recover',model_id:'example/recovered',location_id:'kit',size_bytes:20,members:['example/recovered','example/assistant'],member_count:2,expires_at:9999999999,upload_id:'1'.repeat(32),publisher_key_ids:['ed25519:'+'a'.repeat(64)],requires_owner_acceptance:true,requires_code_permission:true,publication:'registry-historical',file_coverage:'partial-or-missing'};

test('fresh Core file restore requires separate capture and code consent',async({page})=>{
  let restored=false;
  await page.route('**/api/library?offset=*',route=>route.fulfill({json:{locations:[],models:restored?[{...models[0],model_id:recoveryPlan.model_id}]:[],copies:[],jobs:restored?[{...jobs[0],kind:'recover',copy_id:null}]:[],uploads:[],next_offset:null}}));
  await page.route('**/api/upload-create',route=>route.fulfill({json:{upload_id:'1'.repeat(32),size_bytes:20,offset:0,complete:false}}));
  await page.route('**/api/upload/*',route=>route.fulfill({json:{upload_id:'1'.repeat(32),size_bytes:20,offset:20,complete:true}}));
  await page.route('**/api/plan',route=>route.fulfill({json:recoveryPlan}));
  let started:unknown;
  await page.route('**/api/start',route=>{started=route.request().postDataJSON();restored=true;return route.fulfill({json:{...jobs[0],kind:'recover'}})});
  await page.goto('/');
  await page.getByRole('button',{name:'Restore from recovery kit',exact:true}).click();
  await page.getByLabel('Recovery kit file').setInputFiles({name:'saved.foxkit',mimeType:'application/octet-stream',buffer:Buffer.alloc(20,1)});
  await expect(page.getByRole('heading',{name:'Recover this model'})).toBeVisible();
  await expect(page.getByText('Portable Model Format (PMF)',{exact:true})).toBeVisible();
  await expect(page.getByText('Standalone tools can recover these files without Skulk.',{exact:false})).toBeVisible();
  const restore=page.getByRole('button',{name:'Restore model',exact:true});
  await expect(restore).toBeDisabled();
  await page.getByLabel('I trust this exact recovery copy').check();
  await expect(restore).toBeDisabled();
  await page.getByLabel('Separately, I allow').check();
  await expect(restore).toBeEnabled();
  await restore.click();
  await expect(page.getByRole('heading',{name:'Activity',exact:true})).toBeVisible();
  await expect(page.getByText('Model and companions restored',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Library',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Your models',exact:true})).toBeVisible();
  expect(started).toMatchObject({plan_id:recoveryPlan.plan_id,accept_owner_capture:true,allow_repository_code:true});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});

test('reselecting the same file resumes a retained transfer after page loss',async({page})=>{
  const upload={upload_id:'1'.repeat(32),size_bytes:20,offset:5,complete:false};
  await page.route('**/api/library?offset=*',route=>route.fulfill({json:{locations:[],models:[],copies:[],jobs:[],uploads:[upload],next_offset:null}}));
  await page.route('**/api/upload-status',route=>route.fulfill({json:upload}));
  const offsets:string[]=[];
  await page.route('**/api/upload/*',route=>{const offset=new URL(route.request().url()).searchParams.get('offset')!;offsets.push(offset);return route.fulfill({json:{...upload,offset:offset==='0'?5:20,complete:offset!=='0'}})});
  await page.route('**/api/plan',route=>route.fulfill({json:{...recoveryPlan,requires_code_permission:false}}));
  await page.goto('/');
  await page.getByRole('button',{name:'Restore from recovery kit',exact:true}).click();
  await page.getByLabel('Recovery kit file').setInputFiles({name:'saved.foxkit',mimeType:'application/octet-stream',buffer:Buffer.alloc(20,1)});
  await expect(page.getByRole('heading',{name:'Recover this model'})).toBeVisible();
  expect(offsets).toEqual(['0','5']);
});

test('completed kit download binds its actual job and stays off until clicked',async({page})=>{
  const job={...jobs[1],kind:'kit_export',copy_id:null};
  await page.route('**/api/library?offset=*',route=>route.fulfill({json:{locations:[location],models,copies,jobs:[job],uploads:[],next_offset:null}}));
  await page.goto('/');await page.getByRole('button',{name:'Activity',exact:true}).click();
  await expect(page.getByRole('link',{name:'Download recovery kit'})).toHaveAttribute('href',new RegExp(`/api/download/${job.job_id}$`));
});


test('first launch keeps Guided setup even when models are already installed',async({page})=>{
  await page.route('**/api/library?offset=*',route=>route.fulfill({json:{locations:[],models,copies:[],jobs:[],uploads:[],next_offset:null}}));
  await page.goto('/');
  await expect(page.getByRole('button',{name:'Use this device',exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Restore from recovery kit',exact:true})).toBeVisible();
});


test('removed models need an exact additional restore confirmation',async({page})=>{
  await mock(page);
  await page.route('**/api/plan',route=>route.fulfill({json:{...recoveryPlan,location_id:'device',requires_code_permission:false,deleted_aliases:['example/recovered']}}));
  await page.goto('/');
  await page.getByRole('button',{name:'Restore from recovery kit',exact:true}).click();
  // Use the upload path to reach the real shared review control.
  await page.route('**/api/upload-create',route=>route.fulfill({json:{upload_id:'1'.repeat(32),size_bytes:20,offset:0,complete:false}}));
  await page.route('**/api/upload/*',route=>route.fulfill({json:{upload_id:'1'.repeat(32),size_bytes:20,offset:20,complete:true}}));
  await page.getByLabel('Recovery kit file').setInputFiles({name:'saved.foxkit',mimeType:'application/octet-stream',buffer:Buffer.alloc(20,1)});
  await page.getByLabel('I trust this exact recovery copy').check();
  await expect(page.getByRole('button',{name:'Restore model',exact:true})).toBeDisabled();
  await page.getByLabel('I want to restore these deliberately removed models').check();
  await expect(page.getByRole('button',{name:'Restore model',exact:true})).toBeEnabled();
});

for (const state of ['interrupted','failed','cancelled']) {
test(`clearing ${state} restore files is explicit and separate from fresh review`,async({page})=>{
  const stopped={...jobs[0],kind:'recover',state,copy_id:null};
  await page.route('**/api/library?offset=*',route=>route.fulfill({json:{locations:[location],models:[],copies:[],jobs:[stopped],next_offset:null}}));
  let cleanups=0, reviews=0, starts=0;
  await page.route('**/api/reclaim',async route=>{cleanups++;expect(route.request().postDataJSON()).toEqual({job_id:stopped.job_id});await route.fulfill({json:{indexed_generation_retained:false}});});
  await page.route('**/api/retry-plan',route=>{reviews++;return route.fulfill({json:recoveryPlan});});
  await page.route('**/api/start',route=>{starts++;return route.fulfill({json:stopped});});
  await page.goto('/');
  await page.getByRole('button',{name:'Activity',exact:true}).click();
  expect(cleanups).toBe(0);
  await page.getByRole('button',{name:'Clear unused restore files',exact:true}).click();
  await expect(page.getByRole('status')).toContainText('Unused recovery files cleared');
  expect(cleanups).toBe(1);expect(reviews).toBe(0);expect(starts).toBe(0);
  await page.getByRole('button',{name:'Review again',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Recover this model'})).toBeVisible();
  expect(reviews).toBe(1);expect(starts).toBe(0);
  await expect(page.getByRole('checkbox').first()).not.toBeChecked();
});

}

for(const guided of [false,true])test(`PMF Preserve uses the same review after ${guided?'Guided setup':'Library selection'}`,async({page})=>{
  let configured=!guided;
  await page.route('**/api/library?offset=*',route=>route.fulfill({json:{locations:configured?[location]:[],models,copies:[],jobs:[],next_offset:null}}));
  await page.route('**/api/setup',route=>{configured=true;return route.fulfill({json:location});});
  let selected:unknown;
  await page.route('**/api/plan',route=>{selected=route.request().postDataJSON();return route.fulfill({json:{...recoveryPlan,kind:'preserve',location_id:'device',model_id:models[0].model_id,requires_owner_acceptance:false,requires_code_permission:false}});});
  await page.goto('/');
  if(guided)await page.getByRole('button',{name:'Use this device',exact:true}).click();
  await page.getByRole('button',{name:'Preserve',exact:true}).first().click();
  await expect(page.getByText('Portable Model Format (PMF)',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Preserve model',exact:true})).toBeEnabled();
  expect(selected).toEqual({kind:'preserve',location_id:'device',model_id:models[0].model_id,generation_id:models[0].generation_id});
  await expect(page.getByRole('combobox',{name:/format/i})).toHaveCount(0);
});
