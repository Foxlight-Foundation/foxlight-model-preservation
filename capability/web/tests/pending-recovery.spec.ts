import {test,expect,type Page} from '@playwright/test';

const identity='a'.repeat(64),policy='b'.repeat(64),checkId='c'.repeat(64),kitId='d'.repeat(64),controllerId='e'.repeat(64);
const member=(id:string,role:string,size:number)=>({member_id:id,role,repository:`fixture/${id}`,revision:'f'.repeat(40),format:'fixture',size_bytes:size,file_count:2,license_id:`${id} terms`,terms_reference:`${id}-LICENSE`,notice_paths:[`${id}-LICENSE`]});
const review={manifest_id:identity,archive_format:'preservation-v1',name:'Interrupted community copy',size_bytes:10,workloads:['fixture'],runtime_requirements:[],capture_key_ids:['ed25519:'+'1'.repeat(64),'ed25519:'+'2'.repeat(64)],capture_threshold:2,members:[member('primary','primary',6),member('companion','future-'.repeat(13),4)]};
type Job={job_id:string;owner_id:string;operation_id:string;kind:string;state:string;manifest_id:string;bytes_hashed:number;bytes_copied:number;error_code:string|null;kit_removed:boolean;kit:null|{state:string;manifest_id:string;size_bytes:number;sha256:string}};
const errors=new WeakMap<Page,string[]>();
test.beforeEach(async({page})=>{const found:string[]=[];errors.set(page,found);page.on('pageerror',error=>found.push(error.message));page.on('console',message=>{if(message.type()==='error'&&!message.text().includes('net::ERR_FAILED'))found.push(message.text());});});
test.afterEach(async({page})=>{expect(errors.get(page)??[]).toEqual([]);});

/** UI contract fixture only; full installed Core and actual files are a separate gate. */
async function host(page:Page,options:{supported?:boolean;controller?:boolean}={}) {
  const state={
    model:{manifest_id:identity,state:'pending',pinned:true,blocked:true,error_code:null as string|null,name:review.name,member_count:2,size_bytes:10},
    policy,selectedState:'pending',available:true,metadataError:false,controller:options.controller?controllerId:null as string|null,
    job:null as Job|null,kit:null as Job|null,checks:[] as Record<string,unknown>[],preparations:[] as Record<string,unknown>[],stops:[] as string[],loseReply:false,
  };
  const receipts=new Map<string,Job>();
  await page.route('**/api/library?offset=*',route=>route.fulfill({json:{locations:[],models:[],copies:[],public_copies:[],jobs:[],next_offset:null}}));
  await page.route('**/api/core-features',route=>route.fulfill({json:{'Persistent mirrors':true,'Public copy review':true,'Retained recovery':true,'Pending recovery':options.supported!==false}}));
  await page.route('**/api/mirror-*',async route=>{
    const action=new URL(route.request().url()).pathname.split('/').at(-1),input=route.request().postDataJSON() as Record<string,unknown>;
    if(action==='mirror-read')return route.fulfill({json:{configured:true,available:state.available,policy_id:state.policy,configuration:null,paused:!state.controller,restart_allowed:false,restart_expires:null,controller_job_id:state.controller,models:[state.model],model_count:1,next_offset:null}});
    if(action==='mirror-retained-review')return state.metadataError?route.abort('failed'):route.fulfill({json:review});
    if(action==='mirror-selection')return route.fulfill({json:{manifest_id:identity,configured:true,available:state.available,policy_id:state.policy,model:{...state.model,state:state.selectedState}}});
    if(action==='mirror-recovery-latest')return route.fulfill({json:{manifest_id:identity,job:state.job}});
    if(action==='mirror-kit-latest')return route.fulfill({json:{manifest_id:identity,job:state.kit}});
    if(action==='mirror-history')return route.fulfill({json:{jobs:[state.job,state.kit].filter(Boolean),next_offset:null}});
    if(action==='mirror-observe')return route.fulfill({json:input.job_id===state.kit?.job_id?state.kit:state.job});
    if(action==='mirror-stop'){
      state.stops.push(String(input.job_id));
      if(input.job_id===state.controller){state.controller=null;return route.fulfill({json:{job_id:input.job_id,state:'cancelled'}});}
      state.job!.state='cancelled';return route.fulfill({json:state.job});
    }
    if(action==='mirror-recover'){
      state.checks.push(input);const key=JSON.stringify(input);
      if(!receipts.has(key))receipts.set(key,{job_id:input.previous_job_id?'9'.repeat(64):checkId,owner_id:'library',operation_id:key,kind:'recover',state:'running',manifest_id:identity,bytes_hashed:0,bytes_copied:0,error_code:null,kit_removed:false,kit:null});
      state.job=receipts.get(key)!;
      if(state.loseReply){state.loseReply=false;return route.abort('failed');}
      return route.fulfill({json:state.job});
    }
    if(action==='mirror-prepare'){
      state.preparations.push(input);state.kit={job_id:kitId,owner_id:'library',operation_id:'kit',kind:'prepare_kit',state:'running',manifest_id:identity,bytes_hashed:0,bytes_copied:0,error_code:null,kit_removed:false,kit:null};
      return route.fulfill({json:state.kit});
    }
    throw new Error(`Unexpected control ${action}`);
  });
  await page.goto('/');await expect(page.getByRole('heading',{name:'Retained mirror copies',exact:true})).toBeVisible();
  return state;
}
async function open(page:Page){await page.getByRole('button',{name:'Check local bytes',exact:true}).click();await expect(page.getByRole('dialog').getByRole('heading',{name:review.name,exact:true})).toBeVisible();}
async function stableView(page:Page,path:string){await page.evaluate(async()=>{await Promise.all(document.getAnimations().filter(a=>a.effect?.getTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>undefined)));});await page.screenshot({path,fullPage:false});}

test('Guided recovers an interrupted copy, reopens the same check and follows through to a complete kit',async({page},info)=>{
  const state=await host(page);
  await expect(page.getByRole('heading',{name:'Keep your models within reach.',exact:true})).toBeVisible();
  expect(state.checks).toHaveLength(0);await open(page);
  await page.getByText('Members and declared terms',{exact:true}).click();await expect(page.getByText('companion terms',{exact:true})).toBeVisible();
  await page.getByText('Capture signatures and trust',{exact:true}).click();await expect(page.getByText(/Capture signature threshold: 2/)).toBeVisible();
  await expect(page.getByText(/does not grant registry or runtime trust/)).toBeVisible();
  const sheet=page.getByRole('dialog');await sheet.getByRole('button',{name:'Check local bytes',exact:true}).click();
  await expect(sheet.getByText('running',{exact:true})).toBeVisible();
  expect(state.checks).toEqual([{manifest_id:identity,policy_id:policy}]);expect(state.preparations).toHaveLength(0);
  await page.reload();await open(page);await expect(page.getByText('running',{exact:true})).toBeVisible();expect(state.checks).toHaveLength(1);
  state.job!.state='succeeded';state.job!.bytes_hashed=10;state.selectedState='retained';state.model.state='retained';
  await expect(page.getByRole('button',{name:'Save recovery kit',exact:true})).toBeVisible();
  expect(state.model.pinned&&state.model.blocked).toBe(true);expect(state.stops).toEqual([]);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
  await stableView(page,info.outputPath('pending-copy-checked.png'));
  await page.getByRole('button',{name:'Save recovery kit',exact:true}).click();
  await page.getByRole('button',{name:'Prepare recovery kit',exact:true}).click();
  await expect(page.getByText('running',{exact:true})).toBeVisible();
  state.kit!.state='succeeded';state.kit!.bytes_hashed=10;state.kit!.bytes_copied=10;state.kit!.kit={state:'prepared',manifest_id:identity,size_bytes:27,sha256:'f'.repeat(64)};
  await expect(page.getByRole('link',{name:'Download recovery kit',exact:true})).toHaveAttribute('href',`http://127.0.0.1:4178/api/mirror-kit/${kitId}`);
  expect(state.checks).toHaveLength(1);expect(state.preparations).toEqual([{manifest_id:identity,policy_id:policy}]);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
  await stableView(page,info.outputPath('pending-recovery-kit-ready.png'));
});

test('lost check reply is found after reload and Stop permits only an explicit terminal retry',async({page})=>{
  const state=await host(page);state.loseReply=true;await open(page);
  await page.getByRole('dialog').getByRole('button',{name:'Check local bytes',exact:true}).click();await expect(page.getByRole('alert')).toBeVisible();
  await page.reload();await open(page);await expect(page.getByText('running',{exact:true})).toBeVisible();expect(state.checks).toHaveLength(1);
  await page.getByRole('button',{name:'Stop this check',exact:true}).click();await expect(page.getByText('cancelled',{exact:true})).toBeVisible();
  expect(state.model.state).toBe('pending');expect(state.stops).toEqual([checkId]);
  await page.getByRole('button',{name:'Check local bytes again',exact:true}).click();
  await expect(page.getByText('running',{exact:true})).toBeVisible();expect(state.checks[1]).toEqual({manifest_id:identity,policy_id:policy,previous_job_id:checkId});expect(state.preparations).toHaveLength(0);
});

test('retrying an ambiguous reply keeps the original decision rather than making a new check',async({page})=>{
  const state=await host(page);state.loseReply=true;await open(page);
  await page.getByRole('dialog').getByRole('button',{name:'Check local bytes',exact:true}).click();await expect(page.getByRole('alert')).toBeVisible();
  await page.getByRole('button',{name:'Retry the same check',exact:true}).click();await expect(page.getByText('running',{exact:true})).toBeVisible();
  expect(state.checks).toHaveLength(2);expect(state.checks[0]).toEqual(state.checks[1]);expect(state.job!.job_id).toBe(checkId);
});

test('failed byte verification stays pending and never fetches or prepares a kit',async({page})=>{
  const state=await host(page);await open(page);await page.getByRole('dialog').getByRole('button',{name:'Check local bytes',exact:true}).click();
  state.job!.state='failed';state.job!.error_code='missing_or_corrupt_object';
  await expect(page.getByText(/Incomplete or corrupt copies stay pending/)).toBeVisible();
  await expect(page.getByRole('button',{name:'Save recovery kit',exact:true})).toHaveCount(0);expect(state.preparations).toHaveLength(0);expect(state.model.state).toBe('pending');
});

test('a successful job alone cannot substitute for confirmed retained custody',async({page})=>{
  const state=await host(page);await open(page);await page.getByRole('dialog').getByRole('button',{name:'Check local bytes',exact:true}).click();state.job!.state='succeeded';
  await expect(page.getByText('succeeded',{exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'Save recovery kit',exact:true})).toHaveCount(0);expect(state.preparations).toHaveLength(0);
});

test('retained publication waits for its active check to finish before offering a kit',async({page})=>{
  const state=await host(page);state.selectedState='retained';
  state.job={job_id:checkId,owner_id:'library',operation_id:'original',kind:'recover',state:'running',manifest_id:identity,bytes_hashed:10,bytes_copied:0,error_code:null,kit_removed:false,kit:null};
  await open(page);const dialog=page.getByRole('dialog');
  await expect(dialog.getByRole('button',{name:'Checking local bytes…',exact:true})).toBeDisabled();
  await expect(dialog.getByRole('button',{name:'Save recovery kit',exact:true})).toHaveCount(0);
  expect(state.preparations).toHaveLength(0);state.job.state='succeeded';
  await expect(dialog.getByRole('button',{name:'Save recovery kit',exact:true})).toBeVisible();expect(state.checks).toHaveLength(0);
});

test('old hosts explain pending recovery without sending unsupported decisions',async({page})=>{
  const state=await host(page,{supported:false});await expect(page.getByText(/Update the preservation host to check pending copies offline/)).toBeVisible();await expect(page.getByRole('button',{name:'Check local bytes',exact:true})).toHaveCount(0);expect(state.checks).toHaveLength(0);
});

test('unavailable original review permits only read retry',async({page})=>{
  const state=await host(page);state.metadataError=true;await open(page);await expect(page.getByRole('alert')).toBeVisible();await expect(page.getByRole('dialog').getByRole('button',{name:'Check local bytes',exact:true})).toBeDisabled();expect(state.checks).toHaveLength(0);
  state.metadataError=false;await page.getByRole('button',{name:'Read again',exact:true}).click();await expect(page.getByRole('dialog').getByRole('button',{name:'Check local bytes',exact:true})).toBeEnabled();expect(state.checks).toHaveLength(0);
});

test('active mirror sharing needs its own explicit Stop before checking',async({page})=>{
  const state=await host(page,{controller:true});await open(page);await expect(page.getByRole('dialog').getByRole('button',{name:'Check local bytes',exact:true})).toBeDisabled();expect(state.stops).toEqual([]);expect(state.checks).toHaveLength(0);
  await page.getByRole('button',{name:'Stop mirror sharing',exact:true}).click();await expect(page.getByRole('dialog').getByRole('button',{name:'Check local bytes',exact:true})).toBeEnabled();expect(state.stops).toEqual([controllerId]);expect(state.checks).toHaveLength(0);
});

for(const condition of ['removed','unavailable','changed-policy'])test(`${condition} selection is refused without byte work`,async({page})=>{
  const state=await host(page);if(condition==='removed')state.selectedState='removed';else if(condition==='unavailable')state.available=false;else state.policy='8'.repeat(64);
  await open(page);await expect(page.getByRole('dialog').getByRole('button',{name:'Check local bytes',exact:true})).toBeDisabled();expect(state.checks).toHaveLength(0);expect(state.preparations).toHaveLength(0);
});

test('Sharing exposes the same pending check without starting intake or public participation',async({page})=>{
  const state=await host(page);await page.getByRole('button',{name:'Sharing',exact:true}).click();await page.getByRole('button',{name:'Check local bytes',exact:true}).click();await expect(page.getByRole('dialog').getByRole('heading',{name:review.name,exact:true})).toBeVisible();expect(state.checks).toHaveLength(0);expect(state.preparations).toHaveLength(0);
});
