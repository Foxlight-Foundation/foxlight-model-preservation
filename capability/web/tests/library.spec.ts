import { test, expect, type Page } from '@playwright/test';

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
  const beta=page.locator('.mp-summary').filter({hasText:'example/beta'});
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
