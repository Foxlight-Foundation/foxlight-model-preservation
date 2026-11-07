import {readFileSync} from 'node:fs';
import {test,expect,type Page} from '@playwright/test';

const gib=1024**3;
// The core's licence-family definition, exactly as the Library server sends it.
const families=JSON.parse(readFileSync(new URL('../../src/foxlight_preservation_library/licence_families_v1.json',import.meta.url),'utf8')) as {families:{id:string;default:boolean;licences:{id:string}[]}[]};
const defaultLicences=families.families.filter(family=>family.default).flatMap(family=>family.licences.map(item=>item.id));
const copyleft=families.families.find(family=>family.id==='copyleft')!.licences.map(item=>item.id);
const defaults={name:'Library community mirror',disk_bytes:50*gib,ingress_daily:20*gib,egress_daily:null as number|null,egress_monthly:null as number|null,http:true,exposure:'network',port:8743,ipfs:false,torrent:false,peer_setup_id:null,max_active:2,session_seconds:3600,session_bytes:64*1024**2,upload_rate:1024**2};
const publicCopy=(n:string,model:string)=>({copy_id:n.repeat(64),model_id:model,location_id:'public-copy',manifest_id:String.fromCharCode(n.charCodeAt(0)+1).repeat(64),audience:'public',size_bytes:4*gib,checked_at:1,member_count:1,publisher_key_ids:['ed25519:'+'f'.repeat(64)]});
const apache=publicCopy('1','community/apache-model'),gpl=publicCopy('3','community/gpl-model'),unknown=publicCopy('5','community/unlicensed-model'),mit=publicCopy('7','community/mit-model');
const licences:Record<string,string>={[apache.manifest_id]:'apache-2.0',[gpl.manifest_id]:'GPL-3.0',[unknown.manifest_id]:'other',[mit.manifest_id]:'mit'};
const year=365*86400;
type Held={manifest_id:string;state:'pending'|'retained'|'removed';pinned:boolean;blocked:boolean};
const browserErrors=new WeakMap<Page,string[]>();
test.beforeEach(async({page})=>{const errors:string[]=[];browserErrors.set(page,errors);page.on('pageerror',error=>errors.push(error.message));});
test.afterEach(async({page})=>{expect(browserErrors.get(page)??[]).toEqual([]);});

/** Mocked browser contract; the Library server and Core enforce the same rules. */
type Intake={share:'eligible'|'chosen';licenses:string[];definition:number;withheld:string[]};
async function host(page:Page,options:{existing?:boolean;both?:boolean;older?:boolean;intake?:Intake}={}){
  const idle={state:'idle',step:null,kept:0,total:0,skipped:[] as {copy_id:string;reason:string}[],error_code:null as string|null,expires:null as number|null};
  const state={
    snapshot:{schema_version:'skulk.preservation-mirror.v1',configured:false,available:false,policy_id:null as string|null,configuration:null as typeof defaults|null,paused:true,restart_expires:null as number|null,restart_allowed:false,controller_job_id:null as string|null,activity:null as {running:boolean;http_port:number|null;native_workers:number}|null,models:[] as Held[],model_count:0,next_offset:null,...(options.older?{}:{intake:null as Intake|null})},
    progress:{...idle},starts:[] as Record<string,unknown>[],shares:[] as Record<string,unknown>[],stops:0,reviews:[] as string[][],
  };
  if(options.both)Object.assign(state.snapshot,{configured:true,available:true,policy_id:'a'.repeat(64),configuration:{...defaults},paused:false,restart_expires:Math.floor(Date.now()/1000)+year,restart_allowed:true,controller_job_id:'c'.repeat(64),models:[apache,mit].map(item=>({manifest_id:item.manifest_id,state:'retained',pinned:false,blocked:false})),model_count:2});
  if(options.existing){
    // A mirror set up before this release: a reviewed selection, settings and a 24-hour grant.
    Object.assign(state.snapshot,{configured:true,available:true,policy_id:'a'.repeat(64),configuration:{...defaults,egress_daily:20*gib,egress_monthly:300*gib,name:'Older mirror',exposure:'loopback',port:8080},paused:false,restart_expires:Math.floor(Date.now()/1000)+20*3600,restart_allowed:true,controller_job_id:'c'.repeat(64),activity:{running:true,http_port:8080,native_workers:0},models:[{manifest_id:apache.manifest_id,state:'retained',pinned:true,blocked:false}],model_count:1});
  }
  if(options.intake)Object.assign(state.snapshot,{intake:options.intake});
  const modern=options.older?{}:{'Standing intake':true,'Uncapped uploads':true};
  const read=()=>({progress:state.progress,default_licenses:defaultLicences,licence_families:families,licence_definition:1,defaults:options.older?{...defaults,egress_daily:null,egress_monthly:null}:defaults,consent_seconds:year,disclosure:1});
  await page.route('**/api/library?offset=*',route=>route.fulfill({json:{locations:[],models:[],copies:[],public_copies:options.both?[apache,mit,gpl,unknown]:[apache,gpl,unknown],bittorrent_available:true,jobs:[],next_offset:null}}));
  await page.route('**/api/core-features',route=>route.fulfill({json:{'Portable handoffs':true,'Persistent mirrors':true,'Public copy review':true,'Retained recovery':true,...modern}}));
  await page.route('**/api/mirror-*',async route=>{
    const action=new URL(route.request().url()).pathname.split('/').at(-1);
    const input=(route.request().postDataJSON()??{}) as Record<string,unknown>;
    if(action==='mirror-read')return route.fulfill({json:state.snapshot});
    if(action==='mirror-history')return route.fulfill({json:{jobs:[],next_offset:null}});
    if(action==='mirror-share-read')return route.fulfill({json:read()});
    if(action==='mirror-share-review'){
      const listed=(input.licenses as string[]).map(item=>item.toLowerCase());state.reviews.push(listed);
      return route.fulfill({json:{copies:(input.copies as {copy_id:string;manifest_id:string}[]).map(item=>{
        const held=state.snapshot.models.find(model=>model.manifest_id===item.manifest_id);const licence=licences[item.manifest_id];
        const status=held?(held.blocked?'kept_back':'sharing'):licence==='other'?'no_license':listed.includes(licence.toLowerCase())?'eligible':'license_not_selected';
        return {...item,status,licenses:[licence]};})}});
    }
    if(action==='mirror-share-start'){
      state.shares.push(input);const copies=input.copies as {manifest_id:string}[];
      state.progress={...idle,state:'working',step:'keeping',total:copies.length};
      // The next read reports the finished, recorded Core decisions.
      setTimeout(()=>{const expires=Math.floor(Date.now()/1000)+year;if(!options.older)Object.assign(state.snapshot,{intake:{share:input.share,licenses:input.licenses,definition:1,withheld:[]}});Object.assign(state.snapshot,{configured:true,available:true,policy_id:'b'.repeat(64),configuration:input.configuration,paused:false,restart_expires:expires,restart_allowed:true,controller_job_id:'d'.repeat(64),activity:{running:true,http_port:8743,native_workers:0},models:[...state.snapshot.models.filter(model=>!copies.some(item=>item.manifest_id===model.manifest_id)).map(model=>({...model,blocked:true})),...copies.map(item=>({manifest_id:item.manifest_id,state:'retained' as const,pinned:false,blocked:input.share!=='chosen'&&Boolean(state.snapshot.models.find(model=>model.manifest_id===item.manifest_id)?.blocked)}))]});state.progress={...idle,state:'done',kept:copies.length,total:copies.length,expires};},300);
      return route.fulfill({json:read()});
    }
    if(action==='mirror-share-stop'){state.stops++;Object.assign(state.snapshot,{controller_job_id:null,activity:null,paused:true});state.progress={...state.progress,state:'stopped'};return route.fulfill({json:{progress:state.progress,job:null}});}
    if(action==='mirror-start'){state.starts.push(input);return route.fulfill({json:{job_id:'e'.repeat(64),operation_id:input.operation_id,kind:input.kind,state:'succeeded'}});}
    return route.fulfill({status:404,json:{error:'unknown_control'}});
  });
  await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Help keep models available',exact:true})).toBeVisible();
  return state;
}

/** Capture the actual viewport after finite sheet-entry animations finish. */
async function stableView(page:Page,path:string,fullPage=false){
  await page.evaluate(async()=>{await Promise.race([Promise.all(document.getAnimations().filter(animation=>animation.effect?.getTiming().iterations!==Infinity).map(animation=>animation.finished.catch(()=>undefined))),new Promise(resolve=>setTimeout(resolve,1500))]);});
  await page.screenshot({path,fullPage});
}
const shots=(name:string,info:{project:{name:string};outputPath:(name:string)=>string})=>process.env.LIBRARY_SCREENSHOTS?`${process.env.LIBRARY_SCREENSHOTS}/${info.project.name}-${name}.png`:info.outputPath(`${name}.png`);
const card=(page:Page)=>page.getByRole('region',{name:'Help keep models available'});

test('Sharing opens on one switch that is off and serves nothing by default',async({page},info)=>{
  const state=await host(page);
  await expect(card(page).getByText('Off',{exact:true})).toBeVisible();
  await expect(card(page).getByRole('button',{name:'Turn on',exact:true})).toBeEnabled();
  // The one switch comes first; the older mirror controls are collapsed under Advanced.
  const order=await page.evaluate(()=>{const a=document.querySelector('[aria-label="Help keep models available"]')!,b=[...document.querySelectorAll('summary')].find(item=>item.textContent==='Advanced mirror controls')!;return Boolean(a.compareDocumentPosition(b)&Node.DOCUMENT_POSITION_FOLLOWING);});
  expect(order).toBe(true);
  await expect(page.getByRole('heading',{name:'Keep models available',exact:true})).toBeHidden();
  await stableView(page,shots('sharing-off',info));
  expect(state.starts).toEqual([]);expect(state.shares).toEqual([]);expect(state.stops).toBe(0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});

test('one consent after the disclosures shares only public-licence copies for a year',async({page},info)=>{
  const state=await host(page);
  await card(page).getByRole('button',{name:'Turn on',exact:true}).click();
  const sheet=page.getByRole('dialog');
  await expect(sheet.getByRole('heading',{name:'Share your public copies with everyone'})).toBeVisible();
  // What to share, the licence policy and every disclosure precede the one consent.
  await expect(sheet.getByRole('radio',{name:/Every public copy whose licence allows it/})).toBeChecked();
  const list=sheet.getByRole('list',{name:'Public copies'});
  // Copyleft is on by default, so the GPL copy can be shared too.
  await expect(list.getByText('Can be shared',{exact:true})).toHaveCount(2);
  await expect(list.getByText('No licence found · stays private',{exact:true})).toHaveCount(1);
  await expect(sheet.getByText('2 of your 3 public copies match.',{exact:false})).toBeVisible();
  // Upload limits are opt-in: off, with what that means said plainly.
  await expect(sheet.getByRole('switch',{name:/^Limit uploads/})).not.toBeChecked();
  await expect(sheet.getByText('No upload limit. Uploads are counted',{exact:false})).toBeVisible();
  await expect(sheet.getByRole('checkbox',{name:/Accept downloads from other devices/})).toBeChecked();
  await expect(sheet.getByText(/sees its network address\. Downloads aren’t encrypted or anonymous\./)).toBeVisible();
  await expect(sheet.getByRole('checkbox',{name:/Share on public BitTorrent/})).toBeChecked();
  await expect(sheet.getByText(/Other peers see this host’s public IP address and what it shares\. It isn’t anonymous\./)).toBeVisible();
  await expect(sheet.getByText('Sharing keeps running after this host restarts, for one year, then asks again. Stop it at any time with one click.')).toBeVisible();
  // No sentence claims anonymity; every mention denies it.
  const text=await sheet.innerText();
  expect(text.match(/anonym\w*/gi)?.length).toBeGreaterThan(0);
  expect(text).not.toMatch(/(?<!n’t |not |isn’t )anonymous(ly)? (download|shar|access|mirror)/i);
  // Worker, session and rate settings stay folded away with sensible values.
  await expect(sheet.getByLabel('Simultaneous native workers')).toBeHidden();
  const consent=sheet.getByRole('checkbox',{name:/Keep sharing these public copies from this host/});
  await expect(consent).not.toBeChecked();
  await expect(sheet.getByRole('button',{name:'Start sharing',exact:true})).toBeDisabled();
  await stableView(page,shots('sharing-sheet',info));
  // A family switch re-reviews the exact copies; an unidentified id is never offered.
  await sheet.getByRole('switch',{name:/^Copyleft/}).uncheck();
  await expect(list.getByText('Can be shared',{exact:true})).toHaveCount(1);
  await expect(list.getByText('Licence not in your list',{exact:true})).toHaveCount(1);
  await expect(sheet.getByText('1 of your 3 public copies match.',{exact:false})).toBeVisible();
  await sheet.getByLabel('Custom licence id').fill('unknown');await sheet.getByRole('button',{name:'Add',exact:true}).click();
  await expect(sheet.getByText('That doesn’t name a licence',{exact:false})).toBeVisible();
  await sheet.getByText('Advanced settings',{exact:true}).click();
  await expect(sheet.getByLabel('Simultaneous native workers')).toHaveValue('2');
  await expect(sheet.getByLabel('Upload allowance per month (GB)')).toHaveCount(0);
  expect(state.shares).toEqual([]);
  await consent.check();
  await sheet.getByRole('button',{name:'Start sharing',exact:true}).click();
  await expect(page.getByRole('dialog')).toBeHidden();
  expect(state.shares).toHaveLength(1);
  const decision=state.shares[0] as {consent:boolean;disclosure:number;policy_id:null;share:string;licenses:string[];copies:{copy_id:string}[];configuration:typeof defaults};
  expect(decision).toMatchObject({consent:true,disclosure:1,policy_id:null,share:'eligible',licenses:defaultLicences.filter(item=>!copyleft.includes(item))});
  expect(decision.copies.map(item=>item.copy_id)).toEqual([apache.copy_id]);
  expect(decision.configuration).toMatchObject({http:true,exposure:'network',torrent:true,ipfs:false,egress_daily:null,egress_monthly:null,max_active:2});
  expect(decision.configuration.ingress_daily).toBe(20*gib);
  expect(decision.configuration.disk_bytes).toBeGreaterThanOrEqual(apache.size_bytes);
  expect(Number.isSafeInteger(decision.configuration.disk_bytes)).toBe(true);
  await expect(card(page).getByText('Sharing 1 model',{exact:true})).toBeVisible();
  await expect(card(page).getByText(/including after restarts, then asks again/)).toBeVisible();
  await expect(card(page).getByText('No limit · uploads are counted, never stopped',{exact:true})).toBeVisible();
  // The saved choice comes back from the host, not from defaults.
  await expect(card(page).getByText('Permissive, Attribution and Responsible AI',{exact:true})).toBeVisible();
  expect(state.starts).toEqual([]);
  await stableView(page,shots('sharing-on',info));
});

test('choosing copies shares only those, and needs at least one',async({page})=>{
  const state=await host(page);
  await card(page).getByRole('button',{name:'Turn on',exact:true}).click();
  const sheet=page.getByRole('dialog');
  await sheet.getByRole('radio',{name:/Only the copies I choose/}).check();
  await sheet.getByRole('checkbox',{name:/Keep sharing these public copies/}).check();
  await expect(sheet.getByRole('button',{name:'Start sharing',exact:true})).toBeDisabled();
  await sheet.getByRole('checkbox',{name:apache.model_id}).check();
  await sheet.getByRole('button',{name:'Start sharing',exact:true}).click();
  expect((state.shares[0] as {share:string;copies:{copy_id:string}[]})).toMatchObject({share:'chosen',copies:[{copy_id:apache.copy_id,manifest_id:apache.manifest_id,member_count:1}]});
});

test('without any transport nothing can be consented to',async({page})=>{
  const state=await host(page);
  await card(page).getByRole('button',{name:'Turn on',exact:true}).click();
  const sheet=page.getByRole('dialog');
  await sheet.getByRole('checkbox',{name:/Accept downloads from other devices/}).uncheck();
  await sheet.getByRole('checkbox',{name:/Share on public BitTorrent/}).uncheck();
  await expect(sheet.getByText('Choose at least one way for people to download.')).toBeVisible();
  await sheet.getByRole('checkbox',{name:/Keep sharing these public copies/}).check();
  await expect(sheet.getByRole('button',{name:'Start sharing',exact:true})).toBeDisabled();
  expect(state.shares).toEqual([]);
});

test('an existing persistent mirror keeps its selection, settings and grant after the upgrade',async({page})=>{
  const state=await host(page,{existing:true});
  await expect(card(page).getByText('Sharing 1 model',{exact:true})).toBeVisible();
  await expect(card(page).getByText(/including after restarts, then asks again/)).toBeVisible();
  await expect(card(page).getByText(/Sharing stops on .* Choose Change what’s shared to keep it on for a year\./)).toBeVisible();
  await expect(card(page).getByText(/this device only, over HTTP/)).toBeVisible();
  await card(page).getByText('Your public copies · 1 shared').click();
  await expect(card(page).getByRole('listitem').filter({hasText:apache.model_id}).getByText('Sharing',{exact:true})).toBeVisible();
  // Nothing was re-consented or changed by opening the new screen.
  await page.waitForTimeout(500);
  expect(state.starts).toEqual([]);expect(state.shares).toEqual([]);expect(state.stops).toBe(0);
  // Renewing reuses the reviewed settings and the kept selection.
  await card(page).getByRole('button',{name:'Change what’s shared',exact:true}).click();
  const sheet=page.getByRole('dialog');
  await expect(sheet.getByRole('checkbox',{name:/Accept downloads from other devices/})).not.toBeChecked();
  await expect(sheet.getByRole('button',{name:'Save changes',exact:true})).toBeDisabled();
  await sheet.getByRole('checkbox',{name:/Keep sharing these public copies from this host, over this device over HTTP/}).check();
  await sheet.getByRole('button',{name:'Save changes',exact:true}).click();
  const decision=state.shares[0] as {policy_id:string;configuration:Record<string,unknown>;copies:{copy_id:string}[]};
  expect(decision.policy_id).toBe('a'.repeat(64));
  expect(decision.configuration).toEqual({...defaults,egress_daily:20*gib,egress_monthly:300*gib,name:'Older mirror',exposure:'loopback',port:8080});
  // The kept copy stays; the GPL copy is offered under the new copyleft default,
  // and is only sent because the owner reviewed the sheet and consented again.
  expect(decision.copies.map(item=>item.copy_id)).toEqual([apache.copy_id,gpl.copy_id]);
});

test('keeping one copy back is a per-copy block that leaves sharing on',async({page})=>{
  const state=await host(page,{existing:true});
  await card(page).getByText('Your public copies · 1 shared').click();
  await card(page).getByRole('button',{name:'Keep this one back',exact:true}).click();
  expect(state.starts).toHaveLength(1);
  expect(state.starts[0]).toMatchObject({kind:'model',action:'block',manifest_id:apache.manifest_id,policy_id:'a'.repeat(64)});
  expect(state.shares).toEqual([]);expect(state.stops).toBe(0);
});

test('Stop sharing is one action',async({page},info)=>{
  const state=await host(page,{existing:true});
  await stableView(page,shots('sharing-existing',info));
  await card(page).getByRole('button',{name:'Stop sharing',exact:true}).click();
  await expect(card(page).getByText('Off',{exact:true})).toBeVisible();
  expect(state.stops).toBe(1);
  expect(state.starts).toEqual([]);
  await expect(card(page).getByRole('button',{name:'Turn on',exact:true})).toBeVisible();
  await expect(card(page).getByText(/1 copy is already kept for sharing on this host/)).toBeVisible();
});

test('the saved share mode and licences show after a reload',async({page},info)=>{
  await host(page,{existing:true,intake:{share:'chosen',licenses:['apache-2.0','gpl-3.0'],definition:1,withheld:[]}});
  await expect(card(page).getByText('Only the copies you chose',{exact:true})).toBeVisible();
  await expect(card(page).getByText('some permissive and some copyleft',{exact:true})).toBeVisible();
  // An existing upload limit stays as it was.
  await expect(card(page).getByText('20.0 GB a day, 300.0 GB a month',{exact:true})).toBeVisible();
  await card(page).getByRole('button',{name:'Change what’s shared',exact:true}).click();
  const sheet=page.getByRole('dialog');
  await expect(sheet.getByRole('radio',{name:/Only the copies I choose/})).toBeChecked();
  await expect(sheet.getByRole('switch',{name:/^Copyleft/})).not.toBeChecked();
  await expect(sheet.getByRole('switch',{name:/^Limit uploads/})).toBeChecked();
  await expect(sheet.getByLabel('Upload limit per month (GB)')).toHaveValue('300');
  await sheet.getByText('Licences that allow sharing').scrollIntoViewIfNeeded();
  await stableView(page,shots('sharing-licences',info));
  await sheet.getByRole('switch',{name:/^Limit uploads/}).scrollIntoViewIfNeeded();
  await stableView(page,shots('sharing-limits',info));
});

test('an older Skulk host keeps an upload limit on and is told why',async({page})=>{
  const state=await host(page,{older:true});
  await card(page).getByRole('button',{name:'Turn on',exact:true}).click();
  const sheet=page.getByRole('dialog');
  const limit=sheet.getByRole('switch',{name:/^Limit uploads/});
  await expect(limit).toBeChecked();await expect(limit).toBeDisabled();
  await expect(sheet.getByText('This host’s Skulk version needs an upload limit.',{exact:false})).toBeVisible();
  await sheet.getByRole('checkbox',{name:/Keep sharing these public copies/}).check();
  await sheet.getByRole('button',{name:'Start sharing',exact:true}).click();
  const decision=state.shares[0] as {configuration:typeof defaults};
  expect(decision.configuration).toMatchObject({egress_daily:20*gib,egress_monthly:300*gib});
});

test('choosing only one of two shared copies keeps the other back',async({page})=>{
  const state=await host(page,{both:true});
  await expect(card(page).getByText('Sharing 2 models',{exact:true})).toBeVisible();
  await card(page).getByRole('button',{name:'Change what’s shared',exact:true}).click();
  const sheet=page.getByRole('dialog');
  await sheet.getByRole('radio',{name:/Only the copies I choose/}).check();
  await expect(sheet.getByText('Only what you agree to here is served. Copies you don’t choose stop being served; their files stay on this host.')).toBeVisible();
  // What is served now starts chosen, so narrowing is an explicit change.
  await expect(sheet.getByRole('checkbox',{name:apache.model_id})).toBeChecked();
  await expect(sheet.getByRole('checkbox',{name:mit.model_id})).toBeChecked();
  await sheet.getByRole('checkbox',{name:mit.model_id}).uncheck();
  await sheet.getByRole('checkbox',{name:/Keep sharing these public copies/}).check();
  await sheet.getByRole('button',{name:'Save changes',exact:true}).click();
  expect(state.shares[0]).toMatchObject({share:'chosen',copies:[{copy_id:apache.copy_id}]});
  await expect(card(page).getByText('Sharing 1 model',{exact:true})).toBeVisible();
  await card(page).getByText('Your public copies · 1 shared').click();
  await expect(card(page).getByRole('listitem').filter({hasText:mit.model_id}).getByText('Kept back',{exact:true})).toBeVisible();
});

test('switching from every eligible copy to chosen ones narrows what is served',async({page})=>{
  const state=await host(page,{both:false});
  await page.unroute('**/api/library?offset=*');
  await page.route('**/api/library?offset=*',route=>route.fulfill({json:{locations:[],models:[],copies:[],public_copies:[apache,mit,gpl,unknown],bittorrent_available:true,jobs:[],next_offset:null}}));
  await page.reload();await page.getByRole('button',{name:'Sharing',exact:true}).click();
  await card(page).getByRole('button',{name:'Turn on',exact:true}).click();
  let sheet=page.getByRole('dialog');
  await expect(sheet.getByText('Only what you agree to here is served. Copies whose licence isn’t on your list stop being served; their files stay on this host.')).toBeVisible();
  // Copyleft is on by default; this owner turns it off, so the GPL copy stays private.
  await sheet.getByRole('switch',{name:/^Copyleft/}).uncheck();
  await sheet.getByRole('checkbox',{name:/Keep sharing these public copies/}).check();
  await sheet.getByRole('button',{name:'Start sharing',exact:true}).click();
  expect((state.shares[0] as {share:string;copies:{copy_id:string}[]}).copies.map(item=>item.copy_id)).toEqual([apache.copy_id,mit.copy_id]);
  await expect(card(page).getByText('Sharing 2 models',{exact:true})).toBeVisible();
  await card(page).getByRole('button',{name:'Change what’s shared',exact:true}).click();
  sheet=page.getByRole('dialog');
  await sheet.getByRole('radio',{name:/Only the copies I choose/}).check();
  await sheet.getByRole('checkbox',{name:apache.model_id}).uncheck();
  await sheet.getByRole('checkbox',{name:/Keep sharing these public copies/}).check();
  await sheet.getByRole('button',{name:'Save changes',exact:true}).click();
  expect(state.shares[1]).toMatchObject({share:'chosen',copies:[{copy_id:mit.copy_id}]});
  await expect(card(page).getByText('Sharing 1 model',{exact:true})).toBeVisible();
});
