import {test,expect,type Page} from '@playwright/test';
const mid='b'.repeat(64),sid='a'.repeat(64),plan='e'.repeat(64),jid='c'.repeat(64);
const peer={runtime:'missing',setup_id:'d'.repeat(64),bootstrap:['/ip4/203.0.113.1/tcp/4001/p2p/example'],allow_private:false,active:false};
const native={supported:true,plan_id:plan,plan:{version:'0.43.1',target:'linux-amd64',artifact:{archive_bytes:55000000,executable_bytes:125000000,archive_sha512:'f'.repeat(128),executable_sha256:'a'.repeat(64)},download_urls:['https://dist.ipfs.tech/kubo/test.tar.gz','https://github.com/ipfs/kubo/releases/test.tar.gz']},job:null as null|Record<string,unknown>};
const queued={job_id:jid,owner_id:'library',state:'queued',bytes_copied:0,error_code:null};
async function base(page:Page){
 await page.route('**/api/library?offset=*',r=>r.fulfill({json:{locations:[],models:[],copies:[],jobs:[],uploads:[],next_offset:null}}));
 await page.route('**/api/discovery-read',r=>r.fulfill({json:{sources:[{source_id:sid,name:'Community peer source',enabled:true,transport:'peer',snapshot_count:1,freshness:'fresh',root_version:1,catalog_id:mid}],jobs:[],next_offset:null}}));
 await page.route('**/api/discovery-search',r=>r.fulfill({json:{results:[{manifest_id:mid,suppliers:[{source_id:sid,source_name:'Community peer source',entry_id:'model',name:'Preserved model',summary:'Complete companions',freshness:'fresh',model_keys_available:true,transport:'peer'}],supplier_count:1,next_supplier_offset:null}],next_offset:null}}));
 await page.route('**/api/peer-read',r=>r.fulfill({json:peer}));
}
async function open(page:Page){await page.goto('/');await page.getByRole('button',{name:'Find a model',exact:true}).click();}

test('setup survives reload and completion grants no peer access',async({page},info)=>{
 await base(page);let current={...native};let installed=false;const starts:Record<string,unknown>[]=[];let peerEffects=0;const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/peer-read',r=>r.fulfill({json:{...peer,runtime:installed?'available':'missing'}}));
 await page.route('**/api/peer-runtime-read',r=>r.fulfill({json:current}));
 await page.route('**/api/peer-runtime-start',r=>{starts.push(r.request().postDataJSON());current={...native,job:queued};return r.fulfill({json:queued});});
 await page.route('**/api/discovery-start',r=>{peerEffects++;return r.fulfill({json:{}});});
 await open(page);await expect(page.getByRole('button',{name:'Set up peer support',exact:true})).toBeDisabled();expect(starts).toHaveLength(0);
 await page.getByLabel('Download and install this verified peer support on Core.').check();expect(starts).toHaveLength(0);await page.getByRole('button',{name:'Set up peer support',exact:true}).click();await expect(page.getByText('Setup is queued on Core.')).toBeVisible();expect(starts[0]).toMatchObject({plan_id:plan,confirmed:true});expect(Object.keys(starts[0]).sort()).toEqual(['confirmed','operation_id','plan_id']);
 await page.reload();await page.getByRole('button',{name:'Find a model',exact:true}).click();await expect(page.getByRole('button',{name:'Stop setup',exact:true})).toBeVisible();expect(starts).toHaveLength(1);expect(peerEffects).toBe(0);await page.screenshot({path:info.outputPath('native-setup-running.png'),fullPage:true});
 installed=true;current={...native,job:{...queued,state:'succeeded',bytes_copied:55000000}};
 await expect(page.getByRole('heading',{name:'Set up peer support',exact:true})).toHaveCount(0);await expect(page.getByLabel('Allow peer connections for the selected source update')).not.toBeChecked();await expect(page.getByRole('button',{name:'Review model',exact:true})).toBeDisabled();expect(peerEffects).toBe(0);expect(errors).toEqual([]);expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);await page.screenshot({path:info.outputPath('native-setup-complete.png'),fullPage:true});
});

test('stop retains receipt and another attempt needs fresh consent',async({page})=>{
 await base(page);let current={...native,job:queued as Record<string,unknown>|null};let stops=0,starts=0;
 await page.route('**/api/peer-runtime-read',r=>r.fulfill({json:current}));await page.route('**/api/peer-runtime-stop',r=>{expect(r.request().postDataJSON()).toEqual({job_id:jid});stops++;current={...native,job:{...queued,state:'cancelled'}};return r.fulfill({json:current.job});});await page.route('**/api/peer-runtime-start',r=>{starts++;return r.fulfill({json:queued});});
 await open(page);await page.getByRole('button',{name:'Stop setup',exact:true}).click();await expect(page.getByText('Setup stopped.')).toBeVisible();await expect(page.getByLabel('Download and install this verified peer support on Core.')).not.toBeChecked();await expect(page.getByRole('button',{name:'Set up peer support',exact:true})).toBeDisabled();expect(stops).toBe(1);expect(starts).toBe(0);
});

test('an uncertain start reuses the original operation identity',async({page})=>{
 await base(page);const starts:Record<string,unknown>[]=[];await page.route('**/api/peer-runtime-read',r=>r.fulfill({json:native}));await page.route('**/api/peer-runtime-start',r=>{starts.push(r.request().postDataJSON());return starts.length===1?r.fulfill({status:503,json:{error:'host_unavailable'}}):r.fulfill({json:queued});});
 await open(page);await page.getByLabel('Download and install this verified peer support on Core.').check();await page.getByRole('button',{name:'Set up peer support',exact:true}).click();await expect(page.getByRole('alert')).toBeVisible();await page.getByRole('button',{name:'Set up peer support',exact:true}).click();await expect(page.getByRole('button',{name:'Stop setup',exact:true})).toBeVisible();expect(starts).toHaveLength(2);expect(starts[0]).toEqual(starts[1]);
});

test('unsupported acquisition preserves manual runtime recheck',async({page})=>{
 await base(page);let starts=0;await page.route('**/api/peer-runtime-read',r=>r.fulfill({json:{supported:false,plan_id:null,plan:null,job:null}}));await page.route('**/api/peer-runtime-start',r=>{starts++;return r.fulfill({json:queued});});await open(page);await expect(page.getByText('Automatic setup is unavailable on this platform.',{exact:false})).toBeVisible();await expect(page.getByLabel('Download and install this verified peer support on Core.')).toHaveCount(0);await expect(page.getByRole('button',{name:'Check installed runtime again'})).toBeVisible();expect(starts).toBe(0);
});

test('completion preserves unfinished contact settings',async({page})=>{
 await base(page);let installed=false;let current={...native};await page.route('**/api/peer-read',r=>r.fulfill({json:{...peer,setup_id:null,bootstrap:[],runtime:installed?'available':'missing'}}));await page.route('**/api/peer-runtime-read',r=>r.fulfill({json:current}));await page.route('**/api/peer-runtime-start',r=>{current={...native,job:queued};return r.fulfill({json:queued});});await open(page);const contacts=page.getByLabel('Starting contacts');await contacts.fill('/ip4/127.0.0.1/tcp/4001/p2p/draft');await page.getByLabel('Download and install this verified peer support on Core.').check();await page.getByRole('button',{name:'Set up peer support',exact:true}).click();installed=true;current={...native,job:{...queued,state:'succeeded'}};await expect(page.getByRole('heading',{name:'Set up peer support',exact:true})).toHaveCount(0);await expect(contacts).toHaveValue('/ip4/127.0.0.1/tcp/4001/p2p/draft');await expect(page.getByLabel('I understand peers can see')).not.toBeChecked();
});

test('a conservative post-install observation can be rechecked without losing contacts',async({page})=>{
 await base(page);let available=false;let current={...native};await page.route('**/api/peer-read',r=>r.fulfill({json:{...peer,setup_id:null,bootstrap:[],runtime:available?'available':'missing'}}));await page.route('**/api/peer-runtime-read',r=>r.fulfill({json:current}));await page.route('**/api/peer-runtime-start',r=>{current={...native,job:queued};return r.fulfill({json:queued});});await open(page);const contacts=page.getByLabel('Starting contacts');await contacts.fill('/ip4/127.0.0.1/tcp/4001/p2p/draft');await page.getByLabel('Download and install this verified peer support on Core.').check();await page.getByRole('button',{name:'Set up peer support',exact:true}).click();current={...native,job:{...queued,state:'succeeded'}};await expect(page.getByText('Peer support installed.',{exact:false})).toBeVisible();available=true;await page.getByRole('button',{name:'Check installed runtime again'}).click();await expect(page.getByRole('heading',{name:'Set up peer support',exact:true})).toHaveCount(0);await expect(contacts).toHaveValue('/ip4/127.0.0.1/tcp/4001/p2p/draft');await expect(page.getByLabel('I understand peers can see')).not.toBeChecked();
});
