import {test,expect} from '@playwright/test';

const copy={copy_id:'1'.repeat(64),model_id:'community/future',location_id:'public-1',manifest_id:'2'.repeat(64),audience:'public',size_bytes:10000,checked_at:1,member_count:2,publisher_key_ids:['ed25519:'+'3'.repeat(64)]};
const plan={plan_id:'a'.repeat(64),kind:'http_share',model_id:copy.model_id,location_id:copy.location_id,manifest_id:copy.manifest_id,size_bytes:copy.size_bytes,members:['community/future'],member_count:2,expires_at:9999999999,publisher_key_ids:copy.publisher_key_ids,sharing:{public_network:false,listen_port:0,duration_seconds:3600,upload_limit_bytes:1073741824,upload_rate_bytes:1048576,advertised_url:null}};

test('public mirror review has fresh consent, preserves exact limits, and requires no private archive',async({page})=>{
  let started:Record<string,unknown>|null=null;
  let planned:Record<string,unknown>|null=null;
  await page.route('**/api/library?offset=*',route=>route.fulfill({json:{locations:[],models:[],copies:[],public_copies:[copy],bittorrent_available:true,jobs:[],next_offset:null}}));
  await page.route('**/api/sharing-plan',route=>{planned=route.request().postDataJSON();return route.fulfill({json:plan});});
  await page.route('**/api/start',route=>{started=route.request().postDataJSON();return route.fulfill({json:{}});});
  await page.goto('/');
  await page.getByRole('button',{name:'Sharing',exact:true}).click();
  expect(started).toBeNull();
  await page.getByRole('button',{name:'Set up HTTP mirror'}).click();
  await expect(page.getByLabel('Network access')).toHaveValue('local');
  await page.getByRole('button',{name:'Review sharing session'}).click();
  await expect(page.getByRole('heading',{name:'Start this HTTP mirror'})).toBeVisible();
  await expect(page.getByRole('button',{name:'Start reviewed session'})).toBeDisabled();
  await page.getByLabel('I choose this sharing exposure').check();
  await page.reload();
  await expect(page.getByRole('button',{name:'Start reviewed session'})).toBeDisabled();
  await page.getByLabel('I choose this sharing exposure').check();
  await page.getByRole('button',{name:'Start reviewed session'}).click();
  expect(planned).toMatchObject({kind:'http_share',copy_id:copy.copy_id,public_network:false,upload_limit_bytes:1073741824,duration_seconds:3600});
  expect(started).toMatchObject({plan_id:plan.plan_id,public_participation:true});
  expect(started).not.toHaveProperty('allow_repository_code');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});

test('public import verifies without starting a public session or granting runtime permission',async({page})=>{
  const imported={...plan,kind:'mirror_import',sharing:null,requires_owner_acceptance:true,location_id:'public-kit'};
  let started:Record<string,unknown>|null=null;
  await page.route('**/api/library?offset=*',route=>route.fulfill({json:{locations:[],models:[],copies:[],public_copies:[],uploads:[],jobs:[],next_offset:null,bittorrent_available:false}}));
  await page.route('**/api/upload-create',route=>route.fulfill({json:{upload_id:'1'.repeat(32),size_bytes:20,offset:0,complete:false}}));
  await page.route('**/api/upload/*',route=>route.fulfill({json:{upload_id:'1'.repeat(32),size_bytes:20,offset:20,complete:true}}));
  await page.route('**/api/sharing-plan',route=>route.fulfill({json:imported}));
  await page.route('**/api/start',route=>{started=route.request().postDataJSON();return route.fulfill({json:{}});});
  await page.goto('/');
  await page.getByRole('button',{name:'Sharing',exact:true}).click();
  await page.getByRole('button',{name:'Import public bundle'}).click();
  await page.getByLabel('Public bundle file').setInputFiles({name:'public.foxkit',mimeType:'application/octet-stream',buffer:Buffer.alloc(20,1)});
  await expect(page.getByRole('button',{name:'Import public copy',exact:true})).toBeDisabled();
  await page.getByLabel('I accept this exact public copy').check();
  await page.getByRole('button',{name:'Import public copy',exact:true}).click();
  expect(started).toMatchObject({accept_owner_capture:true});
  expect(started).not.toHaveProperty('public_participation');
  expect(started).not.toHaveProperty('allow_repository_code');
});

test('stop waits for owned completion and retained torrent download joins no swarm',async({page})=>{
  let stopping=false;
  const job={job_id:'4'.repeat(64),model_id:copy.model_id,kind:'torrent_share',state:'running',stage:'sharing',bytes_copied:100,bytes_hashed:100,created_at:1,sharing_policy:plan.sharing,sharing:{ready:true,listen_port:4000,charged_bytes:100,connected_peers:2,accounting:'observed-torrent-payload',metainfo_sha256:'5'.repeat(64)}};
  await page.route('**/api/library?offset=*',route=>route.fulfill({json:{locations:[],models:[],copies:[],public_copies:[copy],jobs:[{...job,state:stopping?'cancelling':'running'}],next_offset:null,bittorrent_available:true}}));
  await page.route('**/api/cancel',route=>{stopping=true;return route.fulfill({json:{...job,state:'cancelling'}});});
  await page.goto('/');
  await page.getByRole('button',{name:'Sharing',exact:true}).click();
  await expect(page.getByRole('link',{name:'Download torrent'})).toHaveAttribute('href',new RegExp(`/api/torrent/${job.job_id}$`));
  await page.getByRole('button',{name:'Stop sharing',exact:true}).click();
  await expect(page.getByRole('button',{name:'Stopping…',exact:true})).toBeDisabled();
  await expect(page.getByText('Only this device · preview · up to 3600 seconds')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});

for(const destination of ['Storage & keys','Activity','Your copies','Sharing'])test(`public import exits through ${destination}`,async({page})=>{
  let starts=0;
  await page.route('**/api/library?offset=*',route=>route.fulfill({json:{locations:[{location_id:'private',label:'Private archive',purpose:'private',reachable:true,free_bytes:10000,publisher_key_id:'capture'}],models:[],copies:[],public_copies:[],jobs:[],uploads:[],next_offset:null,bittorrent_available:false}}));
  await page.route('**/api/start',route=>{starts++;return route.fulfill({json:{}});});
  await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();
  await page.getByRole('button',{name:'Import public bundle',exact:true}).click();
  await expect(page.getByLabel('Public bundle file')).toBeVisible();
  await page.getByRole('button',{name:destination,exact:true}).click();
  await expect(page.getByLabel('Public bundle file')).toHaveCount(0);
  if(destination==='Storage & keys')await expect(page.getByRole('heading',{name:'Storage & keys',exact:true})).toBeVisible();
  if(destination==='Activity')await expect(page.getByRole('heading',{name:'Activity',exact:true})).toBeVisible();
  if(destination==='Your copies')await expect(page.getByRole('heading',{name:'Your models, kept close.'})).toBeVisible();
  if(destination==='Sharing')await expect(page.getByRole('heading',{name:'Keep a community copy available.'})).toBeVisible();
  expect(starts).toBe(0);
});

test('stopped temporary files require inspection and fresh cleanup consent',async({page})=>{
  const job={job_id:'6'.repeat(64),kind:'torrent_share',model_id:copy.model_id,state:'failed',stage:'failed',created_at:1,bytes_copied:0,bytes_hashed:0,sharing:{ready:false,listen_port:0,charged_bytes:0,connected_peers:0,accounting:'observed-torrent-payload'}};
  let cleared=0,starts=0;
  await page.route('**/api/library?offset=*',route=>route.fulfill({json:{locations:[],models:[],copies:[],public_copies:[copy],jobs:[job],temporary_work_jobs:cleared?[]:[job.job_id],next_offset:null,bittorrent_available:true}}));
  await page.route('**/api/work-preview',route=>route.fulfill({json:{job_id:job.job_id,found:true,logical_bytes:2048,files:2,complete:false,removed:false}}));
  await page.route('**/api/reclaim-work',route=>{expect(route.request().postDataJSON()).toEqual({job_id:job.job_id});cleared++;return route.fulfill({json:{job_id:job.job_id,found:true,logical_bytes:2048,files:2,complete:true,removed:true}});});
  await page.route('**/api/start',route=>{starts++;return route.fulfill({json:{}});});
  await page.goto('/');await page.getByRole('button',{name:'Activity',exact:true}).click();
  await page.getByRole('button',{name:'Review temporary files'}).click();
  await expect(page.getByText('At least 2.0 KB in 2 regular files')).toBeVisible();
  await expect(page.getByRole('button',{name:'Clear temporary files',exact:true})).toBeDisabled();
  await page.getByLabel('Clear only this stopped job’s temporary files').check();
  await page.getByRole('button',{name:'Sharing',exact:true}).click();
  await page.getByRole('button',{name:'Activity',exact:true}).click();
  await page.getByRole('button',{name:'Review temporary files'}).click();
  await expect(page.getByRole('button',{name:'Clear temporary files',exact:true})).toBeDisabled();
  expect(cleared).toBe(0);
  await page.getByLabel('Clear only this stopped job’s temporary files').check();
  await page.getByRole('button',{name:'Clear temporary files',exact:true}).click();
  await expect(page.getByRole('status')).toContainText('Temporary files cleared');
  await expect(page.getByRole('button',{name:'Review temporary files'})).toHaveCount(0);
  await page.getByRole('button',{name:'Sharing',exact:true}).click();
  await expect(page.getByRole('button',{name:'Set up HTTP mirror'})).toBeVisible();
  expect(starts).toBe(0);expect(cleared).toBe(1);
});
