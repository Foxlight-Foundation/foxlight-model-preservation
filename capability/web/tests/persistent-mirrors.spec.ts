import {test,expect,type Page} from '@playwright/test';

const identity='2'.repeat(64), initialPolicy='a'.repeat(64);
const copy={copy_id:'1'.repeat(64),model_id:'community/future',location_id:'public-copy',manifest_id:identity,audience:'public',size_bytes:10000,checked_at:1,member_count:2,publisher_key_ids:['ed25519:'+'3'.repeat(64)]};
const configuration={name:'Community mirror',disk_bytes:1024**3,ingress_daily:1024**3,egress_daily:1024**3,egress_monthly:10*1024**3,http:true,exposure:'loopback',port:0,ipfs:false,torrent:false,peer_setup_id:null,max_active:2,session_seconds:3600,session_bytes:64*1024**2,upload_rate:1024**2};
const review={schema_version:'skulk.preservation-public-review.v1',copy_id:copy.copy_id,manifest_id:identity,archive_format:'preservation-v1',name:'Future community model',size_bytes:10000,workloads:['fixture'],runtime_requirements:[],members:[
  {member_id:'primary',role:'primary',repository:'community/future',revision:'b'.repeat(40),format:'fixture',size_bytes:6000,file_count:2,license_id:'primary-terms',terms_reference:'LICENSE',notice_paths:['LICENSE']},
  {member_id:'companion',role:'assistant',repository:'community/companion',revision:'c'.repeat(40),format:'fixture',size_bytes:4000,file_count:2,license_id:'companion-terms',terms_reference:'COMPANION-LICENSE',notice_paths:['COMPANION-LICENSE']},
]};
type Held={manifest_id:string;state:'pending'|'retained'|'removed';pinned:boolean;blocked:boolean;error_code:null};
type Receipt={job_id:string;owner_id:string;operation_id:string;kind:string;state:string;created_at:number;updated_at:number;manifest_id:string|null;activity:null;error_code:string|null;bytes_hashed:number;bytes_copied:number;bytes_received:number};
const browserErrors=new WeakMap<Page,string[]>();
test.beforeEach(async({page})=>{const errors:string[]=[];browserErrors.set(page,errors);page.on('pageerror',error=>errors.push(error.message));});
test.afterEach(async({page})=>{expect(browserErrors.get(page)??[]).toEqual([]);});

/** Capture the actual viewport after finite sheet-entry animations finish. */
async function stableView(page:Page,path:string){
  await page.evaluate(async()=>{await Promise.all(document.getAnimations().filter(animation=>animation.effect?.getTiming().iterations!==Infinity).map(animation=>animation.finished.catch(()=>undefined)));});
  await page.screenshot({path,fullPage:false});
}

/** Mocked browser contract only; actual host/archive acceptance is separate. */
async function host(page:Page,options:{configured?:boolean;terms?:boolean;held?:boolean;pinned?:boolean;offPageRemoved?:boolean;recovery?:boolean}={}) {
  const state={
    snapshot:{schema_version:'skulk.preservation-mirror.v1',configured:options.configured!==false,available:options.configured!==false,policy_id:options.configured===false?null:initialPolicy,configuration:options.configured===false?null:{...configuration},paused:true,restart_expires:null as number|null,restart_allowed:false,controller_job_id:null as string|null,activity:null as {running:boolean;http_port:number|null;native_workers:number}|null,models:[] as Held[],model_count:0,next_offset:null},
    jobs:[] as Receipt[],starts:[] as Record<string,unknown>[],stops:[] as string[],loseNext:false,
  };
  if(options.held){state.snapshot.models=[{manifest_id:identity,state:'retained',pinned:options.pinned===true,blocked:false,error_code:null}];state.snapshot.model_count=1;}
  const receipts=new Map<string,Receipt>();let sequence=8;
  await page.route('**/api/library?offset=*',route=>route.fulfill({json:{locations:[],models:[],copies:[],public_copies:[copy],bittorrent_available:true,jobs:[],next_offset:null}}));
  await page.route('**/api/core-features',route=>route.fulfill({json:{'Portable handoffs':true,'Persistent mirrors':true,'Public copy review':options.terms!==false,'Retained recovery':options.recovery!==false}}));
  await page.route('**/api/mirror-*',async route=>{
    const action=new URL(route.request().url()).pathname.split('/').at(-1);
    const input=route.request().postDataJSON() as Record<string,unknown>;
    if(action==='mirror-read')return route.fulfill({json:state.snapshot});
    if(action==='mirror-history')return route.fulfill({json:{jobs:state.jobs,next_offset:null}});
    if(action==='mirror-selection')return route.fulfill({json:{schema_version:'skulk.preservation-mirror-selection.v1',manifest_id:identity,configured:state.snapshot.configured,available:state.snapshot.available,policy_id:state.snapshot.policy_id,model:options.offPageRemoved?{manifest_id:identity,state:'removed',pinned:false,blocked:false,error_code:null}:state.snapshot.models.find(model=>model.manifest_id===identity)??null}});
    if(action==='mirror-copy-review'){
      expect(input).toEqual({copy_id:copy.copy_id,manifest_id:identity,member_count:2});
      return route.fulfill({json:review});
    }
    if(action==='mirror-stop'){
      const selected=String(input.job_id);state.stops.push(selected);
      if(state.snapshot.controller_job_id===selected){state.snapshot.controller_job_id=null;state.snapshot.activity=null;state.snapshot.paused=true;state.snapshot.restart_allowed=false;}
      const receipt=state.jobs.find(job=>job.job_id===selected);
      if(receipt)receipt.state='cancelled';
      return route.fulfill({json:receipt??{job_id:selected,state:'cancelled'}});
    }
    if(action!=='mirror-start')return route.fulfill({status:404,json:{error:'unknown_control'}});
    state.starts.push(input);
    const operation=String(input.operation_id);
    let receipt=receipts.get(operation);
    if(!receipt){
      receipt={job_id:(sequence++).toString(16).padStart(64,'0'),owner_id:'library',operation_id:operation,kind:String(input.kind),state:'succeeded',created_at:1,updated_at:1,manifest_id:typeof input.manifest_id==='string'?input.manifest_id:null,activity:null,error_code:null,bytes_hashed:0,bytes_copied:0,bytes_received:0};
      receipts.set(operation,receipt);state.jobs.unshift(receipt);
      if(input.kind==='configure'){state.snapshot.configuration=input.configuration as typeof configuration;state.snapshot.configured=true;state.snapshot.available=true;state.snapshot.policy_id='b'.repeat(64);}
      if(input.kind==='retain'){receipt.state='running';state.snapshot.models=[{manifest_id:identity,state:'pending',pinned:false,blocked:false,error_code:null}];state.snapshot.model_count=1;state.snapshot.policy_id='c'.repeat(64);}
      if(input.kind==='grant_resume')state.snapshot.restart_expires=Number(input.resume_expires);
      if(input.kind==='revoke_resume')state.snapshot.restart_expires=null;
      if(input.kind==='run'){receipt.state='running';state.snapshot.controller_job_id=receipt.job_id;state.snapshot.activity={running:true,http_port:41000,native_workers:0};state.snapshot.paused=false;state.snapshot.restart_allowed=input.mode==='resume';}
      if(input.kind==='prune')state.snapshot.models[0].state='removed';
      if(input.kind==='model'&&input.action==='unpin'){state.snapshot.models[0].pinned=false;state.snapshot.policy_id='d'.repeat(64);}
      if(input.kind==='model'&&input.action==='pin')state.snapshot.models[0].pinned=true;
    }
    if(state.loseNext){state.loseNext=false;return route.abort('failed');}
    return route.fulfill({json:receipt});
  });
  await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Keep models available',exact:true})).toBeVisible();
  return state;
}

test('first configuration selects no transport and creates no network session',async({page},info)=>{
  const state=await host(page,{configured:false});
  await page.getByRole('button',{name:'Set up mirror',exact:true}).click();
  await expect(page.getByRole('checkbox',{name:'HTTP mirror',exact:true})).not.toBeChecked();
  await expect(page.getByRole('checkbox',{name:'Public IPFS sharing',exact:true})).not.toBeChecked();
  await expect(page.getByRole('checkbox',{name:'Public BitTorrent sharing',exact:true})).not.toBeChecked();
  expect(state.starts).toHaveLength(0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
  await stableView(page,info.outputPath('mirror-settings.png'));
  await page.getByRole('button',{name:'Save settings',exact:true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(state.starts).toHaveLength(1);
  expect(state.starts[0]).toMatchObject({kind:'configure',policy_id:null,configuration:{http:false,ipfs:false,torrent:false,exposure:'loopback'}});
  expect(state.starts[0]).not.toHaveProperty('public_participation');
  expect(state.snapshot.controller_job_id).toBeNull();
  await expect(page.getByRole('button',{name:'Start sharing',exact:true})).toBeDisabled();
});

test('exact lookup finds removed selections outside the displayed page before intake consent',async({page})=>{
  const state=await host(page,{offPageRemoved:true});
  expect(state.snapshot.models).toHaveLength(0);
  await page.getByText('Keep another public copy',{exact:true}).click();
  await page.getByRole('button',{name:'Keep available',exact:true}).click();
  await page.getByRole('checkbox',{name:/I reviewed the terms for every listed member/}).check();
  await expect(page.getByRole('button',{name:'Keep this public copy',exact:true})).toBeDisabled();
  await page.getByRole('checkbox',{name:/I deliberately want to return this previously removed exact selection/}).check();
  await page.getByRole('button',{name:'Keep this public copy',exact:true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(state.starts[0]).toMatchObject({kind:'retain',confirm_reintake:true});
});

test('an older recovery contract keeps existing controller Stop available',async({page})=>{
  const state=await host(page,{held:true,recovery:false});
  state.snapshot.controller_job_id='f'.repeat(64);
  await expect(page.getByRole('button',{name:'Stop sharing',exact:true})).toBeVisible();
  await page.getByText('Keep another public copy',{exact:true}).click();
  await expect(page.getByText(/exact retained-selection lookup and recovery kits/)).toBeVisible();
  await page.getByRole('button',{name:'Stop sharing',exact:true}).click();
  expect(state.stops).toEqual(['f'.repeat(64)]);expect(state.starts).toHaveLength(0);
});

test('all companion terms precede intake and a lost reply retries the unchanged decision',async({page},info)=>{
  const state=await host(page);state.loseNext=true;
  await page.getByText('Keep another public copy',{exact:true}).click();
  await page.getByRole('button',{name:'Keep available',exact:true}).click();
  await expect(page.getByText('companion-terms',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Keep this public copy',exact:true})).toBeDisabled();
  await page.getByRole('checkbox',{name:/I reviewed the terms for every listed member/}).check();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
  await stableView(page,info.outputPath('mirror-all-member-review.png'));
  await page.getByRole('button',{name:'Keep this public copy',exact:true}).click();
  await expect(page.getByRole('alert')).toBeVisible();
  expect(state.starts).toHaveLength(1);
  await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve()))));
  expect(state.starts).toHaveLength(1);
  await page.getByRole('button',{name:'Keep this public copy',exact:true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(state.starts).toHaveLength(2);
  expect(state.starts[1]).toEqual(state.starts[0]);
  expect(state.starts[1]).toMatchObject({kind:'retain',policy_id:initialPolicy,manifest_id:identity,confirm_redistribution:true,confirm_reintake:false});
  expect(state.jobs).toHaveLength(1);
  expect(state.snapshot.controller_job_id).toBeNull();
  await page.getByText('Mirror activity and decisions',{exact:true}).click();
  await page.getByRole('button',{name:'Stop this operation',exact:true}).click();
  expect(state.stops).toEqual([state.jobs[0].job_id]);
});

test('standing permission does not start sharing and Stop preserves retained models',async({page})=>{
  const state=await host(page,{held:true});
  await page.getByRole('button',{name:'Restart permission',exact:true}).click();
  await expect(page.getByRole('button',{name:'Allow sharing after restart',exact:true})).toBeDisabled();
  await page.getByRole('checkbox',{name:/Allow this exact policy to resume/}).check();
  await page.getByRole('button',{name:'Allow sharing after restart',exact:true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(state.starts.map(input=>input.kind)).toEqual(['grant_resume']);
  expect(state.snapshot.controller_job_id).toBeNull();
  await page.getByRole('button',{name:'Start sharing',exact:true}).click();
  await page.getByRole('checkbox',{name:/Use separately saved restart permission/}).check();
  await expect(page.getByRole('button',{name:'Share retained copies',exact:true})).toBeDisabled();
  await page.getByRole('checkbox',{name:/Allow this mirror to serve these retained public copies/}).check();
  await page.getByRole('button',{name:'Share retained copies',exact:true}).click();
  await expect(page.getByRole('button',{name:'Stop sharing',exact:true})).toBeVisible();
  const controller=state.snapshot.controller_job_id;
  expect(state.starts.at(-1)).toMatchObject({kind:'run',mode:'resume',public_participation:true,policy_id:initialPolicy});
  await page.getByRole('button',{name:'Stop sharing',exact:true}).click();
  await expect(page.getByRole('button',{name:'Start sharing',exact:true})).toBeVisible();
  expect(state.stops).toEqual([controller]);
  expect(state.snapshot.models[0].state).toBe('retained');
  expect(state.snapshot.paused).toBe(true);
  expect(state.starts.some(input=>input.kind==='prune')).toBe(false);
});

test('an older terms contract keeps Stop available even without live activity details',async({page})=>{
  const state=await host(page,{held:true,terms:false});
  const owned='f'.repeat(64);state.snapshot.controller_job_id=owned;state.snapshot.activity=null;
  await expect(page.getByRole('button',{name:'Stop sharing',exact:true})).toBeVisible();
  await page.getByText('Keep another public copy',{exact:true}).click();
  await expect(page.getByText(/Existing Stop controls remain available/)).toBeVisible();
  await page.getByRole('button',{name:'Stop sharing',exact:true}).click();
  expect(state.stops).toEqual([owned]);
  expect(state.snapshot.models[0].state).toBe('retained');
});

test('removal requires unpinning and fresh exact-selection consent after navigation',async({page})=>{
  const state=await host(page,{held:true,pinned:true});
  const controls=()=>page.getByRole('button',{name:'Mirror controls for community/future',exact:true});
  await controls().click();
  await expect(page.getByRole('menuitem',{name:'Remove mirror copy',exact:true})).toBeDisabled();
  await page.getByRole('menuitem',{name:'Unpin mirror copy',exact:true}).click();
  await page.getByRole('checkbox',{name:/Confirm: unpin mirror copy/}).check();
  await page.getByRole('button',{name:'Unpin mirror copy',exact:true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await controls().click();await page.getByRole('menuitem',{name:'Remove mirror copy',exact:true}).click();
  await expect(page.getByRole('button',{name:'Remove mirror copy',exact:true})).toBeDisabled();
  await page.getByRole('checkbox',{name:'Delete only this exact retained mirror copy.',exact:true}).check();
  await page.getByRole('button',{name:'Storage',exact:true}).click();
  await page.getByRole('button',{name:'Sharing',exact:true}).click();
  await controls().click();await page.getByRole('menuitem',{name:'Remove mirror copy',exact:true}).click();
  await expect(page.getByRole('checkbox',{name:'Delete only this exact retained mirror copy.',exact:true})).not.toBeChecked();
  expect(state.starts.some(input=>input.kind==='prune')).toBe(false);
  await page.getByRole('checkbox',{name:'Delete only this exact retained mirror copy.',exact:true}).check();
  await page.getByRole('button',{name:'Remove mirror copy',exact:true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(state.starts.at(-1)).toMatchObject({kind:'prune',policy_id:'d'.repeat(64),manifest_id:identity,confirm_delete_manifest:identity});
  expect(state.snapshot.models[0].state).toBe('removed');
  expect(state.starts.some(input=>input.kind==='retain')).toBe(false);
});

test('late terms responses cannot reopen a sheet after leaving Sharing',async({page})=>{
  const state=await host(page);let release:()=>void=()=>{};
  const held=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/api/mirror-copy-review',async route=>{await held;await route.fulfill({json:review});});
  await page.getByText('Keep another public copy',{exact:true}).click();
  const requested=page.waitForRequest('**/api/mirror-copy-review');
  await page.getByRole('button',{name:'Keep available',exact:true}).click();await requested;
  await page.getByRole('button',{name:'Library',exact:true}).click();
  const response=page.waitForResponse('**/api/mirror-copy-review');release();await(await response).finished();
  await page.getByRole('button',{name:'Sharing',exact:true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(state.starts).toHaveLength(0);
});

test('unavailable storage is not presented as an empty mirror and Stop remains usable',async({page})=>{
  const state=await host(page,{held:true});const owned='f'.repeat(64);
  state.snapshot.available=false;state.snapshot.models=[];state.snapshot.configuration=null;state.snapshot.policy_id=null;state.snapshot.controller_job_id=owned;
  await expect(page.getByText(/An unavailable observation does not mean the mirror is empty/)).toBeVisible();
  await expect(page.getByText(/No retained selections yet/)).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Mirror settings',exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'Stop sharing',exact:true}).click();
  expect(state.stops).toEqual([owned]);
});

test('a slow intake cancellation does not disable the current controller Stop',async({page})=>{
  const state=await host(page,{held:true});const controller='f'.repeat(64),intake='e'.repeat(64);
  state.snapshot.controller_job_id=controller;
  state.jobs=[{job_id:intake,owner_id:'library',operation_id:'intake',kind:'retain',state:'running',created_at:1,updated_at:1,manifest_id:identity,activity:null,error_code:null,bytes_hashed:0,bytes_copied:0,bytes_received:0}];
  let release:()=>void=()=>{};const held=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/api/mirror-stop',async route=>{if(route.request().postDataJSON().job_id!==intake)return route.fallback();state.stops.push(intake);await held;state.jobs[0].state='cancelled';await route.fulfill({json:state.jobs[0]});});
  try{
    await page.getByText('Mirror activity and decisions',{exact:true}).click();
    const request=page.waitForRequest(request=>request.url().endsWith('/api/mirror-stop')&&request.postDataJSON().job_id===intake);
    await page.getByRole('button',{name:'Stop this operation',exact:true}).click();await request;
    await expect(page.getByRole('button',{name:'Stop sharing',exact:true})).toBeEnabled();
    await page.getByRole('button',{name:'Stop sharing',exact:true}).click();
    expect(state.stops).toEqual([intake,controller]);
    expect(state.snapshot.models[0].state).toBe('retained');
  }finally{release();}
});

test('long original terms and notice paths remain readable without horizontal clipping',async({page})=>{
  await host(page);
  const longReference='https://example.invalid/'+ 'original-terms'.repeat(100);
  await page.route('**/api/mirror-copy-review',route=>route.fulfill({json:{...review,members:review.members.map(member=>({...member,terms_reference:longReference,notice_paths:['notices/'+'original-notice'.repeat(60)]}))}}));
  await page.getByText('Keep another public copy',{exact:true}).click();
  await page.getByRole('button',{name:'Keep available',exact:true}).click();
  await expect(page.getByText(longReference,{exact:true})).toHaveCount(2);
  await expect(page.getByRole('checkbox',{name:/I reviewed the terms for every listed member/})).toBeVisible();
  const clipped=await page.locator('.sk-sheet-body,.sk-mirror-member,.sk-mirror-member .sk-row-v').evaluateAll(elements=>elements.some(element=>element.scrollWidth>element.clientWidth+1));
  expect(clipped).toBe(false);
});
