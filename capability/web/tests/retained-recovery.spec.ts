import {test,expect,type Page} from '@playwright/test';

const identity='a'.repeat(64),policy='b'.repeat(64),jobId='c'.repeat(64);
const model={manifest_id:identity,state:'retained',pinned:false,blocked:false,error_code:null,name:'Community model and companions',size_bytes:10,member_count:2};
const review={schema_version:'skulk.preservation-mirror-review.v1',manifest_id:identity,archive_format:'preservation-v1',name:model.name,size_bytes:10,workloads:['chat'],runtime_requirements:['fixture'],capture_key_ids:['ed25519:'+'d'.repeat(64)],capture_threshold:1,members:[
  {member_id:'primary',role:'primary',repository:'fixture/model',revision:'e'.repeat(40),format:'fixture',size_bytes:6,file_count:2,license_id:'MIT',terms_reference:'LICENSE',notice_paths:['LICENSE']},
  {member_id:'companion',role:'future'.repeat(15),repository:'fixture/companion',revision:'f'.repeat(40),format:'fixture',size_bytes:4,file_count:1,license_id:'Companion terms',terms_reference:'COMPANION-LICENSE',notice_paths:['COMPANION-LICENSE']},
]};
type Job={job_id:string;owner_id:string;operation_id:string;kind:string;state:string;manifest_id:string;created_at:number;updated_at:number;bytes_hashed:number;bytes_copied:number;error_code:null;kit_removed:boolean;kit:null|{state:string;manifest_id:string;size_bytes:number;sha256:string}};
const errors=new WeakMap<Page,string[]>();
test.beforeEach(async({page})=>{const found:string[]=[];errors.set(page,found);page.on('pageerror',error=>found.push(error.message));page.on('console',message=>{if(message.type()==='error'&&!message.text().includes('net::ERR_FAILED'))found.push(message.text());});});
test.afterEach(async({page})=>{expect(errors.get(page)??[]).toEqual([]);});

/** Browser contract fixture; genuine Core/archive acceptance is a separate gate. */
async function host(page:Page){
  const state={job:null as Job|null,history:[] as Job[],preparations:[] as Record<string,unknown>[],removed:[] as string[],loseReply:false,metadataError:false,showModels:true};
  await page.route('**/api/library?offset=*',route=>route.fulfill({json:{locations:[],models:[],copies:[],public_copies:[],jobs:[],next_offset:null}}));
  await page.route('**/api/core-features',route=>route.fulfill({json:{'Retained recovery':true,'Persistent mirrors':true,'Public copy review':true}}));
  await page.route('**/api/mirror-*',async route=>{
    const action=new URL(route.request().url()).pathname.split('/').at(-1);
    if(action==='mirror-read')return route.fulfill({json:{schema_version:'skulk.preservation-mirror.v1',configured:true,available:true,policy_id:policy,configuration:null,paused:true,restart_expires:null,restart_allowed:false,controller_job_id:null,models:state.showModels?[model]:[],model_count:state.showModels?1:0,next_offset:null}});
    if(action==='mirror-history'){const offset=Number(route.request().postDataJSON().offset??0);return route.fulfill({json:{jobs:state.history.slice(offset,offset+20),next_offset:offset+20<state.history.length?offset+20:null}});}
    if(action==='mirror-retained-review')return state.metadataError?route.abort('failed'):route.fulfill({json:review});
    if(action==='mirror-kit-latest')return route.fulfill({json:{manifest_id:identity,job:state.job}});
    if(action==='mirror-prepare'){
      const input=route.request().postDataJSON() as Record<string,unknown>;state.preparations.push(input);
      if(!state.job||input.previous_job_id){state.job={job_id:input.previous_job_id?'e'.repeat(64):jobId,owner_id:'library',operation_id:'fixture',kind:'prepare_kit',state:'running',manifest_id:identity,created_at:1,updated_at:1,bytes_hashed:0,bytes_copied:0,error_code:null,kit_removed:false,kit:null};state.history.unshift(state.job);}
      if(state.loseReply){state.loseReply=false;return route.abort('failed');}
      return route.fulfill({json:state.job});
    }
    if(action==='mirror-observe')return route.fulfill({json:state.history.find(job=>job.job_id===route.request().postDataJSON().job_id)});
    if(action==='mirror-stop'){const selected=state.history.find(job=>job.job_id===route.request().postDataJSON().job_id)!;selected.state='cancelled';return route.fulfill({json:selected});}
    if(action==='mirror-kit-remove'){
      const input=route.request().postDataJSON();const selected=state.history.find(job=>job.job_id===input.job_id)!;expect(input.confirm_job_id).toBe(selected.job_id);
      state.removed.push(input.job_id);selected.kit_removed=true;selected.kit=null;
      return route.fulfill({json:selected});
    }
    throw new Error(`Unexpected control: ${action}`);
  });
  await page.goto('/');
  await expect(page.getByRole('heading',{name:'Retained mirror copies',exact:true})).toBeVisible();
  return state;
}

async function open(page:Page){
  await page.getByRole('button',{name:'Save recovery kit',exact:true}).click();
  await expect(page.getByRole('dialog').getByRole('heading',{name:model.name,exact:true})).toBeVisible();
  await expect(page.getByText('Complete retained selection',{exact:true})).toBeVisible();
}

test('first launch finds retained custody after original import loss, reopens preparation and offers exact kit-only cleanup',async({page},info)=>{
  const state=await host(page);
  await expect(page.getByRole('heading',{name:'Keep your models within reach.',exact:true})).toBeVisible();
  expect(state.preparations).toHaveLength(0);
  await open(page);
  await page.getByText('Members and declared terms',{exact:true}).click();
  await expect(page.getByText('Companion terms',{exact:true})).toBeVisible();
  await page.getByText('Capture signatures and trust',{exact:true}).click();
  await expect(page.getByText(/does not grant registry or runtime trust/)).toBeVisible();
  await page.getByRole('button',{name:'Prepare recovery kit',exact:true}).click();
  await expect(page.getByText('running',{exact:true})).toBeVisible();
  expect(state.preparations).toEqual([{manifest_id:identity,policy_id:policy}]);
  await page.reload();await open(page);
  await expect(page.getByText('running',{exact:true})).toBeVisible();
  expect(state.preparations).toHaveLength(1);
  state.job!.state='succeeded';state.job!.bytes_hashed=10;state.job!.bytes_copied=10;
  state.job!.kit={state:'prepared',manifest_id:identity,size_bytes:17,sha256:'f'.repeat(64)};
  await expect(page.getByRole('link',{name:'Download recovery kit',exact:true})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
  await page.evaluate(async()=>{await Promise.all(document.getAnimations().filter(a=>a.effect?.getTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>undefined)));});
  await page.screenshot({path:info.outputPath('retained-recovery-ready.png')});
  // This fixture checks UI metadata and the exact native download link. Chrome
  // downloads bypass mocked page routes; genuine bytes are qualified against
  // the running Library bridge/Core, not invented by this browser fixture.
  await expect(page.getByRole('link',{name:'Download recovery kit',exact:true})).toHaveAttribute('href',`http://127.0.0.1:4178/api/mirror-kit/${jobId}`);
  await expect(page.getByRole('link',{name:'Download recovery kit',exact:true})).toHaveAttribute('download','');
  await page.getByRole('button',{name:'Remove this prepared kit…',exact:true}).click();
  await expect(page.getByRole('button',{name:'Remove only this kit',exact:true})).toBeDisabled();
  await page.getByRole('checkbox',{name:/Delete only this preparation’s kit files/}).check();
  await page.getByRole('button',{name:'Remove only this kit',exact:true}).click();
  await expect(page.getByText('kit removed',{exact:true})).toBeVisible();
  expect(state.removed).toEqual([jobId]);expect(model.state).toBe('retained');
  await expect(page.getByRole('link',{name:'Download recovery kit',exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'Close retained recovery',exact:true}).click();
  await open(page);expect(state.preparations).toHaveLength(1);
  await page.getByRole('button',{name:'Prepare another kit',exact:true}).click();
  expect(state.preparations[1]).toEqual({manifest_id:identity,policy_id:policy,previous_job_id:jobId});
});

test('lost preparation reply is found after reload without starting a second job or sharing',async({page})=>{
  const state=await host(page);state.loseReply=true;
  await open(page);await page.getByRole('button',{name:'Prepare recovery kit',exact:true}).click();
  await expect(page.getByRole('alert')).toBeVisible();
  expect(state.preparations).toHaveLength(1);
  await page.reload();await open(page);
  await expect(page.getByText('running',{exact:true})).toBeVisible();
  expect(state.preparations).toHaveLength(1);
  await page.getByRole('button',{name:'Stop this preparation',exact:true}).click();
  await expect(page.getByText('cancelled',{exact:true})).toBeVisible();
  expect(model.state).toBe('retained');
});

test('unavailable authenticated metadata leaves preparation disabled and needs only a read retry',async({page})=>{
  const state=await host(page);state.metadataError=true;
  await page.getByRole('button',{name:'Save recovery kit',exact:true}).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByRole('button',{name:'Prepare recovery kit',exact:true})).toBeDisabled();
  expect(state.preparations).toHaveLength(0);
  state.metadataError=false;await page.getByRole('button',{name:'Read again',exact:true}).click();
  await expect(page.getByRole('button',{name:'Prepare recovery kit',exact:true})).toBeEnabled();
  expect(state.preparations).toHaveLength(0);
});

test('a newer failed preparation keeps the earlier kit downloadable and separately removable',async({page},info)=>{
  const state=await host(page);await open(page);
  await page.getByRole('button',{name:'Prepare recovery kit',exact:true}).click();
  await expect(page.getByText('running',{exact:true})).toBeVisible();
  const first=state.job!;first.state='succeeded';first.kit={state:'prepared',manifest_id:identity,size_bytes:17,sha256:'f'.repeat(64)};
  await expect(page.getByRole('link',{name:'Download recovery kit',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Prepare another kit',exact:true}).click();
  await expect(page.getByText('running',{exact:true})).toBeVisible();
  state.job!.state='failed';
  await expect(page.getByText('failed',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Earlier recovery kits',exact:true}).click();
  const earlier=page.getByRole('article',{name:`Recovery kit receipt ${first.job_id}`,exact:true});
  await earlier.getByRole('button',{name:'Recovery kit actions',exact:true}).click();
  const selected=page.getByRole('dialog').filter({has:page.getByRole('heading',{name:'Recovery kit actions',exact:true})});
  await expect(selected.getByRole('link',{name:'Download this recovery kit',exact:true})).toHaveAttribute('href',`http://127.0.0.1:4178/api/mirror-kit/${first.job_id}`);
  await expect(selected.getByRole('button',{name:'Remove only this kit',exact:true})).toBeDisabled();
  await selected.getByRole('checkbox',{name:/Delete only this preparation’s kit files/}).check();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
  await page.screenshot({path:info.outputPath('earlier-kit-actions.png')});
  await selected.getByRole('button',{name:'Remove only this kit',exact:true}).click();
  await expect(selected.getByText('kit removed',{exact:true})).toBeVisible();
  expect(state.removed).toEqual([first.job_id]);expect(state.job!.state).toBe('failed');expect(state.job!.kit_removed).toBe(false);expect(model.state).toBe('retained');
  expect(state.preparations).toHaveLength(2);
});

test('permanent history manages an older kit after model metadata loss and beyond twenty other decisions',async({page})=>{
  const state=await host(page);
  const first:Job={job_id:jobId,owner_id:'library',operation_id:'old',kind:'prepare_kit',state:'succeeded',manifest_id:identity,created_at:1,updated_at:1,bytes_hashed:10,bytes_copied:10,error_code:null,kit_removed:false,kit:{state:'prepared',manifest_id:identity,size_bytes:17,sha256:'f'.repeat(64)}};
  state.history=Array.from({length:20},(_,i)=>({...first,job_id:(i+100).toString(16).padStart(64,'0'),kind:'model',kit:null}));state.history.push(first);state.showModels=false;state.metadataError=true;
  await page.getByRole('button',{name:'Sharing',exact:true}).click();
  await page.getByText('Mirror activity and decisions',{exact:true}).click();
  await page.getByRole('button',{name:'Older decisions',exact:true}).click();
  await page.getByRole('article',{name:`Mirror decision ${first.job_id}`,exact:true}).getByRole('button',{name:'Recovery kit actions',exact:true}).click();
  await expect(page.getByRole('link',{name:'Download this recovery kit',exact:true})).toBeVisible();
  await page.getByRole('checkbox',{name:/Delete only this preparation’s kit files/}).check();
  await page.getByRole('button',{name:'Remove only this kit',exact:true}).click();
  await expect(page.getByText('kit removed',{exact:true})).toBeVisible();expect(state.removed).toEqual([first.job_id]);expect(state.preparations).toHaveLength(0);
});
